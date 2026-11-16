"""The orchestrator's settings: config.yaml (project root) plus the service URLs and tokens from the environment.

config.yaml holds the policy: which campaigns, how big K is, the TOTAL budget for one run and how it is
split between the two workers, how long to wait for a worker and how often to retry. The code checks every
value. Tokens and keys never live in config.yaml: they come from services/orchestrator/.env (or the shell).
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from .settings import ConfigError, RetryPolicy, int_value, load_env, number_value, read_yaml, section

PACKAGE_DIR = Path(__file__).resolve().parent
SERVICE_DIR = PACKAGE_DIR.parent                 # services/orchestrator
REPO_ROOT = SERVICE_DIR.parent.parent            # the project folder
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"
ENV_FILE = SERVICE_DIR / ".env"

WORKERS = ("researcher", "verifier")
DIMENSIONS = ("steps", "fetches", "tokens", "seconds")


@dataclass(frozen=True)
class Target:
    title: str
    topic: str


@dataclass(frozen=True)
class Service:
    name: str
    url: str
    token: str          # may be empty until require_tokens() is called
    url_env: str
    token_env: str


@dataclass(frozen=True)
class Amounts:
    """One number for each thing a run can spend."""

    steps: float
    fetches: float
    tokens: float
    seconds: float

    def get(self, dimension: str) -> float:
        return getattr(self, dimension)


@dataclass(frozen=True)
class NetworkSettings:
    connect_timeout: float        # seconds to open a connection
    read_margin: float            # added to the worker's own time budget to get our timeout
    cold_start_allowance: float   # added too, until the worker has answered once (Render wakes up slowly)
    warmup_timeout: float         # how long the start-of-run GET /healthz may keep trying
    retry_window: float           # extra seconds a call may spend waiting and retrying
    max_response_bytes: int


@dataclass(frozen=True)
class RetrySettings:
    max_attempts: int             # tries per call, the first included
    base_delay: float
    max_delay: float
    max_retry_after: float        # a worker asking us to wait longer than this ends the call


@dataclass(frozen=True)
class Config:
    topic: str
    k: int
    targets: tuple
    researcher: Service
    verifier: Service
    total: Amounts
    researcher_share: Amounts
    minimum: dict                 # worker -> Amounts: a call is skipped if its grant would be smaller
    reserve_seconds: float
    max_claims: int
    network: NetworkSettings
    retry: RetrySettings
    app_retry: RetryPolicy
    site_budget_scale: float = 0.34   # a run started from the website (one campaign) gets this share of the total

    def service(self, name: str) -> Service:
        return {"researcher": self.researcher, "verifier": self.verifier}[name]

    def require_tokens(self) -> None:
        missing = [s.token_env for s in (self.researcher, self.verifier) if not s.token]
        if missing:
            raise ConfigError(f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set. Add "
                              f"{'it' if len(missing) == 1 else 'them'} to services/orchestrator/.env (see .env.example). "
                              "Each must match the token the worker itself was started with.")


def _is_local(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _service(name: str, raw: dict) -> Service:
    url_env = str(raw.get("url_env") or f"{name.upper()}_URL")
    token_env = str(raw.get("token_env") or f"{name.upper()}_TOKEN")
    url = (os.environ.get(url_env, "").strip() or str(raw.get("default_url") or "")).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{url_env} must be a web address like https://my-verifier.onrender.com (got {url!r}).")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ConfigError(f"{url_env} must be just the address of the service, without a path or login (got {url!r}).")
    if parts.scheme == "http" and not _is_local(parts.hostname):
        raise ConfigError(f"{url_env} uses plain http:// for a remote host. The bearer token would cross the internet "
                          "unencrypted. Use https://.")
    return Service(name=name, url=url, token=os.environ.get(token_env, "").strip(), url_env=url_env, token_env=token_env)


def _amounts(sec: dict, where: str, defaults: dict, *, low: dict, high: dict) -> Amounts:
    values = {}
    for dim in DIMENSIONS:
        values[dim] = number_value(sec, dim, defaults[dim], low[dim], high[dim], where)
    return Amounts(**values)


def load_config(path=None, *, load_dotenv: bool = True) -> Config:
    if load_dotenv:
        load_env(ENV_FILE)
    raw = read_yaml(Path(path) if path else DEFAULT_CONFIG)

    topic = str(raw.get("topic") or "Market intel for CrowdCampaign briefs").strip()
    k = int_value(raw, "k", 5, 1, 10, "config")  # the contract allows at most 10 developments per request

    campaigns = raw.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        raise ConfigError("config.yaml needs a 'campaigns' list with at least one campaign (title and topic).")
    if len(campaigns) > 10:
        raise ConfigError("config.yaml lists more than 10 campaigns; the app saves at most 10 per run.")
    targets = []
    for index, entry in enumerate(campaigns, start=1):
        if not isinstance(entry, dict) or not str(entry.get("title") or "").strip() or not str(entry.get("topic") or "").strip():
            raise ConfigError(f"campaigns[{index}] needs a title and a topic.")
        topic_text = str(entry["topic"]).strip()
        if len(topic_text) > 300:
            raise ConfigError(f"campaigns[{index}].topic is longer than 300 characters (the contract's limit).")
        targets.append(Target(title=str(entry["title"]).strip(), topic=topic_text))
    titles = [t.title.lower() for t in targets]
    if len(set(titles)) != len(titles):
        raise ConfigError("two campaigns in config.yaml have the same title.")

    workers = section(raw, "workers")
    researcher = _service("researcher", section(workers, "researcher"))
    verifier = _service("verifier", section(workers, "verifier"))

    budget = section(raw, "budget")
    total = _amounts(section(budget, "total"), "budget.total",
                     {"steps": 80, "fetches": 150, "tokens": 500_000, "seconds": 1800},
                     low={"steps": 2, "fetches": 2, "tokens": 2000, "seconds": 60},
                     high={"steps": 400, "fetches": 400, "tokens": 10_000_000, "seconds": 7200})
    share = _amounts(section(budget, "researcher_share"), "budget.researcher_share",
                     {"steps": 0.5, "fetches": 0.4, "tokens": 0.75, "seconds": 0.6},
                     low={d: 0.05 for d in DIMENSIONS}, high={d: 0.95 for d in DIMENSIONS})
    minimum_raw = section(budget, "minimum_call")
    default_min = {"researcher": {"steps": 3, "fetches": 2, "tokens": 6000, "seconds": 30},
                   "verifier": {"steps": 1, "fetches": 2, "tokens": 3000, "seconds": 15}}
    minimum = {name: _amounts(section(minimum_raw, name), f"budget.minimum_call.{name}", default_min[name],
                              low={"steps": 1, "fetches": 0, "tokens": 1, "seconds": 1},
                              high={"steps": 200, "fetches": 200, "tokens": 5_000_000, "seconds": 3600})
               for name in WORKERS}
    reserve = number_value(budget, "reserve_seconds", 20, 0, 600, "budget")
    if reserve >= total.seconds:
        raise ConfigError("budget.reserve_seconds must be smaller than budget.total.seconds.")

    claims = section(raw, "claims")
    max_claims = int_value(claims, "max_per_campaign", 12, 1, 50, "claims")  # the contract allows 50 per request

    net = section(raw, "network")
    network = NetworkSettings(
        connect_timeout=number_value(net, "connect_timeout_seconds", 15, 1, 120, "network"),
        read_margin=number_value(net, "read_margin_seconds", 15, 1, 300, "network"),
        cold_start_allowance=number_value(net, "cold_start_allowance_seconds", 90, 0, 600, "network"),
        warmup_timeout=number_value(net, "warmup_timeout_seconds", 120, 1, 900, "network"),
        retry_window=number_value(net, "retry_window_seconds", 120, 0, 900, "network"),
        max_response_bytes=int_value(net, "max_response_bytes", 2_000_000, 10_000, 50_000_000, "network"),
    )
    ret = section(raw, "retries")
    retry = RetrySettings(
        max_attempts=int_value(ret, "max_attempts", 4, 1, 10, "retries"),
        base_delay=number_value(ret, "base_delay_seconds", 2, 0, 60, "retries"),
        max_delay=number_value(ret, "max_delay_seconds", 30, 1, 300, "retries"),
        max_retry_after=number_value(ret, "max_retry_after_seconds", 60, 1, 600, "retries"),
    )
    app = section(raw, "app")
    app_retry = RetryPolicy(max_retries=int_value(app, "max_retries", 2, 0, 10, "app"),
                            base_delay=number_value(app, "base_delay_seconds", 2, 0, 60, "app"),
                            max_wait=number_value(app, "max_wait_seconds", 30, 1, 300, "app"))

    site = section(raw, "site")
    site_scale = number_value(site, "budget_scale", 0.34, 0.05, 1, "site")

    return Config(topic=topic, k=k, targets=tuple(targets), researcher=researcher, verifier=verifier, total=total,
                  researcher_share=share, minimum=minimum, reserve_seconds=reserve, max_claims=max_claims,
                  network=network, retry=retry, app_retry=app_retry, site_budget_scale=site_scale)


def for_site_run(config: Config) -> Config:
    """The settings for ONE campaign started from the website: the same policy, but the TOTAL budget is cut to
    site.budget_scale of the full run's (the full run covers several campaigns; this one covers a single campaign).
    The split between the workers, the timeouts and the retry rules are unchanged."""
    scale = config.site_budget_scale
    floors = Amounts(steps=2, fetches=2, tokens=2000, seconds=60)       # the same lowest values load_config accepts
    total = Amounts(**{d: round(max(config.total.get(d) * scale, floors.get(d))) for d in DIMENSIONS})
    return replace(config, total=total, reserve_seconds=min(config.reserve_seconds, total.seconds / 4))
