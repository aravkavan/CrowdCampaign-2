#!/usr/bin/env python3
"""Set up and check the three services' .env files. Standard library only.

  python3 scripts/setup_env.py              create the three .env files and make the two secret tokens
  python3 scripts/setup_env.py --check      say which values are set or empty (never prints a secret)
  python3 scripts/setup_env.py --show verifier   print the verifier token, to paste into Render

It never overwrites a value you already filled in. The .env files are ignored by git.
"""
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ["researcher", "verifier", "orchestrator"]
MODEL_KEYS = ["GEMINI_API_KEY", "GROQ_API_KEY"]
# What each service needs: ("all", [names]) or ("one", [names]) for "at least one of".
NEEDS = {
    "researcher": [("all", ["RESEARCHER_TOKEN", "TAVILY_API_KEY"]), ("one", MODEL_KEYS)],
    "verifier": [("all", ["VERIFIER_TOKEN"]), ("one", MODEL_KEYS)],
    "orchestrator": [("all", ["RESEARCHER_TOKEN", "VERIFIER_TOKEN", "CROWDCAMPAIGN_API_URL", "TRACKER_USERNAME", "TRACKER_PASSWORD"])],
}


def env_path(service):
    return ROOT / "services" / service / ".env"


def example_path(service):
    return ROOT / "services" / service / ".env.example"


def parse(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
            if m:
                values[m.group(1)] = m.group(2).strip()
    return values


def set_value(text, key, value):
    pattern = re.compile(rf"^{key}=.*$", re.M)
    if pattern.search(text):
        return pattern.sub(lambda _m: f"{key}={value}", text, count=1)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def existing_token(name):
    for service in SERVICES:
        value = parse(env_path(service)).get(name, "")
        if value:
            return value
    return ""


def setup():
    tokens = {name: existing_token(name) or secrets.token_urlsafe(32) for name in ("RESEARCHER_TOKEN", "VERIFIER_TOKEN")}
    wanted = {"researcher": ["RESEARCHER_TOKEN"], "verifier": ["VERIFIER_TOKEN"], "orchestrator": ["RESEARCHER_TOKEN", "VERIFIER_TOKEN"]}
    for service in SERVICES:
        target = env_path(service)
        if not example_path(service).exists():
            print(f"  ! {example_path(service).relative_to(ROOT)} is missing. Is this the full project folder?")
            sys.exit(1)
        created = not target.exists()
        text = target.read_text(encoding="utf-8") if not created else example_path(service).read_text(encoding="utf-8")
        have = parse(target)
        changed = []
        for name in wanted[service]:
            if not have.get(name):
                text = set_value(text, name, tokens[name])
                changed.append(name)
        if created or changed:
            target.write_text(text, encoding="utf-8")
        rel = target.relative_to(ROOT)
        if created:
            print(f"  created {rel}" + (f" (made {', '.join(changed)})" if changed else ""))
        elif changed:
            print(f"  updated {rel} (made {', '.join(changed)})")
        else:
            print(f"  kept    {rel} (nothing to change)")
    print("\nNext: open services/researcher/.env and services/verifier/.env in VS Code and paste your keys.")
    print("Then run:  python3 scripts/setup_env.py --check")


def check():
    problems = 0
    parsed = {s: parse(env_path(s)) for s in SERVICES}
    for service in SERVICES:
        path = env_path(service)
        print(f"services/{service}/.env")
        if not path.exists():
            print("  X  the file does not exist. Run:  python3 scripts/setup_env.py")
            problems += 1
            continue
        for kind, names in NEEDS[service]:
            if kind == "all":
                for name in names:
                    ok = bool(parsed[service].get(name))
                    print(f"  {'ok' if ok else 'X '}  {name} is {'set' if ok else 'EMPTY'}")
                    problems += 0 if ok else 1
            else:
                setnames = [n for n in names if parsed[service].get(n)]
                ok = bool(setnames)
                print(f"  {'ok' if ok else 'X '}  a model key: " + (", ".join(setnames) + " set" if ok else "none set (fill in GEMINI_API_KEY or GROQ_API_KEY)"))
                problems += 0 if ok else 1
    print("Cross-checks")
    rt = [parsed["researcher"].get("RESEARCHER_TOKEN"), parsed["orchestrator"].get("RESEARCHER_TOKEN")]
    vt = [parsed["verifier"].get("VERIFIER_TOKEN"), parsed["orchestrator"].get("VERIFIER_TOKEN")]
    for label, pair in (("researcher token is the same in the researcher and the orchestrator", rt), ("verifier token is the same in the verifier and the orchestrator", vt)):
        ok = bool(pair[0]) and pair[0] == pair[1]
        print(f"  {'ok' if ok else 'X '}  {label}")
        problems += 0 if ok else 1
    ok = bool(rt[0]) and rt[0] != vt[0]
    print(f"  {'ok' if ok else 'X '}  the two tokens are different from each other")
    problems += 0 if ok else 1
    print("\nAll good." if not problems else f"\n{problems} thing(s) to fix. Edit the file named above, save, and run this again.")
    sys.exit(1 if problems else 0)


def show(service):
    if service not in ("researcher", "verifier"):
        print("Use:  --show researcher   or   --show verifier")
        sys.exit(2)
    name = f"{service.upper()}_TOKEN"
    value = parse(env_path(service)).get(name, "")
    if not value:
        print(f"{name} is not set. Run:  python3 scripts/setup_env.py")
        sys.exit(1)
    print(f"{name}={value}")


def main(argv):
    if len(argv) == 1:
        setup()
    elif argv[1] == "--check":
        check()
    elif argv[1] == "--show" and len(argv) == 3:
        show(argv[2])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
