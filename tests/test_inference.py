"""Tests for the production inference adapter."""

import json
import urllib.error
from collections.abc import Callable
from typing import Any, Self

import pytest

from doom_team.config import ConfigLoader
from doom_team.inference import (
    InferenceError,
    InferenceResponse,
    OpenAICompatibleInferenceClient,
    ProviderRouting,
)
from doom_team.ports import InferencePort


class FakeResponse:
    """Minimal file-like object with a status and a body."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def make_fake_urlopen(state: dict[str, Any]) -> Callable[..., Any]:
    """Return a urlopen-compatible callable that simulates provider responses."""

    def fake_urlopen(req: Any, timeout: float | None = None) -> FakeResponse:
        behavior = state["behavior"]
        if behavior == "success":
            body = json.dumps(
                {
                    "id": "chatcmpl-123",
                    "object": "chat.completion",
                    "model": "deepseek-v4",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Hello!",
                                "reasoning_content": "secret reasoning",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                    "provider": {"name": "CoreWeave"},
                }
            ).encode()
            return FakeResponse(200, body)
        if behavior == "truncated":
            return FakeResponse(200, json.dumps({"choices": []}).encode())
        if behavior == "overload":
            raise urllib.error.HTTPError(req.full_url, 429, "overloaded", {}, None)
        if behavior == "rejection":
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)
        if behavior == "malformed":
            return FakeResponse(200, b"not json")
        if behavior == "timeout":
            raise urllib.error.URLError(TimeoutError("timed out"))
        raise urllib.error.URLError(OSError("network unreachable"))

    return fake_urlopen


def _make_client(
    state: dict[str, Any], *, timeout: float = 1.0, routing: ProviderRouting | None = None
) -> OpenAICompatibleInferenceClient:
    return OpenAICompatibleInferenceClient(
        base_url="http://fake.invalid",
        api_key="test-key",
        model="test-model",
        timeout=timeout,
        routing=routing,
        urlopen=make_fake_urlopen(state),
    )


def test_success_response_with_provenance() -> None:
    state = {"behavior": "success"}
    client = _make_client(state)
    response = client.complete([{"role": "user", "content": "hi"}])
    assert isinstance(response, InferenceResponse)
    assert response.content == "Hello!"
    assert response.model == "deepseek-v4"
    assert response.provider == "CoreWeave"
    assert response.prompt_tokens == 10
    assert response.completion_tokens == 5
    assert response.total_tokens == 15
    assert "secret reasoning" not in response.content


def test_timeout_normalized() -> None:
    state = {"behavior": "timeout"}
    client = _make_client(state, timeout=0.1)
    with pytest.raises(InferenceError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.failure.kind == "timeout"


def test_overload_normalized() -> None:
    state = {"behavior": "overload"}
    client = _make_client(state)
    with pytest.raises(InferenceError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.failure.kind == "overload"


def test_rejection_normalized() -> None:
    state = {"behavior": "rejection"}
    client = _make_client(state)
    with pytest.raises(InferenceError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.failure.kind == "rejection"


def test_malformed_normalized() -> None:
    state = {"behavior": "malformed"}
    client = _make_client(state)
    with pytest.raises(InferenceError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.failure.kind == "malformed"


def test_truncated_response_normalized() -> None:
    state = {"behavior": "truncated"}
    client = _make_client(state)
    with pytest.raises(InferenceError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.failure.kind == "malformed"


def test_provider_routing_defaults() -> None:
    routing = ProviderRouting()
    payload = routing.to_payload()
    assert payload["order"] == ["CoreWeave", "DeepInfra", "SiliconFlow"]
    assert payload["ignore"] == ["Relace"]
    assert payload["allow_fallbacks"] is True


def test_config_provider_order_default(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    settings = ConfigLoader().load(
        state_dir=str(tmp_path / "state"),
        model_provider="nvidia",
        model_name="m",
    )
    assert settings.provider_order == ("CoreWeave", "DeepInfra", "SiliconFlow")
    assert settings.forbidden_providers == ("Relace",)
    assert settings.allow_fallbacks is True
    assert settings.request_timeout == 60.0


def test_config_provider_order_from_env(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    monkeypatch.setenv("DOOM_PROVIDER_ORDER", "A,B")
    settings = ConfigLoader().load(
        state_dir=str(tmp_path / "state"),
        model_provider="openrouter",
        model_name="m",
    )
    assert settings.provider_order == ("A", "B")


def test_client_implements_inference_port() -> None:
    client = _make_client({"behavior": "success"})
    assert isinstance(client, InferencePort)


# build_inference_client factory (T1): the single canonical construction seam


def _factory_settings(tmp_path, monkeypatch, **overrides):
    from doom_team.config import ConfigLoader

    monkeypatch.setenv("DOOM_API_KEY", "factory-secret")
    kwargs = {
        "state_dir": str(tmp_path / "state"),
        "model_provider": "openrouter",
        "model_name": "factory-model",
        "provider_base_url": "https://router.example/api",
    }
    kwargs.update(overrides)
    return ConfigLoader().load(**kwargs)


def test_factory_constructs_existing_client_type(tmp_path, monkeypatch) -> None:
    from doom_team.inference import build_inference_client

    client = build_inference_client(_factory_settings(tmp_path, monkeypatch))
    assert isinstance(client, OpenAICompatibleInferenceClient)


def test_factory_preserves_settings_configuration(tmp_path, monkeypatch) -> None:
    from doom_team.inference import build_inference_client

    settings = _factory_settings(
        tmp_path,
        monkeypatch,
        request_timeout=12.5,
        provider_order="Alpha,Beta",
        forbidden_providers="Gamma",
        allow_fallbacks=False,
    )
    client = build_inference_client(settings)
    assert client._base_url == "https://router.example/api"
    assert client._model == "factory-model"
    assert client._timeout == 12.5
    assert client._routing is not None
    assert client._routing.order == ("Alpha", "Beta")
    assert client._routing.forbidden == ("Gamma",)
    assert client._routing.allow_fallbacks is False


def test_factory_applies_custom_base_url(tmp_path, monkeypatch) -> None:
    from doom_team.inference import build_inference_client

    settings = _factory_settings(
        tmp_path, monkeypatch, provider_base_url="https://custom.example/v9/"
    )
    client = build_inference_client(settings)
    assert client._base_url == "https://custom.example/v9"


def test_factory_fails_closed_without_base_url(tmp_path, monkeypatch) -> None:
    from doom_team.config import ConfigError
    from doom_team.inference import build_inference_client

    settings = _factory_settings(tmp_path, monkeypatch, provider_base_url=None)
    assert settings.provider_base_url is None
    with pytest.raises(ConfigError, match="provider_base_url"):
        build_inference_client(settings)


def test_factory_performs_no_network_call(tmp_path, monkeypatch) -> None:
    import urllib.request

    from doom_team.inference import build_inference_client

    def _explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("network call during construction")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    client = build_inference_client(_factory_settings(tmp_path, monkeypatch))
    assert isinstance(client, OpenAICompatibleInferenceClient)


def test_factory_credential_not_in_ordinary_serialization(tmp_path, monkeypatch) -> None:
    import json as _json

    from doom_team.inference import build_inference_client

    settings = _factory_settings(tmp_path, monkeypatch)
    build_inference_client(settings)
    assert "factory-secret" not in _json.dumps(settings.as_evidence_dict())
    assert "factory-secret" not in settings.log_line()


def test_factory_omits_openrouter_routing_for_other_providers(
    tmp_path, monkeypatch
) -> None:
    from doom_team.inference import build_inference_client

    settings = _factory_settings(tmp_path, monkeypatch, model_provider="internal-gateway")
    client = build_inference_client(settings)
    assert client._routing is None
