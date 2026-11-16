"""Loads the researcher's policy (config.yaml) and its secrets (.env or the host's environment).

config.yaml says what the agent may do; this module checks every value and fills in defaults.
The generic helpers (checked numbers, fetch and retry policy, model provider) live in settings.py,
which is shared with the verifier.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import (ConfigError, FetchPolicy, ModelSettings, RetryPolicy, fetch_policy, int_value, load_env,
                       read_yaml, resolve_model, retry_policy, section)

PACKAGE_DIR = Path(__file__).resolve().parent
SERVICE_DIR = PACKAGE_DIR.parent
DEFAULT_CONFIG_PATH = SERVICE_DIR / "config.yaml"
ENV_PATH = SERVICE_DIR / ".env"
ALL_TOOLS = ("search_web", "fetch_article", "finish")


@dataclass
class SearchSettings:
    provider: str = "tavily"
    base_url: str = "https://api.tavily.com"
    topic: str = "news"
    time_range: str = "month"
    max_results: int = 6
    search_depth: str = "basic"


@dataclass
class Limits:
    """The most this service will spend on ONE request, whatever the caller asks for."""
    max_steps: int = 40
    max_searches: int = 3
    max_fetches: int = 40
    max_tokens: int = 200_000
    max_seconds: int = 900


@dataclass
class ServerSettings:
    port: int = 8001
    max_concurrent: int = 2
    retry_after_cap_seconds: int = 30
    remembered_requests: int = 200


@dataclass
class Config:
    path: Path
    instructions: str
    tools: tuple
    model_section: dict
    search: SearchSettings
    limits: Limits
    retries: RetryPolicy
    fetch: FetchPolicy
    server: ServerSettings

    def model(self) -> ModelSettings:
        """Pick the model provider (needs its API key in the environment)."""
        return resolve_model(self.model_section)


def load_config(path: Path | str | None = None) -> Config:
    """Read and check config.yaml. Raises ConfigError with a plain-language message."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    load_env(ENV_PATH)
    raw = read_yaml(path)

    tools = raw.get("tools") or list(ALL_TOOLS)
    if not isinstance(tools, list) or any(t not in ALL_TOOLS for t in tools):
        raise ConfigError(f"tools may only contain {', '.join(ALL_TOOLS)}.")
    if "finish" not in tools:
        raise ConfigError("tools must include finish, or the agent could never end a request.")

    lim = section(raw, "limits")
    limits = Limits(
        max_steps=int_value(lim, "max_steps", 40, 2, 200, "limits"),
        max_searches=int_value(lim, "max_searches", 3, 0, 20, "limits"),
        max_fetches=int_value(lim, "max_fetches", 40, 0, 200, "limits"),
        max_tokens=int_value(lim, "max_tokens", 200_000, 2000, 5_000_000, "limits"),
        max_seconds=int_value(lim, "max_seconds", 900, 5, 3600, "limits"),
    )

    se = section(raw, "search")
    if se.get("provider", "tavily") != "tavily":
        raise ConfigError("search.provider must be tavily (the only search service this tracker supports).")
    search = SearchSettings(
        base_url=str(se.get("base_url") or "https://api.tavily.com").rstrip("/"),
        topic=str(se.get("topic", "news")),
        time_range=str(se.get("time_range", "month")),
        max_results=int_value(se, "max_results", 6, 1, 20, "search"),
        search_depth=str(se.get("search_depth", "basic")),
    )
    if search.topic not in ("general", "news", "finance"):
        raise ConfigError("search.topic must be general, news or finance.")
    if search.time_range not in ("day", "week", "month", "year"):
        raise ConfigError("search.time_range must be day, week, month or year.")
    if search.search_depth not in ("basic", "advanced", "fast", "ultra-fast"):
        raise ConfigError("search.search_depth must be basic, advanced, fast or ultra-fast.")

    model_section = section(raw, "model")
    if not model_section.get("providers"):
        raise ConfigError("config.yaml needs model.providers with at least one provider.")

    srv = section(raw, "server")
    server = ServerSettings(
        port=int_value(srv, "port", 8001, 1, 65535, "server"),
        max_concurrent=int_value(srv, "max_concurrent", 2, 1, 32, "server"),
        retry_after_cap_seconds=int_value(srv, "retry_after_cap_seconds", 30, 1, 600, "server"),
        remembered_requests=int_value(srv, "remembered_requests", 200, 10, 5000, "server"),
    )

    return Config(
        path=path,
        instructions=str(raw.get("instructions") or "").strip(),
        tools=tuple(tools),
        model_section=model_section,
        search=search,
        limits=limits,
        retries=retry_policy(raw),
        fetch=fetch_policy(raw),
        server=server,
    )
