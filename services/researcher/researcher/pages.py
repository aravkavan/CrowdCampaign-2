"""Fetching one web page as clean text, through the guardrail. Used by both workers.

fetch_article never raises for a bad URL. Its result has a status:
  fetched   the page was downloaded and has readable text
  rejected  the guardrail refused the URL before any request was sent (nothing was fetched)
  failed    the URL passed the guardrail but the download did not work
A BudgetExhausted raised by the gate (no fetches or no time left) is NOT turned into a result: it
passes through, because the caller has to stop.

Shared file: edit shared/pages.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import time

from .extract import decode, extract, looks_like_instructions
from .failures import TimeBudgetExhausted
from .guard import FetchFailed, Rejected, RobotsCache, check_url, fetch_page
from .textcheck import url_key  # noqa: F401  (the workers import it from here)


def fetch_article(url: str, policy, *, trace=None, robots: RobotsCache | None = None, deadline: float | None = None,
                  gate=None, service: str = "fetch_article") -> dict:
    """Download one article through the guardrail and return its text.

    deadline is a time.monotonic() value: the end of the whole request. The download also stops
    after policy.timeout_seconds, whichever comes first.
    """
    result = {"url": url}
    try:
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            raise TimeBudgetExhausted("fetch", "the time budget ran out")
        check_url(url, policy)  # refuse bad URLs before anything else, robots.txt included
        if robots is not None:
            reason = robots.check(url)
            if reason:
                raise Rejected(reason)
        fetch_deadline = now + policy.timeout_seconds
        if deadline is not None:
            fetch_deadline = min(fetch_deadline, deadline)
        page = fetch_page(url, policy, trace=trace, service=service, deadline=fetch_deadline, gate=gate)
        html = decode(page.body, page.charset)
        content = extract(html, page.content_type)
        text = content["text"]
        flagged = looks_like_instructions(content["title"] + "\n" + text)
        result.update(
            final_url=page.final_url,
            title=content["title"] or url,
            published=content["published"],
            http_status=page.http_status,
            bytes=len(page.body),
            truncated=page.truncated,
            flagged=flagged,
            words=len(text.split()),
            text=text,
        )
        if len(text) < 200:
            result.update(status="failed", reason="the page has almost no readable text (it may need JavaScript or a login)")
        else:
            result["status"] = "fetched"
    except Rejected as err:
        result.update(status="rejected", reason=err.reason)
    except FetchFailed as err:
        result.update(status="failed", reason=err.reason, http_status=err.http_status)
    return result
