#!/usr/bin/env python3
"""Copy the files in shared/ into the services that use them.

Why copies? Each service must run on its own (the verifier is deployed to Render from its own folder
and must not import code from another service). But the helpers they have in common (the contract,
the fetch guard, the trace log ...) should be written once. shared/ is the one place to edit them;
this script puts identical copies inside each service's package.

    python scripts/sync_shared.py            copy everything (run this after editing anything in shared/)
    python scripts/sync_shared.py --check    change nothing; exit 1 if any copy differs from shared/
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"

EVERYONE = ["researcher", "verifier", "orchestrator"]
WORKERS = ["researcher", "verifier"]

# file in shared/  ->  services that get a copy
MANIFEST = {
    "contract.schema.json": EVERYONE,  # the one file all three agree on
    "contract.py": EVERYONE,           # loads it and checks JSON against it
    "trace.py": EVERYONE,
    "failures.py": EVERYONE,
    "settings.py": EVERYONE,
    "textcheck.py": EVERYONE,
    "worker_base.py": WORKERS,
    "guard.py": WORKERS,
    "extract.py": WORKERS,
    "pages.py": WORKERS,
    "llm.py": WORKERS,
}


def destination(service: str, name: str) -> Path:
    return ROOT / "services" / service / service / name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report differences; copy nothing")
    args = parser.parse_args(argv)

    problems, copied = [], 0
    for name, services in MANIFEST.items():
        source = SHARED / name
        if not source.exists():
            problems.append(f"shared/{name} is missing")
            continue
        data = source.read_bytes()
        for service in services:
            target = destination(service, name)
            same = target.exists() and target.read_bytes() == data
            if same:
                continue
            if args.check:
                problems.append(f"{target.relative_to(ROOT)} {'differs from' if target.exists() else 'is missing; copy of'} shared/{name}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                copied += 1
    # a stray copy of a shared file in a service that is not in the manifest would drift unnoticed
    for service in EVERYONE:
        folder = ROOT / "services" / service / service
        for name in MANIFEST:
            if (folder / name).exists() and service not in MANIFEST[name]:
                problems.append(f"{(folder / name).relative_to(ROOT)} should not exist (not in the manifest for {service})")

    if args.check:
        if problems:
            print("Shared files are out of sync:")
            for line in problems:
                print("  -", line)
            print("Fix: python scripts/sync_shared.py")
            return 1
        print(f"All {sum(len(v) for v in MANIFEST.values())} copies match shared/.")
        return 0
    for line in problems:
        print("  !", line)
    print(f"Copied {copied} file(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
