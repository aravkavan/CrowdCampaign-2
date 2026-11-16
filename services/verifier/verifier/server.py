"""Wires the verifier together: the contract (worker_base) around the claim checker."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

from .config import SERVICE_DIR, load_config
from .guard import RobotsCache
from .judge import Verifier
from .llm import ChatModel
from .pages import fetch_article
from .settings import ConfigError
from .trace import Redactor, ServiceTrace
from .worker_base import IdempotencyStore, WorkerApp, serve

SERVICE = "verifier"


def trace_path() -> Path | None:
    """Where the trace log is also written: TRACE_FILE, or traces/verifier.jsonl in the project folder if there is one.
    On a host like Render there is no such folder, so the log goes to the host's log viewer (stdout) only."""
    explicit = os.environ.get("TRACE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    traces = SERVICE_DIR.parent.parent / "traces"
    return traces / f"{SERVICE}.jsonl" if traces.is_dir() else None


def make_trace(path=None, stream=sys.stdout) -> ServiceTrace:
    redactor = Redactor()
    redactor.learn_from_env()  # every value in a variable named *TOKEN*, *KEY*, *PASSWORD*, *SECRET* is scrubbed
    return ServiceTrace(SERVICE, path=path, stream=stream, redactor=redactor)


def no_duplicate_ids(payload: dict) -> str | None:
    """A rule JSON Schema can't express: the answers are matched to the claims by claim_id, so ids must be unique."""
    ids = [c["claim_id"] for c in payload["claims"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    return f"payload.claims: claim_id must be unique (repeated: {', '.join(dupes[:5])})" if dupes else None


def build_app(config, *, trace=None, model=None, fetch=None, clock=time.monotonic) -> WorkerApp:
    """The verifier service. model and fetch can be replaced (tests use fakes)."""
    trace = trace or make_trace()
    if model is None:
        settings = config.model()  # fails now, not on the first request, if no model key is set

        def model(ctx):
            return ChatModel(settings, config.retries, ctx.trace, session=requests.Session(), time_left=ctx.time_left)
    if fetch is None:
        def fetch(ctx, robots, url):
            return fetch_article(url, config.fetch, trace=ctx.trace, robots=robots, deadline=ctx.deadline,
                                 gate=ctx.budget.gate)

    def work(ctx):
        robots = RobotsCache(config.fetch, ctx.trace, gate=ctx.budget.gate)
        return Verifier(config=config, model=model(ctx), ctx=ctx, fetch_fn=lambda url: fetch(ctx, robots, url)).run()

    lim = config.limits
    return WorkerApp(
        name=SERVICE, route="/verify", token_env="VERIFIER_TOKEN",
        request_def="verifyRequest", response_def="verifyOk", empty_result={"verdicts": []},
        work=work, trace=trace, clock=clock, check_payload=no_duplicate_ids,
        store=IdempotencyStore(max_done=config.server.remembered_requests, clock=clock),
        max_concurrent=config.server.max_concurrent, retry_after_cap=config.server.retry_after_cap_seconds,
        min_step_seconds=2.0,
        ceilings={"max_steps": lim.max_steps, "max_fetches": lim.max_fetches, "max_tokens": lim.max_tokens,
                  "max_time_ms": lim.max_seconds * 1000},
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m verifier", description="Run the CrowdCampaign verifier service.")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="address to listen on (default 0.0.0.0)")
    parser.add_argument("--port", type=int, help="port (default: $PORT, else server.port in config.yaml, 8002)")
    parser.add_argument("--config", help="path to config.yaml (default: services/verifier/config.yaml)")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if not os.environ.get("VERIFIER_TOKEN", "").strip():
            raise ConfigError("VERIFIER_TOKEN is not set. Put a long random string in services/verifier/.env, or in "
                              "Render's Environment settings (make one with: python -c \"import secrets; "
                              "print(secrets.token_urlsafe(32))\").")
        model = config.model()
    except ConfigError as err:
        print(f"Can't start: {err}", file=sys.stderr)
        return 2
    port = args.port or int(os.environ.get("PORT") or config.server.port)
    app = build_app(config, trace=make_trace(path=trace_path()))
    lim = config.limits
    print(f"verifier: model {model.label} ({model.name}); ceilings {lim.max_steps} steps / {lim.max_fetches} fetches / "
          f"{lim.max_tokens:,} tokens / {lim.max_seconds} s per request", flush=True)
    serve(app, args.host, port)
    return 0
