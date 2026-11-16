"""Fakes and builders shared by the orchestrator's tests. Nothing here touches the network."""
import io
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.client import CallResult, Health  # noqa: E402
from orchestrator.config import DEFAULT_CONFIG, load_config  # noqa: E402
from orchestrator.contract import Contract  # noqa: E402
from orchestrator.run import Orchestrator  # noqa: E402
from orchestrator.trace import Redactor, ServiceTrace  # noqa: E402

CONTRACT = Contract()
CONFIG = load_config(DEFAULT_CONFIG, load_dotenv=False)


def make_ids():
    counter = iter(range(1, 100_000))
    return lambda: f"00000000-0000-4000-8000-{next(counter):012d}"


# ---------------------------------------------------------------------------- answers a worker could send

USAGE = {"steps": 5, "fetches": 6, "tokens": 20_000, "searches": 2, "elapsed_ms": 30_000}
VUSAGE = {"steps": 4, "fetches": 5, "tokens": 6_000, "elapsed_ms": 9_000}


def ok(parsed, *, replayed=False, attempts=1):
    return CallResult(outcome="ok", http_status=200, parsed=parsed, parse_problem=None, attempts=attempts,
                      elapsed_ms=1500, reason="answered", replayed=replayed, retries=attempts - 1)


def down(reason="connection refused or dropped; gave up after 4 tries"):
    return CallResult(outcome="unavailable", http_status=0, parsed=None, parse_problem=None, attempts=4, elapsed_ms=15_000,
                      reason=reason, retries=3, waited_s=14.0)


def refused(status=401, reason="rejected our token (HTTP 401)"):
    return CallResult(outcome="refused", http_status=status, parsed=None, parse_problem=None, attempts=1, elapsed_ms=20,
                      reason=reason)


def worker_500(envelope, code="upstream_error", message="the API key was rejected", usage=None, valid=True):
    body = {"request_id": envelope["request_id"], "run_id": envelope["run_id"], "status": "failed",
            "usage": usage or {"steps": 1, "fetches": 0, "tokens": 300, "elapsed_ms": 900}, "result": None,
            "error": {"code": code, "message": message, "retryable": False}}
    if not valid:
        body["surprise"] = True
    return CallResult(outcome="worker_failed", http_status=500, parsed=body, parse_problem=None, attempts=1,
                      elapsed_ms=900, reason="reported a failure")


def dev(n, title, claims):
    """claims: list of (text, url)"""
    return {"id": f"d{n}", "title": title, "summary": f"Summary of {title}.",
            "claims": [{"claim_id": f"d{n}-c{i}", "text": text, "source_url": url} for i, (text, url) in enumerate(claims, 1)]}


def research_ok(envelope, devs, *, status="complete", error=None, usage=None):
    if status == "partial" and error is None:
        error = {"code": "time_budget_exhausted", "message": "the time budget ran out", "retryable": False}
    return ok({"request_id": envelope["request_id"], "run_id": envelope["run_id"], "status": status,
               "usage": usage or dict(USAGE), "result": {"developments": devs}, "error": error})


def verify_ok(envelope, decide, *, status="complete", error=None, usage=None, only=None):
    verdicts = []
    for c in envelope["payload"]["claims"]:
        if only is not None and c["claim_id"] not in only:
            continue
        verdict, evidence = decide(c)
        verdicts.append({"claim_id": c["claim_id"], "verdict": verdict, "evidence": evidence})
    if status == "partial" and error is None:
        error = {"code": "time_budget_exhausted", "message": "the time budget ran out", "retryable": False}
    return ok({"request_id": envelope["request_id"], "run_id": envelope["run_id"], "status": status,
               "usage": usage or dict(VUSAGE), "result": {"verdicts": verdicts}, "error": error})


def slug(topic):
    return sum(map(ord, topic)) % 1000


def standard_devs(topic, count=2, claims_each=2):
    s = slug(topic)
    devs = []
    for i in range(1, count + 1):
        title = f"Brand{s} launches product{i} line nationwide across stores"
        claims = [(f"Brand{s} launched product{i} claim number {j} in stores.", f"https://news.example.com/{s}/{i}/{j}")
                  for j in range(1, claims_each + 1)]
        devs.append(dev(i, title, claims))
    return devs


def standard_research(path, envelope):
    return research_ok(envelope, standard_devs(envelope["payload"]["topic"]))


def cycle_decide():
    state = {"n": 0}
    order = [("supported", None), ("unsupported", None), ("unreachable", None)]

    def decide(claim):
        verdict, _ = order[state["n"] % 3]
        state["n"] += 1
        return (verdict, f"Excerpt for {claim['claim_id']}: {claim['text'][:40]}" if verdict == "supported" else None)

    return decide


def standard_verify(path, envelope):
    return verify_ok(envelope, cycle_decide())


# ---------------------------------------------------------------------------- stand-ins for the real clients

class ScriptedWorker:
    """Stands in for WorkerClient: the handler decides what each call returns."""

    def __init__(self, name, handler, *, health=None):
        self.name, self.handler, self.calls, self.warm = name, handler, [], True
        self._health = health or Health(True, 1, 20, "awake")

    def health(self, run_id, *, timeout):
        return self._health

    def call(self, path, envelope, *, step, give_up_at):
        self.calls.append((path, envelope))
        return self.handler(path, envelope)


class FakeApp:
    def __init__(self, titles, state=None):
        self.trace = None
        self._campaigns = [{"id": f"aaaaaaaa-0000-4000-8000-{i:012d}", "title": t, "brand": f"Brand {i}"}
                           for i, t in enumerate(titles, 1)]
        self.state = state or {"next_run_number": 1, "last_run": None, "seen_urls": [], "campaigns": []}
        self.saved = []

    def login(self):
        return {}

    def campaigns(self):
        return self._campaigns

    def load_state(self):
        return self.state

    def save_run(self, payload):
        self.saved.append(payload)
        return {"number": self.state["next_run_number"], "counts": {}}


class Rig:
    def __init__(self, researcher=standard_research, verifier=standard_verify, *, campaigns=2, config=None, state=None):
        self.config = config or replace(CONFIG, targets=CONFIG.targets[:campaigns])
        self.log = io.StringIO()
        self.trace = ServiceTrace("orchestrator", stream=self.log, redactor=Redactor())
        self.researcher = ScriptedWorker("researcher", researcher)
        self.verifier = ScriptedWorker("verifier", verifier)
        self.app = FakeApp([t.title for t in self.config.targets], state)
        self.said = []
        self.orch = Orchestrator(self.config, researcher=self.researcher, verifier=self.verifier, app=self.app,
                                 trace=self.trace, say=self.said.append, new_id=make_ids())

    def run(self):
        return self.orch.run()

    def lines(self):
        return [json.loads(x) for x in self.log.getvalue().splitlines() if x.strip()]
