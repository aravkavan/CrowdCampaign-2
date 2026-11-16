"""Wires the researcher together: the contract (worker_base) around the research agent."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .agent import ResearchAgent
from .config import SERVICE_DIR, load_config
from .guard import RobotsCache
from .llm import ChatModel
from .pages import fetch_article
from .settings import ConfigError
from .tools import search_web
from .trace import Redactor, ServiceTrace
from .worker_base import IdempotencyStore, WorkerApp, serve

SERVICE = "researcher"


def trace_path() -> Path | None:
    """Where the trace log is also written: TRACE_FILE, or traces/researcher.jsonl in the project folder."""
    explicit = os.environ.get("TRACE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    traces = SERVICE_DIR.parent.parent / "traces"
    return traces / f"{SERVICE}.jsonl" if traces.is_dir() else None


def make_trace(path=None, stream=sys.stdout) -> ServiceTrace:
    redactor = Redactor()
    redactor.learn_from_env()  # every value in a variable named *TOKEN*, *KEY*, *PASSWORD*, *SECRET* is scrubbed
    return ServiceTrace(SERVICE, path=path, stream=stream, redactor=redactor)


def build_app(config, *, trace=None, model=None, search=None, fetch=None, today=None, clock=time.monotonic) -> WorkerApp:
    """The researcher service. model, search, fetch and today can be replaced (tests use fakes)."""
    trace = trace or make_trace()
    if model is None:
        settings = config.model()  # fails now, not on the first request, if no model key is set

        def model(ctx):
            return ChatModel(settings, config.retries, ctx.trace, session=requests.Session(), time_left=ctx.time_left)
    if search is None:
        def search(ctx, query):
            return search_web(query, config=config, trace=ctx.trace, time_left=ctx.time_left)
    if fetch is None:
        def fetch(ctx, robots, url):
            return fetch_article(url, config.fetch, trace=ctx.trace, robots=robots, deadline=ctx.deadline,
                                 gate=ctx.budget.gate)
    today = today or (lambda: datetime.now(timezone.utc).date().isoformat())

    def work(ctx):
        robots = RobotsCache(config.fetch, ctx.trace, gate=ctx.budget.gate)
        agent = ResearchAgent(
            config=config, model=model(ctx), ctx=ctx, topic=ctx.payload["topic"], k=ctx.payload["k"], today=today(),
            search_fn=lambda query: search(ctx, query), fetch_fn=lambda url: fetch(ctx, robots, url),
        )
        return agent.run()

    lim = config.limits
    return WorkerApp(
        name=SERVICE, route="/research", token_env="RESEARCHER_TOKEN",
        request_def="researchRequest", response_def="researchOk", empty_result={"developments": []},
        work=work, trace=trace, clock=clock,
        store=IdempotencyStore(max_done=config.server.remembered_requests, clock=clock),
        max_concurrent=config.server.max_concurrent, retry_after_cap=config.server.retry_after_cap_seconds,
        min_step_seconds=3.0,  # a model call needs a few seconds; less than that left means "partial", not a call that can't finish
        ceilings={"max_steps": lim.max_steps, "max_fetches": lim.max_fetches, "max_tokens": lim.max_tokens,
                  "max_time_ms": lim.max_seconds * 1000},
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m researcher", description="Run the CrowdCampaign researcher service.")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="address to listen on (default 0.0.0.0)")
    parser.add_argument("--port", type=int, help="port (default: $PORT, else server.port in config.yaml, 8001)")
    parser.add_argument("--config", help="path to config.yaml (default: services/researcher/config.yaml)")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if not os.environ.get("RESEARCHER_TOKEN", "").strip():
            raise ConfigError("RESEARCHER_TOKEN is not set. Put a long random string in services/researcher/.env "
                              "(make one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\").")
        config.model()
        if "search_web" in config.tools and not os.environ.get("TAVILY_API_KEY", "").strip():
            raise ConfigError("TAVILY_API_KEY is not set. Add it to services/researcher/.env (free key at https://app.tavily.com).")
    except ConfigError as err:
        print(f"Can't start: {err}", file=sys.stderr)
        return 2
    port = args.port or int(os.environ.get("PORT") or config.server.port)
    app = build_app(config, trace=make_trace(path=trace_path()))
    model = config.model()
    print(f"researcher: model {model.label} ({model.name}), search Tavily, "
          f"ceilings {config.limits.max_steps} steps / {config.limits.max_fetches} fetches / {config.limits.max_tokens:,} tokens "
          f"/ {config.limits.max_seconds} s per request", flush=True)
    serve(app, args.host, port)
    return 0
