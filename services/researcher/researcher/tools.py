"""The researcher's web search tool (Tavily). Fetching pages is in pages.py, shared with the verifier.

search_web(query) never decides anything: it sends the query and returns a list of results.
Every round trip is one trace line (service "search_web"); transient failures are retried with
backoff and never past the request's time budget; a bad key or used-up credits stop the work.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlsplit

import requests

from .failures import TerminalError, TransientError, classify_http_error, with_retries


def search_web(query: str, *, config, trace, time_left=None, session=None) -> dict:
    """Search recent news with Tavily. Returns {'query', 'results': [...], 'credits'}."""
    query = (query or "").strip()
    if not 2 <= len(query) <= 400:
        raise ValueError("the search query must be 2 to 400 characters")
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise TerminalError("search", "TAVILY_API_KEY is not set. Add it to this service's .env (free key at https://app.tavily.com).")
    s = config.search
    body = {
        "query": query,
        "topic": s.topic,
        "time_range": s.time_range,
        "max_results": s.max_results,
        "search_depth": s.search_depth,
        "include_answer": False,
        "include_raw_content": False,
        "include_published_date": True,
        "include_usage": True,
        "exclude_domains": list(config.fetch.blocked_hosts)[:150],
    }
    url = f"{s.base_url}/search"
    parts = urlsplit(url)
    http = session or requests

    def attempt(number: int) -> dict:
        read_timeout = 30.0
        if time_left is not None:
            read_timeout = max(1.0, min(read_timeout, time_left()))
        started = time.monotonic()
        status = None

        def log(why=None):
            trace.event("http", service="search", tool="search_web", method="POST", host=parts.hostname,
                        path=parts.path, attempt=number, status=status, reason=why,
                        latency_ms=int((time.monotonic() - started) * 1000))

        try:
            response = http.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=(10, read_timeout))
            status = response.status_code
        except requests.Timeout:
            log("timeout")
            raise TransientError("search", "the search request timed out") from None
        except requests.ConnectionError:
            log("connection error")
            raise TransientError("search", f"can't reach {parts.hostname} (is the internet connection up?)") from None
        log()
        if status >= 400:
            raise classify_http_error("search", status, response.headers, response.text)
        try:
            return response.json()
        except ValueError:
            raise TransientError("search", "the search reply was not JSON") from None

    data = with_retries(attempt, service="search", policy=config.retries, trace=trace, time_left=time_left)
    results = []
    for item in data.get("results") or []:
        link = str(item.get("url") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue
        results.append({
            "title": str(item.get("title") or "")[:200],
            "url": link,
            "published": str(item.get("published_date") or "")[:40],
            "snippet": " ".join(str(item.get("content") or "").split())[:280],
        })
    credits = (data.get("usage") or {}).get("credits")
    if credits is None:
        credits = 2 if s.search_depth == "advanced" else 1
    return {"query": query, "results": results, "credits": int(credits)}
