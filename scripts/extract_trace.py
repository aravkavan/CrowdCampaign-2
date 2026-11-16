#!/usr/bin/env python3
"""Turn a log copied from Render into the verifier's trace file (traces/verifier.jsonl).

On Render the verifier has no disk to keep a trace file on, so it prints every trace line to its log
instead. Copy that log (Render dashboard > your service > Logs, select all, copy, paste into a text file
or use the download button) and run:

    python scripts/extract_trace.py render-log.txt                  writes traces/verifier.jsonl
    python scripts/extract_trace.py render-log.txt --out some.jsonl

Render adds its own lines ("==> Your service is live", timestamps in front of a line ...). This keeps only the
lines that are trace lines (a JSON object with the trace fields), drops the rest and any exact duplicates (when
two copies of the log overlap), and writes them in time order. Nothing is changed inside a line.

Do this soon after your runs: Render keeps free-tier logs for a limited time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = {"ts", "service", "direction", "endpoint_or_tool", "status", "latency_ms"}


def _record_in(line: str):
    """The trace record that starts somewhere in this line (text before it is ignored, text after it too), or None."""
    decoder = json.JSONDecoder()
    start = line.find("{")
    while start != -1:
        try:
            candidate, _ = decoder.raw_decode(line[start:])
        except ValueError:
            start = line.find("{", start + 1)
            continue
        return candidate if isinstance(candidate, dict) and REQUIRED <= candidate.keys() else None
    return None


def trace_lines(text: str):
    """(trace lines in time order, count of other non-empty lines skipped). Exact duplicates are dropped."""
    kept, seen, skipped = [], set(), 0
    for raw in text.splitlines():
        record = _record_in(raw)
        if record is None:
            skipped += bool(raw.strip())
            continue
        line = json.dumps(record, ensure_ascii=False)
        if line not in seen:
            seen.add(line)
            kept.append((str(record["ts"]), line))
    kept.sort(key=lambda pair: pair[0])      # sorted() is stable: lines with the same time keep their order
    return [line for _, line in kept], skipped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", help="the text file with the log copied from Render")
    parser.add_argument("--out", default=str(ROOT / "traces" / "verifier.jsonl"), help="where to write (default: traces/verifier.jsonl)")
    args = parser.parse_args(argv)
    try:
        text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        print(f"Can't read {args.log}: {err.strerror or err}")
        return 2
    lines, skipped = trace_lines(text)
    if not lines:
        print("No trace lines found in that file. Did the verifier run? Each trace line is a JSON object that starts "
              '{"ts": ... "service": "verifier" ...}.')
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    services = sorted({json.loads(x)["service"] for x in lines})
    print(f"Kept {len(lines)} trace lines ({', '.join(services)}), skipped {skipped} other lines. Written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
