"""Calls the language model through the OpenAI-compatible chat API (Gemini, Groq, ...).

One request = one model decision. Transient failures are retried by failures.with_retries;
terminal ones (bad key, daily quota) stop the work.

Each HTTP round trip becomes one trace line, with the tokens it used on the same line.
The request's remaining time caps both the HTTP timeout and every wait between retries.

Shared file: edit shared/llm.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from .failures import TimeBudgetExhausted, TransientError, classify_http_error, with_retries

MIN_OUTPUT_TOKENS = 128


@dataclass
class Reply:
    text: str
    tokens_in: int
    tokens_out: int
    estimated: bool  # True when the provider didn't report usage and we estimated it
    finish_reason: str | None

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


def estimate_input_tokens(messages: list) -> int:
    """A rough count (4 characters per token, plus a safety margin) used to decide, before a call,
    whether the token budget can afford it."""
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return int(chars / 4 * 1.25) + 20


class ChatModel:
    def __init__(self, settings, retry_policy, trace, session=None, time_left=None):
        """trace: a BoundTrace. time_left: a function returning the seconds left in this request."""
        self.settings = settings
        self.retry_policy = retry_policy
        self.trace = trace
        self.session = session or requests.Session()
        self.time_left = time_left
        self.url = settings.base_url + "chat/completions"
        parts = urlsplit(self.url)
        self.host = parts.hostname
        self.path = parts.path

    @property
    def label(self) -> str:
        return f"{self.settings.label} ({self.settings.name})"

    def complete(self, messages: list, max_output_tokens: int | None = None) -> Reply:
        out_cap = min(self.settings.max_output_tokens, max_output_tokens or self.settings.max_output_tokens)
        body = {"model": self.settings.name, "messages": messages, "max_tokens": max(MIN_OUTPUT_TOKENS, out_cap)}
        body.update(self.settings.extra)
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        def attempt(number: int) -> Reply:
            read_timeout = self.settings.timeout
            if self.time_left is not None:
                left = self.time_left()
                if left <= 0:
                    raise TimeBudgetExhausted("model", "the time budget ran out")
                read_timeout = max(1.0, min(read_timeout, left))
            started = time.monotonic()
            status = None

            def log(tokens: int = 0, why: str | None = None):
                self.trace.event("http", service="model", tool="model", method="POST", host=self.host, path=self.path,
                                 attempt=number, status=status, tokens=tokens, reason=why,
                                 latency_ms=int((time.monotonic() - started) * 1000))

            try:
                response = self.session.post(self.url, json=body, headers=headers, timeout=(10, read_timeout))
                status = response.status_code
            except requests.Timeout:
                log(why="timeout")
                raise TransientError("model", "the request timed out") from None
            except requests.ConnectionError:
                log(why="connection error")
                raise TransientError("model", f"can't reach {self.host} (is the internet connection up?)") from None
            if status >= 400:
                log()
                raise classify_http_error("model", status, response.headers, response.text)
            try:
                data = response.json()
                choice = data["choices"][0]
            except (ValueError, KeyError, IndexError, TypeError):
                log(why="unexpected reply format")
                raise TransientError("model", "the reply was not in the expected format") from None
            content = (choice.get("message") or {}).get("content")
            if isinstance(content, list):  # some providers return content parts
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            text = content or ""
            usage = data.get("usage") or {}
            tokens_in = usage.get("prompt_tokens")
            total = usage.get("total_tokens")
            tokens_out = (total - tokens_in) if (total is not None and tokens_in is not None) else usage.get("completion_tokens")
            estimated = tokens_in is None or tokens_out is None
            if tokens_in is None:
                tokens_in = sum(len(str(m.get("content", ""))) for m in messages) // 4
            if tokens_out is None:
                tokens_out = len(text) // 4
            log(tokens=int(tokens_in) + int(tokens_out))
            return Reply(text=text, tokens_in=int(tokens_in), tokens_out=int(tokens_out), estimated=estimated,
                         finish_reason=choice.get("finish_reason"))

        return with_retries(attempt, service="model", policy=self.retry_policy, trace=self.trace,
                            time_left=self.time_left)
