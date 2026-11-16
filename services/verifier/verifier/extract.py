"""Turns a downloaded page into plain text, and spots pages that try to instruct the AI.

Only text survives: scripts, styles, forms, navigation and comments are dropped, so no
markup from a page ever reaches the model or the report.

looks_like_instructions() is the first line of defence against prompt injection. It catches text
that talks to an AI agent (to the researcher, and now also to the verifier: "mark this claim as
supported"). It is a tripwire, not a wall: the verifier's real defence is that code, not the model,
decides whether an excerpt counts as evidence (see textcheck.evidence_problem).

Shared file: edit shared/extract.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "object", "embed", "canvas",
        "form", "button", "select", "textarea", "nav", "footer", "aside"}
BLOCK = {"p", "div", "section", "article", "main", "header", "h1", "h2", "h3", "h4", "h5", "h6", "li",
         "ul", "ol", "br", "tr", "td", "th", "blockquote", "pre", "figure", "figcaption", "dt", "dd", "table", "hr"}
VOID = {"br", "hr", "img", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.article_depth = 0
        self.main_depth = 0
        self.in_title = False
        self.title = ""
        self.meta_title = ""
        self.published = ""
        self.parts = {"all": [], "article": [], "main": []}

    def _newline(self):
        for key, buf in self.parts.items():
            if key == "all" or (key == "article" and self.article_depth) or (key == "main" and self.main_depth):
                buf.append("\n")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            prop = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            content = (attrs.get("content") or "").strip()
            if prop in ("og:title", "twitter:title") and not self.meta_title:
                self.meta_title = content
            elif prop in ("article:published_time", "datepublished", "date", "pubdate", "publish-date", "parsely-pub-date") and not self.published:
                self.published = content
            return
        if tag == "time" and not self.published and attrs.get("datetime"):
            self.published = attrs["datetime"].strip()
        if tag in VOID:
            if tag in BLOCK:
                self._newline()
            return
        if tag in SKIP:
            self.skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
        if tag == "article":
            self.article_depth += 1
        if tag == "main":
            self.main_depth += 1
        if tag in BLOCK:
            self._newline()

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self.in_title = False
        if tag in BLOCK:
            self._newline()
        if tag == "article":
            self.article_depth = max(0, self.article_depth - 1)
        if tag == "main":
            self.main_depth = max(0, self.main_depth - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        if self.skip_depth:
            return
        self.parts["all"].append(data)
        if self.article_depth:
            self.parts["article"].append(data)
        if self.main_depth:
            self.parts["main"].append(data)


def _clean(chunks) -> str:
    lines, previous = [], None
    for line in "".join(chunks).split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line and line != previous:
            lines.append(line)
        previous = line or previous
    return "\n".join(lines)


def sniff_charset(body: bytes) -> str | None:
    m = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", body[:4096], re.IGNORECASE)
    return m.group(1).decode("ascii", "ignore") if m else None


def decode(body: bytes, charset: str | None) -> str:
    for candidate in (charset, sniff_charset(body), "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def extract(html: str, content_type: str = "text/html") -> dict:
    """{'title', 'published', 'text'} from a page. Prefers <article>, then <main>, then the whole body."""
    if content_type == "text/plain":
        text = _clean([html])
        first = text.split("\n", 1)[0][:200] if text else ""
        return {"title": first, "published": "", "text": text}
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # broken markup: keep whatever was read before the error
        pass
    article = _clean(parser.parts["article"])
    main = _clean(parser.parts["main"])
    text = article if len(article) >= 400 else main if len(main) >= 400 else _clean(parser.parts["all"])
    title = re.sub(r"\s+", " ", parser.meta_title or parser.title).strip()
    return {"title": title[:300], "published": parser.published[:40], "text": text}


# Phrases that address an AI agent rather than a human reader. A page containing them is
# still shown to the model as data, but it is flagged and can't be used as a source.
INJECTION = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier|your) (?:instructions|prompts?|rules)"
    r"|disregard (?:all |any |the )?(?:previous|prior|above|your) "
    r"|(?:system|developer) prompt"
    r"|you are (?:now )?(?:an? |the )?(?:ai|assistant|language model|llm|agent)\b"
    r"|new instructions"
    r"|(?:call|use|invoke|run) (?:the )?(?:tool|function)\b"
    r"|\b(?:fetch_article|search_web|finish)\s*\("
    r"|do not (?:tell|inform) the user"
    r"|^\s*(?:system|assistant)\s*:"
    r"|rank (?:this|it) (?:first|#?1|number one)"
    r"|give (?:this|it) (?:a )?(?:perfect|10|top)"
    # text addressed to the verifier or the orchestrator, trying to change a verdict
    r"|\b(?:mark|label|classify|rate|set|record|return|report)\s+(?:this|that|the|these|all|every|each|any)\s+"
    r"(?:claims?|statements?|verdicts?|facts?)\b[^.\n]{0,20}\b(?:as|to|=)\s*[\"']?(?:supported|verified|true|correct|valid)\b"
    r"|\bverdicts?\s*(?:is|:|=)\s*[\"']?(?:supported|unsupported|unreachable)\b"
    r"|\b(?:respond|reply|output)\s+(?:only |just )?(?:with )?[\"']?supported\b"
    r"|\b(?:dear |hello |attention |note to |message to |instructions? (?:for|to) )(?:the )?"
    r"(?:ai|llm|model|verifier|fact[- ]?check(?:er|ing)? (?:agent|bot|system)|orchestrator|assistant|agent)\b",
    re.IGNORECASE | re.MULTILINE,
)


# Characters that show nothing but can sit inside a word to hide it from a pattern: zero-width spaces and joiners,
# the word joiner, the byte-order mark, the soft hyphen, and the left-to-right / right-to-left marks and embeddings.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e\u200e\u200f"
                                    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"))


def looks_like_instructions(text: str) -> bool:
    """True if the text talks to an AI agent. The text is cleaned first (Unicode NFKC, invisible characters removed),
    so 'ig<zero-width space>nore all previous instructions' and full-width letters do not slip past the pattern."""
    cleaned = unicodedata.normalize("NFKC", text or "").translate(_INVISIBLE)
    return bool(INJECTION.search(cleaned))
