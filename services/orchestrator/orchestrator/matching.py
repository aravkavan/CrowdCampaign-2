"""What the orchestrator does with the researcher's developments: compare them with what the app remembers,
choose which claims go to the verifier, and put the verdicts back.

The researcher is stateless: it knows nothing about earlier runs. So "new", "still in the top list" and
"back" are decided HERE, by code, from the app's saved state:

  A development counts as one the app already knows when its title is at least 60% similar to a known title,
  or when it cites a source the known development already has and the titles are at least 30% similar
  (or too short to compare). A development that matches nothing is new.

Nothing here asks a model anything, and no text from a worker changes what the code does: titles are only
compared as word sets, claim text is only passed along.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .textcheck import content_words, normalize, similar_titles, url_key

SIMILAR = 0.6          # titles this similar are the same development
SIMILAR_WITH_SOURCE = 0.3
MAX_CLAIMS_PER_ITEM = 12      # what the app stores for one development
VERDICTS = ("supported", "unsupported", "unreachable", "unverified")

# What the CrowdCampaign API accepts for one development.
TITLE_MAX = 200
SUMMARY_MAX = 1000
SOURCE_TITLE_MAX = 300
SOURCES_MAX = 10


@dataclass
class ClaimRow:
    ref: str                      # the claim_id the researcher gave it
    text: str
    source_url: str
    verdict: str = "unverified"
    evidence: str | None = None


@dataclass
class Item:
    """One development in this run's top list."""

    ref: str                      # an existing development id, or "new:<n>"
    change: str                   # new | still | back
    title: str
    summary: str
    claims: list
    sources: list                 # sources to save with this run (the ones the app doesn't have yet)
    all_sources: list             # every source, for the report
    previous_rank: int | None = None
    first_run: int | None = None
    researcher_id: str = ""


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def source_for(url: str) -> dict:
    """The app wants a source as {url, title, quote}. The researcher no longer passes quotes on (the verifier's
    evidence excerpt is the quote that was actually checked), so the title is the site and the quote is empty."""
    return {"url": url, "title": clip(host_of(url) or url, SOURCE_TITLE_MAX), "quote": ""}


def _too_short(title: str) -> bool:
    return len(content_words(title)) < 3


def _score(dev_title: str, dev_urls: set, known: dict):
    """How well a researcher development matches a known one: a number, or None for no match."""
    similarity = similar_titles(dev_title, known["title"])
    known_urls = {url_key(s["url"]) for s in known.get("sources", [])}
    shares_source = bool(dev_urls & known_urls)
    if similarity >= SIMILAR:
        return similarity + (0.5 if shares_source else 0.0)
    if shares_source and (similarity >= SIMILAR_WITH_SOURCE or _too_short(dev_title) or _too_short(known["title"])):
        return similarity + 0.5
    return None


def match_developments(developments: list, memory: dict) -> list:
    """Turn the researcher's developments (in its ranking order) into Items, using the app's saved state for
    this campaign: {"developments": [...], "last_top": [{"rank", "development_id"}]}."""
    known = memory.get("developments", []) or []
    last_rank = {t["development_id"]: t["rank"] for t in memory.get("last_top", []) or []}
    taken: dict = {}
    items, new_count = [], 0
    for dev in developments:
        claims = [ClaimRow(c.claim_id, c.text, c.source_url) for c in dev.claims]
        urls = {url_key(c.source_url) for c in dev.claims}
        best, best_score = None, None
        for candidate in known:
            score = _score(dev.title, urls, candidate)
            if score is not None and (best_score is None or score > best_score):
                best, best_score = candidate, score
        if best is not None and best["id"] in taken:
            # The researcher listed the same development twice: keep one entry and pool the claims.
            mine = taken[best["id"]]
            have = {(normalize(c.text), url_key(c.source_url)) for c in mine.claims}
            for c in claims:
                if len(mine.claims) < MAX_CLAIMS_PER_ITEM and (normalize(c.text), url_key(c.source_url)) not in have:
                    mine.claims.append(c)
            _add_sources(mine, claims)
            continue
        if best is not None:
            item = Item(ref=best["id"], change="still" if best["id"] in last_rank else "back", title=best["title"],
                        summary=best["summary"], claims=claims[:MAX_CLAIMS_PER_ITEM], sources=[],
                        all_sources=[dict(s) for s in best.get("sources", [])],
                        previous_rank=last_rank.get(best["id"]), first_run=best.get("first_run"),
                        researcher_id=dev.id)
            taken[best["id"]] = item
        else:
            new_count += 1
            item = Item(ref=f"new:{new_count}", change="new", title=clip(dev.title, TITLE_MAX),
                        summary=clip(dev.summary, SUMMARY_MAX), claims=claims[:MAX_CLAIMS_PER_ITEM], sources=[],
                        all_sources=[], researcher_id=dev.id)
        _add_sources(item, claims)
        items.append(item)
    return items


def _add_sources(item: Item, claims: list) -> None:
    have = {url_key(s["url"]) for s in item.all_sources}
    for c in claims:
        key = url_key(c.source_url)
        if key in have or len(item.sources) >= SOURCES_MAX:
            continue
        have.add(key)
        source = source_for(c.source_url)
        item.sources.append(source)
        item.all_sources.append(source)


def carried_items(memory: dict, exclude_refs: set, limit: int) -> list:
    """The previous top list, in its old order, for a campaign whose research was cut short: nothing is dropped
    without a new ranking. `exclude_refs` are developments already in the new list."""
    known = {d["id"]: d for d in memory.get("developments", []) or []}
    out = []
    for entry in sorted(memory.get("last_top", []) or [], key=lambda t: t["rank"]):
        dev = known.get(entry["development_id"])
        if dev is None or dev["id"] in exclude_refs:
            continue
        out.append(Item(ref=dev["id"], change="still", title=dev["title"], summary=dev["summary"], claims=[], sources=[],
                        all_sources=[dict(s) for s in dev.get("sources", [])], previous_rank=entry["rank"],
                        first_run=dev.get("first_run")))
        if len(out) >= limit:
            break
    return out


def dropped_items(memory: dict, kept_refs: set) -> list:
    """Known developments that were in the last top list and are not in this run's."""
    known = {d["id"]: d for d in memory.get("developments", []) or []}
    out = []
    for entry in sorted(memory.get("last_top", []) or [], key=lambda t: t["rank"]):
        dev = known.get(entry["development_id"])
        if dev is not None and dev["id"] not in kept_refs:
            out.append({"id": dev["id"], "title": dev["title"], "last_rank": entry["rank"]})
    return out


def stale_top(memory: dict) -> list:
    """Last run's top list, for a campaign this run could not research."""
    known = {d["id"]: d for d in memory.get("developments", []) or []}
    out = []
    for entry in sorted(memory.get("last_top", []) or [], key=lambda t: t["rank"]):
        dev = known.get(entry["development_id"])
        if dev is not None:
            out.append({"rank": entry["rank"], "id": dev["id"], "title": dev["title"], "summary": dev["summary"],
                        "sources": [dict(s) for s in dev.get("sources", [])]})
    return out


# ---------------------------------------------------------------------------- claims for the verifier

def claim_key(claim: ClaimRow) -> tuple:
    return normalize(claim.text), url_key(claim.source_url)


def select_claims(items: list, cap: int) -> list:
    """Which claims to send to the verifier: round-robin by development rank (every development's first claim,
    then every second claim ...), at most `cap`, identical claims once."""
    chosen, seen, depth = [], set(), 0
    while len(chosen) < cap:
        progressed = False
        for item in items:
            if depth >= len(item.claims):
                continue
            progressed = True
            claim = item.claims[depth]
            key = claim_key(claim)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(claim)
            if len(chosen) >= cap:
                break
        if not progressed:
            break
        depth += 1
    return chosen


def apply_verdicts(items: list, verdicts: dict) -> dict:
    """Put the verifier's verdicts on the claims. Every claim without one stays "unverified" (never sent, or the
    verifier didn't get to it). Identical claims share the verdict of the copy that was sent. Returns the counts."""
    by_key = {}
    for item in items:
        for claim in item.claims:
            if claim.ref in verdicts:
                by_key[claim_key(claim)] = verdicts[claim.ref]
    counts = {name: 0 for name in VERDICTS}
    for item in items:
        for claim in item.claims:
            found = verdicts.get(claim.ref) or by_key.get(claim_key(claim))
            if found:
                claim.verdict, claim.evidence = found
            else:
                claim.verdict, claim.evidence = "unverified", None
            counts[claim.verdict] += 1
    return counts


def count_claims(items: list) -> dict:
    counts = {name: 0 for name in VERDICTS}
    for item in items:
        for claim in item.claims:
            counts[claim.verdict] += 1
    return counts
