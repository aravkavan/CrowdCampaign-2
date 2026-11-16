"""The orchestrator's memory lives in the CrowdCampaign database, reached only through the app's API.

It logs in like any user and calls /api/tracker/* with its Bearer token. It never connects to the database
itself, so the API's rules (401 without a valid token, your own data only) apply to it too. The login
token is never written to the trace. The workers never see this client: only the orchestrator keeps state
between runs.
"""
from __future__ import annotations

import os
import time
from urllib.parse import quote, urlsplit

import requests

from .failures import TerminalError, TransientError, with_retries
from .settings import RetryPolicy

DEFAULT_API_URL = "http://localhost:4000"


class AppClient:
    def __init__(self, trace, *, base_url: str | None = None, username: str | None = None, password: str | None = None,
                 session=None, retry: RetryPolicy | None = None, sleep=time.sleep, token: str | None = None):
        """trace: a BoundTrace (each HTTP round trip becomes one `out` line).

        token: a login token the website made for the head person who pressed "Research this campaign". When it is
        given (or TRACKER_TOKEN is set) there is no password login: the run is saved under that person."""
        self.base_url = (base_url or os.environ.get("CROWDCAMPAIGN_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.username = username if username is not None else os.environ.get("TRACKER_USERNAME", "").strip()
        self.password = password if password is not None else os.environ.get("TRACKER_PASSWORD", "")
        self.fixed_token = (token if token is not None else os.environ.get("TRACKER_TOKEN", "")).strip()
        self.trace = trace
        self.session = session or requests.Session()
        self.token = None
        self.host = urlsplit(self.base_url).hostname
        # Neon can take a few seconds to wake up, so allow a couple of quick retries.
        self.retry_policy = retry or RetryPolicy(max_retries=2, base_delay=2.0, max_wait=30.0)
        self.sleep = sleep

    def _request(self, method: str, path: str, body=None, *, auth: bool = True, timeout: float = 45):
        url = self.base_url + path
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"

        def attempt(number: int):
            started = time.monotonic()
            status = None
            try:
                response = self.session.request(method, url, json=body, headers=headers, timeout=(5, timeout))
                status = response.status_code
            except requests.Timeout:
                raise TransientError("app", f"{method} {path} timed out") from None
            except requests.ConnectionError:
                raise TransientError("app", f"can't reach the CrowdCampaign API at {self.base_url}. "
                                            "Start the backend first (cd backend, then npm start).") from None
            finally:
                self.trace.event("http", service="app", host=self.host, method=method, path=path, attempt=number,
                                 status=status, latency_ms=int((time.monotonic() - started) * 1000))
            try:
                data = response.json()
            except ValueError:
                data = {}
            message = data.get("error") if isinstance(data, dict) else None
            if status >= 500:
                raise TransientError("app", f"the API answered {status} ({message or 'server error'}). "
                                            "If the database is asleep or unreachable, wait a moment and try again.")
            if status == 401 and not auth:
                raise TerminalError("app", f"login failed for '{self.username}': {message or 'wrong username or password'}. "
                                           "Check TRACKER_USERNAME and TRACKER_PASSWORD in services/orchestrator/.env.")
            if status == 401 and self.fixed_token:
                raise TerminalError("app", "the login the website gave this run was rejected (it may have expired, or the "
                                           "password was changed). Start the run again from the campaign page.")
            if status == 401:
                raise TerminalError("app", "the API rejected the login token. Run the orchestrator again.")
            if status >= 400:
                raise TerminalError("app", f"{method} {path} failed (HTTP {status}): {message or 'request refused'}")
            return data

        return with_retries(attempt, service="app", policy=self.retry_policy, trace=self.trace, sleep=self.sleep)

    def login(self) -> dict:
        if self.fixed_token:
            self.token = self.fixed_token
            user = self._request("GET", "/api/auth/me")       # also proves the token still works
            self.username = (user or {}).get("username") or self.username
            return user or {}
        if not self.username or not self.password:
            raise TerminalError("app", "TRACKER_USERNAME and TRACKER_PASSWORD are not set. Add them to "
                                       "services/orchestrator/.env (see .env.example).")
        data = self._request("POST", "/api/auth/login", {"username": self.username, "password": self.password}, auth=False)
        self.token = data.get("token")
        if not self.token:
            raise TerminalError("app", "the login answer had no token")
        return data.get("user") or {}

    def campaigns(self) -> list:
        return self._request("GET", "/api/campaigns").get("campaigns", [])

    def load_state(self) -> dict:
        return self._request("GET", "/api/tracker/state")

    def save_run(self, payload: dict) -> dict:
        return self._request("POST", "/api/tracker/runs", payload, timeout=90)

    def run_detail(self, number: int | None = None) -> dict | None:
        """One saved run in full (claims included): the latest one, or the one with this number. None if there is none."""
        if number is None:
            return self._request("GET", "/api/tracker/runs/latest").get("run")
        runs = self._request("GET", "/api/tracker/runs").get("runs") or []
        match = next((r for r in runs if r.get("number") == number), None)
        if match is None:
            return None
        return self._request("GET", "/api/tracker/runs/" + quote(str(match.get("id")), safe="")).get("run")

    def reset(self) -> dict:
        return self._request("DELETE", "/api/tracker/state")
