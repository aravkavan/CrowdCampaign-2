#!/usr/bin/env python3
"""Find one request in every trace file (all three services). Works the same on Windows and Mac.

  python3 scripts/find_trace.py <request_id>      (Windows: py scripts/find_trace.py <request_id>)

Prints every trace line that has that text, from traces/*.jsonl, with the file it came from.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if len(sys.argv) != 2 or not sys.argv[1].strip():
    print(__doc__)
    sys.exit(2)
needle = sys.argv[1].strip()
files = sorted((ROOT / "traces").glob("*.jsonl"))
if not files:
    print("No trace files in the traces folder yet. Do a run first.")
    sys.exit(1)
hits = 0
for f in files:
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if needle in line:
            print(f"{f.name}: {line}")
            hits += 1
print(f"\n{hits} line(s) found." if hits else f"Nothing found for {needle}. Check you copied the whole id.")
sys.exit(0 if hits else 1)
