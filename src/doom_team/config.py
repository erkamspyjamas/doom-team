"""Typed, validated, secret-safe production configuration.

Precedence (highest first):

1. Explicit arguments to ``ConfigLoader.load``
2. Environment variables (``DOOM_*``)
3. Configuration file (JSON object)
4. Safe defaults for optional settings

Required settings (``state_dir``, ``model_provider``, ``model_name``) and the
provider credential (referenced by environment variable name) have no default:
startup fails closed when they are missing. Secret values are never stored in
files or code; they are referenced by environment variable name and redacted
from reprs, error messages, logs and evidence views.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

REDACTED = "[REDACTED]"

_ENV_PREFIX = "DOOM_"
_DEFAULT_CREDENTIAL_ENV = "DOOM_API_KEY"
_DEFAULT_RUNTIME = "local"
_DEFAULT_SEMANTIC_RETRIES = 3

_REQUIRED_SETTINGS = ("state_dir", "model_provider", "model_name")

_KNOWN_SETTINGS = frozenset(
    {
        "state_dir",
        "model_provider",
        "model_name",
        "runtime",
        "semantic_retries",
        "provider_api_key_env",
    }
)

_SECRET_MARKERS = (
    "token",
    "password",
    "secret",
    "apikey",
    "api_key",
    "access_key",
    "private_key",
    "credential",
    "auth",
)


class ConfigError(Exception):
    """Raised when configuration is invalid or required settings are missing."""


def _is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


@dataclass(frozen=True)
class Redactor:
    """Redacts known secret values from arbitrary text."""

    _values: tuple[str, ...] = ()

    def redact(self, text: str) -> str:
        """Return ``text`` with any known secret values replaced."""
        result = text
        for value in self._values:
            if value and value in result:
                result = result.replace(value, REDACTED)
        return result


@dataclass(frozen=True)
class Settings:
    """Validated, immutable Doom Team runtime settings.

    Credentials are referenced by environment variable name
    (``provider_api_key_env``); their values are never stored in files or code.
    """

    state_dir: Path
    model_provider: str
    model_name: str
    runtime: str = _DEFAULT_RUNTIME
    semantic_retries: int = _DEFAULT_SEMANTIC_RETRIES
    provider_api_key_env: str = _DEFAULT_CREDENTIAL_ENV
    _redactor: Redactor = field(default_factory=Redactor, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not str(self.state_dir).strip():
            raise ConfigError("state_dir is required")
        if not self.model_provider.strip():
            raise ConfigError("model_provider is required")
        if not self.model_name.strip():
            raise ConfigError("model_name is required")
        if not self.provider_api_key_env.strip():
            raise ConfigError("provider_api_key_env is required")
        if self.semantic_retries < 0:
            raise ConfigError("semantic_retries must be non-negative")

    def __repr__(self) -> str:
        base = (
            f"Settings(state_dir={self.state_dir!r}, "
            f"model_provider={self.model_provider!r}, "
            f"model_name={self.model_name!r}, runtime={self.runtime!r}, "
            f"semantic_retries={self.semantic_retries!r}, "
            f"provider_api_key_env={self.provider_api_key_env!r}, "
            "provider_api_key=[REDACTED])"
        )
        return self.redact(base)

    def redact(self, text: str) -> str:
        """Redact known secret values from arbitrary text."""
        return self._redactor.redact(text)

    def log_line(self) -> str:
        """A redacted one-line description safe to emit in logs."""
        return repr(self)

    def as_evidence_dict(self) -> dict[str, str]:
        """An evidence-safe view containing no credential values."""
        return {
            "state_dir": str(self.state_dir),
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "runtime": self.runtime,
            "semantic_retries": str(self.semantic_retries),
            "provider_api_key_env": self.provider_api_key_env,
        }


class ConfigLoader:
    """Loads and validates :class:`Settings` from layered sources.

    Precedence: explicit arguments > environment variables > config file >
    defaults. Missing required settings or credentials cause a fail-closed
    :class:`ConfigError` at startup.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    def _env_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in self._env.items():
            if key.startswith(_ENV_PREFIX):
                values[key[len(_ENV_PREFIX) :].lower()] = value
        return values

    def _config_file_values(self, config_file: str | Path | None) -> dict[str, Any]:
        if config_file is None:
            return {}
        path = Path(config_file)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid config file {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"config file {path} must contain a JSON object")
        return data

    def _coerce(self, name: str, value: Any) -> Any:
        if name == "semantic_retries":
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ConfigError(f"setting '{name}' must be an integer") from None
        return value

    def load(
        self,
        *,
        state_dir: str | Path | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        runtime: str | None = None,
        semantic_retries: int | None = None,
        provider_api_key_env: str | None = None,
        config_file: str | Path | None = None,
    ) -> Settings:
        secret_values: list[str] = []
        for key, value in self._env.items():
            if value and _is_secret_name(key):
                secret_values.append(value)
        redactor = Redactor(tuple(secret_values))

        defaults: dict[str, Any] = {
            "runtime": _DEFAULT_RUNTIME,
            "semantic_retries": _DEFAULT_SEMANTIC_RETRIES,
            "provider_api_key_env": _DEFAULT_CREDENTIAL_ENV,
        }
        merged: dict[str, Any] = dict(defaults)
        merged.update(self._config_file_values(config_file))
        env_values = self._env_values()
        merged.update(
            {key: value for key, value in env_values.items() if key in _KNOWN_SETTINGS}
        )
        explicit: dict[str, Any] = {
            "state_dir": state_dir,
            "model_provider": model_provider,
            "model_name": model_name,
            "runtime": runtime,
            "semantic_retries": semantic_retries,
            "provider_api_key_env": provider_api_key_env,
        }
        for name, value in explicit.items():
            if value is not None:
                merged[name] = value

        coerced = {name: self._coerce(name, value) for name, value in merged.items()}

        for name in _REQUIRED_SETTINGS:
            if not str(coerced.get(name, "")).strip():
                raise ConfigError(
                    redactor.redact(f"required setting '{name}' is missing")
                )

        cred_env = str(coerced.get("provider_api_key_env", "")).strip()
        if not cred_env:
            raise ConfigError(redactor.redact("provider_api_key_env is required"))
        if not self._env.get(cred_env, ""):
            raise ConfigError(
                redactor.redact(
                    f"credential environment variable '{cred_env}' is not set"
                )
            )

        cred_value = self._env.get(cred_env, "")
        if cred_value:
            unique: list[str] = []
            for value in (*secret_values, cred_value):
                if value and value not in unique:
                    unique.append(value)
            redactor = Redactor(tuple(unique))

        return Settings(
            state_dir=Path(str(coerced["state_dir"])),
            model_provider=str(coerced["model_provider"]),
            model_name=str(coerced["model_name"]),
            runtime=str(coerced["runtime"]),
            semantic_retries=int(coerced["semantic_retries"]),
            provider_api_key_env=cred_env,
            _redactor=redactor,
        )


def load_config(
    *,
    state_dir: str | Path | None = None,
    model_provider: str | None = None,
    model_name: str | None = None,
    runtime: str | None = None,
    semantic_retries: int | None = None,
    provider_api_key_env: str | None = None,
    config_file: str | Path | None = None,
) -> Settings:
    """Load configuration from the process environment."""
    return ConfigLoader().load(
        state_dir=state_dir,
        model_provider=model_provider,
        model_name=model_name,
        runtime=runtime,
        semantic_retries=semantic_retries,
        provider_api_key_env=provider_api_key_env,
        config_file=config_file,
    )
