"""Command line for the orchestrator. Run it from services/orchestrator/:

    python -m orchestrator check                 are both workers and the app awake? are the tokens right?
    python -m orchestrator run                   research every campaign in config.yaml, verify the claims, save the run
    python -m orchestrator status                what the app remembers
    python -m orchestrator reset                 forget every saved run (asks first)
    python -m orchestrator stats [files ...]     round trips, time and tokens from the trace logs
    python -m orchestrator handcheck             the claims to check by hand for ORCHESTRATION.md question 5
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from . import handcheck
from .appclient import AppClient
from .client import WorkerClient
from .config import REPO_ROOT, ConfigError, for_site_run, load_config
from .failures import TerminalError
from .report import payload, render
from .run import Orchestrator, RunAborted, one_line
from .summary import describe
from .trace import Redactor, ServiceTrace

REPORTS = REPO_ROOT / "reports"
TRACES = REPO_ROOT / "traces"


def say(text: str = "") -> None:
    print(text, flush=True)


def trace_path() -> Path:
    return Path(os.environ.get("TRACE_FILE") or TRACES / "orchestrator.jsonl")


def make_trace(config) -> ServiceTrace:
    redactor = Redactor()
    redactor.learn_from_env()
    redactor.add(config.researcher.token, config.verifier.token, os.environ.get("TRACKER_PASSWORD", ""),
                 os.environ.get("TRACKER_TOKEN", ""))
    return ServiceTrace("orchestrator", path=trace_path(), redactor=redactor)


def make_clients(config, trace) -> dict:
    return {s.name: WorkerClient(s.name, s.url, s.token, trace, retry=config.retry, network=config.network)
            for s in (config.researcher, config.verifier)}


def load(args, *, need_tokens: bool):
    try:
        config = load_config(args.config)
        if need_tokens:
            config.require_tokens()
        return config
    except ConfigError as err:
        say(f"Can't start: {err}")
        return None


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else plural or word + 's'}"


def _rel(path) -> str:
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _local(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%a %b %d, %I:%M %p")
    except (TypeError, ValueError):
        return str(iso)


# ---------------------------------------------------------------------------- run

_CAMPAIGN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def site_target(args):
    """(campaign_id, topic) for a run started from the website (one campaign), or None for the normal run.
    The website passes a campaign id and a topic; both are checked here, as if a person had typed them."""
    if not args.campaign and not args.topic:
        return None
    if not args.campaign or not args.topic:
        raise ConfigError("--campaign and --topic go together.")
    campaign = args.campaign.strip()
    topic = " ".join(args.topic.split())
    if not _CAMPAIGN_ID.match(campaign):
        raise ConfigError("--campaign must be a campaign id.")
    if not 1 <= len(topic) <= 300:
        raise ConfigError("--topic must be between 1 and 300 characters (the contract's limit).")
    return campaign.lower(), topic


def cmd_run(args) -> int:
    config = load(args, need_tokens=True)
    if config is None:
        return 2
    try:
        only = site_target(args)
    except ConfigError as err:
        say(f"Can't start: {err}")
        return 2
    if only:
        config = for_site_run(config)       # one campaign: a share of the total budget
    trace = make_trace(config)
    clients = make_clients(config, trace)
    app = AppClient(trace.bind(None, None), retry=config.app_retry)
    t = config.total
    say("CrowdCampaign orchestrator")
    say(f"  Researcher: {config.researcher.url}")
    say(f"  Verifier:   {config.verifier.url}")
    say(f"  App:        {app.base_url} as {'the head person who started this run' if app.fixed_token else app.username or '(TRACKER_USERNAME not set)'}")
    say(f"  Budget:     {t.steps:.0f} steps, {t.fetches:.0f} downloads, {t.tokens:,.0f} tokens, {t.seconds / 60:.0f} min for the whole run; "
        f"K = {config.k}, up to {config.max_claims} claims verified per campaign")

    orchestrator = Orchestrator(config, researcher=clients["researcher"], verifier=clients["verifier"], app=app, trace=trace,
                                say=say)
    try:
        result = orchestrator.run(only)
    except RunAborted as err:
        say(f"\n{err}")
        return 2
    except TerminalError as err:
        say(f"\nStopped before starting: {err.message}")
        return 2
    finally:
        trace.close()

    markdown = render(result, config)
    report_path = None if only else REPORTS / f"orch-run{result.run_number}.md"
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown, encoding="utf-8")
    try:
        saved = app.save_run(payload(result, markdown, config))
    except TerminalError as err:
        say(f"\n{'Report written to ' + _rel(report_path) + ', but the' if report_path else 'The run finished, but the'} "
            f"run could not be saved to the app: {err.message}")
        say("The next run will not know about this one. Fix the problem and run the orchestrator again.")
        return 1
    if report_path and saved.get("number") != result.run_number:  # another run was saved in between: keep the file name in step
        new_path = REPORTS / f"orch-run{saved['number']}.md"
        report_path.replace(new_path)
        report_path = new_path
    result.run_number = saved.get("number", result.run_number)

    counts = saved.get("counts", {})
    spent = result.budget.spent()
    say("")
    say(f"Run {result.run_number}: {result.status}. {counts.get('new', 0)} new, {counts.get('still', 0)} still in the top list, "
        f"{counts.get('dropped', 0)} dropped.")
    claims = {k: sum(o.counts.get(k, 0) for o in result.outcomes) for k in ("supported", "unsupported", "unreachable", "unverified")}
    say(f"Claims: {claims['supported']} supported, {claims['unsupported']} unsupported, {claims['unreachable']} unreachable, "
        f"{claims['unverified']} unverified.")
    if result.failed_workers:
        say(f"Failed worker{'s' if len(result.failed_workers) > 1 else ''}: {', '.join(result.failed_workers)}.")
    say(f"Charged to the budget: {spent['steps']:.0f} steps, {spent['fetches']:.0f} downloads, {spent['tokens']:,.0f} tokens, "
        f"{spent['seconds']:.0f} s of worker time; the run took {result.seconds // 60} min {result.seconds % 60} s.")
    say(f"Report: {_rel(report_path) if report_path else 'saved in the app (open the run under Market intel)'}")
    say(f"Trace:  {_rel(trace_path())}  (run {result.run_id})")
    say("See it in the app: open the frontend and choose Market intel." if not only
        else "See it on this campaign's page, under Market intel, or in Market intel for the full report.")
    return 0


# ---------------------------------------------------------------------------- check

def cmd_check(args) -> int:
    config = load(args, need_tokens=False)
    if config is None:
        return 2
    trace = make_trace(config)
    clients = make_clients(config, trace)
    run_id = "00000000-0000-4000-8000-000000000000"
    ok = True
    say("Checking the services (this also wakes up any that are asleep; a Render free service can take about a minute).")
    for name, path in (("researcher", "/research"), ("verifier", "/verify")):
        service = config.service(name)
        health = clients[name].health(run_id, timeout=config.network.warmup_timeout)
        if not health.ok:
            ok = False
            say(f"  {name:<10} {service.url}: NOT awake ({health.reason}) after {health.ms / 1000:.1f} s")
            continue
        say(f"  {name:<10} {service.url}: awake after {health.ms / 1000:.1f} s")
        if not service.token:
            ok = False
            say(f"  {'':<10} {service.token_env} is not set in services/orchestrator/.env")
            continue
        status, text = clients[name].probe_token(path, run_id)
        if status != 400:
            ok = False
        say(f"  {'':<10} token: {text}")
    app = AppClient(trace.bind(run_id, None), retry=config.app_retry)
    try:
        started = datetime.now()
        app.login()
        state = app.load_state()
        say(f"  app        {app.base_url}: logged in as {app.username}, {state['next_run_number'] - 1} saved run(s) "
            f"({(datetime.now() - started).total_seconds():.1f} s; the database may take a few seconds to wake up)")
    except TerminalError as err:
        ok = False
        say(f"  app        {app.base_url}: {err.message}")
    trace.close()
    say("\nEverything is ready." if ok else "\nSomething above needs fixing before a run.")
    return 0 if ok else 1


# ---------------------------------------------------------------------------- status / reset / stats

def _connect(args):
    config = load(args, need_tokens=False)
    if config is None:
        return None, None, None
    trace = make_trace(config)
    app = AppClient(trace.bind(None, None), retry=config.app_retry)
    try:
        app.login()
        state = app.load_state()
    except TerminalError as err:
        say(f"Stopped: {err.message}")
        return None, None, None
    return app, state, trace


def cmd_status(args) -> int:
    app, state, trace = _connect(args)
    if app is None:
        return 2
    try:
        campaigns = {c["id"]: c["title"] for c in app.campaigns()}
    except TerminalError as err:
        say(f"Stopped: {err.message}")
        return 2
    finally:
        trace.close()
    last = state.get("last_run")
    if not last:
        say(f"No saved runs for {app.username}. Run: python -m orchestrator run")
        return 0
    failed = last.get("failed_workers") or []
    say(f"Last run: #{last['number']} on {_local(last['started_at'])} ({last['status']}"
        + (f"; failed: {', '.join(failed)}" if failed else "") + f"). Next run: #{state['next_run_number']}.")
    for c in state["campaigns"]:
        devs = {d["id"]: d for d in c["developments"]}
        say(f"\n{campaigns.get(c['campaign_id'], c['campaign_id'])}: {len(devs)} developments known")
        for t in c["last_top"]:
            say(f"  #{t['rank']} {one_line(devs.get(t['development_id'], {}).get('title', '?'), 100)}")
    return 0


def cmd_reset(args) -> int:
    app, state, trace = _connect(args)
    if app is None:
        return 2
    runs = state["next_run_number"] - 1
    if not args.yes:
        answer = input(f"This deletes all {runs} saved runs for {app.username} (reports in the app, claims, what was seen, "
                       f"the top lists). Type reset to continue: ")
        if answer.strip().lower() != "reset":
            say("Nothing was deleted.")
            trace.close()
            return 1
    try:
        result = app.reset()
    except TerminalError as err:
        say(f"Stopped: {err.message}")
        return 2
    finally:
        trace.close()
    deleted = result.get("deleted", {})
    say(f"Reset done: deleted {deleted.get('runs', 0)} runs and {deleted.get('developments', 0)} developments. "
        "The next run will be run 1 and will treat every development as new.")
    if args.delete_files:
        removed = 0
        for folder, pattern in ((REPORTS, "orch-run*.md"), (TRACES, "orchestrator.jsonl"), (TRACES, "researcher.jsonl"),
                                (TRACES, "verifier.jsonl")):
            for path in folder.glob(pattern):
                path.unlink()
                removed += 1
        say(f"Also deleted {removed} report and trace files.")
    else:
        say("Report and trace files were kept (add --delete-files to remove the orchestrator's reports and the three trace logs).")
    return 0


def cmd_stats(args) -> int:
    paths = [Path(p) for p in args.files] or [p for p in (TRACES / "orchestrator.jsonl", TRACES / "researcher.jsonl",
                                                         TRACES / "verifier.jsonl") if p.exists()]
    missing = [p for p in paths if not p.exists()]
    if missing or not paths:
        say("No such file: " + ", ".join(str(p) for p in missing) if missing else "No trace files found in traces/. Run the orchestrator first.")
        return 2
    say(describe(paths, args.run))
    return 0


def cmd_handcheck(args) -> int:
    app, state, trace = _connect(args)
    if app is None:
        return 2
    try:
        run = app.run_detail(args.run)
    except TerminalError as err:
        say(f"Stopped: {err.message}")
        return 2
    finally:
        trace.close()
    if not run:
        say(f"Run {args.run} was not found." if args.run else "No saved run to check. Run: python -m orchestrator run")
        return 1
    found = handcheck.collect(run)
    seed = args.seed if args.seed is not None else handcheck.default_seed(run)
    sample = handcheck.pick(found, args.samples, seed)
    sheet = handcheck.render(run, found, sample, samples=args.samples, seed=seed)
    path = REPORTS / f"handcheck-run{run.get('number')}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sheet, encoding="utf-8")
    say(sheet)
    say(f"Saved as {_rel(path)}")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(prog="python -m orchestrator", description="CrowdCampaign orchestrator")
    parser.add_argument("--config", help="path to config.yaml (default: the one in the project folder)")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("run", help="research every campaign, verify the claims, save the run")
    p.add_argument("--campaign", help="research only this campaign (its id); the website uses this with --topic")
    p.add_argument("--topic", help="the research topic for --campaign (1 to 300 characters)")
    sub.add_parser("check", help="are both workers and the app awake? are the tokens right?")
    sub.add_parser("status", help="show what the app remembers")
    p = sub.add_parser("reset", help="forget every saved run")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.add_argument("--delete-files", action="store_true", help="also delete the orchestrator's reports and the trace logs")
    p = sub.add_parser("stats", help="summarise trace logs")
    p.add_argument("files", nargs="*", help="trace files (default: traces/orchestrator.jsonl, researcher.jsonl, verifier.jsonl)")
    p.add_argument("--run", help="a run_id, or the start of one (default: the latest run)")
    p = sub.add_parser("handcheck", help="list the claims to check by hand: every unsupported one and a random pick of supported ones")
    p.add_argument("--run", type=int, help="the run number (default: the latest run)")
    p.add_argument("--samples", type=int, default=handcheck.SAMPLE_DEFAULT,
                   help=f"how many supported claims to pick at random (default {handcheck.SAMPLE_DEFAULT})")
    p.add_argument("--seed", help="seed for the random pick (default: fixed by the run, so the same run always gives the same pick)")
    args = parser.parse_args(argv)
    commands = {"run": cmd_run, "check": cmd_check, "status": cmd_status, "reset": cmd_reset, "stats": cmd_stats,
                "handcheck": cmd_handcheck}
    if args.command not in commands:
        parser.print_help()
        return 1
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        say("\nStopped (Ctrl+C). Nothing from this run was saved to the app.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
