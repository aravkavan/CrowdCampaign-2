"""Loads the verifier's policy (config.yaml) and its secrets (.env, or the host's environment settings)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import (ConfigError, FetchPolicy, ModelSettings, RetryPolicy, fetch_policy, int_value, load_env,
                       number_value, read_yaml, resolve_model, retry_policy, section)

PACKAGE_DIR = Path(__file__).resolve().parent
SERVICE_DIR = PACKAGE_DIR.parent
DEFAULT_CONFIG_PATH = SERVICE_DIR / "config.yaml"
ENV_PATH = SERVICE_DIR / ".env"


@dataclass
class Verification:
    max_excerpt_chars: int = 300     # the contract's limit for evidence
    min_word_overlap: float = 0.3    # share of the claim's content words that the excerpt must contain
    numbers_in_excerpt: bool = True  # every number in the claim (years excepted) must be in the excerpt
    reasks: int = 1                  # extra tries when the model's reply is not usable JSON


@dataclass
class Limits:
    """The most this service will spend on ONE request, whatever the caller asks for."""
    max_steps: int = 100
    max_fetches: int = 200
    max_tokens: int = 300_000
    max_seconds: int = 600


@dataclass
class ServerSettings:
    port: int = 8002
    max_concurrent: int = 2
    retry_after_cap_seconds: int = 30
    remembered_requests: int = 200


@dataclass
class Config:
    path: Path
    model_section: dict
    verification: Verification
    limits: Limits
    retries: RetryPolicy
    fetch: FetchPolicy
    server: ServerSettings

    def model(self) -> ModelSettings:
        return resolve_model(self.model_section)


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    load_env(ENV_PATH)
    raw = read_yaml(path)

    ver = section(raw, "verification")
    verification = Verification(
        max_excerpt_chars=int_value(ver, "max_excerpt_chars", 300, 50, 300, "verification"),
        min_word_overlap=number_value(ver, "min_word_overlap", 0.3, 0, 1, "verification"),
        numbers_in_excerpt=bool(ver.get("numbers_in_excerpt", True)),
        reasks=int_value(ver, "reasks", 1, 0, 3, "verification"),
    )
    lim = section(raw, "limits")
    limits = Limits(
        max_steps=int_value(lim, "max_steps", 100, 1, 200, "limits"),
        max_fetches=int_value(lim, "max_fetches", 200, 0, 200, "limits"),
        max_tokens=int_value(lim, "max_tokens", 300_000, 2000, 5_000_000, "limits"),
        max_seconds=int_value(lim, "max_seconds", 600, 5, 3600, "limits"),
    )
    model_section = section(raw, "model")
    if not model_section.get("providers"):
        raise ConfigError("config.yaml needs model.providers with at least one provider.")
    srv = section(raw, "server")
    server = ServerSettings(
        port=int_value(srv, "port", 8002, 1, 65535, "server"),
        max_concurrent=int_value(srv, "max_concurrent", 2, 1, 32, "server"),
        retry_after_cap_seconds=int_value(srv, "retry_after_cap_seconds", 30, 1, 600, "server"),
        remembered_requests=int_value(srv, "remembered_requests", 200, 10, 5000, "server"),
    )
    return Config(path=path, model_section=model_section, verification=verification, limits=limits,
                  retries=retry_policy(raw), fetch=fetch_policy(raw), server=server)
