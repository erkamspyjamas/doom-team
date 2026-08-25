"""Production inference adapter with provenance and bounded timeouts.

Implements an OpenAI-compatible chat completions client behind the Doom Team
InferencePort. The adapter records which provider/model/upstream actually
served each call, enforces bounded per-request timeouts, normalizes failures
into a small taxonomy, and never persists private reasoning contents.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

TIMEOUT = "timeout"
OVERLOAD = "overload"
REJECTION = "rejection"
MALFORMED = "malformed"


@dataclass(frozen=True)
class ProviderRouting:
    """Configurable provider routing policy for OpenRouter."""

    order: tuple[str, ...] = ("CoreWeave", "DeepInfra", "SiliconFlow")
    forbidden: tuple[str, ...] = ("Relace",)
    allow_fallbacks: bool = True

    def to_payload(self) -> dict[str, Any]:
        """Return the OpenRouter `provider` request body fragment."""
        payload: dict[str, Any] = {"order": list(self.order)}
        if self.forbidden:
            payload["ignore"] = list(self.forbidden)
        if self.allow_fallbacks:
            payload["allow_fallbacks"] = True
        return payload


@dataclass(frozen=True)
class InferenceResponse:
    """Normalized, safe inference result."""

    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""


@dataclass(frozen=True)
class InferenceFailure:
    """Normalized failure kind."""

    kind: str
    detail: str
    status: int | None = None


class InferenceError(Exception):
    """Raised when an inference request fails in a normalized way."""

    def __init__(self, failure: InferenceFailure) -> None:
        super().__init__(failure.detail)
        self.failure = failure


class OpenAICompatibleInferenceClient:
    """HTTP client for OpenAI-compatible chat completions endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 60.0,
        routing: ProviderRouting | None = None,
        provider_field: str = "provider",
        urlopen: Callable[..., Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._routing = routing
        self._provider_field = provider_field
        self._urlopen = urlopen if urlopen is not None else urllib.request.urlopen

    def complete(self, messages: list[dict[str, str]]) -> InferenceResponse:
        """Send a chat completion request and return a normalized response."""
        payload: dict[str, Any] = {"model": self._model, "messages": messages}
        if self._routing is not None:
            payload[self._provider_field] = self._routing.to_payload()

        body = json.dumps(payload).encode("utf-8")
        url = f"{self._base_url}/v1/chat/completions"
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with self._urlopen(req, timeout=self._timeout) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise self._map_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                raise InferenceError(
                    InferenceFailure(TIMEOUT, "request timed out")
                ) from exc
            raise InferenceError(
                InferenceFailure(OVERLOAD, f"network error: {exc}")
            ) from exc
        except TimeoutError as exc:
            raise InferenceError(
                InferenceFailure(TIMEOUT, "request timed out")
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InferenceError(
                InferenceFailure(MALFORMED, "invalid JSON response")
            ) from exc

        if 200 <= status < 300:
            return self._parse_success(data)
        raise self._map_http_error(status, raw)

    def _map_http_error(
        self, exc_or_status: urllib.error.HTTPError | int, raw: str = ""
    ) -> InferenceError:
        if isinstance(exc_or_status, urllib.error.HTTPError):
            status = exc_or_status.code
        else:
            status = exc_or_status
        if status in (429, 503):
            return InferenceError(
                InferenceFailure(OVERLOAD, f"HTTP {status}: overload", status)
            )
        return InferenceError(
            InferenceFailure(REJECTION, f"HTTP {status}: rejected", status)
        )

    def _parse_success(self, data: dict[str, Any]) -> InferenceResponse:
        try:
            choices = data["choices"]
            if not choices:
                raise KeyError("choices empty")
            choice = choices[0]
            message = choice["message"]
            content = message.get("content", "")
            finish_reason = choice.get("finish_reason", "")
            model = data.get("model", self._model)
            provider = self._extract_provider(data)
            usage = data.get("usage", {})
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
            total_tokens = int(usage.get("total_tokens", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise InferenceError(
                InferenceFailure(MALFORMED, f"malformed response: {exc}")
            ) from exc

        return InferenceResponse(
            content=content,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
        )

    def _extract_provider(self, data: dict[str, Any]) -> str:
        provider_info = data.get("provider")
        if isinstance(provider_info, dict):
            name = provider_info.get("name")
            if name:
                return str(name)
        if isinstance(provider_info, str):
            return provider_info
        return urlparse(self._base_url).netloc
