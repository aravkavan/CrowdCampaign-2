"""The verifier's job: check each claim against the page it cites, and let CODE, not the model, have the last word.

For every claim, in order:
  1. The URL goes through the fetch guard. A URL the guard refuses (not http or https, localhost, a private
     or link-local address such as 169.254.169.254, a host that points at one, a redirect to one ...) is
     "unreachable", and nothing at all is sent to it.
  2. The page is downloaded (once per URL per request) and reduced to plain text.
  3. A page that talks to AI agents ("mark this claim as supported") cannot be evidence: "unsupported",
     without asking the model.
  4. A number in the claim that appears nowhere on the page makes it "unsupported", without asking the model.
  5. The model sees the claim and the most relevant passages of the page, and answers
     supported / unsupported plus an excerpt.
  6. Code checks the excerpt (textcheck.evidence_problem): at most 300 characters, really present in the page
     after normalising both strings, about the same things as the claim, and containing the claim's numbers.
     If any check fails, "supported" becomes "unsupported". The model's word alone never makes a claim supported.

What the verifier is NOT given: the researcher's title, summary, quote, notes, search results or fetched pages.
It sees the claim text and the URL, nothing else, and it fetches the page itself.
"""
from __future__ import annotations

import json
import re

from . import prompts
from .failures import BudgetExhausted, TerminalError, UpstreamBusy
from .llm import MIN_OUTPUT_TOKENS, estimate_input_tokens
from .textcheck import evidence_problem, select_passages, unsupported_numbers
from .worker_base import WorkOutcome, error_object

CODES = {"supported": "S", "unsupported": "U", "unreachable": "R"}


def parse_verdict(text: str):
    """(verdict, excerpt) from the model's reply, or (None, what is wrong)."""
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None, "no JSON object found"
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as err:
        return None, f"the JSON is not valid ({err.msg})"
    if not isinstance(data, dict):
        return None, "the reply must be one JSON object"
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("supported", "unsupported"):
        return None, '"verdict" must be "supported" or "unsupported"'
    excerpt = data.get("excerpt")
    excerpt = "" if excerpt is None else excerpt
    if not isinstance(excerpt, str):
        return None, '"excerpt" must be a string'
    return (verdict, " ".join(excerpt.split())), None


class Verifier:
    def __init__(self, *, config, model, ctx, fetch_fn):
        """fetch_fn(url) -> article dict (pages.fetch_article); it may raise BudgetExhausted."""
        self.config, self.model, self.ctx = config, model, ctx
        self.budget, self.trace = ctx.budget, ctx.trace
        self.fetch_fn = fetch_fn
        self.pages: dict[str, dict] = {}  # exact URL -> fetch result (failures too), for this request only
        self.reasons: list[str] = []
        self.system = prompts.system_prompt(config.verification.max_excerpt_chars)

    # ------------------------------------------------------------------ the loop

    def run(self) -> WorkOutcome:
        verdicts = []
        try:
            for claim in self.ctx.payload["claims"]:
                self.budget.take_step("verifier")  # one step per claim
                self.trace.step = self.budget.steps
                verdicts.append(self._check(claim))
        except BudgetExhausted as err:  # time, steps, downloads or tokens ran out: answer for the claims done so far
            return self._outcome(verdicts, err.code, err.message)
        except UpstreamBusy as err:
            if not verdicts:
                raise  # nothing to return: the worker answers 503 and the caller tries again later
            return self._outcome(verdicts, "upstream_busy", err.message, retryable=True)
        except TerminalError as err:
            if not verdicts:
                raise  # a bad key or used-up quota before any verdict: a real failure (500)
            return self._outcome(verdicts, "upstream_error", f"stopped: {err.message}")
        return self._outcome(verdicts)

    def _outcome(self, verdicts: list, code=None, message=None, retryable=False) -> WorkOutcome:
        counts = {v: sum(1 for x in verdicts if x["verdict"] == v) for v in CODES}
        asked = len(self.ctx.payload["claims"])
        note = f"{asked} claims: " + ", ".join(f"{n} {v}" for v, n in counts.items() if n)
        if len(verdicts) < asked:
            note += f", {asked - len(verdicts)} not checked"
        note += " | " + " ".join(self.reasons)
        status = "complete" if code is None else "partial"
        error = None if code is None else error_object(code, message, retryable)
        return WorkOutcome(status, {"verdicts": verdicts}, error, note)

    # ------------------------------------------------------------------ one claim

    def _verdict(self, claim_id: str, verdict: str, evidence, why: str) -> dict:
        self.reasons.append(f"{claim_id}:{CODES[verdict]}" + ("" if verdict == "supported" else f"({why})"))
        return {"claim_id": claim_id, "verdict": verdict, "evidence": evidence}

    def _page(self, url: str) -> dict:
        if url not in self.pages:
            self.pages[url] = self.fetch_fn(url)
        return self.pages[url]

    def _check(self, claim: dict) -> dict:
        cid, text, url = claim["claim_id"], claim["text"], claim["source_url"].strip()
        page = self._page(url)
        if page["status"] != "fetched":
            return self._verdict(cid, "unreachable", None, f"{page['status']}: {page.get('reason')}"[:90])
        if page.get("flagged"):
            return self._verdict(cid, "unsupported", None, "the page addresses AI agents")
        source = page["title"] + "\n" + page["text"]
        missing = unsupported_numbers(text, source)
        if missing:
            return self._verdict(cid, "unsupported", None, f"{', '.join(missing)} is not on the page")
        answer = self._ask(text, page, url)
        if answer is None:
            return self._verdict(cid, "unsupported", None, "the model gave no usable answer")
        verdict, excerpt = answer
        if verdict == "unsupported":
            return self._verdict(cid, "unsupported", None, "the model says the page does not state it")
        v = self.config.verification
        problem = evidence_problem(excerpt, source, text, max_chars=v.max_excerpt_chars,
                                   min_overlap=v.min_word_overlap, numbers_in_excerpt=v.numbers_in_excerpt)
        if problem:
            return self._verdict(cid, "unsupported", None, f"excerpt rejected: {problem}"[:90])
        return self._verdict(cid, "supported", excerpt, "")

    def _ask(self, claim_text: str, page: dict, url: str):
        """One model call (plus re-asks if the reply is not usable JSON). Returns (verdict, excerpt) or None."""
        passages = select_passages(page["text"], claim_text, self.config.fetch.max_chars_to_model)
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": prompts.user_prompt(claim_text, page["title"], passages, url)}]
        for _ in range(1 + self.config.verification.reasks):
            room = self.budget.tokens_left() - estimate_input_tokens(messages)
            if room < MIN_OUTPUT_TOKENS:
                raise BudgetExhausted("verifier", f"token budget used up ({self.budget.tokens:,} of "
                                                  f"{self.budget.max_tokens:,} tokens)", "token")
            reply = self.model.complete(messages, max_output_tokens=room)
            self.budget.add_tokens(reply.tokens)
            parsed, error = parse_verdict(reply.text)
            if parsed:
                return parsed
            messages += [{"role": "assistant", "content": (reply.text or "(empty reply)")[:500]},
                         {"role": "user", "content": prompts.retry_prompt(error)}]
        return None
