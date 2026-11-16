"""Settings helpers for the workers: .env loading, checked numbers, fetch and retry policy, model provider.

config.yaml says what a service may do; these helpers check every value, fill in defaults, and
refuse anything unsafe (for example, a URL scheme other than http or https).

Shared file: edit shared/settings.py, then run  python scripts/sync_shared.py
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SAFE_SCHEMES = ("http", "https")

# Known model providers. All of them speak the OpenAI chat-completions format.
PROVIDERS = {
    "gemini": {
        "label": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
    },
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1/", "key_env": "GROQ_API_KEY"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1/", "key_env": "OPENROUTER_API_KEY"},
    "ollama": {"label": "Ollama (local)", "base_url": "http://localhost:11434/v1/", "key_env": None},
}


class ConfigError(Exception):
    """config.yaml or the .env file has a problem the user needs to fix."""


@dataclass
class ModelSettings:
    provider: str
    label: str
    name: str
    base_url: str
    api_key: str | None
    key_env: str | None
    extra: dict = field(default_factory=dict)  # e.g. reasoning_effort, temperature
    max_output_tokens: int = 2048
    timeout: float = 60.0


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 2.0
    max_wait: float = 65.0


@dataclass
class FetchPolicy:
    allowed_schemes: tuple = ("http", "https")
    allowed_ports: tuple = (80, 443)
    allowed_hosts: tuple = ("*",)
    blocked_hosts: tuple = ()
    respect_robots_txt: bool = True
    timeout_seconds: float = 15.0
    max_bytes: int = 2_000_000
    max_redirects: int = 4
    max_chars_to_model: int = 3500
    user_agent: str = "Mozilla/5.0 (compatible; CrowdCampaignTracker/1.0)"


def load_env(path: Path) -> None:
    """Read KEY=VALUE lines from a .env file into the environment.

    Variables that are already set win, so a key exported in the terminal (or set in Render's
    environment settings) overrides the file.
    """
    path = Path(path)
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def read_yaml(path: Path) -> dict:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"Can't find {path}.") from None
    except yaml.YAMLError as err:
        raise ConfigError(f"{path.name} is not valid YAML: {err}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must contain settings (key: value lines).")
    return raw


def section(raw: dict, name: str) -> dict:
    value = raw.get(name) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{name}' in config.yaml must be a section of settings.")
    return value


def int_value(sec: dict, key: str, default: int, low: int, high: int, where: str) -> int:
    value = sec.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{where}.{key} must be a whole number from {low} to {high} (got {value!r}).")
    return value


def number_value(sec: dict, key: str, default: float, low: float, high: float, where: str) -> float:
    value = sec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ConfigError(f"{where}.{key} must be a number from {low} to {high} (got {value!r}).")
    return float(value)


def host_list(values, where: str) -> tuple:
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ConfigError(f"{where} must be a list of host names.")
    return tuple(v.strip().lower().lstrip(".") for v in values)


def retry_policy(raw: dict) -> RetryPolicy:
    ret = section(raw, "retries")
    return RetryPolicy(
        max_retries=int_value(ret, "max_retries", 3, 0, 10, "retries"),
        base_delay=number_value(ret, "base_delay_seconds", 2, 0, 60, "retries"),
        max_wait=number_value(ret, "max_wait_seconds", 65, 1, 600, "retries"),
    )


def fetch_policy(raw: dict) -> FetchPolicy:
    fe = section(raw, "fetch")
    schemes = tuple(s.lower() for s in fe.get("allowed_schemes", ["http", "https"]))
    unsafe = [s for s in schemes if s not in SAFE_SCHEMES]
    if unsafe:
        raise ConfigError(f"fetch.allowed_schemes may only contain http and https (found {', '.join(unsafe)}).")
    ports = fe.get("allowed_ports", [80, 443])
    if not isinstance(ports, list) or not all(isinstance(p, int) and 1 <= p <= 65535 for p in ports):
        raise ConfigError("fetch.allowed_ports must be a list of port numbers.")
    return FetchPolicy(
        allowed_schemes=schemes,
        allowed_ports=tuple(ports),
        allowed_hosts=host_list(fe.get("allowed_hosts", ["*"]), "fetch.allowed_hosts"),
        blocked_hosts=host_list(fe.get("blocked_hosts", []), "fetch.blocked_hosts"),
        respect_robots_txt=bool(fe.get("respect_robots_txt", True)),
        timeout_seconds=number_value(fe, "timeout_seconds", 15, 1, 120, "fetch"),
        max_bytes=int_value(fe, "max_bytes", 2_000_000, 10_000, 20_000_000, "fetch"),
        max_redirects=int_value(fe, "max_redirects", 4, 0, 10, "fetch"),
        max_chars_to_model=int_value(fe, "max_chars_to_model", 3500, 500, 50_000, "fetch"),
        user_agent=str(fe.get("user_agent") or FetchPolicy.user_agent),
    )


def resolve_model(sec: dict) -> ModelSettings:
    """Choose the provider: the one named in config.yaml, or with "auto" the first whose key is set."""
    providers = sec.get("providers") or {}
    if not isinstance(providers, dict) or not providers:
        raise ConfigError("config.yaml needs model.providers with at least one provider.")
    wanted = str(sec.get("provider") or "auto").lower()

    def settings(name: str) -> ModelSettings:
        entry = providers.get(name) or {}
        known = PROVIDERS.get(name, {})
        base_url = str(entry.get("base_url") or known.get("base_url") or "")
        if not base_url:
            raise ConfigError(f"Provider '{name}' needs a base_url in config.yaml.")
        key_env = entry.get("key_env", known.get("key_env"))
        api_key = os.environ.get(key_env, "").strip() if key_env else None
        if key_env and not api_key:
            raise ConfigError(f"{key_env} is not set. Add it to this service's .env file (see .env.example).")
        if not entry.get("name"):
            raise ConfigError(f"model.providers.{name}.name (the model to use) is missing.")
        extra = {}
        for key in ("reasoning_effort", "temperature", "top_p"):
            if entry.get(key) is not None:
                extra[key] = entry[key]
        return ModelSettings(
            provider=name,
            label=str(entry.get("label") or known.get("label") or name),
            name=str(entry["name"]),
            base_url=base_url if base_url.endswith("/") else base_url + "/",
            api_key=api_key,
            key_env=key_env,
            extra=extra,
            max_output_tokens=int_value(entry, "max_output_tokens", sec.get("max_output_tokens", 2048), 256, 32768,
                                        f"model.providers.{name}"),
            timeout=number_value(sec, "request_timeout_seconds", 60, 5, 600, "model"),
        )

    if wanted != "auto":
        if wanted not in providers:
            raise ConfigError(f"model.provider is '{wanted}', but model.providers has no '{wanted}' entry.")
        return settings(wanted)
    for name, entry in providers.items():
        key_env = (entry or {}).get("key_env", PROVIDERS.get(name, {}).get("key_env"))
        if key_env and os.environ.get(key_env, "").strip():
            return settings(name)
    needed = [((e or {}).get("key_env") or PROVIDERS.get(n, {}).get("key_env")) for n, e in providers.items()]
    needed = [n for n in needed if n]
    raise ConfigError(
        "No model API key found. Put one of these in this service's .env file: " + ", ".join(needed)
        + ". A free Gemini key comes from https://aistudio.google.com/apikey."
    )
