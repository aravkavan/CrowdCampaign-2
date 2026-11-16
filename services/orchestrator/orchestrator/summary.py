"""`python -m orchestrator stats`: round trips, time and tokens from the trace logs.

All three services write the same line format (ts, service, run_id, request_id, step, direction, endpoint_or_tool,
status, latency_ms, attempt, tokens, detail), so you can give this the orchestrator's log and the workers' logs
together and follow one run through all of them:

    python -m orchestrator stats ../../traces/orchestrator.jsonl ../../traces/researcher.jsonl ../../traces/verifier.jsonl

By default it summarises the latest run in the files; --run takes a run_id (or the start of one).
It can also read the older Assignment 2 trace files (traces/run1.jsonl ...) so the two designs can be compared.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _time(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def read_records(paths) -> list:
    records = []
    for path in paths:
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                record["_file"] = Path(path).name
                records.append(record)
    return records


def is_old_format(records) -> bool:
    return bool(records) and all("kind" in r and "direction" not in r for r in records)


def pick_run(records, wanted: str | None):
    """The run_id to summarise: the one asked for (prefix), else the latest the orchestrator logged."""
    ids, order = {}, []
    for r in records:
        run_id = r.get("run_id")
        if not run_id:
            continue
        if run_id not in ids:
            ids[run_id] = r.get("service")
            order.append(run_id)
    if wanted:
        matches = [i for i in order if i.startswith(wanted)]
        return matches[-1] if matches else None
    ours = [i for i in order if ids[i] == "orchestrator"]
    return (ours or order or [None])[-1]


def summarize(records, run_id) -> dict:
    mine = [r for r in records if r.get("run_id") == run_id]
    starts = [_time(r["ts"]).timestamp() - int(r.get("latency_ms") or 0) / 1000 for r in mine if r.get("ts")]
    ends = [_time(r["ts"]).timestamp() for r in mine if r.get("ts")]
    cross = [r for r in mine if r.get("service") == "orchestrator" and r.get("direction") == "out"
             and str(r.get("endpoint_or_tool", "")).split(" ")[0] in ("researcher", "verifier")]
    time_by = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # service -> kind -> [calls, ms]
    tokens = defaultdict(int)
    replays = defaultdict(int)
    served = defaultdict(lambda: [0, 0])                          # service -> [requests, ms]
    for r in mine:
        service, direction = r.get("service", "?"), r.get("direction")
        label = str(r.get("endpoint_or_tool", ""))
        if direction == "out":
            kind = label.split(" ")[0] or "?"
            cell = time_by[service][kind]
            cell[0] += 1
            cell[1] += int(r.get("latency_ms") or 0)
            if kind == "model":
                tokens[service] += int(r.get("tokens") or 0)
        elif direction == "in" and "/healthz" not in label:
            served[service][0] += 1
            served[service][1] += int(r.get("latency_ms") or 0)
            if str(r.get("detail", "")).startswith("replayed"):
                replays[service] += 1
    return {
        "run_id": run_id,
        "lines": len(mine),
        "files": sorted({r["_file"] for r in mine}),
        "wall_s": (max(ends) - min(starts)) if starts and ends else 0.0,
        "cross": cross,
        "time_by": {s: {k: list(v) for k, v in kinds.items()} for s, kinds in time_by.items()},
        "model_tokens": dict(tokens),
        "replays": dict(replays),
        "served": {s: list(v) for s, v in served.items()},
    }


def format_summary(s: dict) -> str:
    lines = [f"Trace summary for run {s['run_id']}", f"  {s['lines']} log lines from {', '.join(s['files'])}",
             f"  Wall-clock time: {s['wall_s']:.1f} s", ""]
    lines.append("Calls from the orchestrator to the workers (latency measured by the orchestrator):")
    if not s["cross"]:
        lines.append("  (none)")
    for r in s["cross"]:
        lines.append(f"  {str(r.get('ts', ''))[11:23]}  {str(r.get('endpoint_or_tool', '')):<26} try {r.get('attempt', 1)}  "
                     f"HTTP {r.get('status') or 'none':<4} {int(r.get('latency_ms') or 0) / 1000:>7.1f} s   "
                     f"request {str(r.get('request_id') or '-')[:8]}  {str(r.get('detail') or '')[:70]}")
    lines += ["", "Where the time went (every outbound call, by service and kind; calls overlap, so this can exceed wall-clock):"]
    for service in sorted(s["time_by"]):
        for kind, (calls, ms) in sorted(s["time_by"][service].items(), key=lambda kv: -kv[1][1]):
            extra = f"   tokens {s['model_tokens'].get(service, 0):,}" if kind == "model" else ""
            lines.append(f"  {service:<13} {kind:<14} {calls:>4} calls  {ms / 1000:>8.1f} s{extra}")
    lines += ["", "Requests each service answered:"]
    for service, (count, ms) in sorted(s["served"].items()):
        replayed = s["replays"].get(service, 0)
        lines.append(f"  {service:<13} {count:>3} requests, {ms / 1000:.1f} s in total" + (f", {replayed} answered from the stored copy" if replayed else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Assignment 2 (older) trace files

def summarize_old(records) -> dict:
    trips, trip_ms = defaultdict(int), defaultdict(int)
    totals = defaultdict(int)
    first = last = run = None
    for r in records:
        run = r.get("run", run)
        ts = r.get("ts")
        if ts:
            first, last = first or ts, ts
        kind = r.get("kind")
        if kind == "http":
            trips[r.get("service", "?")] += 1
            trip_ms[r.get("service", "?")] += int(r.get("latency_ms") or 0)
        elif kind == "model":
            totals["model_calls"] += 1
            totals["tokens_in"] += int(r.get("tokens_in") or 0)
            totals["tokens_out"] += int(r.get("tokens_out") or 0)
        elif kind == "tool":
            totals["credits"] += int(r.get("credits") or 0)
    wall = (_time(last) - _time(first)).total_seconds() if first and last else 0.0
    return {"run": run, "wall_s": wall, "trips": dict(trips), "trip_ms": dict(trip_ms), **totals}


def format_old(s: dict) -> str:
    lines = [f"Assignment 2 tracker trace, run {s['run']}", f"  Wall-clock time: {s['wall_s']:.1f} s",
             f"  Model calls: {s.get('model_calls', 0)}   tokens in: {s.get('tokens_in', 0):,}   tokens out: {s.get('tokens_out', 0):,}   "
             f"(total {s.get('tokens_in', 0) + s.get('tokens_out', 0):,})",
             f"  Search credits: {s.get('credits', 0)}"]
    for service, count in sorted(s["trips"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {service:<8} {count:>4} round trips  {s['trip_ms'].get(service, 0) / 1000:>7.1f} s")
    return "\n".join(lines)


def describe(paths, run: str | None = None) -> str:
    records = read_records(paths)
    if not records:
        return "The trace files are empty."
    if is_old_format(records):
        return format_old(summarize_old(records))
    run_id = pick_run(records, run)
    if run_id is None:
        return "No run found in these files" + (f" that starts with {run!r}." if run else ".")
    return format_summary(summarize(records, run_id))
