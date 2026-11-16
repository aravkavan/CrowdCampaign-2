"""One run: for each campaign, ask the researcher, check its answer, ask the verifier about the claims,
check that answer, and keep a record. The run's budget is split and charged along the way.

This is plain control flow. It decides what to do from three things only: the config, the HTTP status
codes, and numbers and ids it has checked against the contract. No text a worker sent (a claim, a title,
an error message) is ever read to decide what happens next; such text is only passed on, shown, or saved.

Failure rules (each is tested):
  verifier down or broken  -> its claims are "unverified"; the campaign is partial and names the verifier
  researcher down or broken-> "no new results"; last run's top list is shown, marked stale; names the researcher
  a worker's answer breaks the contract -> that worker counts as failed, the answer is thrown away
  a worker stops early (a budget ran out) -> partial: what it had is used, the rest is marked unverified / carried over
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .budget import Grant, RunBudget
from .client import WorkerClient
from .config import WORKERS, Config, Target
from .contract import Contract
from .matching import (apply_verdicts, carried_items, count_claims, dropped_items, match_developments, select_claims,
                       stale_top)
from .validate import Violation, check_research, check_verify, failure_details

_SAFE_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

PLAIN_CODES = {
    "time_budget_exhausted": "its time budget ran out",
    "step_budget_exhausted": "its step budget ran out",
    "fetch_budget_exhausted": "its download budget ran out",
    "token_budget_exhausted": "its token budget ran out",
    "upstream_busy": "the model or search service kept rate limiting it",
    "upstream_error": "the model or search service refused it",
}


class RunAborted(Exception):
    """The run can't start at all (the app is unreachable, no campaign exists ...). Nothing is saved."""


def one_line(text, limit: int = 160) -> str:
    """Text from a worker, made safe to print: no control characters (no terminal escape codes), one line, short."""
    text = _CONTROL.sub("", " ".join(str(text or "").split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def plain_code(code) -> str:
    if isinstance(code, str) and code in PLAIN_CODES:
        return PLAIN_CODES[code]
    return "it stopped early"


@dataclass
class Failure:
    worker: str
    kind: str          # unreachable | refused | worker_error | contract | skipped
    message: str       # one plain sentence in our own words

    @property
    def is_failure(self) -> bool:
        """A skipped call (the budget couldn't afford it) is not the worker's failure."""
        return self.kind != "skipped"


@dataclass
class WorkerCall:
    worker: str
    endpoint: str
    request_id: str
    step: int
    attempts: int
    http_status: int
    outcome: str          # complete | partial | failed | refused | unavailable | unexpected | rejected
    latency_ms: int
    replayed: bool
    basis: str            # how it was charged: reported | full grant | free
    steps: int
    fetches: int
    tokens: int
    seconds: float
    detail: str = ""


@dataclass
class CampaignOutcome:
    campaign: dict
    topic: str
    status: str = "complete"                     # complete | partial | failed
    reasons: list = field(default_factory=list)
    failed_workers: list = field(default_factory=list)
    items: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    claims_sent: int = 0
    calls: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def stop_reason(self) -> str | None:
        return "; ".join(self.reasons) or None

    def fail_worker(self, worker: str) -> None:
        if worker not in self.failed_workers:
            self.failed_workers.append(worker)


@dataclass
class RunResult:
    run_id: str
    run_number: int
    started: datetime
    finished: datetime
    status: str
    outcomes: list
    missing: list
    budget: RunBudget
    warm: dict
    seconds: int
    notes: list = field(default_factory=list)

    @property
    def failed_workers(self) -> list:
        names = {w for o in self.outcomes for w in o.failed_workers}
        return [w for w in WORKERS if w in names]

    @property
    def stop_reason(self) -> str | None:
        parts = [f"{o.campaign['title']}: {o.stop_reason}" for o in self.outcomes if o.status != "complete" and o.stop_reason]
        parts += [f"{title}: not found in the app" for title in self.missing]
        return "; ".join(parts)[:500] or None


class Orchestrator:
    def __init__(self, config: Config, *, researcher: WorkerClient, verifier: WorkerClient, app, trace,
                 contract: Contract | None = None, clock=time.monotonic, now=None, say=print, new_id=None):
        self.config = config
        self.clients = {"researcher": researcher, "verifier": verifier}
        self.app = app
        self.trace = trace                    # a ServiceTrace
        self.contract = contract or Contract()
        self.clock = clock
        self.now = now or (lambda: datetime.now().astimezone())
        self.say = say
        self.new_id = new_id or (lambda: str(uuid.uuid4()))
        self.blocked: dict = {}               # worker -> Failure: a refusal that would repeat on every call (wrong token ...)
        self.warm_threads: dict = {}
        self.warm_results: dict = {}
        self._step = 0
        self.budget: RunBudget | None = None
        self.run_id: str | None = None

    # ------------------------------------------------------------------ the run

    def run(self, only: tuple | None = None) -> RunResult:
        """Research every campaign in config.yaml, or, when `only` is (campaign_id, topic), just that one campaign
        with that topic (a run started from the website)."""
        cfg = self.config
        self.run_id = run_id = self.new_id()
        self.app.trace = self.trace.bind(run_id, None)
        self.app.login()
        campaigns = self.app.campaigns()
        state = self.app.load_state()
        memory = {c["campaign_id"]: c for c in state.get("campaigns", [])}
        if only is not None:
            campaign_id, topic = only
            chosen = next((c for c in campaigns if str(c.get("id", "")).lower() == campaign_id.lower()), None)
            if chosen is None:
                raise RunAborted("That campaign was not found in the app (was it deleted?), so nothing was researched.")
            found, missing = [(Target(title=chosen["title"], topic=topic), chosen)], []
        else:
            by_title = {c["title"].strip().lower(): c for c in campaigns}
            found = [(t, by_title[t.title.lower()]) for t in cfg.targets if t.title.lower() in by_title]
            missing = [t.title for t in cfg.targets if t.title.lower() not in by_title]
        if not found:
            raise RunAborted("None of the campaigns in config.yaml exist in the app, so nothing was researched. "
                             "Check the titles (or run npm run seed in backend to add the demo campaigns).")
        run_number = state["next_run_number"]

        started, t0 = self.now(), self.clock()
        self.budget = RunBudget(cfg.total, cfg.researcher_share, cfg.minimum, deadline=t0 + cfg.total.seconds,
                                reserve_seconds=cfg.reserve_seconds, clock=self.clock)
        self._start_warmups(run_id)

        outcomes = []
        for index, (target, campaign) in enumerate(found, start=1):
            calls_left = len(found) - index + 1
            self.say(f"\n[{index}/{len(found)}] {one_line(campaign['title'], 100)} ({one_line(campaign.get('brand'), 60)})")
            info = {"id": campaign["id"], "title": campaign["title"], "brand": campaign.get("brand") or ""}
            try:
                outcome = self._campaign(run_id, info, target.topic, memory.get(campaign["id"], {}), calls_left)
            except Exception as err:  # one campaign's bug must not lose the others' results
                outcome = CampaignOutcome(campaign=info, topic=target.topic, status="failed",
                                          reasons=[f"orchestrator error ({err.__class__.__name__})"],
                                          stale=stale_top(memory.get(campaign["id"], {})))
                outcome.notes.append(f"{err.__class__.__name__}: {one_line(err)}")
            outcomes.append(outcome)
            self._print_campaign(outcome)

        for thread in self.warm_threads.values():
            thread.join(timeout=1)
        finished = self.now()
        status = "complete" if all(o.status == "complete" for o in outcomes) and not missing else "partial"
        return RunResult(run_id=run_id, run_number=run_number, started=started, finished=finished, status=status,
                         outcomes=outcomes, missing=missing, budget=self.budget, warm=dict(self.warm_results),
                         seconds=int(self.clock() - t0))

    # ------------------------------------------------------------------ warm-up

    def _start_warmups(self, run_id: str) -> None:
        """Wake both workers at the start of the run, in the background, and time how long that takes. The
        verifier is not needed until the first campaign's research is done, so its cold start overlaps the research."""
        for name in WORKERS:
            self.warm_results[name] = None
            thread = threading.Thread(target=self._warm, args=(name, run_id), daemon=True, name=f"warm-{name}")
            self.warm_threads[name] = thread
            thread.start()

    def _warm(self, name: str, run_id: str) -> None:
        try:
            self.warm_results[name] = self.clients[name].health(run_id, timeout=self.config.network.warmup_timeout)
        except Exception as err:  # never let the helper thread take anything down
            self.warm_results[name] = None
            self.trace.write(direction="out", endpoint_or_tool=f"{name} GET /healthz", status=0, latency_ms=0,
                             run_id=run_id, detail=f"warm-up error: {err.__class__.__name__}")

    def _await_warm(self, name: str) -> None:
        thread = self.warm_threads.get(name)
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.config.network.warmup_timeout + 5)

    # ------------------------------------------------------------------ one campaign

    def _campaign(self, run_id: str, info: dict, topic: str, memory: dict, calls_left: int) -> CampaignOutcome:
        cfg = self.config
        out = CampaignOutcome(campaign=info, topic=topic)

        # 1. research
        self.say("  Asking the researcher (the slow part: it searches the web and reads pages)...")
        answer, failure = self._research(run_id, out, topic, calls_left)
        if failure is not None:
            out.status = "failed"
            out.reasons.append(("researcher failed: " if failure.is_failure else "not researched: ") + failure.message)
            out.stale = stale_top(memory)
            return out
        out.items = match_developments(answer.developments, memory)
        if answer.status == "partial" or not out.items:
            out.status = "partial"
            out.reasons.append("research stopped early: " + (plain_code((answer.error or {}).get("code")) if answer.status == "partial"
                                                              else "it found nothing to rank"))
            # nothing is dropped without a new ranking: last run's top list stays, after the new findings
            out.items += carried_items(memory, {i.ref for i in out.items}, max(0, 2 * cfg.k - len(out.items)))
        else:
            out.dropped = dropped_items(memory, {i.ref for i in out.items})

        # 2. verification
        claims = select_claims(out.items, cfg.max_claims)
        total_claims = sum(len(i.claims) for i in out.items)
        if not claims:
            out.counts = count_claims(out.items)
            return out
        self.say(f"  The researcher found {len(out.items)} developments. Asking the verifier to check {len(claims)} claims...")
        verdicts, failure, cut = self._verify(run_id, out, claims, calls_left)
        out.counts = apply_verdicts(out.items, verdicts)
        unverified = out.counts["unverified"]
        if failure is not None:
            out.status = "partial"
            what = "verifier failed" if failure.is_failure else "verification skipped"
            out.reasons.append(f"{what}: {failure.message}; {unverified} of {total_claims} claims are unverified")
        elif cut:
            out.status = "partial"
            out.reasons.append(cut)
        return out

    # ------------------------------------------------------------------ the two calls

    def _research(self, run_id: str, out: CampaignOutcome, topic: str, calls_left: int):
        if "researcher" in self.blocked:
            out.fail_worker("researcher")
            return None, self.blocked["researcher"]
        grant = self.budget.grant("researcher", calls_left)
        if grant is None:
            return None, Failure("researcher", "skipped", self.budget.last_skip or "the budget is used up")
        self._await_warm("researcher")
        payload = {"topic": topic, "k": self.config.k}
        k = self.config.k
        return self._call(run_id, out, "researcher", "/research", grant, payload,
                          lambda call, rid: check_research(self.contract, call, request_id=rid, run_id=run_id, k=k))

    def _verify(self, run_id: str, out: CampaignOutcome, claims: list, calls_left: int):
        """Returns (verdicts, Failure or None, cut). verdicts: {claim_id: (verdict, evidence)}, only those that passed
        the checks. cut: a sentence when chosen claims were left unchecked (the grant was small, or the verifier
        stopped early), else None."""
        if "verifier" in self.blocked:
            out.fail_worker("verifier")
            return {}, self.blocked["verifier"], None
        grant = self.budget.grant("verifier", calls_left)
        if grant is None:
            return {}, Failure("verifier", "skipped", self.budget.last_skip or "the budget is used up"), None
        send = claims[: grant.steps]               # one step per claim, so the grant decides how many we can send
        grant = grant.with_steps(len(send))
        out.claims_sent = len(send)
        self._await_warm("verifier")
        payload = {"claims": [{"claim_id": c.ref, "text": c.text, "source_url": c.source_url} for c in send]}
        sent_ids = [c.ref for c in send]
        answer, failure = self._call(run_id, out, "verifier", "/verify", grant, payload,
                                     lambda call, rid: check_verify(self.contract, call, request_id=rid, run_id=run_id,
                                                                    sent_ids=sent_ids))
        if failure is not None:
            return {}, failure, None
        cut = None
        if answer.status == "partial":
            cut = (f"verification stopped early after {len(answer.verdicts)} of {len(send)} claims: "
                   f"{plain_code((answer.error or {}).get('code'))}")
        elif len(send) < len(claims):
            cut = f"only {len(send)} of the {len(claims)} chosen claims fit the verifier's budget"
        return answer.verdicts, None, cut

    def _call(self, run_id: str, out: CampaignOutcome, worker: str, path: str, grant: Grant, payload: dict, checker):
        """One request to a worker, from envelope to charge. Returns (validated answer or None, Failure or None)."""
        client = self.clients[worker]
        net = self.config.network
        request_id = self.new_id()
        self._step += 1
        step = self._step
        envelope = {"request_id": request_id, "run_id": run_id, "time_budget_ms": grant.time_budget_ms,
                    "budget": grant.envelope_budget(), "payload": payload}
        give_up_at = self.clock() + grant.seconds + net.read_margin + (0 if client.warm else net.cold_start_allowance) + net.retry_window
        result = client.call(path, envelope, step=step, give_up_at=give_up_at)

        answer, failure, usage, free, outcome = None, None, None, False, result.outcome
        if result.outcome == "ok":
            started = self.clock()
            try:
                answer = checker(result, request_id)
                usage, outcome = answer.usage, answer.status
                self._log_check(run_id, request_id, step, worker, 200, started, f"passed: {answer.status}")
            except Violation as violation:
                outcome = "rejected"
                failure = Failure(worker, "contract", f"its answer broke the contract and was thrown away ({one_line(violation, 140)})")
                self._log_check(run_id, request_id, step, worker, 502, started, f"rejected: {one_line(violation, 200)}")
        elif result.outcome == "worker_failed":
            usage, code, message = failure_details(self.contract, result)
            outcome = "failed"
            shown = f" ({code}: {one_line(message, 120)})" if code else ""
            failure = Failure(worker, "worker_error", f"it reported a failure{shown}")
        elif result.outcome == "refused":
            free = True
            failure = Failure(worker, "refused", result.reason)
            if result.http_status in (400, 401):   # the same request would be refused again: stop asking this run
                self.blocked[worker] = Failure(worker, "refused", result.reason + " (not asked again in this run)")
        else:  # unavailable | unexpected
            failure = Failure(worker, "unreachable" if result.outcome == "unavailable" else "contract", result.reason)

        charge = self.budget.charge(request_id, worker, grant, usage, free=free)
        out.calls.append(WorkerCall(worker=worker, endpoint=f"POST {path}", request_id=request_id, step=step,
                                    attempts=result.attempts, http_status=result.http_status, outcome=outcome,
                                    latency_ms=result.elapsed_ms, replayed=result.replayed, basis=charge.basis,
                                    steps=charge.steps, fetches=charge.fetches, tokens=charge.tokens, seconds=charge.seconds,
                                    detail=result.reason))
        if charge.overran:
            out.notes.append(f"the {worker} reported more than its grant ({', '.join(charge.overran)})")
        if failure is not None:
            out.fail_worker(worker)
        return answer, failure

    def _log_check(self, run_id, request_id, step, worker, status, started, detail) -> None:
        self.trace.write(direction="out", endpoint_or_tool=f"check {worker} answer", status=status,
                         latency_ms=(self.clock() - started) * 1000, run_id=run_id, request_id=request_id, step=step,
                         detail=detail)

    # ------------------------------------------------------------------ screen output

    def _print_campaign(self, out: CampaignOutcome) -> None:
        say = self.say
        if out.status == "failed":
            say(f"  Failed: {one_line(out.stop_reason or '', 300)}")
            if out.stale:
                say(f"  Last known top list kept ({len(out.stale)} developments, marked stale).")
            return
        new = sum(1 for i in out.items if i.change == "new")
        line = f"  {out.status.capitalize()}: top {len(out.items)} ({new} new, {len(out.items) - new} still"
        line += f", {len(out.dropped)} dropped)" if out.status == "complete" else ")"
        say(line)
        for rank, item in enumerate(out.items, start=1):
            say(f"   {'NEW ' if item.change == 'new' else '    '}#{rank} {one_line(item.title, 90)}")
        c = out.counts
        if c:
            say(f"  Claims: {c.get('supported', 0)} supported, {c.get('unsupported', 0)} unsupported, "
                f"{c.get('unreachable', 0)} unreachable, {c.get('unverified', 0)} unverified")
        for reason in out.reasons:
            say(f"  Note: {one_line(reason, 300)}")
