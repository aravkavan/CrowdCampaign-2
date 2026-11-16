"""The worker half of the contract. The researcher and the verifier both run on this file, unchanged.

A worker supplies a name, an endpoint and one function, work(ctx), that does the job. Everything the
contract demands is done here, in this order, for every request:

  GET /healthz       answered first, no token needed:  {"status": "ok"}
  1. token           missing or wrong -> 401. Checked BEFORE the body is read, let alone validated.
  2. route, method   unknown path -> 404, wrong method -> 405
  3. body            larger than 1 MB, not JSON, or not matching contract.schema.json -> 400
  4. idempotency     same request_id + different body            -> 409
                     same request_id + same body, finished       -> the stored answer again, no new work
                     same request_id + same body, still running  -> 503 with Retry-After
  5. capacity        too many requests at once -> 503 with Retry-After
  6. the work        runs with a Budget. Its clock started when the request ARRIVED (not when work began).
  7. the answer      checked against the schema before it leaves:
                     200 complete / 200 partial (budget ran out; what was finished is returned)
                     500 failed, retryable false (cannot be fixed by trying again)
                     503 retryable (could not take it now, nothing was stored, try again later)

The idempotency store lives in memory. If the process restarts (Render does this after a deploy, a crash,
or the 15-minute idle spin-down) it is empty, so a repeated request_id is simply treated as new and the
work is done again. ORCHESTRATION.md explains what that means.

Shared file: edit shared/worker_base.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import hmac
import json
import math
import os
import re
import signal
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contract import BadJSON, Contract, body_hash, canonical_json, parse_json  # noqa: F401  (re-exported for the workers)
from .failures import BudgetExhausted, TerminalError, TimeBudgetExhausted, UpstreamBusy

MAX_BODY_BYTES = 1_000_000
ZERO_USAGE = {"steps": 0, "fetches": 0, "tokens": 0, "elapsed_ms": 0}
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# ---------------------------------------------------------------------------- one request's budget

class Budget:
    """What one request may spend. The worker enforces it; the orchestrator also checks what is reported.

    steps    one unit of agent work (a model decision for the researcher, a claim for the verifier)
    fetches  every outbound HTTP request to a web page: the page, each redirect hop, and robots.txt
    tokens   model tokens, input and output
    time     the clock started when the request arrived
    """

    def __init__(self, max_steps: int, max_fetches: int, max_tokens: int, deadline: float, *, received: float | None = None,
                 clock=time.monotonic, min_step_seconds: float = 0.0):
        self.max_steps, self.max_fetches, self.max_tokens = max_steps, max_fetches, max_tokens
        self.deadline = deadline
        self.min_step_seconds = min_step_seconds  # a step started with less time than this could not finish
        self.clock = clock
        self.received = clock() if received is None else received
        self.steps = self.fetches = self.tokens = self.searches = 0
        self._lock = threading.Lock()

    def time_left(self) -> float:
        return self.deadline - self.clock()

    def time_fraction_used(self) -> float:
        total = self.deadline - self.received
        return 1.0 if total <= 0 else min(1.0, (self.clock() - self.received) / total)

    def check_time(self, service: str = "worker") -> None:
        if self.time_left() <= 0:
            raise TimeBudgetExhausted(service)

    def take_step(self, service: str = "worker") -> int:
        self.check_time(service)
        if self.time_left() < self.min_step_seconds:
            raise TimeBudgetExhausted(service, "too little time is left to start another step")
        with self._lock:
            if self.steps >= self.max_steps:
                raise BudgetExhausted(service, f"all {self.max_steps} steps are used", "step")
            self.steps += 1
            return self.steps

    def gate(self) -> None:
        """Called before every outbound web request. Counts it, or raises before anything is sent."""
        self.check_time("fetch")
        with self._lock:
            if self.fetches >= self.max_fetches:
                raise BudgetExhausted("fetch", f"all {self.max_fetches} fetches are used", "fetch")
            self.fetches += 1

    def add_tokens(self, count: int) -> None:
        with self._lock:
            self.tokens += max(0, int(count))

    def tokens_left(self) -> int:
        return self.max_tokens - self.tokens

    def usage(self) -> dict:
        out = {"steps": self.steps, "fetches": self.fetches, "tokens": self.tokens,
               "elapsed_ms": max(0, int((self.clock() - self.received) * 1000))}
        if self.searches:
            out["searches"] = self.searches
        return out


@dataclass
class RequestContext:
    """Everything work(ctx) is given about the request it is serving."""
    request_id: str
    run_id: str
    payload: dict
    time_budget_ms: int
    budget: Budget
    trace: object  # a BoundTrace: ctx.trace.step = n labels the calls that follow
    attempt: int = 1

    @property
    def deadline(self) -> float:
        return self.budget.deadline

    def time_left(self) -> float:
        return self.budget.time_left()


@dataclass
class WorkOutcome:
    """What work(ctx) returns. status "partial" needs an error object saying why it stopped early."""
    status: str
    result: dict
    error: dict | None = None
    note: str | None = None  # a short summary for the trace log, e.g. "8 claims: 5 supported, 2 unsupported, 1 unreachable"


class WorkerFailure(Exception):
    """Raised by work() to answer with a specific error (default: 500, cannot be fixed by retrying)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int = 500,
                 retry_after: float | None = None):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.http_status, self.retry_after = http_status, retry_after


def error_object(code: str, message: str, retryable: bool) -> dict:
    return {"code": code, "message": str(message)[:600], "retryable": bool(retryable)}


# ---------------------------------------------------------------------------- idempotency

@dataclass
class _Entry:
    digest: str
    state: str  # "running" | "done"
    deadline: float
    status: int = 0
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    finished: float = 0.0


class IdempotencyStore:
    """Answers remembered by request_id, in memory only.

    begin() decides what a request is:
      "new"       first time we see this request_id; it is now marked running
      "conflict"  the request_id was used with a different body
      "replay"    same body, already finished: the stored answer is returned
      "running"   same body, the first copy is still being worked on
    Only 200 and 500 answers are stored (finish). A 503 means "not done, try again", so abort()
    forgets the request and a retry runs fresh.
    """

    def __init__(self, max_done: int = 500, ttl_seconds: float = 6 * 3600, clock=time.monotonic):
        self.max_done, self.ttl, self.clock = max_done, ttl_seconds, clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def begin(self, request_id: str, digest: str, deadline: float):
        with self._lock:
            self._expire()
            entry = self._entries.get(request_id)
            if entry is None:
                self._entries[request_id] = _Entry(digest=digest, state="running", deadline=deadline)
                return "new", None
            if entry.digest != digest:
                return "conflict", None
            if entry.state == "done":
                self._entries.move_to_end(request_id)
                return "replay", entry
            return "running", entry

    def finish(self, request_id: str, status: int, body: bytes, headers: dict | None = None) -> None:
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None:
                return
            entry.state, entry.status, entry.body = "done", status, body
            entry.headers, entry.finished = dict(headers or {}), self.clock()
            self._entries.move_to_end(request_id)

    def abort(self, request_id: str) -> None:
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is not None and entry.state == "running":
                del self._entries[request_id]

    def _expire(self) -> None:
        now = self.clock()
        for key in list(self._entries):
            entry = self._entries[key]
            if entry.state == "done" and now - entry.finished > self.ttl:
                del self._entries[key]
            elif entry.state == "running" and now > entry.deadline + 120:  # safety net: a request can't run forever
                del self._entries[key]
        done = [k for k, e in self._entries.items() if e.state == "done"]
        for key in done[: max(0, len(done) - self.max_done)]:
            del self._entries[key]

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------- the app

@dataclass
class Response:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)

    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else None


class BodyProblem(Exception):
    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.message, self.code = message, code


def _header(headers, name: str):
    if headers is None:
        return None
    value = headers.get(name)
    if value is not None:
        return value
    low = name.lower()
    for key in list(headers):
        if str(key).lower() == low:
            return headers[key]
    return None


class WorkerApp:
    def __init__(self, *, name: str, route: str, token_env: str, request_def: str, response_def: str,
                 empty_result: dict, work, trace, store: IdempotencyStore | None = None, max_concurrent: int = 4,
                 retry_after_cap: int = 30, contract: Contract | None = None, clock=time.monotonic,
                 min_step_seconds: float = 0.5, ceilings: dict | None = None, check_payload=None):
        self.name = name
        self.route = route
        self.token_env = token_env
        self.token = os.environ.get(token_env, "").strip()
        self.request_def, self.response_def = request_def, response_def
        self.empty_result = empty_result
        self.work = work
        self.trace = trace
        self.store = store or IdempotencyStore(clock=clock)
        self.slots = threading.BoundedSemaphore(max(1, max_concurrent))
        self.retry_after_cap = retry_after_cap
        self.min_step_seconds = min_step_seconds
        # The most this worker will ever spend on one request, whatever the caller asks for. It protects
        # the worker's own API keys; the caller still sees the usual "partial" when it is reached.
        self.ceilings = dict(ceilings or {})
        self.check_payload = check_payload  # optional: payload -> a problem string, for rules a JSON schema can't express
        self.contract = contract or Contract()
        self.clock = clock
        trace.redactor.add(self.token)

    def close(self) -> None:
        self.trace.close()

    # --- building answers

    def _reply(self, status: int, obj: dict, headers: dict | None = None) -> Response:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        return Response(status, body, dict(headers or {}))

    def _failed(self, status: int, request_id, run_id, code: str, message: str, retryable: bool, *, usage=None,
                headers: dict | None = None) -> Response:
        message = self.trace.redactor.clean(message)
        return self._reply(status, {
            "request_id": request_id, "run_id": run_id, "status": "failed", "usage": usage or dict(ZERO_USAGE),
            "result": None, "error": error_object(code, message, retryable),
        }, headers)

    def _retry_after(self, seconds: float) -> dict:
        return {"Retry-After": str(int(min(max(1, math.ceil(seconds)), self.retry_after_cap)))}

    # --- one request

    def handle_request(self, method: str, target: str, headers, read_body, received: float | None = None) -> Response:
        """Serve one request. read_body() returns the body bytes (or raises BodyProblem); it is only
        called after the token has been checked. Never raises."""
        received = self.clock() if received is None else received
        path = target.split("?", 1)[0]
        if path == "/healthz":
            if method in ("GET", "HEAD"):
                return self._reply(200, {"status": "ok"})
            return self._failed(405, None, None, "method_not_allowed", "use GET /healthz", False, headers={"Allow": "GET, HEAD"})

        attempt = 1
        try:
            attempt = max(1, int(_header(headers, "X-Request-Attempt") or 1))
        except (TypeError, ValueError):
            pass
        ids = {"request_id": None, "run_id": None}

        def finish(response: Response, *, tokens: int = 0, detail: str | None = None) -> Response:
            label = f"{method} {path[:80]}"
            self.trace.write(direction="in", endpoint_or_tool=label, status=response.status,
                             latency_ms=(self.clock() - received) * 1000, run_id=ids["run_id"],
                             request_id=ids["request_id"], step=0, attempt=attempt, tokens=tokens, detail=detail)
            return response

        try:
            # 1. the token: before anything about the body is looked at
            if not self._authorized(headers):
                return finish(self._failed(401, None, None, "unauthorized", "missing or wrong bearer token", False,
                                           headers={"WWW-Authenticate": "Bearer"}), detail="unauthorized")
            # 2. route and method
            if path != self.route:
                return finish(self._failed(404, None, None, "not_found", f"unknown path {path[:80]}", False), detail="not_found")
            if method != "POST":
                return finish(self._failed(405, None, None, "method_not_allowed", f"use POST {self.route}", False,
                                           headers={"Allow": "POST"}), detail="method_not_allowed")
            # 3. the body
            try:
                raw = read_body()
            except BodyProblem as err:
                return finish(self._failed(400, None, None, err.code, err.message, False), detail=err.code)
            try:
                parsed = parse_json(raw)
            except BadJSON as err:
                return finish(self._failed(400, None, None, "invalid_json", str(err), False), detail="invalid_json")
            if isinstance(parsed, dict):
                for key in ids:
                    value = parsed.get(key)
                    if isinstance(value, str) and UUID.match(value):
                        ids[key] = value
            problem = self.contract.problem(self.request_def, parsed)
            if not problem and self.check_payload:
                problem = self.check_payload(parsed["payload"])
            if problem:
                return finish(self._failed(400, ids["request_id"], ids["run_id"], "invalid_request", problem, False),
                              detail="invalid_request")
            return self._serve(parsed, ids, attempt, received, finish)
        except Exception as err:  # a bug here must still be a JSON answer, never a dropped connection
            self.trace.write(direction="in", endpoint_or_tool=f"{method} {path[:80]}", status=500, latency_ms=0,
                             run_id=ids["run_id"], request_id=ids["request_id"], attempt=attempt,
                             detail=f"internal error: {err.__class__.__name__}")
            return self._failed(500, ids["request_id"], ids["run_id"], "internal_error", "the worker hit an unexpected error", False)

    def _authorized(self, headers) -> bool:
        if not self.token:
            return False  # no token configured: nobody gets in
        value = (_header(headers, "Authorization") or "").strip()
        scheme, _, given = value.partition(" ")
        if scheme.lower() != "bearer" or not given.strip():
            return False
        return hmac.compare_digest(given.strip().encode("utf-8"), self.token.encode("utf-8"))

    def _serve(self, parsed: dict, ids: dict, attempt: int, received: float, finish) -> Response:
        request_id, run_id = parsed["request_id"], parsed["run_id"]
        budget_ms = min(parsed["time_budget_ms"], self.ceilings.get("max_time_ms", parsed["time_budget_ms"]))
        deadline = received + budget_ms / 1000 - min(0.25, 0.1 * budget_ms / 1000)
        # 4. idempotency
        state, entry = self.store.begin(request_id, body_hash(parsed), deadline)
        if state == "conflict":
            return finish(self._failed(409, request_id, run_id, "request_id_conflict",
                                       "this request_id was already used with a different body", False), detail="conflict")
        if state == "replay":
            headers = dict(entry.headers)
            headers["Idempotent-Replay"] = "true"
            return finish(Response(entry.status, entry.body, headers), detail="replayed: stored answer, no new work")
        if state == "running":
            wait = max(1, math.ceil(entry.deadline - self.clock()) + 1)
            return finish(self._failed(503, request_id, run_id, "request_in_progress",
                                       "the first copy of this request is still running; ask again after Retry-After", True,
                                       headers=self._retry_after(wait)), detail="duplicate while running")
        # 5. capacity
        if not self.slots.acquire(blocking=False):
            self.store.abort(request_id)
            return finish(self._failed(503, request_id, run_id, "busy", "too many requests at once", True,
                                       headers=self._retry_after(5)), detail="busy")
        try:
            return self._run(parsed, request_id, run_id, attempt, received, deadline, finish)
        finally:
            self.slots.release()

    def _run(self, parsed: dict, request_id: str, run_id: str, attempt: int, received: float, deadline: float, finish) -> Response:
        b, cap = parsed["budget"], self.ceilings
        budget = Budget(min(b["max_steps"], cap.get("max_steps", b["max_steps"])),
                        min(b["max_fetches"], cap.get("max_fetches", b["max_fetches"])),
                        min(b["max_tokens"], cap.get("max_tokens", b["max_tokens"])),
                        deadline, received=received, clock=self.clock, min_step_seconds=self.min_step_seconds)
        ctx = RequestContext(request_id=request_id, run_id=run_id, payload=parsed["payload"],
                             time_budget_ms=parsed["time_budget_ms"], budget=budget,
                             trace=self.trace.bind(run_id, request_id), attempt=attempt)
        keep = True  # store the answer for replays? (200 and 500: yes; 503: no)
        detail = None
        wait = None  # seconds to put in Retry-After if the answer is a 503
        try:
            try:
                outcome = self.work(ctx)
                status, body = 200, {"request_id": request_id, "run_id": run_id, "status": outcome.status,
                                     "usage": budget.usage(), "result": outcome.result, "error": outcome.error}
                detail = outcome.status + (f": {outcome.error['code']}" if outcome.error else "") + (
                    f" | {outcome.note}" if outcome.note else "")
            except WorkerFailure as err:
                status, keep, wait = err.http_status, err.http_status != 503, err.retry_after
                body = self._failed_body(request_id, run_id, err.code, err.message, err.retryable, budget)
                detail = err.code
            except BudgetExhausted as err:  # nothing was produced before a limit was reached
                status = 200
                body = {"request_id": request_id, "run_id": run_id, "status": "partial", "usage": budget.usage(),
                        "result": self.empty_result, "error": error_object(err.code, err.message, False)}
                detail = f"partial: {err.code}"
            except UpstreamBusy as err:  # the model or search API is rate limiting us; nothing stored, ask again later
                status, keep, wait = 503, False, err.retry_after
                body = self._failed_body(request_id, run_id, "upstream_busy", err.message, True, budget)
                detail = "upstream_busy"
            except TerminalError as err:  # a bad key, a used-up daily quota: trying again cannot help
                status = 500
                body = self._failed_body(request_id, run_id, "upstream_error", err.message, False, budget)
                detail = "upstream_error"
            except Exception as err:
                status = 500
                body = self._failed_body(request_id, run_id, "internal_error",
                                         f"the worker hit an unexpected error ({err.__class__.__name__})", False, budget)
                detail = f"internal error: {err.__class__.__name__}"
            # 7. never let a malformed answer leave this process
            if status == 200:
                problem = self.contract.problem(self.response_def, body)
                if problem:
                    status, keep = 500, True
                    detail = f"internal error: answer failed its own schema ({problem[:120]})"
                    body = self._failed_body(request_id, run_id, "internal_error",
                                             "the worker produced an answer that breaks the contract", False, budget)
            headers = self._retry_after(wait or 5) if status == 503 else {}
            response = self._reply(status, body, headers)
        except BaseException:
            self.store.abort(request_id)
            raise
        if keep:
            self.store.finish(request_id, response.status, response.body, response.headers)
        else:
            self.store.abort(request_id)
        return finish(response, tokens=budget.tokens, detail=detail)

    def _failed_body(self, request_id, run_id, code, message, retryable, budget) -> dict:
        return {"request_id": request_id, "run_id": run_id, "status": "failed", "usage": budget.usage(), "result": None,
                "error": error_object(code, self.trace.redactor.clean(message), retryable)}

    # --- for tests: serve a request without a socket

    def call(self, method: str, path: str, *, headers=None, body=None, token: str | None = "", received=None) -> Response:
        headers = dict(headers or {})
        if token == "":
            token = self.token
        if token is not None and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {token}"
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body or b""
        return self.handle_request(method, path, headers, lambda: data, received)


# ---------------------------------------------------------------------------- the HTTP server

def make_handler(app: WorkerApp):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "CrowdCampaignWorker/2"
        sys_version = ""
        timeout = 60  # a connection that goes silent for this long is dropped

        def log_message(self, format, *args):  # the trace log replaces the default access log
            pass

        def _discard_body(self) -> bool:
            """Read and throw away a request body nobody asked for (we answered 401, 404 or 405 before reading it).

            Returns True when the connection is still in step with the caller. If we simply closed the connection
            with the body unread, a caller that reuses its connection (every requests.Session does) would send its
            next request into a dead socket and see "connection reset" instead of our answer.
            """
            if self.headers.get("Transfer-Encoding"):
                return False
            length = (self.headers.get("Content-Length") or "0").strip()
            if length == "0":
                return True
            if not length.isdigit() or int(length) > MAX_BODY_BYTES:
                return False
            remaining = int(length)
            try:
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        return False
                    remaining -= len(chunk)
            except OSError:
                return False
            return True

        def _serve(self):
            received = app.clock()
            state = {"read": False, "broken": False}

            def read_body() -> bytes:
                length = self.headers.get("Content-Length")
                if length is None:
                    if self.headers.get("Transfer-Encoding"):
                        state["broken"] = True
                        raise BodyProblem("chunked bodies are not supported; send a Content-Length")
                    state["read"] = True
                    return b""
                if not length.strip().isdigit():
                    state["broken"] = True
                    raise BodyProblem("the Content-Length is not a number")
                size = int(length)
                if size > MAX_BODY_BYTES:
                    state["broken"] = True
                    raise BodyProblem(f"the body is larger than {MAX_BODY_BYTES} bytes", "body_too_large")
                try:
                    data = self.rfile.read(size)
                except OSError:
                    state["broken"] = True
                    raise BodyProblem("the body could not be read (timed out)") from None
                state["read"] = True
                if len(data) < size:
                    state["broken"] = True
                    raise BodyProblem("the body ended early")
                return data

            response = app.handle_request(self.command, self.path, self.headers, read_body, received)
            if state["broken"] or (not state["read"] and not self._discard_body()):
                self.close_connection = True  # the caller's next request would start in the middle of this body
            headers = dict(response.headers)  # a copy: a stored answer's own headers must not change
            if self.close_connection:
                headers["Connection"] = "close"  # say so, so the caller does not reuse a connection we are about to drop
            try:
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response.body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response.body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True  # the caller gave up; the answer is stored for its retry

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _serve

    return Handler


class WorkerServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        """A caller that hangs up mid-request is normal (a retry, a timeout, a test killing a worker), not an
        error worth a traceback. Windows reports it as WinError 10053 / 10058. Anything else still prints."""
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(app: WorkerApp, host: str, port: int) -> WorkerServer:
    return WorkerServer((host, port), make_handler(app))


def serve(app: WorkerApp, host: str = "0.0.0.0", port: int = 8000, announce=print) -> None:
    """Run until SIGTERM / Ctrl-C (Render sends SIGTERM when it stops or redeploys a service)."""
    server = make_server(app, host, port)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, stop)
        except ValueError:  # not the main thread
            pass
    if announce:
        announce(f"{app.name} listening on {host}:{server.server_address[1]}  (POST {app.route}, GET /healthz)")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        app.close()
