"""The research agent for one /research request: search -> fetch -> observe -> decide -> synthesize.

Who decides what:
  The model decides  which searches to run, which results to read, what each article says (notes and
                     claims), whether two notes are the same event, and the ranking.
  The code decides   everything that must not be left to a model: the budgets and when to stop, which
                     URLs may be fetched (only ones from search results, through the guardrail), whether
                     each quote is really in its article, whether every number in a title, summary or
                     claim is in the article, and the partial answer when a budget runs out.

The agent is stateless: it knows nothing about earlier runs. Remembering, and deciding what is new,
is the orchestrator's job.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import prompts
from .extract import looks_like_instructions
from .failures import BudgetExhausted, TerminalError, UpstreamBusy
from .llm import MIN_OUTPUT_TOKENS, estimate_input_tokens
from .pages import url_key
from .textcheck import normalize, quote_problem, similar_titles, unsupported_numbers
from .worker_base import WorkOutcome, error_object

MAX_CLAIMS_PER_NOTE = 3
CLAIM_MIN, CLAIM_MAX = 15, 250
MAX_CLAIMS_PER_DEVELOPMENT = 6


class ActionError(ValueError):
    """The model's reply wasn't a usable action."""


@dataclass
class Action:
    tool: str
    args: dict
    notes: list


@dataclass
class Note:
    id: str
    url: str
    key: str
    article_title: str
    title: str
    summary: str
    quote: str
    claims: list = field(default_factory=list)
    same_as: str | None = None


def parse_action(text: str, allowed_tools) -> Action:
    """Turn the model's reply into an Action, or raise ActionError saying what's wrong."""
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ActionError("no JSON object found")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as err:
        raise ActionError(f"the JSON is not valid ({err.msg})") from None
    if not isinstance(data, dict):
        raise ActionError("the reply must be one JSON object")
    tool = data.get("tool")
    if tool not in allowed_tools:
        raise ActionError(f'"tool" must be one of {", ".join(allowed_tools)} (got {json.dumps(tool)})')
    args = data.get("args")
    args = {} if args is None else args
    notes = data.get("notes")
    notes = [] if notes is None else notes
    if not isinstance(args, dict):
        raise ActionError('"args" must be a JSON object')
    if not isinstance(notes, list):
        raise ActionError('"notes" must be a list')
    if tool == "search_web":
        query = args.get("query")
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 400:
            raise ActionError('search_web needs {"query": "..."} with 2 to 400 characters')
    elif tool == "fetch_article":
        if not isinstance(args.get("url"), str) or not args["url"].strip():
            raise ActionError('fetch_article needs {"url": "..."}')
    elif tool == "finish":
        if not isinstance(args.get("top"), list):
            raise ActionError('finish needs {"top": ["n1", "n2", ...]}')
    return Action(tool=tool, args=args, notes=notes[:8])


class ResearchAgent:
    SIMILAR = 0.6  # note titles this similar are treated as the same development

    def __init__(self, *, config, model, ctx, topic: str, k: int, today: str, search_fn, fetch_fn):
        """search_fn(query) -> {"query", "results", "credits"}; fetch_fn(url) -> article dict (pages.fetch_article).
        Both may raise BudgetExhausted / TerminalError / UpstreamBusy, which end the work (see _stop)."""
        self.config = config
        self.model = model
        self.ctx = ctx
        self.budget = ctx.budget
        self.trace = ctx.trace
        self.topic, self.k, self.today = topic, k, today
        self.search_fn, self.fetch_fn = search_fn, fetch_fn

        self.notes: dict[str, Note] = {}
        self.fetched: dict[str, dict] = {}  # url key -> article fetched in this request
        self.search_urls: dict[str, dict] = {}  # url key -> search result offered to the model
        self.article_messages: list[tuple[int, str]] = []
        self.compacted: set[int] = set()
        self._pending_article: str | None = None
        self.credits = 0
        self.finish_attempts = 0
        self.final_keys: list = []
        self.forced_reason: str | None = None  # a budget code, once the model has been told to finish now
        self.messages = [
            {"role": "system", "content": prompts.system_prompt(config, k)},
            {"role": "user", "content": prompts.task_prompt(topic=topic, k=k, today=today, left=self._left())},
        ]

    # ------------------------------------------------------------------ the loop

    def run(self) -> WorkOutcome:
        while True:
            try:
                self.budget.take_step("researcher")
                self.trace.step = self.budget.steps
                room = self.budget.tokens_left() - estimate_input_tokens(self.messages)
                if room < MIN_OUTPUT_TOKENS:
                    raise BudgetExhausted("researcher", f"token budget used up ({self.budget.tokens:,} of "
                                                        f"{self.budget.max_tokens:,} tokens)", "token")
                reply = self.model.complete(self.messages, max_output_tokens=room)
            except (BudgetExhausted, UpstreamBusy, TerminalError) as err:
                return self._stop(err)
            self.budget.add_tokens(reply.tokens)
            try:
                action = parse_action(reply.text, self.config.tools)
                error = None
            except ActionError as err:
                action, error = None, str(err)
            self.messages.append({"role": "assistant", "content": (reply.text or "(empty reply)")[:6000]})
            self._compact_replies()

            if action is None:
                self._observe(prompts.invalid_reply(error))
                continue

            notes_before = len(self.notes)
            feedback = self._take_notes(action.notes)
            new_ids = list(self.notes)[notes_before:]
            if action.tool == "finish" and new_ids and self.budget.steps < self.budget.max_steps:
                # Ids are given out by the code, so notes in this same reply had none yet when
                # the model ranked. Let it rank once more with them.
                self._observe(feedback + "finish was not accepted yet: you added notes in the same reply, so they had "
                              f"no ids when you ranked. Call finish again and include any of {', '.join(new_ids)} "
                              "that belong in the top list.")
                continue
            if action.tool == "finish":
                problem = self._finish(action.args, extra=new_ids)
                if problem is None:
                    return self._complete()
                self.finish_attempts += 1
                if self.finish_attempts >= 3:
                    return self._partial("invalid_ranking", "the model could not produce a valid ranking after 3 tries")
                self._observe(feedback + problem)
                continue

            if self.budget.steps >= self.budget.max_steps:
                continue  # that was the last allowed reply; running the tool would be wasted (the next loop stops)
            try:
                if action.tool == "search_web":
                    observation = self._search(action.args["query"])
                else:
                    observation = self._fetch(action.args["url"])
            except (BudgetExhausted, UpstreamBusy, TerminalError) as err:
                return self._stop(err)
            article_key, self._pending_article = self._pending_article, None
            self._observe(feedback + observation, article_key=article_key)

    def _stop(self, err) -> WorkOutcome:
        """The work cannot go on because of err. Answer "partial" with what was found, or fail when nothing was done."""
        if isinstance(err, BudgetExhausted):
            return self._partial(err.code, err.message)
        if isinstance(err, UpstreamBusy):
            if self.notes:
                return self._partial("upstream_busy", err.message, retryable=True)
            raise err  # nothing to return: the worker answers 503 and the caller tries again later
        if self.budget.steps <= 1 and not self.notes:
            raise err  # the very first call failed for good (bad key, no credits): a real failure (500)
        return self._partial("upstream_error", f"stopped: {err.message}")

    def _left(self) -> dict:
        b = self.budget
        return {"steps": max(0, b.max_steps - b.steps), "fetches": max(0, b.max_fetches - b.fetches),
                "searches": max(0, self.config.limits.max_searches - b.searches)}

    def _last_call_reason(self) -> str | None:
        """Why the next model call must be the last one (so the model should finish), as a budget code."""
        b = self.budget
        if b.steps + 1 >= b.max_steps:
            return "step_budget_exhausted"
        if b.tokens >= 0.85 * b.max_tokens:
            return "token_budget_exhausted"
        if b.time_fraction_used() >= 0.85:
            return "time_budget_exhausted"
        return None

    def _observe(self, text: str, *, article_key: str | None = None) -> None:
        reason = self._last_call_reason()
        if reason:
            self.forced_reason = reason
            text += "\n\n" + prompts.FINISH_NOW
        else:
            text += "\n" + prompts.budget_line(self._left())
        self.messages.append({"role": "user", "content": text})
        if article_key:
            self.article_messages.append((len(self.messages) - 1, article_key))
            # Keep only the two most recent articles in full; older ones become a one-line summary.
            for index, key in self.article_messages[:-2]:
                if index not in self.compacted:
                    ids = [n.id for n in self.notes.values() if n.key == key]
                    self.messages[index] = {"role": "user", "content": prompts.compacted_article(self.fetched[key], ids)}
                    self.compacted.add(index)

    def _compact_replies(self) -> None:
        """Keep the model's last two replies whole; older ones lose their notes, which are
        already recorded under ids (n1, n2...). This keeps every request small."""
        replies = [i for i, m in enumerate(self.messages) if m["role"] == "assistant"]
        for index in replies[:-2]:
            if index in self.compacted:
                continue
            try:
                action = json.loads(self.messages[index]["content"])
            except ValueError:
                action = None
            if isinstance(action, dict):
                short = {"tool": action.get("tool"), "args": action.get("args")}
                if action.get("notes"):
                    short["notes"] = f"({len(action['notes'])} notes, see the ids in the runtime's answer)"
                self.messages[index] = {"role": "assistant", "content": json.dumps(short)[:1500]}
            else:
                self.messages[index] = {"role": "assistant", "content": self.messages[index]["content"][:300]}
            self.compacted.add(index)

    # ------------------------------------------------------------------ tools

    def _search(self, query: str) -> str:
        if self.budget.searches >= self.config.limits.max_searches:
            return "No searches left in the budget. Read articles from the results you have, or finish."
        self.budget.searches += 1
        data = self.search_fn(query)
        self.credits += data["credits"]
        labels = {}
        for r in data["results"]:
            key = url_key(r["url"])
            self.search_urls[key] = r
            if key in self.fetched:
                labels[r["url"]] = "already read in this request"
        return prompts.search_observation(query, data["results"], labels)

    def _fetch(self, url: str) -> str:
        url = url.strip()
        key = url_key(url)
        if key not in self.search_urls:
            # Only URLs from search results: a page can't make the agent visit an address it mentions.
            return "fetch_article refused: you can only fetch URLs from your search results. Pick one of those."
        if key in self.fetched:
            return "fetch_article skipped: you already read this article in this request. Pick a different result."
        try:
            result = self.fetch_fn(url)
        except BudgetExhausted as err:
            if err.kind != "fetch":
                raise
            self.forced_reason = "fetch_budget_exhausted"
            return "fetch_article refused: no downloads are left in the budget. Finish with the notes you have."
        if result["status"] != "fetched":
            verb = "refused" if result["status"] == "rejected" else "failed"
            return f"fetch_article {verb}: {result.get('reason')}. Pick a different result."
        self.fetched[key] = result
        self._pending_article = key
        return prompts.article_observation(result, self.config.fetch.max_chars_to_model)

    # ------------------------------------------------------------------ notes

    def _take_notes(self, raw_notes: list) -> str:
        if not raw_notes:
            return ""
        accepted, rejected = [], []
        for raw in raw_notes:
            if not isinstance(raw, dict):
                rejected.append(("(not an object)", "each note must be a JSON object"))
                continue
            title = str(raw.get("title") or "").strip()
            summary = str(raw.get("summary") or "").strip()
            quote = str(raw.get("quote") or "").strip()
            url = str(raw.get("url") or "").strip()
            label = title[:60] or "(untitled)"
            article = self.fetched.get(url_key(url))
            if article is None:
                rejected.append((label, "its url is not an article you read in this request"))
                continue
            if article.get("flagged"):
                rejected.append((label, "that page contained instructions aimed at AI agents, so it can't be a source"))
                continue
            if not 3 <= len(title) <= 160:
                rejected.append((label, "the title must be 3 to 160 characters"))
                continue
            if not 20 <= len(summary) <= 500:
                rejected.append((label, "the summary must be 20 to 500 characters"))
                continue
            source_text = article["title"] + "\n" + article["text"]
            problem = quote_problem(quote, source_text)
            if problem:
                rejected.append((label, problem))
                continue
            invented = unsupported_numbers(title + " " + summary, source_text)
            if invented:
                rejected.append((label, f"{', '.join(invented)} does not appear in the article"))
                continue
            claims, claim_problems = self._check_claims(raw.get("claims"), source_text)
            if not claims:
                rejected.append((label, "it needs 1 to 3 claims" + (f" ({'; '.join(claim_problems)})" if claim_problems else "")))
                continue
            if any(n.key == url_key(url) and n.title.lower() == title.lower() for n in self.notes.values()):
                continue  # the same note twice
            if len(self.notes) >= 4 * self.k:
                rejected.append((label, "too many notes; finish with the ones you have"))
                continue
            same_as = raw.get("same_as")
            same_as = str(same_as).strip().lower() if same_as else None
            if same_as and same_as not in self.notes:
                same_as = None
            note = Note(id=f"n{len(self.notes) + 1}", url=article["url"], key=url_key(url),
                        article_title=article["title"], title=title, summary=summary, quote=quote, claims=claims,
                        same_as=same_as)
            self.notes[note.id] = note
            accepted.append((note, claim_problems))
        parts = []
        if accepted:
            parts.append("Notes accepted: " + "; ".join(f'{n.id} "{prompts.web_text(n.title)}" ({len(n.claims)} claims)'
                                                       for n, _ in accepted) + ".")
            dropped = [f"{n.id}: {why}" for n, problems in accepted for why in problems]
            if dropped:
                parts.append("Some claims were dropped (" + "; ".join(prompts.web_text(d) for d in dropped) + ").")
        if rejected:
            parts.append("Notes rejected: " + "; ".join(f'"{prompts.web_text(lbl)}" ({why})' for lbl, why in rejected)
                         + ". Rejected notes are not saved.")
        return "\n".join(parts) + "\n\n"

    def _check_claims(self, raw, source_text: str):
        """The claims of one note that pass the code's checks, and why the others did not."""
        problems, claims, seen = [], [], set()
        if not isinstance(raw, list):
            return [], ['"claims" must be a list of statements']
        for item in raw[: MAX_CLAIMS_PER_NOTE + 2]:
            text = item.get("text") if isinstance(item, dict) else item
            text = " ".join(str(text or "").split())
            if not CLAIM_MIN <= len(text) <= CLAIM_MAX:
                problems.append(f"a claim must be {CLAIM_MIN} to {CLAIM_MAX} characters")
                continue
            if looks_like_instructions(text):
                problems.append("a claim contained instruction-like text")
                continue
            invented = unsupported_numbers(text, source_text)
            if invented:
                problems.append(f"{', '.join(invented)} in a claim does not appear in the article")
                continue
            key = normalize(text)
            if key in seen:
                continue
            seen.add(key)
            claims.append(text)
            if len(claims) == MAX_CLAIMS_PER_NOTE:
                break
        return claims, problems

    def _root(self, note_id: str, depth: int = 0) -> str:
        """Follow same_as links to the first note about the same event."""
        note = self.notes.get(note_id)
        if note is None or not note.same_as or depth > 20:
            return note_id
        return self._root(note.same_as, depth + 1)

    # ------------------------------------------------------------------ finish

    def _finish(self, args: dict, extra=()) -> str | None:
        """Accept the model's ranking, or say what is wrong with it.

        extra: notes added in the same reply on the very last step; they go after the ranking.
        """
        chosen, errors = [], []
        for raw in args.get("top") or []:
            ident = raw.get("id") if isinstance(raw, dict) else raw
            ident = str(ident or "").strip().lower()
            if ident in self.notes:
                key = self._root(ident)
            else:
                errors.append(f"{ident or '(blank)'} is not an accepted note")
                continue
            if key not in chosen:
                chosen.append(key)
        for note_id in extra:
            key = self._root(note_id)
            if key not in chosen:
                chosen.append(key)
        if errors and self.finish_attempts < 2 and not extra:
            valid = ", ".join(self.notes) or "none yet"
            return "finish was not accepted: " + "; ".join(errors) + f". Valid ids: {valid}. Call finish again."
        if not chosen and self.notes and self.finish_attempts < 1:
            return ("finish listed no developments, but you have accepted notes "
                    f"({', '.join(self.notes)}). List the ids that matter most, or call finish with [] again.")
        self.final_keys = self._merge_similar(chosen)[: self.k]
        return None

    def _merge_similar(self, keys: list) -> list:
        """A safety net for duplicates the model didn't link with same_as: near-identical titles merge."""
        merged = []
        for key in keys:
            title = self.notes[key].title
            match = next((k for k in merged if similar_titles(title, self.notes[k].title) >= self.SIMILAR), None)
            if match is not None:
                self.notes[key].same_as = match
            else:
                merged.append(key)
        return merged

    # ------------------------------------------------------------------ results

    def _group(self, key: str) -> list:
        return [n for n in self.notes.values() if n.id == key or self._root(n.id) == key]

    def _developments(self, keys: list) -> list:
        out = []
        for rank, key in enumerate(keys[: self.k], start=1):
            notes = sorted(self._group(key), key=lambda n: (n.id != key, n.id))  # the lead note first
            lead = notes[0]
            claims, seen = [], set()
            for n in notes:
                for text in n.claims:
                    marker = (n.key, normalize(text))
                    if marker in seen or len(claims) >= MAX_CLAIMS_PER_DEVELOPMENT:
                        continue
                    seen.add(marker)
                    claims.append({"claim_id": f"d{rank}-c{len(claims) + 1}", "text": text, "source_url": n.url})
            out.append({"id": f"d{rank}", "title": lead.title, "summary": lead.summary, "claims": claims})
        return out

    def _result(self, status: str, keys: list, code=None, message=None, retryable=False) -> WorkOutcome:
        error = None if status == "complete" else error_object(code, message, retryable)
        return WorkOutcome(status, {"developments": self._developments(keys)}, error)

    def _complete(self) -> WorkOutcome:
        if self.forced_reason:
            # A budget cut the research short: keep the model's ranking, but say so.
            return self._result("partial", self.final_keys, self.forced_reason,
                                "a budget ran out; ranked with the evidence gathered so far")
        return self._result("complete", self.final_keys)

    def _partial(self, code: str, message: str, retryable: bool = False) -> WorkOutcome:
        """Stop early and return the verified notes gathered so far, without a new ranking."""
        keys = []
        for note_id in self.notes:
            root = self._root(note_id)
            if root not in keys:
                keys.append(root)
        return self._result("partial", self._merge_similar(keys)[: self.k], code, message, retryable)
