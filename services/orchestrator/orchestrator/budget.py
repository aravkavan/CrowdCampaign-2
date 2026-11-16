"""One budget for the whole run: split between the two workers, handed out in slices, charged once per request.

The total comes from config.yaml (budget.total). At the start of the run it is split in two pools: the
researcher's and the verifier's (budget.researcher_share says how). Before each call the orchestrator hands
that worker a GRANT: its pool's remaining amount divided by the campaigns still to do ("dynamic slicing", so
what an earlier campaign did not use rolls forward), and never more than the wall-clock time that is left.
The grant goes into the request. The worker enforces it and cuts its work short when it runs out.

After the call the grant is settled:
  * a usable answer (200, or a 500 with a valid error body) is charged what the worker REPORTED it used;
  * a request with no usable answer (the worker never answered, kept saying 503, or sent something that breaks
    the contract) is charged its FULL grant, because the worker may well have spent it;
  * a request refused before any work (400, 401, 409) is free.
A request_id is charged once, however many times it was sent: retries reuse the id.

Nothing here reads text from a worker. Only the numbers in a validated `usage` object are used.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .config import DIMENSIONS, WORKERS, Amounts

# The contract's own limits on one request.
CONTRACT_MAX = {"steps": 200, "fetches": 200, "tokens": 5_000_000, "seconds": 3600}


@dataclass(frozen=True)
class Grant:
    worker: str
    steps: int
    fetches: int
    tokens: int
    seconds: float

    @property
    def time_budget_ms(self) -> int:
        return max(1, int(self.seconds * 1000))

    def envelope_budget(self) -> dict:
        return {"max_steps": self.steps, "max_fetches": self.fetches, "max_tokens": self.tokens}

    def with_steps(self, steps: int) -> "Grant":
        return Grant(self.worker, max(1, min(self.steps, int(steps))), self.fetches, self.tokens, self.seconds)


@dataclass
class Charge:
    request_id: str
    worker: str
    steps: int
    fetches: int
    tokens: int
    seconds: float
    basis: str                       # "reported" | "full grant" | "free"
    searches: int = 0
    overran: list = field(default_factory=list)   # which of the grant's limits the worker's own report went past


class RunBudget:
    def __init__(self, total: Amounts, researcher_share: Amounts, minimum: dict, *, deadline: float | None = None,
                 reserve_seconds: float = 0.0, clock=time.monotonic):
        self.total = {d: (float(total.get(d)) if d == "seconds" else int(total.get(d))) for d in DIMENSIONS}
        self.minimum = minimum
        self.deadline = deadline          # time.monotonic() value when the whole run must be over
        self.reserve = reserve_seconds    # kept back for writing the report and saving the run
        self.clock = clock
        self.pool = {"researcher": {}, "verifier": {}}
        for dim in DIMENSIONS:
            if dim == "seconds":
                mine = self.total[dim] * researcher_share.get(dim)
            else:
                mine = math.floor(self.total[dim] * researcher_share.get(dim))
            self.pool["researcher"][dim] = mine
            self.pool["verifier"][dim] = self.total[dim] - mine      # the two pools add up to the total exactly
        self.used = {w: {d: 0.0 for d in DIMENSIONS} for w in WORKERS}
        self.charges: dict = {}
        self.last_skip: str | None = None

    # ---- what is left

    def remaining(self, worker: str, dimension: str) -> float:
        return max(0.0, self.pool[worker][dimension] - self.used[worker][dimension])

    def wall_left(self) -> float:
        if self.deadline is None:
            return float("inf")
        return max(0.0, self.deadline - self.clock() - self.reserve)

    # ---- handing out a slice

    def grant(self, worker: str, calls_left: int) -> Grant | None:
        """The slice for the next call to `worker`, or None when the run can't afford a useful call."""
        n = max(1, int(calls_left))
        floor_ = self.minimum[worker]
        amounts = {}
        for dim in DIMENSIONS:
            have = self.remaining(worker, dim)
            if dim == "seconds":
                have = min(have, self.wall_left())
            need = floor_.get(dim)
            fair = have / n
            # If the fair share is below the smallest useful call, give the whole minimum to this campaign
            # (if the pool can cover it) instead of starving every campaign a little.
            amount = fair if fair >= need else (need if have >= need else 0)
            amount = min(amount, have, CONTRACT_MAX[dim])
            if dim != "seconds":
                amount = int(math.floor(amount))
            lowest = need if dim == "fetches" else max(need, 1)   # the contract allows 0 fetches but not 0 steps/tokens/time
            if amount < lowest:
                self.last_skip = self._why(worker, dim)
                return None
            amounts[dim] = amount
        self.last_skip = None
        return Grant(worker, amounts["steps"], amounts["fetches"], amounts["tokens"], float(amounts["seconds"]))

    def _why(self, worker: str, dim: str) -> str:
        if dim == "seconds" and self.wall_left() < self.remaining(worker, dim):
            return "the run is out of time"
        pool = self.pool[worker][dim]
        label = {"steps": "step", "fetches": "fetch", "tokens": "token", "seconds": "time"}[dim]
        return f"the {worker}'s {label} budget for this run is used up ({self._show(self.remaining(worker, dim), dim)} of {self._show(pool, dim)} left)"

    @staticmethod
    def _show(value: float, dim: str) -> str:
        return f"{value:,.0f} s" if dim == "seconds" else f"{value:,.0f}"

    # ---- settling a request

    def charge(self, request_id: str, worker: str, grant: Grant, usage: dict | None, *, free: bool = False) -> Charge:
        """Charge a request once. usage is a validated usage object, or None when there is no usable answer."""
        if request_id in self.charges:
            return self.charges[request_id]
        if free:
            charge = Charge(request_id, worker, 0, 0, 0, 0.0, "free")
        elif usage is None:
            charge = Charge(request_id, worker, grant.steps, grant.fetches, grant.tokens, grant.seconds, "full grant")
        else:
            seconds = usage["elapsed_ms"] / 1000
            charge = Charge(request_id, worker, int(usage["steps"]), int(usage["fetches"]), int(usage["tokens"]), seconds,
                            "reported", searches=int(usage.get("searches") or 0))
            if charge.steps > grant.steps:
                charge.overran.append("steps")
            if charge.fetches > grant.fetches:
                charge.overran.append("fetches")
            if charge.tokens > grant.tokens:
                charge.overran.append("tokens")
            if seconds > grant.seconds + 1.0:
                charge.overran.append("seconds")
        for dim in DIMENSIONS:
            self.used[worker][dim] += getattr(charge, dim)
        self.charges[request_id] = charge
        return charge

    # ---- totals for the report

    def spent(self, worker: str | None = None) -> dict:
        workers = [worker] if worker else list(WORKERS)
        return {d: sum(self.used[w][d] for w in workers) for d in DIMENSIONS}

    def searches(self) -> int:
        return sum(c.searches for c in self.charges.values())

    def overruns(self) -> list:
        return [(c.worker, c.request_id, c.overran) for c in self.charges.values() if c.overran]

    def summary(self) -> dict:
        return {
            "total": dict(self.total),
            "pool": {w: dict(p) for w, p in self.pool.items()},
            "used": {w: dict(u) for w, u in self.used.items()},
            "spent": self.spent(),
            "requests_charged": len(self.charges),
        }
