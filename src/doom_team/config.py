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
import math
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

# Reasoning effort for the NVIDIA primary route's request profile. Exact,
# case-sensitive vocabulary - an unrecognized value fails closed rather than
# silently becoming another profile. "max" is the rc1 historical behavior.
NVIDIA_REASONING_EFFORTS = ("max", "high", "none")
DEFAULT_NVIDIA_REASONING_EFFORT = "max"
OPENROUTER_REASONING_EFFORTS = ("low", "high", "max")


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
    validation_command_timeout: float = 300.0
    provider_base_url: str | None = None
    # Where task attempts execute. Trusted operator configuration: a task
    # attempt can name WHICH policy applies, never where or what runs.
    # NVIDIA-route reasoning effort (max | high | none). Applies ONLY to the
    # request profile of a route whose provider is nvidia; OpenRouter and any
    # other provider never see it. Default preserves rc1 behavior exactly.
    nvidia_reasoning_effort: str = DEFAULT_NVIDIA_REASONING_EFFORT
    # Empty preserves the legacy OpenRouter payload. Explicit reasoning is
    # enabled independently of NVIDIA, for primary and fallback routes alike.
    openrouter_reasoning_effort: str = ""
    openrouter_max_tokens: int = 65536
    openrouter_json_mode: bool = False
    provider_only: tuple[str, ...] = ()
    fallback_provider_only: tuple[str, ...] = ()
    quality_model_route: str = "primary"
    # Optional single fallback inference route (provider availability only).
    # Configured as a unit: naming any fallback field requires all of them,
    # with its own independent credential. Empty means no Doom Team fallback.
    fallback_provider: str = ""
    fallback_model_name: str = ""
    fallback_base_url: str | None = None
    fallback_api_key_env: str = ""
    task_transport: str = TRANSPORT_IN_PROCESS
    gas_city_binary: str | None = None
    gas_city_city: str | None = None
    gas_city_agent: str = "mayor"
    gas_city_attempt_timeout: float = 900.0
    _api_key: str = field(repr=False, default="")
    _fallback_api_key: str = field(repr=False, default="")

    def redact(self, value: str) -> str:
        """Replace the configured secret values with a redaction marker.

        Longest first: replacing a shorter secret that is a substring of a
        longer one would split the longer secret around the marker and leave
        recognizable cleartext residue.
        """
        for secret in self.redaction_secrets():
            if secret in value:
                value = value.replace(secret, "[REDACTED]")
        return value

    def redaction_secrets(self) -> tuple[str, ...]:
        """Secret values for redaction sinks, longest first (see redact)."""
        return tuple(
            sorted(
                (s for s in (self._api_key, self._fallback_api_key) if s),
                key=len,
                reverse=True,
            )
        )

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
            f"validation_command_timeout={self.validation_command_timeout} "
            f"provider_base_url={self.provider_base_url} "
            f"nvidia_reasoning_effort={self.nvidia_reasoning_effort} "
            f"openrouter_reasoning_effort={self.openrouter_reasoning_effort} "
            f"openrouter_max_tokens={self.openrouter_max_tokens} "
            f"openrouter_json_mode={self.openrouter_json_mode} "
            f"provider_only={self.provider_only} "
            f"fallback_provider_only={self.fallback_provider_only} "
            f"quality_model_route={self.quality_model_route} "
            f"fallback_provider={self.fallback_provider} "
            f"fallback_model_name={self.fallback_model_name} "
            f"fallback_base_url={self.fallback_base_url} "
            f"fallback_api_key_env={self.fallback_api_key_env} "
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
            "validation_command_timeout": self.validation_command_timeout,
            "task_transport": self.task_transport,
            "gas_city_city": self.gas_city_city,
            "gas_city_agent": self.gas_city_agent,
            "provider_base_url": self.provider_base_url,
            "nvidia_reasoning_effort": self.nvidia_reasoning_effort,
            "openrouter_reasoning_effort": self.openrouter_reasoning_effort,
            "openrouter_max_tokens": self.openrouter_max_tokens,
            "openrouter_json_mode": self.openrouter_json_mode,
            "provider_only": list(self.provider_only),
            "fallback_provider_only": list(self.fallback_provider_only),
            "quality_model_route": self.quality_model_route,
            "fallback_provider": self.fallback_provider,
            "fallback_model_name": self.fallback_model_name,
            "fallback_base_url": self.fallback_base_url,
            "fallback_api_key_env": self.fallback_api_key_env,
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
            f"validation_command_timeout={self.validation_command_timeout!r}, "
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
        validation_command_timeout: float | None = None,
        provider_base_url: str | None = None,
        nvidia_reasoning_effort: str | None = None,
        openrouter_reasoning_effort: str | None = None,
        openrouter_max_tokens: int | None = None,
        openrouter_json_mode: bool | None = None,
        provider_only: str | None = None,
        fallback_provider_only: str | None = None,
        quality_model_route: str | None = None,
        fallback_provider: str | None = None,
        fallback_model_name: str | None = None,
        fallback_base_url: str | None = None,
        fallback_api_key_env: str | None = None,
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

        validation_timeout_raw: Any = validation_command_timeout
        if validation_timeout_raw is None:
            validation_timeout_raw = env_value("validation_command_timeout")
        if validation_timeout_raw is None:
            validation_timeout_raw = file_values.get("validation_command_timeout", 300.0)
        if isinstance(validation_timeout_raw, bool):
            raise ConfigError("validation_command_timeout must be finite and in (0, 3600]")
        try:
            validation_timeout = float(validation_timeout_raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConfigError("validation_command_timeout must be finite and in (0, 3600]") from exc
        if not math.isfinite(validation_timeout) or not 0 < validation_timeout <= 3600:
            raise ConfigError("validation_command_timeout must be finite and in (0, 3600]")

        def validated_base_url(raw: Any, label: str) -> str | None:
            if raw is None:
                return None
            value = str(raw).strip()
            parsed = urlparse(value)
            if parsed.username is not None or parsed.password is not None:
                # Never echo the value: userinfo would be a credential, and
                # secrets must stay in the environment, not in URLs, logs,
                # or evidence.
                raise ConfigError(
                    f"{label} must not embed credentials; "
                    "keep secrets in the environment"
                )
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or any(ch.isspace() for ch in value)
            ):
                raise ConfigError(f"{label} must be an http(s) URL: {value!r}")
            if parsed.query or parsed.fragment:
                # A query string is where a credential would hide, and base
                # URLs are recorded in logs and evidence by design.
                raise ConfigError(
                    f"{label} must not carry a query string or fragment"
                )
            return value

        base_url_value = validated_base_url(
            provider_base_url
            or env_value("provider_base_url")
            or file_values.get("provider_base_url"),
            "provider_base_url",
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

        effort_raw: Any = nvidia_reasoning_effort
        if effort_raw is None:
            # An empty environment variable is "unset" (env_value returns
            # None for it); an EXPLICITLY configured empty string is not -
            # it falls through to validation and fails closed like any other
            # out-of-vocabulary value, never silently becoming the default.
            effort_raw = env_value("nvidia_reasoning_effort")
        if effort_raw is None:
            effort_raw = file_values.get("nvidia_reasoning_effort")
        if effort_raw is None:
            effort_raw = DEFAULT_NVIDIA_REASONING_EFFORT
        effort = str(effort_raw)
        if effort not in NVIDIA_REASONING_EFFORTS:
            # Exact vocabulary, no normalization: "High" or "off" silently
            # becoming another profile would invalidate any profile
            # comparison the operator believes they are running.
            raise ConfigError(
                f"nvidia_reasoning_effort must be one of "
                f"{', '.join(NVIDIA_REASONING_EFFORTS)}: got {effort!r}"
            )

        router_effort_raw: Any = openrouter_reasoning_effort
        if router_effort_raw is None:
            router_effort_raw = env_value("openrouter_reasoning_effort")
        if router_effort_raw is None:
            router_effort_raw = file_values.get("openrouter_reasoning_effort", "")
        router_effort = str(router_effort_raw)
        if router_effort and router_effort not in OPENROUTER_REASONING_EFFORTS:
            raise ConfigError("openrouter_reasoning_effort must be low, high, or max when set")

        router_tokens_raw: Any = openrouter_max_tokens
        if router_tokens_raw is None:
            router_tokens_raw = env_value("openrouter_max_tokens")
        if router_tokens_raw is None:
            router_tokens_raw = file_values.get("openrouter_max_tokens", 65536)
        if isinstance(router_tokens_raw, str):
            router_tokens_raw = _parse_int(router_tokens_raw)
        if type(router_tokens_raw) is not int or not 1024 <= router_tokens_raw <= 262144:
            raise ConfigError("openrouter_max_tokens must be an integer between 1024 and 262144")

        router_json_raw: Any = openrouter_json_mode
        if router_json_raw is None:
            router_json_raw = env_value("openrouter_json_mode")
        if router_json_raw is None:
            router_json_raw = file_values.get("openrouter_json_mode", False)
        if isinstance(router_json_raw, str):
            if router_json_raw.lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                raise ConfigError("openrouter_json_mode must be a boolean")
            router_json_raw = _parse_bool(router_json_raw)
        if not isinstance(router_json_raw, bool):
            raise ConfigError("openrouter_json_mode must be a boolean")

        def endpoint_only(value: str | None, name: str) -> tuple[str, ...]:
            raw: Any = value
            if raw is None:
                raw = env_value(name)
            if raw is None:
                raw = file_values.get(name, ())
            if isinstance(raw, str):
                parts = _split_csv(raw)
            elif isinstance(raw, (list, tuple)) and all(isinstance(p, str) for p in raw):
                parts = tuple(p.strip() for p in raw)
            else:
                raise ConfigError(f"{name} must be a CSV string or list of endpoint names")
            if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", part) for part in parts):
                raise ConfigError(f"{name} contains an invalid endpoint name")
            return parts

        only = endpoint_only(provider_only, "provider_only")
        fb_only = endpoint_only(fallback_provider_only, "fallback_provider_only")
        quality_route = str(quality_model_route or env_value("quality_model_route")
                            or file_values.get("quality_model_route") or "primary")
        if quality_route not in ("primary", "fallback"):
            raise ConfigError("quality_model_route must be primary or fallback")

        fb_provider = str(
            fallback_provider
            or env_value("fallback_provider")
            or file_values.get("fallback_provider")
            or ""
        ).strip()
        fb_model = str(
            fallback_model_name
            or env_value("fallback_model_name")
            or file_values.get("fallback_model_name")
            or ""
        ).strip()
        fb_base_url = validated_base_url(
            fallback_base_url
            or env_value("fallback_base_url")
            or file_values.get("fallback_base_url"),
            "fallback_base_url",
        )
        fb_key_env = str(
            fallback_api_key_env
            or env_value("fallback_api_key_env")
            or file_values.get("fallback_api_key_env")
            or ""
        ).strip()
        fb_api_key = ""
        fallback_fields = (fb_provider, fb_model, fb_base_url, fb_key_env)
        if not any(fallback_fields) and (fb_only or quality_route == "fallback"):
            raise ConfigError("fallback endpoint selection and quality routing require a fallback route")
        if any(fallback_fields):
            # The fallback route is configured as a unit and fails closed: a
            # half-configured fallback discovered mid-mission would either
            # crash the call or silently leave the product single-route.
            if not all(fallback_fields):
                raise ConfigError(
                    "fallback route requires fallback_provider, "
                    "fallback_model_name, fallback_base_url and "
                    "fallback_api_key_env together"
                )
            if not is_sensitive_env_key(fb_key_env):
                raise ConfigError(
                    f"fallback_api_key_env {fb_key_env!r} would not be "
                    "stripped from child processes; use a name containing "
                    f"one of {', '.join(sorted(SENSITIVE_SUBSTRINGS))}"
                )
            fb_api_key = os.environ.get(fb_key_env, "")
            if not fb_api_key:
                raise ConfigError(
                    f"credential environment variable {fb_key_env} must be set"
                )

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
            validation_command_timeout=validation_timeout,
            provider_base_url=base_url_value,
            nvidia_reasoning_effort=effort,
            openrouter_reasoning_effort=router_effort,
            openrouter_max_tokens=router_tokens_raw,
            openrouter_json_mode=router_json_raw,
            provider_only=only,
            fallback_provider_only=fb_only,
            quality_model_route=quality_route,
            fallback_provider=fb_provider,
            fallback_model_name=fb_model,
            fallback_base_url=fb_base_url,
            fallback_api_key_env=fb_key_env,
            task_transport=transport,
            gas_city_binary=str(gc_binary) if gc_binary else None,
            gas_city_city=str(gc_city) if gc_city else None,
            gas_city_agent=gc_agent,
            gas_city_attempt_timeout=gc_timeout,
            _api_key=api_key,
            _fallback_api_key=fb_api_key,
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
