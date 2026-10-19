"""Production inference adapter with provenance and bounded timeouts.

Implements an OpenAI-compatible chat completions client behind the Doom Team
InferencePort. The adapter records which provider/model/upstream actually
served each call, enforces bounded per-request timeouts, normalizes failures
into a small taxonomy, and never persists private reasoning contents.
"""

from __future__ import annotations

import http.client
import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

from doom_team.config import ConfigError, Settings
from doom_team.observability import Event, EventSink

TIMEOUT = "timeout"
OVERLOAD = "overload"
REJECTION = "rejection"
MALFORMED = "malformed"

# Canonical request profile for the NVIDIA V4 Pro primary route. Attached only
# when the configured provider is NVIDIA - these are top-level OpenAI-compatible
# body fields, and reasoning_effort=max is the NVIDIA NIM reasoning switch.
NVIDIA_REQUEST_PROFILE: dict[str, Any] = {
    "reasoning_effort": "max",
    "temperature": 1.0,
    "top_p": 0.95,
    "max_tokens": 16384,
    "seed": 42,
    "stream": False,
}

# Provider-availability statuses that make a primary-route failure eligible for
# the single fallback route. 429/503 and network errors already normalize to
# OVERLOAD and timeouts to TIMEOUT; these are the remaining transient HTTP
# statuses that normalize to REJECTION.
_FALLBACK_ELIGIBLE_STATUSES = frozenset({408, 500, 502, 504})


def fallback_eligible(failure: InferenceFailure) -> bool:
    """Whether a normalized primary failure may use the fallback route.

    Availability only: timeouts, overload/rate-limit/network failures, and
    transient 5xx/408. A 400/401/403/404/422 is a configuration or request
    error the operator must SEE - masking a broken primary credential or model
    name behind a working fallback would hide exactly the misconfiguration
    this policy needs surfaced. A successful HTTP response never falls back,
    however unwelcome its content.
    """
    if failure.kind in (TIMEOUT, OVERLOAD):
        return True
    return failure.kind == REJECTION and failure.status in _FALLBACK_ELIGIBLE_STATUSES


@dataclass(frozen=True)
class ProviderRouting:
    """Configurable provider routing policy for OpenRouter."""

    order: tuple[str, ...] = ("CoreWeave", "DeepInfra", "SiliconFlow")
    forbidden: tuple[str, ...] = ("Relace",)
    allow_fallbacks: bool = True
    only: tuple[str, ...] = ()
    require_parameters: bool = False

    def to_payload(self) -> dict[str, Any]:
        """Return the OpenRouter `provider` request body fragment."""
        payload: dict[str, Any] = {"order": list(self.order)}
        if self.forbidden:
            payload["ignore"] = list(self.forbidden)
        if self.allow_fallbacks:
            payload["allow_fallbacks"] = True
        if self.only:
            payload["only"] = list(self.only)
        if self.require_parameters:
            payload["require_parameters"] = True
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
        extra_body: dict[str, Any] | None = None,
        urlopen: Callable[..., Any] | None = None,
        event_sink: EventSink | None = None,
        producer_run: str = "",
        record_usage: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._routing = routing
        # Additional top-level request-body fields for THIS route only (e.g.
        # the NVIDIA reasoning/sampling profile). Never model or messages.
        self._extra_body = {
            k: v for k, v in dict(extra_body or {}).items()
            if k not in ("model", "messages")
        }
        self._provider_field = provider_field
        self._urlopen = urlopen if urlopen is not None else urllib.request.urlopen
        self._event_sink = event_sink
        self._producer_run = producer_run
        self._record_usage = record_usage

    def _record_error(self, kind: str, detail: str, status: int | None = None) -> None:
        if self._event_sink is not None:
            details: dict[str, Any] = {"kind": kind, "detail": detail}
            if status is not None:
                details["status"] = status
            self._event_sink.record(
                Event(
                    kind="PROVIDER_ERROR",
                    details=details,
                    producer_role="inference",
                    producer_run=self._producer_run,
                )
            )

    def complete(self, messages: list[dict[str, str]]) -> InferenceResponse:
        """Send a chat completion request and return a normalized response."""
        started = time.monotonic()
        payload: dict[str, Any] = {"model": self._model, "messages": messages}
        payload.update(self._extra_body)
        if self._routing is not None:
            payload[self._provider_field] = self._routing.to_payload()

        body = json.dumps(payload).encode("utf-8")
        # Vendors document their base URL inconsistently: OpenRouter as
        # https://openrouter.ai/api, NVIDIA as https://integrate.api.nvidia.com/v1.
        # Accept both spellings and never construct a doubled /v1/v1 path.
        if self._base_url.endswith("/v1"):
            url = f"{self._base_url}/chat/completions"
        else:
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
            failure = self._map_http_error(exc)
            self._record_error(failure.failure.kind, failure.failure.detail, failure.failure.status)
            raise failure from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                self._record_error(TIMEOUT, "request timed out")
                raise InferenceError(
                    InferenceFailure(TIMEOUT, "request timed out")
                ) from exc
            self._record_error(OVERLOAD, f"network error: {exc}")
            raise InferenceError(
                InferenceFailure(OVERLOAD, f"network error: {exc}")
            ) from exc
        except TimeoutError as exc:
            self._record_error(TIMEOUT, "request timed out")
            raise InferenceError(
                InferenceFailure(TIMEOUT, "request timed out")
            ) from exc
        except UnicodeDecodeError as exc:
            self._record_error(MALFORMED, "response was not valid UTF-8")
            raise InferenceError(
                InferenceFailure(MALFORMED, "response was not valid UTF-8")
            ) from exc
        except (http.client.HTTPException, OSError) as exc:
            # A connection that dies AFTER connect - reset or truncated while
            # the (non-streamed) body is delivered - is the same availability
            # failure as one refused at connect time. urllib only wraps
            # request-phase errors in URLError; getresponse()/read() failures
            # arrive raw and must not escape the taxonomy, or the fallback
            # route is silently forfeited exactly when it is needed.
            self._record_error(OVERLOAD, f"connection failed mid-response: {exc!r}")
            raise InferenceError(
                InferenceFailure(OVERLOAD, f"connection failed mid-response: {exc!r}")
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._record_error(MALFORMED, "invalid JSON response")
            raise InferenceError(
                InferenceFailure(MALFORMED, "invalid JSON response")
            ) from exc

        if 200 <= status < 300:
            response = self._parse_success(data)
            if self._record_usage and self._event_sink is not None:
                # Numeric usage only; never store reasoning text, raw provider
                # payloads, prompts, or credentials. Metrics cannot eat success.
                try:
                    usage = data.get("usage", {})
                    details = usage.get("completion_tokens_details") or {}
                    reasoning_tokens = details.get("reasoning_tokens")
                    cost = usage.get("cost")
                    self._event_sink.record(Event(
                        kind="PROVIDER_USAGE",
                        details={
                            "configured_model": self._model,
                            "model": response.model,
                            "provider": response.provider,
                            "prompt_tokens": response.prompt_tokens,
                            "completion_tokens": response.completion_tokens,
                            "reasoning_tokens": reasoning_tokens if type(reasoning_tokens) is int else None,
                            "cost_usd": cost if type(cost) in (int, float) and math.isfinite(cost) else None,
                            "finish_reason": response.finish_reason,
                            "duration_seconds": round(time.monotonic() - started, 3),
                        },
                        producer_role="inference", producer_run=self._producer_run,
                    ))
                except Exception:
                    pass
            return response
        failure = self._map_http_error(status, raw)
        self._record_error(failure.failure.kind, failure.failure.detail, failure.failure.status)
        raise failure

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
            self._record_error(MALFORMED, f"malformed response: {exc}")
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


def _clip_detail(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit]


class RoutedInferenceClient:
    """One primary route plus at most ONE fallback route per logical call.

    The bound is structural: a call invokes the primary client exactly once
    and, only for an availability-eligible failure, the fallback client
    exactly once. There is no loop, no third route, and no route back into
    the primary. Configuration and semantic failures raise unchanged, so a
    broken primary credential or model name stays visible.
    """

    def __init__(
        self,
        primary: OpenAICompatibleInferenceClient,
        fallback: OpenAICompatibleInferenceClient,
        *,
        primary_label: str,
        fallback_label: str,
        event_sink: EventSink | None = None,
        producer_run: str = "",
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_label = primary_label
        self._fallback_label = fallback_label
        self._event_sink = event_sink
        self._producer_run = producer_run

    def complete(self, messages: list[dict[str, str]]) -> InferenceResponse:
        try:
            return self._primary.complete(messages)
        except InferenceError as primary_error:
            if not fallback_eligible(primary_error.failure):
                raise
            if self._event_sink is not None:
                try:
                    self._event_sink.record(
                        Event(
                            kind="PROVIDER_FALLBACK",
                            details={
                                "from_route": self._primary_label,
                                "to_route": self._fallback_label,
                                "reason_kind": primary_error.failure.kind,
                                "reason_status": primary_error.failure.status,
                            },
                            producer_role="inference",
                            producer_run=self._producer_run,
                        )
                    )
                except Exception:  # noqa: BLE001 - observability never eats the route
                    # A full disk must not convert a recoverable provider
                    # outage into a dead logical call between the two routes.
                    pass
            try:
                return self._fallback.complete(messages)
            except InferenceError as fallback_error:
                raise InferenceError(
                    InferenceFailure(
                        kind=fallback_error.failure.kind,
                        detail=(
                            f"primary route {self._primary_label} unavailable "
                            f"({primary_error.failure.kind}"
                            + (
                                f" HTTP {primary_error.failure.status}"
                                if primary_error.failure.status is not None
                                else ""
                            )
                            + f": {_clip_detail(primary_error.failure.detail)}); "
                            f"fallback route {self._fallback_label} failed "
                            f"({fallback_error.failure.kind}: "
                            f"{_clip_detail(fallback_error.failure.detail)})"
                        ),
                        status=fallback_error.failure.status,
                    )
                ) from fallback_error


def _route_extras(provider: str, settings: Settings, *, fallback: bool = False) -> tuple[
    ProviderRouting | None, dict[str, Any] | None
]:
    """Per-route request options: profiles belong to routes, never globally.

    OpenRouter speaks the provider-preferences body fragment; the NVIDIA NIM
    endpoint speaks the canonical reasoning/sampling profile. Sending either
    vocabulary to the other endpoint would be rejected or silently misread.
    """
    name = provider.strip().lower()
    if name == "openrouter":
        extra: dict[str, Any] = {}
        if settings.openrouter_reasoning_effort:
            extra.update({
                "reasoning": {"enabled": True, "effort": settings.openrouter_reasoning_effort, "exclude": True},
                "max_tokens": settings.openrouter_max_tokens,
                "temperature": 1.0, "top_p": 0.95, "stream": False,
            })
        if settings.openrouter_json_mode:
            extra["response_format"] = {"type": "json_object"}
            extra["max_tokens"] = settings.openrouter_max_tokens
        return (
            ProviderRouting(
                order=settings.provider_order,
                forbidden=settings.forbidden_providers,
                allow_fallbacks=settings.allow_fallbacks,
                only=settings.fallback_provider_only if fallback else settings.provider_only,
                require_parameters=bool(extra),
            ),
            extra or None,
        )
    if name == "nvidia":
        # The canonical profile with exactly ONE configurable axis: the
        # operator-selected reasoning effort. Every other field is pinned so
        # profile comparisons vary a single variable.
        return None, dict(
            NVIDIA_REQUEST_PROFILE,
            reasoning_effort=settings.nvidia_reasoning_effort,
        )
    return None, None


def build_inference_client(
    settings: Settings,
    *,
    event_sink: EventSink | None = None,
    producer_run: str = "",
) -> OpenAICompatibleInferenceClient | RoutedInferenceClient:
    """Construct the canonical inference client from validated settings.

    This is the single construction seam future planner, specialist runner,
    QA, and Reviewer paths share. It performs no network call. Fail closed:
    the client has no vendor default endpoint, so a configured
    provider_base_url is required.
    """
    if not settings.provider_base_url:
        raise ConfigError(
            "provider_base_url must be configured to construct the inference client"
        )
    routing, extra_body = _route_extras(settings.model_provider, settings)
    primary = OpenAICompatibleInferenceClient(
        settings.provider_base_url,
        settings._api_key,
        settings.model_name,
        timeout=settings.request_timeout,
        routing=routing,
        extra_body=extra_body,
        event_sink=event_sink,
        producer_run=producer_run,
        record_usage=bool(settings.openrouter_reasoning_effort) and settings.model_provider.lower() == "openrouter",
    )
    if not settings.fallback_model_name:
        return primary
    if not settings.fallback_base_url:
        raise ConfigError(
            "fallback_base_url must be configured to construct the fallback route"
        )
    fb_routing, fb_extra = _route_extras(settings.fallback_provider, settings, fallback=True)
    fallback = OpenAICompatibleInferenceClient(
        settings.fallback_base_url,
        settings._fallback_api_key,
        settings.fallback_model_name,
        timeout=settings.request_timeout,
        routing=fb_routing,
        extra_body=fb_extra,
        event_sink=event_sink,
        producer_run=producer_run,
        record_usage=bool(settings.openrouter_reasoning_effort) and settings.fallback_provider.lower() == "openrouter",
    )
    return RoutedInferenceClient(
        primary,
        fallback,
        primary_label=f"{settings.model_provider}/{settings.model_name}",
        fallback_label=f"{settings.fallback_provider}/{settings.fallback_model_name}",
        event_sink=event_sink,
        producer_run=producer_run,
    )


def build_quality_inference_client(
    settings: Settings, *, event_sink: EventSink | None = None, producer_run: str = ""
) -> OpenAICompatibleInferenceClient | RoutedInferenceClient:
    """Trusted operator-selected quality model, with the same bounded failover.

    Swapping the whole route preserves credentials and endpoint restrictions;
    task text and model output cannot choose or weaken the quality route.
    """
    if settings.quality_model_route == "fallback":
        if not settings.fallback_model_name:
            raise ConfigError("quality_model_route=fallback requires a configured fallback route")
        settings = replace(
            settings,
            model_provider=settings.fallback_provider,
            model_name=settings.fallback_model_name,
            provider_base_url=settings.fallback_base_url,
            provider_api_key_env=settings.fallback_api_key_env,
            provider_only=settings.fallback_provider_only,
            _api_key=settings._fallback_api_key,
            fallback_provider=settings.model_provider,
            fallback_model_name=settings.model_name,
            fallback_base_url=settings.provider_base_url,
            fallback_api_key_env=settings.provider_api_key_env,
            fallback_provider_only=settings.provider_only,
            _fallback_api_key=settings._api_key,
            quality_model_route="primary",
        )
    return build_inference_client(settings, event_sink=event_sink, producer_run=producer_run)
