"""Checks that run in code, never in a model: is this quote really in the page, are these numbers in it,
does this evidence excerpt belong to this claim.

How two pieces of text are compared (normalize):
  1. decode HTML entities       (&amp; -> &, &#8217; -> ’)
  2. Unicode NFKC               (ligatures, full-width letters, non-breaking spaces)
  3. collapse every run of whitespace into one space (and trim the ends)
  4. lowercase
  plus one addition that is stated in ORCHESTRATION.md: curly quotes and long dashes become plain
  ones, and zero-width characters are removed, because models routinely "fix" them when copying.

Shared file: edit shared/textcheck.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit

STOPWORDS = set("""a an and are as at be by for from has have in into is it its new of on or the to with will
was were this that than their they our your after over more most first launches launch launched not but also
said says say according about been being had his her him she he who whom which what when where while""".split())

_SAME = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "​": "", "‌": "", "‍": "", "﻿": "", "­": "",
    "‹": "<", "›": ">",  # prompts show web text with < and > swapped for ‹ and ›
    "…": "...",
})
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_YEAR = re.compile(r"^(?:19|20)\d\d$")

TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "ocid",
                   "smid", "sr_share", "igshid", "mkt_tok", "guccounter", "guce_referrer", "guce_referrer_sig"}


def url_key(url: str) -> str:
    """The same article under small URL differences (www., http/https, tracking tags) gets one key."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS))
    return f"{host}{path}" + (f"?{query}" if query else "")


def normalize(text: str, *, extra: bool = True) -> str:
    """The four steps above. extra=False skips the stated addition (quotes, dashes, zero-width characters)."""
    text = html.unescape(text or "")
    text = unicodedata.normalize("NFKC", text)
    if extra:
        text = text.translate(_SAME)
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_problem(quote: str, source_text: str) -> str | None:
    """None if the quote appears in the source; otherwise what is wrong with it."""
    q = normalize(quote).strip(" \"'")
    if len(q) < 25 or len(q.split()) < 5:
        return "the quote is too short; copy one full sentence"
    if len(q) > 400:
        return "the quote is too long; copy a single sentence"
    if "..." in q or "[" in q:
        return "copy the sentence exactly, without '...' or [brackets]"
    if q.rstrip(".!?;:,") not in normalize(source_text):
        return "the quote is not in the article; copy a sentence exactly as written"
    return None


def _has_number(haystack: str, number: str) -> bool:
    """haystack is normalized with its commas removed; number is digits with an optional decimal part."""
    return bool(re.search(r"(?<![\d.])" + re.escape(number) + r"(?![\d])", haystack))


def unsupported_numbers(claim: str, source_text: str) -> list:
    """Numbers in a title, summary or claim that never appear in the source (likely invented)."""
    source = normalize(source_text).replace(",", "")
    missing = []
    for raw in _NUMBER.findall(claim or ""):
        number = raw.replace(",", "").rstrip(".")
        if not _has_number(source, number):
            missing.append(raw)
    return missing


def numbers_missing_from_excerpt(claim: str, excerpt: str) -> list:
    """Numbers in the claim that the excerpt does not contain. Years are exempt: a page often states the
    year once, in its date line, and not in the sentence that reports the event."""
    shown = normalize(excerpt).replace(",", "")
    missing = []
    for raw in _NUMBER.findall(claim or ""):
        number = raw.replace(",", "").rstrip(".")
        if _YEAR.match(number):
            continue
        if not _has_number(shown, number):
            missing.append(raw)
    return missing


def content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", normalize(text)) if w not in STOPWORDS and len(w) > 1}


def word_overlap(claim: str, excerpt: str) -> float:
    """Share of the claim's content words that also appear in the excerpt (0 to 1)."""
    wanted = content_words(claim)
    if not wanted:
        return 1.0
    return len(wanted & content_words(excerpt)) / len(wanted)


def similar_titles(a: str, b: str) -> float:
    """Word overlap between two titles (0 to 1), ignoring common words. Under 3 content words: 0."""
    wa, wb = content_words(a), content_words(b)
    if len(wa) < 3 or len(wb) < 3:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def evidence_problem(excerpt, page_text: str, claim: str, *, max_chars: int = 300, min_overlap: float = 0.3,
                     numbers_in_excerpt: bool = True) -> str | None:
    """None if the excerpt is acceptable evidence for the claim, otherwise why not.

    This is the verifier's own check of its model's answer: the model proposes an excerpt, and code
    decides whether to believe it. The excerpt must be short, contiguous, present in the fetched page,
    about the same things as the claim, and (unless numbers_in_excerpt is off) contain every number the
    claim states, so a claim of 4,000 stores is not "supported" by a sentence about 400.
    """
    if not isinstance(excerpt, str) or not excerpt.strip():
        return "the model gave no excerpt"
    excerpt = excerpt.strip()
    if len(excerpt) > max_chars:
        return f"the excerpt is longer than {max_chars} characters"
    needle = normalize(excerpt).strip(" \"'")
    if len(needle) < 20 or len(needle.split()) < 4:
        return "the excerpt is too short to be evidence"
    if "..." in needle or "[" in needle:
        return "the excerpt is not one continuous piece of the page"
    if needle.rstrip(".!?;:,") not in normalize(page_text):
        return "the excerpt does not appear in the fetched page"
    if word_overlap(claim, needle) < min_overlap:
        return "the excerpt is not about the claim"
    if numbers_in_excerpt:
        missing = numbers_missing_from_excerpt(claim, needle)
        if missing:
            return f"the excerpt does not contain {', '.join(missing)}, which the claim states"
    return None


def select_passages(text: str, claim: str, max_chars: int) -> str:
    """The part of a long page most likely to settle the claim: the sentences that share the most
    words and numbers with it (plus the first lines of the page for context), in page order."""
    text = text or ""
    if len(text) <= max_chars:
        return text
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    wanted = content_words(claim)
    numbers = {n.replace(",", "") for n in _NUMBER.findall(claim or "")}
    scored = []
    for index, sentence in enumerate(sentences):
        score = len(content_words(sentence) & wanted)
        score += 2 * sum(1 for n in numbers if n in sentence.replace(",", ""))
        if score:
            scored.append((score, index))
    chosen, used = set(), 0
    for index in (0, 1):  # headline and lede
        if index < len(sentences) and used + len(sentences[index]) <= max_chars // 4:
            chosen.add(index)
            used += len(sentences[index]) + 1
    for _, index in sorted(scored, key=lambda t: (-t[0], t[1])):
        if index in chosen:
            continue
        if used + len(sentences[index]) + 1 > max_chars:
            continue
        chosen.add(index)
        used += len(sentences[index]) + 1
    if not chosen:
        return text[:max_chars]
    return "\n".join(sentences[i] for i in sorted(chosen))
