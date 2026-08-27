"""Typed, validated, secret-safe production configuration.

Settings are resolved from layered sources: explicit runtime overrides
(CLI/env) take precedence over the configuration file, which takes
precedence over safe defaults. Required settings and the provider
credential fail closed: startup raises :class:`ConfigError` when any
required value is missing. Secret values are never embedded in files,
logs, or evidence; only the environment variable name holding the
credential is recorded.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from doom_team.subprocess_guard import SENSITIVE_SUBSTRINGS, is_sensitive_env_key


class ConfigError(Exception):
    """Raised when configuration is invalid or required values are missing."""


TRANSPORT_IN_PROCESS = "in_process"
TRANSPORT_GAS_CITY = "gas_city"
TASK_TRANSPORTS = frozenset({TRANSPORT_IN_PROCESS, TRANSPORT_GAS_CITY})

_SAFE_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$")


def _env_name(setting: str) -> str:
    return f"DOOM_{setting.upper()}"


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{value} must be an integer") from exc


def _parse_float(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{value} must be a number") from exc


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Typed, validated production configuration (secret-safe)."""

    state_dir: Path
    model_provider: str
    model_name: str
    runtime: str = "local"
    semantic_retries: int = 3
    provider_api_key_env: str = "DOOM_API_KEY"
    provider_order: tuple[str, ...] = ("CoreWeave", "DeepInfra", "SiliconFlow")
    forbidden_providers: tuple[str, ...] = ("Relace",)
    allow_fallbacks: bool = True
    request_timeout: float = 60.0
    provider_base_url: str | None = None
    # Where task attempts execute. Trusted operator configuration: a task
    # attempt can name WHICH policy applies, never where or what runs.
    task_transport: str = TRANSPORT_IN_PROCESS
    gas_city_binary: str | None = None
    gas_city_city: str | None = None
    gas_city_agent: str = "mayor"
    gas_city_attempt_timeout: float = 900.0
    _api_key: str = field(repr=False, default="")

    def redact(self, value: str) -> str:
        """Replace the configured secret value with a redaction marker."""
        if self._api_key and self._api_key in value:
            return value.replace(self._api_key, "[REDACTED]")
        return value

    def redaction_secrets(self) -> tuple[str, ...]:
        """Return secret values used for redaction at the observability sink."""
        return (self._api_key,) if self._api_key else ()

    def log_line(self) -> str:
        """Return a single operator-readable, secret-safe settings line."""
        return (
            f"state_dir={self.state_dir} model_provider={self.model_provider} "
            f"model_name={self.model_name} runtime={self.runtime} "
            f"semantic_retries={self.semantic_retries} "
            f"provider_api_key_env={self.provider_api_key_env} "
            f"provider_order={self.provider_order} "
            f"forbidden_providers={self.forbidden_providers} "
            f"allow_fallbacks={self.allow_fallbacks} "
            f"request_timeout={self.request_timeout} "
            f"provider_base_url={self.provider_base_url} "
            f"task_transport={self.task_transport} "
            f"gas_city_city={self.gas_city_city} credential=[REDACTED]"
        )

    def as_evidence_dict(self) -> dict[str, Any]:
        """Return a secret-safe dict suitable for evidence records."""
        return {
            "state_dir": str(self.state_dir),
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "runtime": self.runtime,
            "semantic_retries": self.semantic_retries,
            "provider_api_key_env": self.provider_api_key_env,
            "provider_order": list(self.provider_order),
            "forbidden_providers": list(self.forbidden_providers),
            "allow_fallbacks": self.allow_fallbacks,
            "request_timeout": self.request_timeout,
            "task_transport": self.task_transport,
            "gas_city_city": self.gas_city_city,
            "gas_city_agent": self.gas_city_agent,
            "provider_base_url": self.provider_base_url,
            "credential": "[REDACTED]",
        }

    def __repr__(self) -> str:
        return (
            f"Settings(state_dir={self.state_dir!r}, model_provider={self.model_provider!r}, "
            f"model_name={self.model_name!r}, runtime={self.runtime!r}, "
            f"semantic_retries={self.semantic_retries!r}, "
            f"provider_api_key_env={self.provider_api_key_env!r}, "
            f"provider_order={self.provider_order!r}, "
            f"forbidden_providers={self.forbidden_providers!r}, "
            f"allow_fallbacks={self.allow_fallbacks!r}, "
            f"request_timeout={self.request_timeout!r}, "
            f"provider_base_url={self.provider_base_url!r}, credential=[REDACTED])"
        )


class ConfigLoader:
    """Resolve settings from layered sources with fail-closed validation."""

    def load(
        self,
        *,
        state_dir: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        runtime: str | None = None,
        semantic_retries: int | None = None,
        provider_api_key_env: str | None = None,
        provider_order: str | None = None,
        forbidden_providers: str | None = None,
        allow_fallbacks: bool | None = None,
        request_timeout: float | None = None,
        provider_base_url: str | None = None,
        task_transport: str | None = None,
        gas_city_binary: str | None = None,
        gas_city_city: str | None = None,
        gas_city_agent: str | None = None,
        gas_city_attempt_timeout: float | None = None,
        config_file: str | Path | None = None,
    ) -> Settings:
        """Load and validate settings; raises :class:`ConfigError` on failure."""
        file_values = self._read_config_file(config_file)
        env = os.environ

        def env_value(name: str) -> str | None:
            value = env.get(_env_name(name), "")
            return value if value else None

        # Required settings (no defaults) - fail closed.
        resolved_state_dir = (
            state_dir or env_value("state_dir") or file_values.get("state_dir")
        )
        if not resolved_state_dir:
            raise ConfigError("state_dir is required")
        resolved_provider = (
            model_provider
            or env_value("model_provider")
            or file_values.get("model_provider")
        )
        if not resolved_provider:
            raise ConfigError("model_provider is required")
        resolved_model = (
            model_name or env_value("model_name") or file_values.get("model_name")
        )
        if not resolved_model:
            raise ConfigError("model_name is required")

        resolved_runtime = (
            runtime or env_value("runtime") or file_values.get("runtime") or "local"
        )

        retries = semantic_retries
        if retries is None:
            env_retries = env_value("semantic_retries")
            if env_retries is not None:
                retries = _parse_int(env_retries)
            elif "semantic_retries" in file_values:
                retries = file_values["semantic_retries"]
            else:
                retries = 3
        if not isinstance(retries, int) or isinstance(retries, bool):
            raise ConfigError(f"semantic_retries must be an integer: {retries!r}")
        if retries < 0:
            raise ConfigError("semantic_retries must be >= 0")

        order_raw = (
            provider_order
            or env_value("provider_order")
            or file_values.get("provider_order")
        )
        provider_order_value: tuple[str, ...]
        if order_raw is None:
            provider_order_value = ("CoreWeave", "DeepInfra", "SiliconFlow")
        elif isinstance(order_raw, str):
            provider_order_value = _split_csv(order_raw)
        else:
            provider_order_value = tuple(str(p) for p in order_raw)

        forbidden_raw = (
            forbidden_providers
            or env_value("forbidden_providers")
            or file_values.get("forbidden_providers")
        )
        forbidden_value: tuple[str, ...]
        if forbidden_raw is None:
            forbidden_value = ("Relace",)
        elif isinstance(forbidden_raw, str):
            forbidden_value = _split_csv(forbidden_raw)
        else:
            forbidden_value = tuple(str(p) for p in forbidden_raw)

        allow = allow_fallbacks
        if allow is None:
            env_allow = env_value("allow_fallbacks")
            if env_allow is not None:
                allow = _parse_bool(env_allow)
            elif "allow_fallbacks" in file_values:
                allow = bool(file_values["allow_fallbacks"])
            else:
                allow = True

        timeout = request_timeout
        if timeout is None:
            env_timeout = env_value("request_timeout")
            if env_timeout is not None:
                timeout = _parse_float(env_timeout)
            elif "request_timeout" in file_values:
                timeout = file_values["request_timeout"]
            else:
                timeout = 60.0
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ConfigError(f"request_timeout must be a number: {timeout!r}")

        base_url_raw = (
            provider_base_url
            or env_value("provider_base_url")
            or file_values.get("provider_base_url")
        )
        base_url_value: str | None
        if base_url_raw is None:
            base_url_value = None
        else:
            base_url_value = str(base_url_raw).strip()
            parsed = urlparse(base_url_value)
            if parsed.username is not None or parsed.password is not None:
                # Never echo the value: userinfo would be a credential, and
                # secrets must stay in the environment, not in URLs, logs,
                # or evidence.
                raise ConfigError(
                    "provider_base_url must not embed credentials; "
                    "keep secrets in the environment"
                )
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or any(ch.isspace() for ch in base_url_value)
            ):
                raise ConfigError(
                    f"provider_base_url must be an http(s) URL: {base_url_value!r}"
                )

        transport = str(
            task_transport
            or env_value("task_transport")
            or file_values.get("task_transport")
            or TRANSPORT_IN_PROCESS
        )
        if transport not in TASK_TRANSPORTS:
            raise ConfigError(
                f"task_transport must be one of {sorted(TASK_TRANSPORTS)}: {transport!r}"
            )
        gc_binary = (
            gas_city_binary
            or env_value("gas_city_binary")
            or file_values.get("gas_city_binary")
        )
        gc_city = (
            gas_city_city or env_value("gas_city_city") or file_values.get("gas_city_city")
        )
        gc_agent = str(
            gas_city_agent
            or env_value("gas_city_agent")
            or file_values.get("gas_city_agent")
            or "mayor"
        )
        gc_timeout_raw = (
            gas_city_attempt_timeout
            or env_value("gas_city_attempt_timeout")
            or file_values.get("gas_city_attempt_timeout")
            or 900.0
        )
        try:
            gc_timeout = float(gc_timeout_raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"gas_city_attempt_timeout must be a number: {gc_timeout_raw!r}"
            ) from exc
        if gc_timeout <= 0:
            raise ConfigError("gas_city_attempt_timeout must be positive")
        if transport == TRANSPORT_GAS_CITY:
            # Fail closed: choosing Gas City execution without saying WHICH
            # Gas City would otherwise be resolved at run time from ambient
            # state, and the runtime must never guess that.
            if not gc_binary:
                raise ConfigError("gas_city_binary is required when task_transport=gas_city")
            if not gc_city:
                raise ConfigError("gas_city_city is required when task_transport=gas_city")
            for label, value in (("gas_city_binary", gc_binary), ("gas_city_city", gc_city)):
                if not Path(str(value)).is_absolute():
                    raise ConfigError(f"{label} must be an absolute path: {value!r}")
            if not os.access(str(gc_binary), os.X_OK):
                raise ConfigError(f"gas_city_binary is not executable: {gc_binary!r}")
            if not _SAFE_AGENT.fullmatch(gc_agent):
                raise ConfigError(f"gas_city_agent must be a simple name: {gc_agent!r}")

        api_key_env = (
            provider_api_key_env
            or env_value("provider_api_key_env")
            or file_values.get("provider_api_key_env")
            or "DOOM_API_KEY"
        )
        # The subprocess guard strips credentials from children by NAME. A
        # credential parked in a variable the guard does not recognize would
        # be inherited by model-authored validation code, so only names the
        # guard will actually strip are accepted.
        if not is_sensitive_env_key(api_key_env):
            raise ConfigError(
                f"provider_api_key_env {api_key_env!r} would not be stripped from "
                "child processes; use a name containing one of "
                f"{', '.join(sorted(SENSITIVE_SUBSTRINGS))}"
            )
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise ConfigError(f"credential environment variable {api_key_env} must be set")

        return Settings(
            state_dir=Path(str(resolved_state_dir)),
            model_provider=str(resolved_provider),
            model_name=str(resolved_model),
            runtime=str(resolved_runtime),
            semantic_retries=int(retries),
            provider_api_key_env=str(api_key_env),
            provider_order=provider_order_value,
            forbidden_providers=forbidden_value,
            allow_fallbacks=bool(allow),
            request_timeout=float(timeout),
            provider_base_url=base_url_value,
            task_transport=transport,
            gas_city_binary=str(gc_binary) if gc_binary else None,
            gas_city_city=str(gc_city) if gc_city else None,
            gas_city_agent=gc_agent,
            gas_city_attempt_timeout=gc_timeout,
            _api_key=api_key,
        )

    @staticmethod
    def _read_config_file(path: str | Path | None) -> dict[str, Any]:
        """Read a JSON config object, rejecting malformed files."""
        if path is None:
            return {}
        file_path = Path(path)
        try:
            data = json.loads(file_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid config file {file_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"invalid config file {file_path}: expected a JSON object")
        return data
