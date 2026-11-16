"""Writes reports/orch-run<N>.md and the run in the shape POST /api/tracker/runs expects.

Per campaign: New since last run, Still in the top list, Dropped. Under every development: its claims,
each with a label (Supported, Unsupported, Unreachable, Unverified), its source, and for supported claims
the evidence excerpt the verifier's code confirmed on the page.

Everything that came from a worker or from the web (titles, summaries, claim text, evidence, error text)
goes through md() first, so it can't add links, images, HTML or formatting to the report. It is text.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from .matching import SUMMARY_MAX, TITLE_MAX, VERDICTS, clip

MODEL_LABEL = "researcher + verifier (two agents)"
LABELS = {"supported": "Supported", "unsupported": "Unsupported", "unreachable": "Unreachable", "unverified": "Unverified"}
STATUS_TEXT = {
    "complete": "Complete",
    "partial": "Partial (a worker failed or stopped early; built from what was gathered)",
    "failed": "Failed",
}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def md(text) -> str:
    """Escape worker- and web-sourced text so it can't add links, images, HTML or formatting to the report."""
    text = _CONTROL.sub("", re.sub(r"\s+", " ", str(text or "")).strip())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]()#|!~])", r"\\\1", text)


def link(url: str, label: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return md(url)
    safe = quote(url, safe=":/?#[]@!$&'*+,;=%~-._")
    safe = safe.replace("(", "%28").replace(")", "%29")
    return f"[{md(label or parts.hostname or url)}]({safe})"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _claim_lines(item) -> list:
    lines = []
    for claim in item.claims:
        lines.append(f"   - **{LABELS[claim.verdict]}:** {md(claim.text)} ({link(claim.source_url, _host(claim.source_url))})")
        if claim.verdict == "supported" and claim.evidence:
            lines.append(f"     Evidence: “{md(claim.evidence)}”")
    return lines


def _source_lines(item) -> list:
    """For a development with no claims this run (carried over): the sources the app already has."""
    lines = []
    for s in item.all_sources:
        host = _host(s["url"])
        quote_text = f": “{md(s['quote'])}”" if s.get("quote") else ""
        lines.append(f"   - Source: {link(s['url'], s.get('title') or host)} ({md(host)}){quote_text}")
    return lines


def _item(rank: int, item, k: int) -> list:
    if item.change == "new":
        note = ""
    elif item.change == "still":
        note = f" (was #{item.previous_rank})" if item.previous_rank else ""
    else:
        note = f" (back in the top {k}; first seen in run {item.first_run})"
    return [f"**#{rank} {md(item.title)}**{note}  ", f"   {md(item.summary)}"] + (_claim_lines(item) or _source_lines(item)) + [""]


def claim_totals(outcomes) -> dict:
    totals = {name: 0 for name in VERDICTS}
    for o in outcomes:
        for item in o.items:
            for claim in item.claims:
                totals[claim.verdict] += 1
    return totals


def run_stats(result, config) -> dict:
    spent = result.budget.spent()
    calls = [c for o in result.outcomes for c in o.calls]
    searches = result.budget.searches()
    totals = claim_totals(result.outcomes)
    return {
        "model_calls": int(spent["steps"]),
        "tokens": int(spent["tokens"]),
        "searches": searches,
        "credits": searches,
        "fetches": int(spent["fetches"]),
        "seconds": result.seconds,
        "campaigns": len(result.outcomes),
        "k": config.k,
        "worker_calls": len(calls),
        "retries": sum(max(0, c.attempts - 1) for c in calls),
        "claims": totals,
        "budget": {"total": {d: round(v, 1) for d, v in result.budget.total.items()},
                   "spent": {d: round(v, 1) for d, v in spent.items()}},
    }


def render(result, config) -> str:
    when = result.started.strftime("%A, %B %d, %Y at %I:%M %p %Z").replace(" 0", " ")
    k = config.k
    lines = [
        f"# Market intel: run {result.run_number}",
        "",
        f"{when} · {STATUS_TEXT.get(result.status, result.status)}  ",
        f"Researcher: {md(_host(config.researcher.url) or config.researcher.url)} · "
        f"Verifier: {md(_host(config.verifier.url) or config.verifier.url)} · K = {k} per campaign · run {result.run_id}",
        "",
    ]
    if result.failed_workers:
        lines += [f"> **Failed worker{'s' if len(result.failed_workers) > 1 else ''}:** {', '.join(result.failed_workers)}. "
                  "See the campaigns below for what that means for each.", ""]
    for note in result.notes:
        lines += [f"> {md(note)}", ""]

    for out in result.outcomes:
        c = out.campaign
        lines += [f"## {md(c['title'])}", "", f"*{md(c['brand'])}* · Research topic: {md(out.topic)}", ""]
        if out.status != "complete":
            lines += [f"**{STATUS_TEXT[out.status].split(' (')[0]}:** {md(out.stop_reason or '')}", ""]
        if out.failed_workers:
            lines += [f"Failed: {', '.join(out.failed_workers)}.", ""]
        for note in out.notes:
            lines += [f"> {md(note)}", ""]
        if out.status == "failed":
            lines += ["No new results for this campaign in this run. Its saved top list is unchanged."]
            if out.stale:
                lines += ["", "Last known top list (stale: not re-checked in this run):", ""]
                for entry in out.stale:
                    lines += [f"**#{entry['rank']} {md(entry['title'])}**  ", f"   {md(entry['summary'])}", ""]
            else:
                lines += [""]
            continue
        ranked = list(enumerate(out.items, start=1))
        new = [(r, i) for r, i in ranked if i.change == "new"]
        still = [(r, i) for r, i in ranked if i.change != "new"]
        lines += ["### New since last run", ""]
        lines += [x for r, i in new for x in _item(r, i, k)] or ["Nothing new this time.", ""]
        lines += [f"### Still in top {k}", ""]
        lines += [x for r, i in still for x in _item(r, i, k)] or ["None.", ""]
        lines += ["### Dropped", ""]
        if out.status == "partial":
            lines += ["Not judged: this campaign's run was partial, so nothing was re-ranked or dropped.", ""]
        elif out.dropped:
            lines += [f"- **{md(d['title'])}** (was #{d.get('last_rank')})" for d in out.dropped] + [""]
        else:
            lines += ["Nothing dropped.", ""]
    for title in result.missing:
        lines += [f"## {md(title)}", "", "Not researched: no campaign with this exact title exists in the app.", ""]

    totals = claim_totals(result.outcomes)
    stats = run_stats(result, config)
    minutes, seconds = divmod(result.seconds, 60)
    lines += [
        "---",
        "",
        f"Claims: {sum(totals.values())} in all: {totals['supported']} supported, {totals['unsupported']} unsupported, "
        f"{totals['unreachable']} unreachable, {totals['unverified']} unverified.",
        "",
        f"Run stats: {stats['worker_calls']} worker calls ({stats['retries']} retries), {stats['model_calls']} steps, "
        f"{stats['tokens']:,} tokens, {stats['searches']} searches, {stats['fetches']} downloads; {minutes} min {seconds} s.",
        "",
    ]
    for name in ("researcher", "verifier"):
        health = result.warm.get(name)
        if health is not None:
            lines += [f"Wake-up of the {name}: " + (f"answered after {health.attempts} {'try' if health.attempts == 1 else 'tries'} "
                                                     f"in {health.ms / 1000:.1f} s." if health.ok else f"did not answer ({md(health.reason)})."), ""]
    calls = [c for o in result.outcomes for c in o.calls]
    if calls:
        lines += ["Worker calls:", ""]
        for call in calls:
            lines += [f"- {call.worker} {call.endpoint}: HTTP {call.http_status or 'none'}, {call.outcome}, "
                      f"{call.attempts} {'try' if call.attempts == 1 else 'tries'}, {call.latency_ms / 1000:.1f} s; "
                      f"charged {call.steps} steps, {call.fetches} downloads, {call.tokens:,} tokens ({call.basis})"
                      + (", stored answer replayed" if call.replayed else "")]
        lines += [""]
    return "\n".join(lines)


def payload(result, markdown: str, config) -> dict:
    """The run, in the shape POST /api/tracker/runs expects."""
    def top_item(item) -> dict:
        return {
            "ref": item.ref,
            "title": clip(item.title, TITLE_MAX),
            "summary": clip(item.summary, SUMMARY_MAX),
            "sources": item.sources,
            "claims": [{"ref": c.ref, "text": c.text, "source_url": c.source_url, "verdict": c.verdict,
                        "evidence": c.evidence if c.verdict == "supported" else None} for c in item.claims],
        }

    return {
        "started_at": result.started.isoformat(),
        "finished_at": result.finished.isoformat(),
        "status": result.status,
        "stop_reason": result.stop_reason,
        "model": MODEL_LABEL,
        "stats": run_stats(result, config),
        "report_markdown": markdown,
        "failed_workers": result.failed_workers,
        "campaigns": [
            {
                "campaign_id": o.campaign["id"],
                "topic": o.topic,
                "status": o.status,
                "stop_reason": clip(o.stop_reason or "", 500) or None,
                "failed_workers": o.failed_workers,
                "top": [] if o.status == "failed" else [top_item(i) for i in o.items],
            }
            for o in result.outcomes
        ],
        "articles": [],
    }
