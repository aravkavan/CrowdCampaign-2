"""Checking what a worker sent back, before any of it is used.

Everything here is a rule about SHAPE and IDENTITY, never about meaning:
  * the answer must be strict JSON and match the contract's schema;
  * it must echo the request_id and run_id we sent;
  * a research answer has at most K developments, and every id (development and claim) is unique;
  * a verification answer may only contain claim_ids we sent, each at most once, and "complete" means
    every claim we sent has a verdict.

Anything else is a Violation: the worker counts as failed for that call, the answer is thrown away
(none of it reaches the report, the database or the next worker), and the call is charged its full grant.
Free text in an answer (claim text, titles, error messages) is data. Nothing in this module, or any
module that decides routing or budget, reads it for meaning.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Violation(Exception):
    """A worker's answer breaks the contract."""


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    source_url: str


@dataclass
class Development:
    id: str
    title: str
    summary: str
    claims: list


@dataclass
class ResearchAnswer:
    status: str                  # complete | partial
    error: dict | None
    usage: dict
    developments: list


@dataclass
class VerifyAnswer:
    status: str                  # complete | partial
    error: dict | None
    usage: dict
    verdicts: dict = field(default_factory=dict)    # claim_id -> (verdict, evidence)


def _common(contract, call, definition: str, request_id: str, run_id: str) -> dict:
    if call.parsed is None:
        raise Violation(f"the answer is not usable JSON ({call.parse_problem or 'no body'})")
    problem = contract.problem(definition, call.parsed)
    if problem:
        raise Violation(f"the answer breaks the contract ({problem})")
    if call.parsed["request_id"].lower() != request_id.lower():
        raise Violation("the answer is for a different request_id")
    if call.parsed["run_id"].lower() != run_id.lower():
        raise Violation("the answer is for a different run_id")
    return call.parsed


def check_research(contract, call, *, request_id: str, run_id: str, k: int) -> ResearchAnswer:
    data = _common(contract, call, "researchOk", request_id, run_id)
    raw = data["result"]["developments"]
    if len(raw) > k:
        raise Violation(f"the answer has {len(raw)} developments but only {k} were asked for")
    dev_ids, claim_ids, developments = set(), set(), []
    for dev in raw:
        if dev["id"] in dev_ids:
            raise Violation(f"the development id {dev['id']!r} appears twice")
        dev_ids.add(dev["id"])
        claims = []
        for claim in dev["claims"]:
            if claim["claim_id"] in claim_ids:
                raise Violation(f"the claim id {claim['claim_id']!r} appears twice")
            claim_ids.add(claim["claim_id"])
            claims.append(Claim(claim["claim_id"], claim["text"], claim["source_url"]))
        developments.append(Development(dev["id"], dev["title"], dev["summary"], claims))
    return ResearchAnswer(data["status"], data["error"], data["usage"], developments)


def check_verify(contract, call, *, request_id: str, run_id: str, sent_ids: list) -> VerifyAnswer:
    data = _common(contract, call, "verifyOk", request_id, run_id)
    sent = set(sent_ids)
    verdicts: dict = {}
    for item in data["result"]["verdicts"]:
        claim_id = item["claim_id"]
        if claim_id not in sent:
            raise Violation(f"a verdict for claim_id {claim_id!r}, which was never sent")
        if claim_id in verdicts:
            raise Violation(f"two verdicts for claim_id {claim_id!r}")
        verdicts[claim_id] = (item["verdict"], item["evidence"])
    if data["status"] == "complete" and set(verdicts) != sent:
        raise Violation(f"the answer says complete but has {len(verdicts)} verdicts for {len(sent)} claims")
    return VerifyAnswer(data["status"], data["error"], data["usage"], verdicts)


def failure_details(contract, call) -> tuple:
    """For a worker's own failure answer (HTTP 500): (usage or None, error code, error message).

    The usage is returned only if the whole answer matches the contract's error shape. The message is
    free text from the worker: it may be shown (escaped), never acted on."""
    parsed = call.parsed
    if parsed is None or contract.problem("errorResponse", parsed):
        return None, None, None
    error = parsed["error"]
    return parsed["usage"], error["code"], error["message"]
