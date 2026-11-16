"""`python -m orchestrator handcheck`: the work sheet for checking the verifier BY HAND (ORCHESTRATION.md, question 5).

From the latest saved run it picks:
  * EVERY claim the verifier called "unsupported"      (was it too strict? does the page really not say it?)
  * 10 "supported" claims, chosen at random              (was it too lenient? is the excerpt on the page, and does it say the claim?)
and lists the "unreachable" ones for reference.

The random pick is repeatable: the same run gives the same ten unless you pass --seed, so you can print the sheet
again tomorrow and get the same list. Only claims checked in THIS run are used. A development that was carried over
from an earlier run shows that run's claims, and those are left out here.

Everything that came from the web or from a worker passes through report.md() first, so it is printed as text:
no links, HTML or terminal codes can come out of a claim or an excerpt.
"""
from __future__ import annotations

import random

from .report import _host, link, md

SAMPLE_DEFAULT = 10
SAMPLE_MAX = 200
VERDICTS = ("supported", "unsupported", "unreachable", "unverified")


def collect(run: dict) -> dict:
    """The claims checked in this run, grouped by verdict, in the order the app shows them."""
    found = {v: [] for v in VERDICTS}
    number = run.get("number")
    for campaign in run.get("campaigns") or []:
        if campaign.get("status") == "failed":      # a failed campaign shows last run's list, which is not this run's work
            continue
        for item in campaign.get("top") or []:
            if item.get("claims_run") != number:     # carried over: its claims were checked in an earlier run
                continue
            for claim in item.get("claims") or []:
                verdict = claim.get("verdict")
                if verdict not in found:
                    continue
                found[verdict].append({
                    "campaign": campaign.get("title") or "",
                    "rank": item.get("rank"),
                    "development": (item.get("development") or {}).get("title") or "",
                    "ref": claim.get("ref") or "",
                    "text": claim.get("text") or "",
                    "source_url": claim.get("source_url") or "",
                    "evidence": claim.get("evidence"),
                })
    return found


def default_seed(run: dict) -> str:
    return str(run.get("id") or run.get("number") or "run")


def pick(found: dict, samples: int, seed) -> list:
    """`samples` supported claims chosen at random (all of them when there are no more than that), in page order."""
    pool = list(found["supported"])
    samples = max(0, min(int(samples), SAMPLE_MAX))
    if len(pool) <= samples:
        return pool
    chosen = sorted(random.Random(seed).sample(range(len(pool)), samples))
    return [pool[i] for i in chosen]


def _where(claim: dict) -> str:
    rank = f" › #{claim['rank']}" if claim.get("rank") else ""
    return f"{md(claim['campaign'])}{rank} {md(claim['development'])}".strip()


def _page(claim: dict) -> str:
    url = claim["source_url"]
    return link(url, _host(url) or url) if url else "(no address)"


def render(run: dict, found: dict, sample: list, *, samples: int, seed) -> str:
    counts = {v: len(found[v]) for v in VERDICTS}
    total = sum(counts.values())
    lines = [
        f"# Hand-check sheet: run {run.get('number')}",
        "",
        f"Claims checked in this run: {total}: {counts['supported']} supported, {counts['unsupported']} unsupported, "
        f"{counts['unreachable']} unreachable, {counts['unverified']} unverified (never sent to the verifier).",
        "",
        f"Random pick: {len(sample)} of {counts['supported']} supported claims (seed `{md(seed)}`; "
        "`--seed` gives a different pick, the same seed gives the same pick again).",
        "",
        "## How to check",
        "",
        "1. Open the page behind each link and look for the words of the claim (Ctrl+F).",
        "2. Tick one box per question, and write a note when the answer is not obvious. The page may have changed since the run.",
        "3. Count your ticks at the bottom and put the numbers in ORCHESTRATION.md, question 5.",
        "",
        f"## A. Every unsupported claim ({counts['unsupported']})",
        "",
        "The verifier says the page does NOT state the claim. Is that right?",
        "",
    ]
    if not found["unsupported"]:
        lines += ["None this run.", ""]
    for n, claim in enumerate(found["unsupported"], start=1):
        lines += [f"**A{n}.** {_where(claim)}  ",
                  f"- Claim: {md(claim['text'])}",
                  f"- Page: {_page(claim)}",
                  "- Does the page say it?  [ ] no, the verifier was right   [ ] yes, the verifier was wrong   [ ] cannot tell",
                  "- Note:", ""]
    lines += [f"## B. {len(sample)} random supported claims", "",
              "The verifier says the page states the claim and gave an excerpt. Is it true?", ""]
    if not sample:
        lines += ["None this run.", ""]
    for n, claim in enumerate(sample, start=1):
        lines += [f"**B{n}.** {_where(claim)}  ",
                  f"- Claim: {md(claim['text'])}",
                  f"- Page: {_page(claim)}",
                  f"- Excerpt: “{md(claim['evidence'] or '')}”",
                  "- Is the excerpt on the page?  [ ] yes   [ ] no",
                  "- Does it say the claim, with every number and name?  [ ] yes   [ ] no   [ ] only partly",
                  "- Note:", ""]
    lines += [f"## C. Unreachable claims ({counts['unreachable']}), for reference", "",
              "The verifier could not fetch these pages (or the address was refused). Not part of the hand-check, "
              "but the count belongs in question 5. Does the page open for you?", ""]
    if not found["unreachable"]:
        lines += ["None this run.", ""]
    for n, claim in enumerate(found["unreachable"], start=1):
        lines += [f"- **C{n}.** {md(claim['text'])} {_page(claim)}   [ ] opens for me   [ ] does not open"]
    if found["unreachable"]:
        lines += [""]
    lines += [
        "## Your tally",
        "",
        f"- Unsupported claims checked: {counts['unsupported']}.  Verifier right: ___   wrong (the page did say it): ___   cannot tell: ___",
        f"- Supported claims checked: {len(sample)}.  Excerpt on the page and says the claim: ___   not: ___   partly: ___",
        "",
    ]
    return "\n".join(lines)
