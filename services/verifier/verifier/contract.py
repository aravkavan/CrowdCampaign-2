"""The contract, as code: load contract.schema.json, check JSON against it, parse JSON strictly, and
say whether two bodies are "the same body".

Used by all three services: the workers check every request they receive and every answer they send,
and the orchestrator checks every answer it receives. Anything that does not match is treated as a
failure of the sender, never repaired and never passed on.

"Same body" means the same PARSED JSON: keys in any order, 5 and 5.0 equal, whitespace ignored. body_hash()
is the sha256 of the canonical text (sorted keys, no spaces), so it is the same for the same JSON.

Shared file: edit shared/contract.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match


class Contract:
    """contract.schema.json, loaded once. problem(name, value) says what is wrong, or None."""

    def __init__(self, path=None):
        path = Path(path) if path else Path(__file__).with_name("contract.schema.json")
        self.schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self._validators = {}

    def validator(self, name: str):
        if name not in self._validators:
            root = {"$schema": self.schema["$schema"], "$defs": self.schema["$defs"], "$ref": f"#/$defs/{name}"}
            self._validators[name] = Draft202012Validator(root)
        return self._validators[name]

    def problem(self, name: str, value) -> str | None:
        error = best_match(self.validator(name).iter_errors(value))
        if error is None:
            return None
        where = ".".join(str(part) for part in error.absolute_path) or "(body)"
        return f"{where}: {error.message}"[:300]


class BadJSON(ValueError):
    pass


def _no_constants(name):
    raise BadJSON(f"{name} is not valid JSON")


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise BadJSON(f"the key {key!r} appears twice")
        out[key] = value
    return out


def parse_json(raw: bytes):
    """Strict JSON: UTF-8, no NaN/Infinity, no duplicate keys."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BadJSON("the body is not UTF-8") from None
    try:
        return json.loads(text, parse_constant=_no_constants, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError as err:
        raise BadJSON(f"the body is not valid JSON ({err.msg} at position {err.pos})") from None
    except RecursionError:
        raise BadJSON("the JSON is nested too deeply") from None


def _plain(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)  # 5.0 and 5 are the same number
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def canonical_json(value) -> str:
    """The same parsed JSON always gives the same text: keys sorted, no spaces, 5.0 written as 5."""
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def body_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
