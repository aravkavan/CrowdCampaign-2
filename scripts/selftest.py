#!/usr/bin/env python3
"""Self-tests for the two workers (Assignment 2: "Self-tests, run before the demo, against each worker").

    python scripts/selftest.py --all
        Tests both workers. Addresses and tokens come from services/orchestrator/.env (the same file the
        orchestrator uses). A worker on this computer is also checked against its log file in traces/.

    python scripts/selftest.py --worker verifier --url https://your-verifier.onrender.com --token "$VERIFIER_TOKEN"
    python scripts/selftest.py --worker researcher --url http://localhost:8001 --token "$RESEARCHER_TOKEN" \
                               --log traces/researcher.jsonl

The seven checks (numbers match the assignment):
  1  no token, or a wrong token, gives 401 (the token is checked before the body)
  2  a body that breaks the schema gives 400
  3  the same request_id and body twice gives the same answer, with no new model calls or fetches in the log
  4  the same request_id with a different body gives 409
  5  the same request_id while the first copy is still running gives 503 with Retry-After
  6  a claim URL of http://169.254.169.254/ or http://localhost/ gives "unreachable" and no fetch is attempted
     (verifier only: the researcher takes no claim URLs)
  7  a tiny time_budget_ms gives "partial" with what the worker had, quickly

Checks 3, 4 and 5 share ONE small real job (a short research, or four claims), so a full run of this script costs
a few cents at most. Everything else does no work at all. Use a worker whose keys are set and whose spending cap
is set. A free Render service may take a minute to wake up; this script waits for it.

Without a log (a worker on Render), checks 3, 4 and 6 are judged from the answers alone and the script prints the
request_id to look for in Render's log. With a log (--log, or a local worker) they are judged from the log too.

Exit code: 0 all passed, 1 something failed, 2 nothing failed but a check could not be decided (run it again).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ROUTE = {"researcher": "/research", "verifier": "/verify"}
EXAMPLE = "https://example.com/"
DEFAULT_CLAIMS = [
    {"claim_id": "t1", "text": "This domain is for use in illustrative examples in documents.", "source_url": EXAMPLE},
    {"claim_id": "t2", "text": "You may use this domain in literature without prior coordination or asking for permission.",
     "source_url": EXAMPLE},
    {"claim_id": "t3", "text": "The page is titled Example Domain.", "source_url": EXAMPLE},
    {"claim_id": "t4", "text": "Example Domain sells running shoes in 40 countries.", "source_url": EXAMPLE},
]
# Addresses the verifier must refuse to fetch (check 6). The last one is an ordinary name that points at 127.0.0.1.
BLOCKED_URLS = ["http://169.254.169.254/latest/meta-data/", "http://localhost/", "http://127.0.0.1:8002/healthz",
                "http://[::1]/", "http://0.0.0.0/", "http://10.0.0.5/admin", "http://[::ffff:169.254.169.254]/",
                "http://localtest.me/"]


@dataclass
class Workload:
    """The small real job that checks 3, 4 and 5 share, and what to send for the cheap checks."""
    job_payload: dict
    changed_payload: dict
    quick_payload: dict
    steps: int = 6
    fetches: int = 4
    tokens: int = 40_000
    time_budget_ms: int = 90_000


def default_workload(worker: str) -> Workload:
    if worker == "researcher":
        job = {"topic": "zero-sugar sparkling drink launches", "k": 1}
        return Workload(job_payload=job, changed_payload={**job, "k": 2}, quick_payload=job, steps=5, fetches=3)
    quick = [{"claim_id": "b1", "text": "A claim about a page.", "source_url": BLOCKED_URLS[0]}, DEFAULT_CLAIMS[0]]
    return Workload(job_payload={"claims": DEFAULT_CLAIMS}, changed_payload={"claims": DEFAULT_CLAIMS[:3]},
                    quick_payload={"claims": quick})


@dataclass
class Result:
    number: int
    name: str
    status: str                        # pass | fail | skip | inconclusive
    detail: str
    notes: list = field(default_factory=list)


@dataclass
class Reply:
    status: int                        # 0 = no answer at all
    headers: dict
    raw: bytes
    body: object
    seconds: float
    problem: str | None = None

    @property
    def error(self) -> dict | None:
        return self.body.get("error") if isinstance(self.body, dict) and isinstance(self.body.get("error"), dict) else None


def canonical(obj) -> bytes:
    """The exact bytes sent. The same bytes are sent for every copy of a request, like the orchestrator does."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


OWN_TOKEN = object()      # "send the worker's real token" (None means: send no token at all)


class Probe:
    def __init__(self, worker: str, url: str, token: str):
        self.worker, self.base, self.token = worker, url.rstrip("/"), token
        self.path = ROUTE[worker]
        self.session = requests.Session()

    def post(self, body: bytes, *, token=OWN_TOKEN, timeout=150.0) -> Reply:
        token = self.token if token is OWN_TOKEN else token
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "crowdcampaign-selftest/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started = time.monotonic()
        try:
            response = self.session.post(self.base + self.path, data=body, headers=headers, timeout=(15, timeout),
                                         allow_redirects=False)
        except requests.RequestException as err:
            return Reply(0, {}, b"", None, time.monotonic() - started, f"no answer ({err.__class__.__name__})")
        try:
            parsed = json.loads(response.content.decode("utf-8")) if response.content else None
        except ValueError:
            parsed = None
        return Reply(response.status_code, dict(response.headers), response.content, parsed, time.monotonic() - started)

    def health(self, timeout=15.0) -> Reply:
        started = time.monotonic()
        try:
            response = requests.get(self.base + "/healthz", timeout=timeout, allow_redirects=False)
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
            return Reply(response.status_code, dict(response.headers), response.content, parsed, time.monotonic() - started)
        except requests.RequestException as err:
            return Reply(0, {}, b"", None, time.monotonic() - started, f"no answer ({err.__class__.__name__})")


def load_contract():
    try:
        from shared.contract import Contract
        return Contract()
    except Exception:  # jsonschema missing, or run from somewhere unexpected: the shape checks are skipped, not the tests
        return None


def header(reply: Reply, name: str):
    for key, value in reply.headers.items():
        if key.lower() == name.lower():
            return value
    return None


class SelfTest:
    def __init__(self, worker, url, token, *, other_token=None, read_log=None, workload=None, say=print, sleep=time.sleep,
                 wake_seconds=150.0, running_gap=0.4):
        self.worker, self.url, self.token, self.other_token = worker, url, token, other_token
        self.read_log, self.say, self.sleep = read_log, say, sleep
        self.workload = workload or default_workload(worker)
        self.wake_seconds, self.running_gap = wake_seconds, running_gap
        self.probe = Probe(worker, url, token)
        self.contract = load_contract()
        self.run_id = str(uuid.uuid4())
        self.job = None

    # ---------------------------------------------------------------- helpers

    def envelope(self, payload, *, request_id=None, time_budget_ms=None, work=True) -> dict:
        w = self.workload
        return {"request_id": request_id or str(uuid.uuid4()), "run_id": self.run_id,
                "time_budget_ms": time_budget_ms if time_budget_ms is not None else w.time_budget_ms,
                "budget": {"max_steps": w.steps, "max_fetches": w.fetches, "max_tokens": w.tokens}, "payload": payload}

    def shape_problem(self, reply: Reply, which: str) -> str | None:
        """None when the body fits the contract (or when we cannot check)."""
        if self.contract is None:
            return None
        if not isinstance(reply.body, dict):
            return "the answer is not a JSON object"
        problem = self.contract.problem(which, reply.body)
        return problem

    def error_problem(self, reply: Reply, *, retryable: bool) -> str | None:
        error = reply.error
        if error is None:
            return "there is no {error: {code, message, retryable}} object in the answer"
        if set(error) != {"code", "message", "retryable"}:
            return f"the error object has the wrong fields ({sorted(error)})"
        if error["retryable"] is not retryable:
            return f"error.retryable should be {str(retryable).lower()}"
        problem = self.shape_problem(reply, "errorResponse")
        return f"the error answer breaks the contract ({problem})" if problem else None

    def log_rows(self, request_id: str):
        if self.read_log is None:
            return None
        try:
            return [r for r in self.read_log() if r.get("request_id") == request_id]
        except Exception:
            return None

    def wake(self) -> str:
        """A free Render service sleeps after 15 minutes. GET /healthz until it answers (no token needed)."""
        begun = time.monotonic()
        tries = 0
        while True:
            tries += 1
            reply = self.probe.health(timeout=30)
            if reply.status == 200 and isinstance(reply.body, dict) and reply.body.get("status") == "ok":
                took = time.monotonic() - begun
                return f"awake after {took:.1f} s" if took > 3 else "awake"
            if time.monotonic() - begun > self.wake_seconds:
                return f"never answered /healthz ({reply.problem or 'HTTP ' + str(reply.status)})"
            if tries == 1:
                self.say("  waking the service (a free Render service can take about a minute)...")
            self.sleep(3)

    # ---------------------------------------------------------------- the checks

    def check1_token(self) -> Result:
        name = "token"
        env_body = canonical(self.envelope(self.workload.quick_payload))
        garbage = b"this is not even JSON"
        wrong = "wrong-" + secrets.token_hex(12)
        cases = [("no token", None), ("a wrong token", wrong)]
        if self.other_token:
            cases.append(("the other worker's token", self.other_token))
        problems = []
        for label, token in cases:
            for body_label, body in (("a valid body", env_body), ("a garbage body", garbage)):
                reply = self.probe.post(body, token=token)
                if reply.status != 401:
                    problems.append(f"{label} with {body_label} gave {reply.status or reply.problem}, not 401")
                    continue
                bad = self.error_problem(reply, retryable=False)
                if bad:
                    problems.append(f"{label}: 401 but {bad}")
        control = self.probe.post(garbage)
        if control.status != 400:
            problems.append(f"the right token with a garbage body gave {control.status or control.problem}, not 400 "
                            "(so the token may not be accepted at all)")
        health = self.probe.health()
        if health.status != 200 or health.body != {"status": "ok"}:
            problems.append(f"GET /healthz without a token gave {health.status or health.problem}, not 200 {{\"status\":\"ok\"}}")
        if problems:
            return Result(1, name, "fail", "; ".join(problems))
        refused = len(cases) * 2
        return Result(1, name, "pass", f"{refused} requests with no / wrong token were refused with 401 (garbage body too: the "
                                       "token is checked first); the right token got through to a 400; /healthz needs no token")

    def check2_bad_body(self) -> Result:
        name = "bad body"
        good = self.envelope(self.workload.quick_payload)
        bad_payload = {"topic": 5, "k": "three"} if self.worker == "researcher" else {"claims": "all of them"}
        cases = [
            ("an empty object", b"{}"),
            ("a list instead of an object", b"[1, 2, 3]"),
            ("a request_id that is not a UUID", canonical({**good, "request_id": "not-a-uuid"})),
            ("a time_budget_ms that is text", canonical({**good, "time_budget_ms": "soon"})),
            ("a payload of the wrong shape", canonical({**good, "payload": bad_payload})),
            ("an unknown extra field", canonical({**good, "surprise": True})),
            ("a missing budget", canonical({k: v for k, v in good.items() if k != "budget"})),
        ]
        problems = []
        for label, body in cases:
            reply = self.probe.post(body)
            if reply.status != 400:
                problems.append(f"{label} gave {reply.status or reply.problem}, not 400")
                continue
            bad = self.error_problem(reply, retryable=False)
            if bad:
                problems.append(f"{label}: 400 but {bad}")
        if problems:
            return Result(2, name, "fail", "; ".join(problems))
        return Result(2, name, "pass", f"{len(cases)} different broken bodies were each refused with 400 and the error object")

    def run_job(self) -> dict:
        """The one real piece of work behind checks 3, 4 and 5: the original, a copy sent while it runs, a copy after it
        finished, and a copy with a changed body."""
        w = self.workload
        request_id = str(uuid.uuid4())
        body = canonical(self.envelope(w.job_payload, request_id=request_id))
        changed = canonical(self.envelope(w.changed_payload, request_id=request_id))
        replies = {}

        def first():
            replies["first"] = self.probe.post(body)

        thread = threading.Thread(target=first, daemon=True)
        thread.start()
        self.sleep(self.running_gap)
        replies["second"] = self.probe.post(body)                  # the first copy should still be running
        thread.join(timeout=w.time_budget_ms / 1000 + 60)
        first_reply = replies.get("first")
        if first_reply is None:
            return {"request_id": request_id, "error": "the first request never came back"}
        pair = sorted([first_reply, replies["second"]], key=lambda r: r.status)
        original = next((r for r in (first_reply, replies["second"]) if r.status == 200), None)
        before = self.log_rows(request_id)
        later = self.probe.post(body)                              # a copy after everything finished
        after = self.log_rows(request_id)
        different = self.probe.post(changed)
        after_changed = self.log_rows(request_id)
        return {"request_id": request_id, "first": first_reply, "second": replies["second"], "pair": pair, "original": original,
                "later": later, "different": different, "log_before": before, "log_after": after, "log_after_changed": after_changed}

    def job_failure(self, job: dict) -> str | None:
        if "error" in job:
            return job["error"]
        original = job["original"]
        if original is None:
            statuses = sorted(r.status for r in (job["first"], job["second"]))
            replies = [r for r in (job["first"], job["second"]) if r.status not in (200, 503)]
            detail = ""
            if replies and replies[0].error:
                detail = f" ({replies[0].error.get('code')}: {str(replies[0].error.get('message'))[:140]})"
            return f"the test job itself did not succeed: it got HTTP {statuses}{detail}. Fix the worker (keys? spending cap? URL?) first"
        problem = self.shape_problem(original, "researchOk" if self.worker == "researcher" else "verifyOk")
        return f"the answer breaks the contract ({problem})" if problem else None

    def check3_same_twice(self) -> Result:
        name = "same request twice"
        job = self.job
        failure = self.job_failure(job)
        if failure:
            return Result(3, name, "fail", failure)
        original, later, request_id = job["original"], job["later"], job["request_id"]
        problems = []
        if later.status != 200:
            problems.append(f"the second copy got HTTP {later.status or later.problem}, not 200")
        else:
            if str(header(later, "Idempotent-Replay")).lower() != "true":
                problems.append("the second copy was not marked as a stored answer (no 'Idempotent-Replay: true' header)")
            if later.raw != original.raw:
                problems.append("the second answer is not byte-for-byte the same as the first")
        notes = []
        before, after = job["log_before"], job["log_after"]
        if before is not None and after is not None:
            new = after[len(before):]
            work = [r for r in new if r.get("direction") == "out"]
            inbound = [r for r in new if r.get("direction") == "in"]
            if work:
                problems.append(f"the log shows {len(work)} new model/search/fetch call(s) for the second copy: "
                                + ", ".join(sorted({str(r.get('endpoint_or_tool')).split(' ')[0] for r in work})))
            if len(inbound) != 1 or not str(inbound[0].get("detail", "")).startswith("replayed"):
                problems.append("the log should show exactly one new 'in' line saying 'replayed'")
            if not any(r.get("direction") == "out" for r in before):
                notes.append("(the original request made no model/search/fetch calls either, so this log check proves little)")
            checked = "the log shows one 'replayed' line and no new model, search or fetch calls"
        else:
            checked = "the log was not checked"
            notes.append(f"Look in the worker's log for request_id {request_id}: after the line whose detail is "
                         "'replayed: stored answer, no new work' there must be no model, search or fetch lines.")
        if problems:
            return Result(3, name, "fail", "; ".join(problems), notes)
        return Result(3, name, "pass", f"the second copy came back marked as a stored answer, byte-for-byte identical; {checked}", notes)

    def check4_different_body(self) -> Result:
        name = "same id, different body"
        job = self.job
        failure = self.job_failure(job)
        if failure:
            return Result(4, name, "fail", failure)
        different = job["different"]
        problems = []
        if different.status != 409:
            problems.append(f"HTTP {different.status or different.problem}, not 409")
        else:
            bad = self.error_problem(different, retryable=False)
            if bad:
                problems.append(f"409 but {bad}")
        after, after_changed = job["log_after"], job["log_after_changed"]
        if after is not None and after_changed is not None:
            work = [r for r in after_changed[len(after):] if r.get("direction") == "out"]
            if work:
                problems.append(f"the log shows {len(work)} new call(s) made for the refused request")
        if problems:
            return Result(4, name, "fail", "; ".join(problems))
        return Result(4, name, "pass", "reusing the request_id with a different body was refused with 409 (retryable: false)")

    def check5_still_running(self) -> Result:
        name = "copy while the first is running"
        job = self.job
        failure = self.job_failure(job)
        if failure:
            return Result(5, name, "fail", failure)
        statuses = [r.status for r in job["pair"]]
        refused = next((r for r in (job["first"], job["second"]) if r.status == 503), None)
        if refused is None:
            if statuses == [200, 200]:
                return Result(5, name, "inconclusive",
                              f"both copies got 200: the first finished in under {self.running_gap:.1f} s, before the second arrived. "
                              "Run the script again (or use a bigger job).")
            return Result(5, name, "fail", f"expected one 200 and one 503, got HTTP {statuses}")
        problems = []
        wait = header(refused, "Retry-After")
        if wait is None:
            problems.append("the 503 has no Retry-After header")
        else:
            try:
                if int(wait) < 1 or int(wait) > 600:
                    problems.append(f"Retry-After is {wait}, which is not a sensible number of seconds")
            except ValueError:
                problems.append(f"Retry-After is {wait!r}, not a number of seconds")
        bad = self.error_problem(refused, retryable=True)
        if bad:
            problems.append(f"503 but {bad}")
        if problems:
            return Result(5, name, "fail", "; ".join(problems))
        return Result(5, name, "pass", f"the copy sent while the first was running got 503 with Retry-After: {wait} "
                                       f"(retryable: true); the first copy finished with 200")

    def check6_blocked_urls(self) -> Result:
        name = "blocked claim URLs"
        if self.worker != "verifier":
            return Result(6, name, "skip", "only the verifier takes claim URLs; the researcher's fetch guard is covered by its unit tests")
        claims = [{"claim_id": f"u{i}", "text": f"The page at address number {i} says something.", "source_url": url}
                  for i, url in enumerate(BLOCKED_URLS, 1)]
        request_id = str(uuid.uuid4())
        reply = self.probe.post(canonical(self.envelope({"claims": claims}, request_id=request_id)))
        if reply.status != 200:
            error = reply.error or {}
            return Result(6, name, "fail", f"HTTP {reply.status or reply.problem} {error.get('code', '')} {str(error.get('message', ''))[:120]}".strip())
        problems = []
        shape = self.shape_problem(reply, "verifyOk")
        if shape:
            problems.append(f"the answer breaks the contract ({shape})")
        verdicts = {v.get("claim_id"): v for v in (reply.body.get("result") or {}).get("verdicts", [])} if isinstance(reply.body, dict) else {}
        for i, url in enumerate(BLOCKED_URLS, 1):
            verdict = verdicts.get(f"u{i}")
            if verdict is None:
                problems.append(f"no verdict for {url}")
            elif verdict.get("verdict") != "unreachable" or verdict.get("evidence") is not None:
                problems.append(f"{url} got {verdict.get('verdict')!r}, not 'unreachable'")
        used = (reply.body.get("usage") or {}).get("fetches") if isinstance(reply.body, dict) else None
        if used != 0:
            problems.append(f"the answer says {used} fetch(es) were made; it must be 0")
        rows = self.log_rows(request_id)
        notes = []
        if rows is not None:
            made = [r for r in rows if r.get("direction") == "out"]
            if made:
                problems.append(f"the log shows {len(made)} outbound call(s) for this request: "
                                + ", ".join(sorted({str(r.get('endpoint_or_tool')) for r in made})[:3]))
            checked = "and the log shows no outbound call"
        else:
            checked = "usage.fetches is 0 (log not checked)"
            notes.append(f"In the worker's log, request_id {request_id} should have one 'in' line and no 'out' lines.")
        if problems:
            return Result(6, name, "fail", "; ".join(problems), notes)
        return Result(6, name, "pass", f"all {len(BLOCKED_URLS)} blocked addresses (cloud metadata, localhost, private, IPv6, a name "
                                       f"pointing at 127.0.0.1) came back 'unreachable' with no evidence, {checked}", notes)

    def check7_tiny_budget(self) -> Result:
        name = "tiny time budget"
        request_id = str(uuid.uuid4())
        reply = self.probe.post(canonical(self.envelope(self.workload.quick_payload, request_id=request_id, time_budget_ms=1)))
        if reply.status != 200:
            error = reply.error or {}
            return Result(7, name, "fail", f"HTTP {reply.status or reply.problem} {error.get('code', '')} (a tiny budget must give "
                                           "200 with status partial, not an error)".strip())
        problems = []
        which = "researchOk" if self.worker == "researcher" else "verifyOk"
        shape = self.shape_problem(reply, which)
        if shape:
            problems.append(f"the answer breaks the contract ({shape})")
        body = reply.body if isinstance(reply.body, dict) else {}
        if body.get("status") != "partial":
            problems.append(f"status is {body.get('status')!r}, not 'partial'")
        error = body.get("error") or {}
        if not error.get("code"):
            problems.append("a partial answer needs an error object saying why")
        result = body.get("result") or {}
        kept = result.get("verdicts" if self.worker == "verifier" else "developments")
        if not isinstance(kept, list):
            problems.append("the answer has no result list")
        elif self.worker == "verifier":
            sent = {c["claim_id"] for c in self.workload.quick_payload["claims"]}
            stray = [v.get("claim_id") for v in kept if v.get("claim_id") not in sent]
            if stray:
                problems.append(f"verdicts for claims that were not sent: {stray}")
        if reply.seconds > 10:
            problems.append(f"it took {reply.seconds:.1f} s to answer a 1 ms budget")
        if problems:
            return Result(7, name, "fail", "; ".join(problems))
        count = len(kept)
        return Result(7, name, "pass", f"with time_budget_ms=1 the worker answered in {reply.seconds:.1f} s with status partial "
                                       f"({error.get('code')}) holding {count} {'verdict' if self.worker == 'verifier' else 'development'}"
                                       f"{'' if count == 1 else 's'}")

    # ---------------------------------------------------------------- the run

    def run(self, only=None) -> list:
        wanted = set(only) if only else set(range(1, 8))
        results = []

        def record(result: Result):
            results.append(result)
            mark = {"pass": "PASS", "fail": "FAIL", "skip": "skip", "inconclusive": "????"}[result.status]
            self.say(f"  {mark}  {result.number}  {result.name}: {result.detail}")
            for note in result.notes:
                self.say(f"          {note}")

        self.say(f"{self.worker} at {self.url}")
        woke = self.wake()
        self.say(f"  {woke}")
        if woke.startswith("never"):
            for n in sorted(wanted):
                record(Result(n, "not run", "fail", "the worker did not answer /healthz, so nothing could be tested"))
            return results
        if wanted & {3, 4, 5}:
            self.say("  running the one small real job that checks 3, 4 and 5 share (this is the slow part)...")
            self.job = self.run_job()
        steps = {1: self.check1_token, 2: self.check2_bad_body, 3: self.check3_same_twice, 4: self.check4_different_body,
                 5: self.check5_still_running, 6: self.check6_blocked_urls, 7: self.check7_tiny_budget}
        for number in sorted(wanted):
            record(steps[number]())
        return results


def summarize(results, say=print) -> int:
    passed = sum(r.status == "pass" for r in results)
    failed = sum(r.status == "fail" for r in results)
    unclear = sum(r.status == "inconclusive" for r in results)
    skipped = sum(r.status == "skip" for r in results)
    say(f"  {passed} passed, {failed} failed" + (f", {unclear} could not be decided" if unclear else "")
        + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 2 if unclear else 0


# -------------------------------------------------------------------- command line

def read_env_file(path: Path) -> dict:
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def file_reader(path: Path):
    def read():
        rows = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass                      # a line still being written
        return rows
    return read


def is_local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Self-tests for the researcher and verifier workers.")
    parser.add_argument("--all", action="store_true", help="test both workers, reading addresses and tokens from services/orchestrator/.env")
    parser.add_argument("--worker", choices=sorted(ROUTE), help="which worker to test")
    parser.add_argument("--url", help="the worker's address, e.g. http://localhost:8001")
    parser.add_argument("--token", help="the worker's bearer token (or set RESEARCHER_TOKEN / VERIFIER_TOKEN)")
    parser.add_argument("--other-token", help="the OTHER worker's token: it must be refused too")
    parser.add_argument("--log", help="the worker's trace log file (JSON lines). Default for a local worker: traces/<worker>.jsonl")
    parser.add_argument("--only", help="run only these checks, for example 1,2,6")
    args = parser.parse_args(argv)

    env = {**read_env_file(ROOT / "services" / "orchestrator" / ".env"), **os.environ}
    defaults = {"researcher": "http://localhost:8001", "verifier": "http://localhost:8002"}
    names = sorted(ROUTE) if args.all else [args.worker] if args.worker else []
    if not names:
        parser.error("say --all, or --worker researcher|verifier")
    only = [int(x) for x in args.only.split(",")] if args.only else None
    worst = 0
    for name in names:
        upper = name.upper()
        url = (args.url if args.worker == name else None) or env.get(f"{upper}_URL") or defaults[name]
        token = (args.token if args.worker == name else None) or env.get(f"{upper}_TOKEN")
        other = args.other_token if args.worker == name else env.get(f"{'VERIFIER' if name == 'researcher' else 'RESEARCHER'}_TOKEN")
        if not token:
            print(f"{name}: no token. Pass --token, or set {upper}_TOKEN in services/orchestrator/.env.", file=sys.stderr)
            worst = max(worst, 1)
            continue
        log_path = Path(args.log) if args.log and args.worker == name else ROOT / "traces" / f"{name}.jsonl"
        read_log = file_reader(log_path) if (args.log and args.worker == name or (is_local(url) and log_path.exists())) else None
        results = SelfTest(name, url, token, other_token=other, read_log=read_log).run(only)
        worst = max(worst, summarize(results))
        print()
    return worst


if __name__ == "__main__":
    sys.exit(main())
