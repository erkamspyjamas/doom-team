"""Two-route provider failover: NVIDIA V4 Pro primary, OpenRouter Flash fallback.

One logical inference call is at most two route calls: the primary once, and -
only for a provider-availability failure - the fallback once. Configuration
and semantic outcomes never fall back; provenance always names the route that
actually answered; each route carries only its own request vocabulary and its
own credential.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from doom_team.config import ConfigError, ConfigLoader
from doom_team.inference import (
    NVIDIA_REQUEST_PROFILE,
    InferenceError,
    OpenAICompatibleInferenceClient,
    ProviderRouting,
    RoutedInferenceClient,
    build_inference_client,
    fallback_eligible,
)
from tests.test_inference import FakeResponse

NVIDIA_MODEL = "deepseek-ai/deepseek-v4-pro-0813"
FLASH_MODEL = "deepseek/deepseek-v4-flash-0731"
MESSAGES = [{"role": "user", "content": "hello"}]


def _success_body(model: str, provider: str | None = None) -> bytes:
    payload: dict[str, Any] = {
        "model": model,
        "choices": [
            {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    if provider is not None:
        payload["provider"] = {"name": provider}
    return json.dumps(payload).encode()


class RouteRecorder:
    """urlopen double that records every request and scripts one behavior."""

    def __init__(self, behavior: str, model: str, provider: str | None = None) -> None:
        self.behavior = behavior
        self.model = model
        self.provider = provider
        self.requests: list[Any] = []

    def __call__(self, req: Any, timeout: float | None = None) -> FakeResponse:
        self.requests.append(req)
        if self.behavior == "success":
            return FakeResponse(200, _success_body(self.model, self.provider))
        if self.behavior == "timeout":
            raise urllib.error.URLError(TimeoutError("timed out"))
        if self.behavior == "connection":
            raise urllib.error.URLError(OSError("connection refused"))
        status = int(self.behavior)
        raise urllib.error.HTTPError(req.full_url, status, "err", {}, None)

    def body(self, index: int = 0) -> dict[str, Any]:
        return dict(json.loads(self.requests[index].data))

    def auth(self, index: int = 0) -> str:
        return str(self.requests[index].headers.get("Authorization"))


def _routed(
    primary_behavior: str, fallback_behavior: str = "success"
) -> tuple[RoutedInferenceClient, RouteRecorder, RouteRecorder]:
    nvidia = RouteRecorder(primary_behavior, NVIDIA_MODEL)
    flash = RouteRecorder(fallback_behavior, FLASH_MODEL, provider="DeepInfra")
    primary = OpenAICompatibleInferenceClient(
        "https://integrate.api.nvidia.com",
        "nvidia-test-credential",
        NVIDIA_MODEL,
        extra_body=dict(NVIDIA_REQUEST_PROFILE),
        urlopen=nvidia,
    )
    fallback = OpenAICompatibleInferenceClient(
        "https://openrouter.ai/api",
        "openrouter-test-credential",
        FLASH_MODEL,
        routing=ProviderRouting(),
        urlopen=flash,
    )
    client = RoutedInferenceClient(
        primary,
        fallback,
        primary_label=f"nvidia/{NVIDIA_MODEL}",
        fallback_label=f"openrouter/{FLASH_MODEL}",
    )
    return client, nvidia, flash


# --- P1: primary success never touches the fallback ------------------------


def test_primary_success_uses_only_nvidia() -> None:
    client, nvidia, flash = _routed("success")
    response = client.complete(MESSAGES)
    assert len(nvidia.requests) == 1
    assert len(flash.requests) == 0
    assert response.model == NVIDIA_MODEL
    assert response.provider == "integrate.api.nvidia.com"


# --- P2-P8: availability failures fall back exactly once --------------------


@pytest.mark.parametrize(
    "behavior", ["connection", "timeout", "429", "500", "502", "503", "504", "408"]
)
def test_availability_failure_falls_back_exactly_once(behavior: str) -> None:
    client, nvidia, flash = _routed(behavior)
    response = client.complete(MESSAGES)
    assert len(nvidia.requests) == 1
    assert len(flash.requests) == 1
    assert response.model == FLASH_MODEL
    assert response.provider == "DeepInfra"


# --- P9-P13: configuration/request errors stay visible ----------------------


@pytest.mark.parametrize("behavior", ["400", "401", "403", "404", "422"])
def test_configuration_errors_never_fall_back(behavior: str) -> None:
    client, nvidia, flash = _routed(behavior)
    with pytest.raises(InferenceError) as excinfo:
        client.complete(MESSAGES)
    assert len(nvidia.requests) == 1
    assert len(flash.requests) == 0
    assert excinfo.value.failure.status == int(behavior)


# --- P14: a successful response never falls back, whatever comes later ------


def test_semantically_rejected_success_does_not_fall_back() -> None:
    from doom_team.planner import Planner, PlanningError

    nvidia = RouteRecorder("success", NVIDIA_MODEL)
    flash = RouteRecorder("success", FLASH_MODEL)
    # The "successful" NVIDIA body content is not a valid plan.
    primary = OpenAICompatibleInferenceClient(
        "https://integrate.api.nvidia.com", "k1", NVIDIA_MODEL, urlopen=nvidia
    )
    fallback = OpenAICompatibleInferenceClient(
        "https://openrouter.ai/api", "k2", FLASH_MODEL, urlopen=flash
    )
    client = RoutedInferenceClient(
        primary, fallback, primary_label="nvidia", fallback_label="openrouter"
    )
    with pytest.raises(PlanningError):
        Planner(client).plan("Build something")
    assert len(nvidia.requests) == 1
    assert len(flash.requests) == 0


# --- P15/P16: both routes fail -> two calls, bounded error, no loop ---------


def test_both_route_failure_is_bounded_and_context_preserving() -> None:
    client, nvidia, flash = _routed("503", "502")
    with pytest.raises(InferenceError) as excinfo:
        client.complete(MESSAGES)
    assert len(nvidia.requests) == 1
    assert len(flash.requests) == 1  # exactly two route calls, ever
    detail = excinfo.value.failure.detail
    assert f"nvidia/{NVIDIA_MODEL}" in detail
    assert f"openrouter/{FLASH_MODEL}" in detail
    assert "nvidia-test-credential" not in detail
    assert "openrouter-test-credential" not in detail
    assert len(detail) < 1200


# --- P17/P18: NVIDIA request body ------------------------------------------


def test_nvidia_request_body_carries_the_canonical_profile() -> None:
    client, nvidia, flash = _routed("success")
    client.complete(MESSAGES)
    body = nvidia.body()
    assert body["model"] == NVIDIA_MODEL
    assert body["reasoning_effort"] == "max"
    assert body["temperature"] == 1.0
    assert body["top_p"] == 0.95
    assert body["max_tokens"] == 16384
    assert body["seed"] == 42
    assert body["stream"] is False
    assert "thinking" not in body
    assert "provider" not in body  # no OpenRouter routing vocabulary
    assert nvidia.requests[0].full_url == (
        "https://integrate.api.nvidia.com/v1/chat/completions"
    )


def test_nvidia_base_url_with_v1_suffix_is_not_doubled() -> None:
    recorder = RouteRecorder("success", NVIDIA_MODEL)
    client = OpenAICompatibleInferenceClient(
        "https://integrate.api.nvidia.com/v1", "k", NVIDIA_MODEL, urlopen=recorder
    )
    client.complete(MESSAGES)
    assert recorder.requests[0].full_url == (
        "https://integrate.api.nvidia.com/v1/chat/completions"
    )


# --- P19: fallback body keeps its own vocabulary ----------------------------


def test_fallback_body_is_flash_with_openrouter_preferences_only() -> None:
    client, nvidia, flash = _routed("timeout")
    client.complete(MESSAGES)
    body = flash.body()
    assert body["model"] == FLASH_MODEL
    assert body["provider"]["order"] == ["CoreWeave", "DeepInfra", "SiliconFlow"]
    assert body["provider"]["ignore"] == ["Relace"]
    assert "reasoning_effort" not in body  # NVIDIA-only setting not inherited
    assert "seed" not in body


# --- P20: provenance --------------------------------------------------------


def test_fallback_success_reports_the_actual_route() -> None:
    client, _, _ = _routed("500")
    response = client.complete(MESSAGES)
    assert response.model == FLASH_MODEL
    assert response.provider == "DeepInfra"


def test_fallback_usage_is_observable_via_event() -> None:
    events: list[Any] = []

    class Sink:
        def record(self, event: Any) -> None:
            events.append(event)

    client, nvidia, flash = _routed("429")
    client._event_sink = Sink()  # type: ignore[assignment]
    client.complete(MESSAGES)
    kinds = [e.kind for e in events]
    assert "PROVIDER_FALLBACK" in kinds
    fallback_event = events[kinds.index("PROVIDER_FALLBACK")]
    assert fallback_event.details["from_route"] == f"nvidia/{NVIDIA_MODEL}"
    assert fallback_event.details["to_route"] == f"openrouter/{FLASH_MODEL}"


# --- eligibility unit table -------------------------------------------------


def test_fallback_eligibility_table() -> None:
    from doom_team.inference import InferenceFailure

    assert fallback_eligible(InferenceFailure("timeout", "t"))
    assert fallback_eligible(InferenceFailure("overload", "o", 429))
    assert fallback_eligible(InferenceFailure("overload", "o", 503))
    assert fallback_eligible(InferenceFailure("overload", "network"))
    for status in (408, 500, 502, 504):
        assert fallback_eligible(InferenceFailure("rejection", "r", status))
    for status in (400, 401, 403, 404, 422):
        assert not fallback_eligible(InferenceFailure("rejection", "r", status))
    assert not fallback_eligible(InferenceFailure("malformed", "m"))


# --- C1/C2/C5: independent credentials per route ----------------------------


def test_each_route_uses_only_its_own_credential() -> None:
    client, nvidia, flash = _routed("timeout")
    client.complete(MESSAGES)
    assert nvidia.auth() == "Bearer nvidia-test-credential"
    assert flash.auth() == "Bearer openrouter-test-credential"


# --- config surface ---------------------------------------------------------


def _base_kwargs(tmp_path: Any) -> dict[str, Any]:
    return {
        "state_dir": str(tmp_path),
        "model_provider": "nvidia",
        "model_name": NVIDIA_MODEL,
        "provider_base_url": "https://integrate.api.nvidia.com",
    }


def _fallback_kwargs() -> dict[str, Any]:
    return {
        "fallback_provider": "openrouter",
        "fallback_model_name": FLASH_MODEL,
        "fallback_base_url": "https://openrouter.ai/api",
        "fallback_api_key_env": "FALLBACK_TEST_API_KEY",
    }


def test_fallback_settings_resolve_as_a_unit(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "fallback-secret")
    settings = ConfigLoader().load(**_base_kwargs(tmp_path), **_fallback_kwargs())
    assert settings.fallback_provider == "openrouter"
    assert settings.fallback_model_name == FLASH_MODEL
    assert set(settings.redaction_secrets()) == {"primary-secret", "fallback-secret"}
    assert settings.redact("x fallback-secret y") == "x [REDACTED] y"
    assert "fallback-secret" not in settings.log_line()
    assert "fallback-secret" not in str(settings.as_evidence_dict())


@pytest.mark.parametrize("missing", list(_fallback_kwargs()))
def test_partial_fallback_configuration_fails_closed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "fallback-secret")
    kwargs = _fallback_kwargs()
    kwargs.pop(missing)
    with pytest.raises(ConfigError, match="fallback"):
        ConfigLoader().load(**_base_kwargs(tmp_path), **kwargs)


def test_missing_fallback_credential_fails_closed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.delenv("FALLBACK_TEST_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="FALLBACK_TEST_API_KEY"):
        ConfigLoader().load(**_base_kwargs(tmp_path), **_fallback_kwargs())


def test_insensitive_fallback_credential_name_rejected(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.setenv("HARMLESS_NAME", "fallback-secret")
    kwargs = _fallback_kwargs() | {"fallback_api_key_env": "HARMLESS_NAME"}
    with pytest.raises(ConfigError, match="stripped"):
        ConfigLoader().load(**_base_kwargs(tmp_path), **kwargs)


# --- C3: missing PRIMARY credential is loud, never a fallback ---------------


def test_missing_primary_credential_is_a_visible_config_error(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOOM_API_KEY", raising=False)
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "fallback-secret")
    with pytest.raises(ConfigError, match="DOOM_API_KEY"):
        ConfigLoader().load(**_base_kwargs(tmp_path), **_fallback_kwargs())


# --- factory wiring ---------------------------------------------------------


def test_factory_builds_routed_client_when_fallback_configured(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "fallback-secret")
    settings = ConfigLoader().load(**_base_kwargs(tmp_path), **_fallback_kwargs())
    client = build_inference_client(settings)
    assert isinstance(client, RoutedInferenceClient)


def test_factory_stays_single_route_without_fallback(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    settings = ConfigLoader().load(**_base_kwargs(tmp_path))
    client = build_inference_client(settings)
    assert isinstance(client, OpenAICompatibleInferenceClient)


def test_factory_attaches_nvidia_profile_only_to_nvidia_route(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "fallback-secret")
    settings = ConfigLoader().load(**_base_kwargs(tmp_path), **_fallback_kwargs())
    client = build_inference_client(settings)
    assert isinstance(client, RoutedInferenceClient)
    assert client._primary._extra_body == NVIDIA_REQUEST_PROFILE
    assert client._primary._routing is None
    assert client._fallback._extra_body == {}
    assert client._fallback._routing is not None
    # Each route holds ITS OWN credential - a swap here would authenticate
    # the fallback with the primary's key and pass every behavioral test.
    assert client._primary._api_key == "primary-secret"
    assert client._fallback._api_key == "fallback-secret"
    assert client._primary._base_url == "https://integrate.api.nvidia.com"
    assert client._fallback._base_url == "https://openrouter.ai/api"


def test_fallback_settings_resolve_from_environment(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "fallback-secret")
    monkeypatch.setenv("DOOM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.setenv("DOOM_FALLBACK_MODEL_NAME", FLASH_MODEL)
    monkeypatch.setenv("DOOM_FALLBACK_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setenv("DOOM_FALLBACK_API_KEY_ENV", "FALLBACK_TEST_API_KEY")
    settings = ConfigLoader().load(**_base_kwargs(tmp_path))
    assert settings.fallback_provider == "openrouter"
    assert settings.fallback_model_name == FLASH_MODEL
    assert settings.fallback_base_url == "https://openrouter.ai/api"
    assert settings.fallback_api_key_env == "FALLBACK_TEST_API_KEY"


# --- mid-response availability failures normalize and fall back -------------


class DyingResponse:
    """A 200 whose body dies while being read."""

    def __init__(self, exc: BaseException) -> None:
        self.status = 200
        self._exc = exc

    def read(self) -> bytes:
        raise self._exc

    def __enter__(self) -> "DyingResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _routed_with_dying_primary(
    exc: BaseException,
) -> tuple[RoutedInferenceClient, list[Any], RouteRecorder]:
    calls: list[Any] = []

    def dying_urlopen(req: Any, timeout: float | None = None) -> DyingResponse:
        calls.append(req)
        return DyingResponse(exc)

    flash = RouteRecorder("success", FLASH_MODEL, provider="DeepInfra")
    primary = OpenAICompatibleInferenceClient(
        "https://integrate.api.nvidia.com", "k1", NVIDIA_MODEL, urlopen=dying_urlopen
    )
    fallback = OpenAICompatibleInferenceClient(
        "https://openrouter.ai/api", "k2", FLASH_MODEL, urlopen=flash
    )
    return (
        RoutedInferenceClient(
            primary, fallback, primary_label="nvidia", fallback_label="openrouter"
        ),
        calls,
        flash,
    )


@pytest.mark.parametrize(
    "exc",
    [ConnectionResetError("reset mid-body"), __import__("http.client", fromlist=["x"]).IncompleteRead(b"partial")],
    ids=["connection-reset", "incomplete-read"],
)
def test_mid_response_availability_failure_falls_back(exc: BaseException) -> None:
    client, calls, flash = _routed_with_dying_primary(exc)
    response = client.complete(MESSAGES)
    assert len(calls) == 1
    assert len(flash.requests) == 1
    assert response.model == FLASH_MODEL


def test_mid_response_decode_failure_is_malformed_not_fallback() -> None:
    exc = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    client, calls, flash = _routed_with_dying_primary(exc)
    with pytest.raises(InferenceError) as excinfo:
        client.complete(MESSAGES)
    assert excinfo.value.failure.kind == "malformed"
    assert len(flash.requests) == 0


# --- observability must never break the call between routes -----------------


def test_broken_event_sink_does_not_forfeit_the_fallback() -> None:
    class ExplodingSink:
        def record(self, event: Any) -> None:
            raise OSError("disk full")

    client, nvidia, flash = _routed("503")
    client._event_sink = ExplodingSink()  # type: ignore[assignment]
    response = client.complete(MESSAGES)
    assert len(flash.requests) == 1
    assert response.model == FLASH_MODEL


# --- redaction ordering and base-url hygiene --------------------------------


def test_overlapping_secrets_redact_longest_first(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The primary key is a SUBSTRING of the fallback key: replacing it first
    # would split the fallback key around the marker, leaving residue.
    monkeypatch.setenv("DOOM_API_KEY", "sk-shared")
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "sk-shared-longer-fallback-key")
    settings = ConfigLoader().load(**_base_kwargs(tmp_path), **_fallback_kwargs())
    redacted = settings.redact("x sk-shared-longer-fallback-key y sk-shared z")
    assert "sk-shared" not in redacted
    assert "-longer-fallback-key" not in redacted
    assert settings.redaction_secrets()[0] == "sk-shared-longer-fallback-key"


@pytest.mark.parametrize("bad", [
    "https://openrouter.ai/api?key=oops",
    "https://openrouter.ai/api#frag",
])
def test_base_url_query_or_fragment_rejected(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "primary-secret")
    monkeypatch.setenv("FALLBACK_TEST_API_KEY", "fallback-secret")
    with pytest.raises(ConfigError, match="query string or fragment"):
        ConfigLoader().load(
            **_base_kwargs(tmp_path),
            **(_fallback_kwargs() | {"fallback_base_url": bad}),
        )
