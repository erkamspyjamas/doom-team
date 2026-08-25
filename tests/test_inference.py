"""Tests for the production inference adapter."""

import json
import socket
import urllib.error
from typing import Any, Callable

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

    def __enter__(self) -> "FakeResponse":
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
            raise urllib.error.URLError(socket.timeout("timed out"))
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
