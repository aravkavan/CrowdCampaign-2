"""The trace log, the same in all three services: one JSON line per request received and per call made.

Fields on every line:
  ts, service, run_id, request_id, step, direction (in|out), endpoint_or_tool,
  status, latency_ms, attempt, tokens            (and, only when useful, a short "detail")

  direction "in"   a request this service received. status is what it answered; latency_ms
                   is how long it took to answer (this machine's own clock).
  direction "out"  a call this service made (to a worker, a model, a search API, a web page,
                   the CrowdCampaign API). latency_ms is measured by the CALLER, so the three
                   logs never depend on each other's clocks. status 0 means no answer at all
                   (connection refused, timeout); detail says which.

Follow one request across all three logs with run_id and request_id.
Secrets never reach the log: every string is cleaned by Redactor first, and tokens and keys
are never passed to this module in the first place.

Shared file: edit shared/trace.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

SECRET_NAME = re.compile(r"(TOKEN|KEY|PASSWORD|SECRET)", re.IGNORECASE)
_PATTERNS = (
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=\-]{6,}", re.IGNORECASE), r"\1[hidden]"),
    (re.compile(r"((?:api[_-]?key|key|token|password|secret)=)[^&\s\"']+", re.IGNORECASE), r"\1[hidden]"),
)


class Redactor:
    """Removes known secret values (and key=..., Bearer ... patterns) from text before it is logged."""

    def __init__(self):
        self._secrets: list[str] = []

    def add(self, *values) -> None:
        for value in values:
            if isinstance(value, str) and len(value) >= 8 and value not in self._secrets:
                self._secrets.append(value)
                self._secrets.sort(key=len, reverse=True)

    def learn_from_env(self, environ=None) -> None:
        for name, value in (environ if environ is not None else os.environ).items():
            if SECRET_NAME.search(name):
                self.add(value.strip())

    def clean(self, text) -> str:
        text = str(text)
        for secret in self._secrets:
            text = text.replace(secret, "[hidden]")
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ServiceTrace:
    """Writes the log lines. Thread-safe: a worker answers several requests at once."""

    def __init__(self, service: str, *, path=None, stream=None, redactor: Redactor | None = None):
        self.service = service
        self.path = Path(path) if path else None
        self.stream = stream  # for example sys.stdout, so a host like Render shows the log
        self.redactor = redactor or Redactor()
        self._lock = threading.Lock()
        self._file = None
        self._file_failed = False

    def write(self, *, direction: str, endpoint_or_tool: str, status, latency_ms, run_id=None, request_id=None,
              step: int = 0, attempt: int = 1, tokens: int = 0, detail=None) -> dict:
        record = {
            "ts": now_iso(),
            "service": self.service,
            "run_id": run_id,
            "request_id": request_id,
            "step": int(step or 0),
            "direction": direction,
            "endpoint_or_tool": self.redactor.clean(endpoint_or_tool)[:300],
            "status": int(status or 0),
            "latency_ms": max(0, int(latency_ms or 0)),
            "attempt": int(attempt or 1),
            "tokens": int(tokens or 0),
        }
        if detail:
            record["detail"] = self.redactor.clean(detail)[:1000]
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            if self.stream is not None:
                print(line, file=self.stream, flush=True)
            if self.path is not None and not self._file_failed:
                try:
                    if self._file is None:
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        self._file = self.path.open("a", encoding="utf-8")
                    self._file.write(line + "\n")
                    self._file.flush()
                except OSError as err:  # a read-only disk must not stop the service
                    self._file_failed = True
                    print(f"trace file {self.path} can't be written ({err.__class__.__name__}); logging to stdout only",
                          file=sys.stderr)
        return record

    def bind(self, run_id, request_id) -> "BoundTrace":
        return BoundTrace(self, run_id, request_id)

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


class BoundTrace:
    """A trace tied to one request: it stamps run_id, request_id and the current step on every line."""

    def __init__(self, trace: ServiceTrace, run_id, request_id):
        self.trace = trace
        self.run_id = run_id
        self.request_id = request_id
        self.step = 0

    def inbound(self, endpoint: str, status, latency_ms, *, attempt: int = 1, tokens: int = 0, detail=None) -> dict:
        return self.trace.write(direction="in", endpoint_or_tool=endpoint, status=status, latency_ms=latency_ms,
                                run_id=self.run_id, request_id=self.request_id, step=0, attempt=attempt,
                                tokens=tokens, detail=detail)

    def call(self, endpoint_or_tool: str, status, latency_ms, *, attempt: int = 1, tokens: int = 0, detail=None) -> dict:
        return self.trace.write(direction="out", endpoint_or_tool=endpoint_or_tool, status=status,
                                latency_ms=latency_ms, run_id=self.run_id, request_id=self.request_id, step=self.step,
                                attempt=attempt, tokens=tokens, detail=detail)

    def event(self, kind: str, **fields):
        """The 1B tracker's trace call. Only HTTP round trips are calls, so only those become log lines."""
        if kind != "http":
            return None
        host = fields.get("host") or ""
        method = fields.get("method") or "GET"
        path = fields.get("path") or ""
        tool = str(fields.get("tool") or fields.get("service") or "")
        # "search_web POST api.tavily.com/search", "fetch_article GET example.com/page", "model POST ..."
        label = (f"{tool} " if tool else "") + f"{method} {host}{path}" if host else (tool or "call")
        notes = [str(fields[k]) for k in ("outcome", "reason") if fields.get(k) not in (None, "ok")]
        return self.call(label, fields.get("status") or 0, fields.get("latency_ms") or 0,
                         attempt=fields.get("attempt") or 1, tokens=fields.get("tokens") or 0,
                         detail="; ".join(notes) or None)
