"""Calling a worker over HTTP. One call() is one logical request, however many attempts it takes.

What is retried, and what is not
  Retried (transient):  connection refused or dropped, no answer in time, 502, 503, 504.
  Never retried:        200 and 500 (a worker's final answer), 400, 401, 409 (our request is wrong or refused),
                        and anything else unexpected.
  Waiting:              exponential backoff (base, 2x, 4x ... up to a cap) with a little random jitter; when the
                        worker sends Retry-After we wait at least that long. A worker asking for more than
                        retries.max_retry_after_seconds ends the call instead.

What a retry sends
  The request body is turned into text ONCE (keys sorted, no spaces) and the identical bytes go out on every
  attempt, with the same request_id. time_budget_ms is part of that body, so a retry never shrinks it. The
  worker's idempotency check relies on exactly this: same id + same body = the stored answer, no new work.

Timeouts
  Our read timeout = the worker's time budget + read_margin (+ cold_start_allowance until this worker has
  answered once). It is always longer than the worker's own deadline, so we do not give up on a worker that
  is still inside its budget.

Every attempt writes one `out` line to the trace log: latency measured here, on the caller's side.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import requests

from .contract import BadJSON, canonical_json, parse_json

USER_AGENT = "crowdcampaign-orchestrator/2.0"
TRANSIENT_STATUS = (502, 503, 504)
REFUSED_STATUS = (400, 401, 409)
_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass
class CallResult:
    outcome: str               # ok (200) | worker_failed (500) | refused (400/401/409) | unavailable | unexpected
    http_status: int           # the last HTTP status we got; 0 when nothing ever answered
    parsed: object             # the answer as strict JSON, or None
    parse_problem: str | None  # why `parsed` is None although something arrived
    attempts: int
    elapsed_ms: int            # the whole call: every attempt and every wait
    reason: str                # one plain sentence
    replayed: bool = False     # the worker said it was a stored answer
    retries: int = 0
    waited_s: float = 0.0


@dataclass
class Health:
    ok: bool
    attempts: int
    ms: int
    reason: str


class _TooBig(Exception):
    pass


def _retry_after(headers) -> float | None:
    value = (headers or {}).get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


class WorkerClient:
    def __init__(self, name: str, base_url: str, token: str, trace, *, retry, network, session=None,
                 sleep=time.sleep, clock=time.monotonic, jitter=random.random):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.trace = trace            # a ServiceTrace
        self.retry = retry
        self.network = network
        self.session = session or requests.Session()
        self.health_session = session or requests.Session()   # the warm-up runs on its own thread
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        self.warm = False             # True once this worker has answered us

    # ------------------------------------------------------------------ logging

    def _log(self, *, method: str, path: str, run_id, request_id, step: int, attempt: int, status: int,
             started: float, tokens: int = 0, detail: str | None = None) -> None:
        self.trace.write(direction="out", endpoint_or_tool=f"{self.name} {method} {path}", status=status,
                         latency_ms=(self.clock() - started) * 1000, run_id=run_id, request_id=request_id, step=step,
                         attempt=attempt, tokens=tokens, detail=detail)

    # ------------------------------------------------------------------ health

    def health(self, run_id, *, timeout: float) -> Health:
        """GET /healthz until it answers {"status":"ok"} or `timeout` seconds pass. No token is sent: the
        endpoint needs none. This is also how a sleeping Render service is woken up and timed."""
        begun = self.clock()
        attempt = 0
        reason = "no answer"
        refused = 0
        while True:
            attempt += 1
            left = timeout - (self.clock() - begun)
            if left <= 0 or attempt > 8:
                break
            t0 = self.clock()
            status, detail = 0, None
            try:
                response = self.health_session.get(self.base_url + "/healthz", headers={"User-Agent": USER_AGENT},
                                                   timeout=(self.network.connect_timeout, max(1.0, left)),
                                                   allow_redirects=False)
                status = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if status == 200 and isinstance(body, dict) and body.get("status") == "ok":
                    self._log(method="GET", path="/healthz", run_id=run_id, request_id=None, step=0, attempt=attempt,
                              status=200, started=t0, detail="awake" if attempt == 1 else f"awake after {attempt} tries")
                    self.warm = True
                    return Health(True, attempt, int((self.clock() - begun) * 1000), "awake")
                detail = f"unexpected answer (HTTP {status})"
            except requests.ConnectTimeout:
                detail = "could not connect in time"
            except requests.ReadTimeout:
                detail = "no answer in time"
            except requests.RequestException as err:
                detail = f"connection failed ({err.__class__.__name__})"
                text = str(err).lower()
                refused = refused + 1 if ("refused" in text or "errno 111" in text or "10061" in text) else 0
            else:
                refused = 0
            reason = detail
            self._log(method="GET", path="/healthz", run_id=run_id, request_id=None, step=0, attempt=attempt,
                      status=status, started=t0, detail=detail)
            if status in (301, 302, 303, 307, 308, 401, 403, 404):
                break  # waiting will not fix a wrong address
            if refused >= 3:
                reason = "nothing is listening at that address (connection refused 3 times)"
                break  # a sleeping Render service holds the connection open; it does not refuse it
            self.sleep(min(3.0 * attempt, 10.0, max(0.0, timeout - (self.clock() - begun))))
        return Health(False, attempt, int((self.clock() - begun) * 1000), reason)

    def probe_token(self, path: str, run_id) -> tuple:
        """Is our token accepted? POST an empty object with the token: a worker checks the token BEFORE the body, so
        401 means the token is wrong and 400 means it was accepted (and the empty body refused). No work is done and
        nothing is charged. Returns (HTTP status or 0, short sentence)."""
        t0 = self.clock()
        status, text = 0, "no answer"
        try:
            response = self.session.post(self.base_url + path, data=b"{}", timeout=(self.network.connect_timeout, 30),
                                         headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
                                                  "User-Agent": USER_AGENT}, allow_redirects=False)
            status = response.status_code
            text = {401: "the token was refused (it must match the token the worker was started with)",
                    400: "the token was accepted"}.get(status, f"unexpected answer (HTTP {status})")
        except requests.RequestException as err:
            text = f"no answer ({err.__class__.__name__})"
        self._log(method="POST", path=path, run_id=run_id, request_id=None, step=0, attempt=1, status=status, started=t0,
                  detail="token check: " + text)
        return status, text

    # ------------------------------------------------------------------ one POST

    def _read(self, response, limit_s: float) -> bytes:
        chunks, size = [], 0
        stop = self.clock() + limit_s
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > self.network.max_response_bytes:
                raise _TooBig()
            chunks.append(chunk)
            if self.clock() > stop:
                raise requests.ReadTimeout()
        return b"".join(chunks)

    def _post(self, path: str, body: bytes, attempt: int, read_timeout: float):
        """One HTTP attempt. Returns (status, raw, headers, transport_problem, answer_problem)."""
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
                   "Accept": "application/json", "X-Request-Attempt": str(attempt), "User-Agent": USER_AGENT}
        response = None
        try:
            response = self.session.post(self.base_url + path, data=body, headers=headers,
                                         timeout=(self.network.connect_timeout, read_timeout), stream=True,
                                         allow_redirects=False)
            try:
                raw = self._read(response, read_timeout)
            except _TooBig:
                return response.status_code, b"", response.headers, None, \
                    f"the answer is larger than {self.network.max_response_bytes:,} bytes"
            return response.status_code, raw, response.headers, None, None
        except requests.ConnectTimeout:
            return 0, b"", {}, f"could not connect within {self.network.connect_timeout:.0f} s", None
        except requests.ReadTimeout:
            return 0, b"", {}, f"no answer within {read_timeout:.0f} s", None
        except requests.exceptions.SSLError:
            return 0, b"", {}, "the secure connection failed (certificate or TLS problem)", None
        except requests.ConnectionError:
            return 0, b"", {}, "connection refused or dropped", None
        except requests.RequestException as err:
            return 0, b"", {}, f"network error ({err.__class__.__name__})", None
        finally:
            if response is not None:
                response.close()

    def _wait(self, attempt: int, asked: float | None) -> float:
        base = min(self.retry.max_delay, self.retry.base_delay * 2 ** (attempt - 1))
        wait = min(self.retry.max_delay, base * (1 + 0.25 * self.jitter()))
        return max(wait, asked) if asked is not None else wait

    # ------------------------------------------------------------------ one logical call

    def call(self, path: str, envelope: dict, *, step: int, give_up_at: float) -> CallResult:
        request_id, run_id = envelope["request_id"], envelope["run_id"]
        body = canonical_json(envelope).encode("utf-8")        # serialised once: every attempt sends these exact bytes
        budget_s = envelope["time_budget_ms"] / 1000
        begun = self.clock()
        waited = 0.0
        attempt = 0
        while True:
            attempt += 1
            read_timeout = budget_s + self.network.read_margin + (0 if self.warm else self.network.cold_start_allowance)
            t0 = self.clock()
            status, raw, headers, transport, answer_problem = self._post(path, body, attempt, read_timeout)

            parsed, parse_problem = None, answer_problem
            if status and raw and not answer_problem:
                try:
                    parsed = parse_json(raw)
                except BadJSON as err:
                    parse_problem = str(err)
            elif status and not raw and not answer_problem:
                parse_problem = "the answer was empty"
            tokens, code, state = 0, None, None
            if isinstance(parsed, dict):
                usage = parsed.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("tokens"), int) and usage["tokens"] >= 0:
                    tokens = usage["tokens"]
                err_obj = parsed.get("error")
                if isinstance(err_obj, dict) and isinstance(err_obj.get("code"), str) and _CODE.match(err_obj["code"]):
                    code = err_obj["code"]
                if parsed.get("status") in ("complete", "partial", "failed"):
                    state = parsed["status"]
            replayed = (headers.get("Idempotent-Replay") == "true") if status else False

            def done(outcome: str, reason: str) -> CallResult:
                self.warm = self.warm or status in (200, 500)
                return CallResult(outcome=outcome, http_status=status, parsed=parsed, parse_problem=parse_problem,
                                  attempts=attempt, elapsed_ms=int((self.clock() - begun) * 1000), reason=reason,
                                  replayed=replayed, retries=attempt - 1, waited_s=round(waited, 3))

            def log(detail: str) -> None:
                self._log(method="POST", path=path, run_id=run_id, request_id=request_id, step=step, attempt=attempt,
                          status=status, started=t0, tokens=tokens, detail=detail)

            if status == 200:
                log(("replayed: " if replayed else "") + (state or "unreadable") + (f": {code}" if code else ""))
                return done("ok", "answered")
            if status == 500:
                log("worker failed" + (f": {code}" if code else ""))
                return done("worker_failed", "reported a failure" + (f" ({code})" if code else ""))
            if status in REFUSED_STATUS:
                log("refused" + (f": {code}" if code else ""))
                meaning = {400: "rejected the request as malformed", 401: "rejected our token",
                           409: "says this request_id was already used with a different body"}[status]
                return done("refused", f"{meaning} (HTTP {status})")
            if status and status not in TRANSIENT_STATUS:
                log(f"unexpected status {status}")
                return done("unexpected", f"answered HTTP {status}, which is not part of the contract")

            # --- transient: no answer at all, or 502/503/504
            asked = _retry_after(headers) if status else None
            if status:
                problem = f"answered {status}" + (f" ({code})" if code else "") + \
                          (f", asking us to wait {asked:.0f} s" if asked is not None else "")
            else:
                problem = transport
            if attempt >= self.retry.max_attempts:
                log(f"{problem}; giving up after {attempt} tries")
                return done("unavailable", f"{problem}; gave up after {attempt} tries")
            if asked is not None and asked > self.retry.max_retry_after:
                log(f"{problem}; longer than our limit of {self.retry.max_retry_after:.0f} s")
                return done("unavailable", f"{problem}; that is longer than we will wait ({self.retry.max_retry_after:.0f} s)")
            wait = self._wait(attempt, asked)
            if self.clock() + wait >= give_up_at:
                log(f"{problem}; no time left to try again")
                return done("unavailable", f"{problem}; no time left to try again")
            log(f"{problem}; trying again in {wait:.1f} s")
            self.sleep(wait)
            waited += wait
