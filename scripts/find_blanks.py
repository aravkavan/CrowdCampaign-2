#!/usr/bin/env python3
"""List the blanks still left in ORCHESTRATION.md. Works the same on Windows and Mac.

  python3 scripts/find_blanks.py        (Windows: py scripts/find_blanks.py)

Each blank is a line with the words FILL IN on it. You are done when it says "No blanks left."
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "ORCHESTRATION.md")
if not path.exists():
    print(f"Can't find {path.name}. Run this from the crowdcampaign-2 folder.")
    sys.exit(2)
marker = "FILL" + " IN"  # split so this file never counts as a blank itself
found = [(n, line.strip()) for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if marker in line]
for n, line in found:
    print(f"  line {n}: {line[:150]}")
print(f"\n{len(found)} blank(s) left in {path.name}." if found else f"No blanks left. {path.name} is filled in.")
sys.exit(1 if found else 0)
