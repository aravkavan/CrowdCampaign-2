"""The HTTP client: what is retried, what is not, and what a retry sends. A fake `requests` session and a fake clock,
so nothing waits for real and nothing touches the network."""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from helpers import CONFIG  # noqa: E402
from orchestrator.client import WorkerClient  # noqa: E402
from orchestrator.config import NetworkSettings, RetrySettings  # noqa: E402
from orchestrator.trace import Redactor, ServiceTrace  # noqa: E402

TOKEN = "super-secret-researcher-token-123456"
RID = "00000000-0000-4000-8000-000000000001"
RUN = "00000000-0000-4000-8000-0000000000aa"
NET = NetworkSettings(connect_timeout=5, read_margin=10, cold_start_allowance=60, warmup_timeout=100, retry_window=100,
                      max_response_bytes=100_000)
RETRY = RetrySettings(max_attempts=4, base_delay=2, max_delay=30, max_retry_after=60)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, raw=None):
        self.status_code = status
        self.headers = headers or {}
        self._raw = raw if raw is not None else json.dumps(body if body is not None else {"ok": True}).encode()
        self.closed = False

    def iter_content(self, chunk_size=65536):
        yield self._raw

    def json(self):
        return json.loads(self._raw)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.posts, self.gets = [], []

    def post(self, url, data=None, headers=None, timeout=None, stream=None, allow_redirects=None):
        self.posts.append({"url": url, "data": data, "headers": dict(headers), "timeout": timeout, "redirects": allow_redirects})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.gets.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def envelope(budget_ms=20_000):
    return {"request_id": RID, "run_id": RUN, "time_budget_ms": budget_ms,
            "budget": {"max_steps": 5, "max_fetches": 5, "max_tokens": 5000}, "payload": {"topic": "x", "k": 3}}


def make(script, *, retry=RETRY, net=NET):
    session, clock, log = FakeSession(script), Clock(), io.StringIO()
    redactor = Redactor()
    redactor.add(TOKEN)
    trace = ServiceTrace("orchestrator", stream=log, redactor=redactor)
    client = WorkerClient("researcher", "https://r.example.com", TOKEN, trace, retry=retry, network=net, session=session,
                          sleep=clock.sleep, clock=clock, jitter=lambda: 0.0)
    return client, session, clock, log


def lines(log):
    return [json.loads(x) for x in log.getvalue().splitlines()]


class WhatIsRetried(unittest.TestCase):
    def test_a_good_answer_is_one_attempt(self):
        client, session, clock, log = make([FakeResponse(200, {"status": "complete"})])
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
        self.assertEqual((result.outcome, result.attempts, result.http_status), ("ok", 1, 200))
        self.assertEqual(clock.slept, [])
        self.assertEqual(len(session.posts), 1)

    def test_connection_refused_is_retried_with_the_same_bytes_and_the_same_request_id(self):
        client, session, clock, log = make([requests.ConnectionError("Connection refused"), requests.ConnectionError("reset"),
                                            FakeResponse(200, {"status": "complete"})])
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
        self.assertEqual((result.outcome, result.attempts, result.retries), ("ok", 3, 2))
        bodies = [p["data"] for p in session.posts]
        self.assertEqual(len(set(bodies)), 1, "every attempt must send the identical bytes")
        sent = json.loads(bodies[0])
        self.assertEqual(sent["request_id"], RID)
        self.assertEqual(sent["time_budget_ms"], 20_000)          # a retry never shrinks the time budget
        self.assertEqual([p["headers"]["X-Request-Attempt"] for p in session.posts], ["1", "2", "3"])

    def test_waits_double_each_time_up_to_the_cap(self):
        retry = RetrySettings(max_attempts=6, base_delay=2, max_delay=10, max_retry_after=60)
        client, session, clock, log = make([FakeResponse(503, {"x": 1})] * 6, retry=retry)
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 5000)
        self.assertEqual(result.outcome, "unavailable")
        self.assertEqual(result.attempts, 6)
        self.assertEqual(clock.slept, [2, 4, 8, 10, 10])

    def test_503_with_retry_after_waits_at_least_that_long(self):
        client, session, clock, log = make([FakeResponse(503, {"x": 1}, {"Retry-After": "7"}), FakeResponse(200, {"status": "complete"})])
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
        self.assertEqual(result.outcome, "ok")
        self.assertEqual(clock.slept, [7])

    def test_a_retry_after_longer_than_our_limit_ends_the_call_at_once(self):
        client, session, clock, log = make([FakeResponse(503, {"x": 1}, {"Retry-After": "300"})])
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 5000)
        self.assertEqual((result.outcome, result.attempts), ("unavailable", 1))
        self.assertEqual(clock.slept, [])
        self.assertIn("longer than we will wait", result.reason)

    def test_502_and_504_are_transient_too(self):
        client, session, clock, log = make([FakeResponse(502), FakeResponse(504), FakeResponse(200, {"status": "complete"})])
        self.assertEqual(client.call("/research", envelope(), step=1, give_up_at=clock() + 500).outcome, "ok")

    def test_a_timeout_is_retried(self):
        client, session, clock, log = make([requests.ReadTimeout(), FakeResponse(200, {"status": "complete"})])
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
        self.assertEqual((result.outcome, result.attempts), ("ok", 2))

    def test_terminal_answers_are_never_retried(self):
        for status, outcome in ((400, "refused"), (401, "refused"), (409, "refused"), (500, "worker_failed"), (404, "unexpected"),
                                (301, "unexpected"), (418, "unexpected")):
            with self.subTest(status=status):
                client, session, clock, log = make([FakeResponse(status, {"error": {"code": "nope"}})] * 2)
                result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
                self.assertEqual((result.outcome, result.attempts), (outcome, 1))
                self.assertEqual(len(session.posts), 1)
                self.assertEqual(clock.slept, [])

    def test_gives_up_after_max_attempts(self):
        client, session, clock, log = make([requests.ConnectionError("refused")] * 4)
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 5000)
        self.assertEqual((result.outcome, result.attempts), ("unavailable", 4))
        self.assertIn("gave up after 4 tries", result.reason)

    def test_does_not_start_a_retry_it_has_no_time_for(self):
        client, session, clock, log = make([requests.ConnectionError("refused"), FakeResponse(200, {"status": "complete"})])
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 1)   # the 2 s wait doesn't fit
        self.assertEqual((result.outcome, result.attempts), ("unavailable", 1))
        self.assertIn("no time left", result.reason)

    def test_redirects_are_not_followed(self):
        client, session, clock, log = make([FakeResponse(301, {})])
        client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
        self.assertIs(session.posts[0]["redirects"], False)


class Timeouts(unittest.TestCase):
    def test_our_timeout_is_longer_than_the_workers_budget(self):
        client, session, clock, log = make([FakeResponse(200, {"status": "complete"}), FakeResponse(200, {"status": "complete"})])
        client.call("/research", envelope(20_000), step=1, give_up_at=clock() + 500)
        connect, read = session.posts[0]["timeout"]
        self.assertEqual(connect, 5)
        self.assertEqual(read, 20 + 10 + 60)          # budget + margin + cold start, until the worker has answered once
        client.call("/research", envelope(20_000), step=2, give_up_at=clock() + 500)
        self.assertEqual(session.posts[1]["timeout"][1], 20 + 10)
        self.assertGreater(session.posts[1]["timeout"][1], 20)


class WhatIsLogged(unittest.TestCase):
    def test_every_attempt_is_one_line_with_the_required_fields_and_no_secret(self):
        client, session, clock, log = make([FakeResponse(503, {"error": {"code": "busy"}}, {"Retry-After": "3"}),
                                            FakeResponse(200, {"status": "complete", "usage": {"tokens": 321}})])
        client.call("/research", envelope(), step=4, give_up_at=clock() + 500)
        rows = lines(log)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(set(row) - {"detail"}, {"ts", "service", "run_id", "request_id", "step", "direction",
                                                     "endpoint_or_tool", "status", "latency_ms", "attempt", "tokens"})
            self.assertEqual((row["service"], row["direction"], row["run_id"], row["request_id"], row["step"]),
                             ("orchestrator", "out", RUN, RID, 4))
        self.assertEqual([(r["attempt"], r["status"]) for r in rows], [(1, 503), (2, 200)])
        self.assertEqual(rows[1]["tokens"], 321)
        self.assertIn("researcher POST /research", rows[0]["endpoint_or_tool"])
        self.assertNotIn(TOKEN, log.getvalue())
        self.assertNotIn("Bearer", log.getvalue())

    def test_latency_is_measured_by_the_caller(self):
        class SlowSession(FakeSession):
            def post(inner, *a, **k):
                clock.now += 2.5
                return super().post(*a, **k)

        session, clock, log = SlowSession([FakeResponse(200, {"status": "complete"})]), Clock(), io.StringIO()
        client = WorkerClient("verifier", "https://v.example.com", TOKEN, ServiceTrace("orchestrator", stream=log),
                              retry=RETRY, network=NET, session=session, sleep=clock.sleep, clock=clock)
        client.call("/verify", envelope(), step=1, give_up_at=clock() + 500)
        self.assertEqual(lines(log)[0]["latency_ms"], 2500)


class Answers(unittest.TestCase):
    def test_a_stored_answer_is_recognised(self):
        client, session, clock, log = make([FakeResponse(200, {"status": "complete"}, {"Idempotent-Replay": "true"})])
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
        self.assertTrue(result.replayed)
        self.assertIn("replayed", lines(log)[0]["detail"])

    def test_an_answer_that_is_not_strict_json_is_kept_as_a_problem_not_as_data(self):
        for raw in (b"not json", b'{"a": NaN}', b'{"a": 1, "a": 2}', b"", b"\xff\xfe"):
            with self.subTest(raw=raw):
                client, session, clock, log = make([FakeResponse(200, raw=raw)])
                result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
                self.assertEqual(result.outcome, "ok")
                self.assertIsNone(result.parsed)
                self.assertTrue(result.parse_problem)

    def test_an_oversized_answer_is_refused(self):
        net = NetworkSettings(5, 10, 60, 100, 100, max_response_bytes=50)
        client, session, clock, log = make([FakeResponse(200, raw=b"x" * 500)], net=net)
        result = client.call("/research", envelope(), step=1, give_up_at=clock() + 500)
        self.assertIsNone(result.parsed)
        self.assertIn("larger than", result.parse_problem)


class Health(unittest.TestCase):
    def test_health_sends_no_token_and_reports_when_awake(self):
        client, session, clock, log = make([requests.ConnectionError("refused-ish reset"),
                                            FakeResponse(200, {"status": "ok"})])
        health = client.health(RUN, timeout=100)
        self.assertTrue(health.ok)
        self.assertEqual(health.attempts, 2)
        self.assertTrue(client.warm)
        self.assertNotIn("Authorization", session.gets[0]["headers"])
        self.assertEqual([r["status"] for r in lines(log)], [0, 200])

    def test_health_gives_up_when_nothing_is_listening(self):
        client, session, clock, log = make([requests.ConnectionError("[Errno 111] Connection refused")] * 8)
        health = client.health(RUN, timeout=1000)
        self.assertFalse(health.ok)
        self.assertEqual(health.attempts, 3)
        self.assertIn("nothing is listening", health.reason)

    def test_the_token_probe_tells_a_wrong_token_from_a_right_one(self):
        client, session, clock, log = make([FakeResponse(401, {}), FakeResponse(400, {})])
        self.assertEqual(client.probe_token("/research", RUN)[0], 401)
        self.assertEqual(client.probe_token("/research", RUN)[0], 400)
        self.assertNotIn(TOKEN, log.getvalue())


if __name__ == "__main__":
    unittest.main()
