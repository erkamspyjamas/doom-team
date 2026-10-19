"""Explicit reasoning, endpoint restrictions, and cross-model quality gates."""
import json

import pytest

from doom_team.config import ConfigError, ConfigLoader
from doom_team.inference import (
    NVIDIA_REQUEST_PROFILE,
    RoutedInferenceClient,
    build_inference_client,
    build_quality_inference_client,
)
from doom_team.routing import route_task, specialist_by_id
from doom_team.task_runner import ProductionTaskRunner
from tests.test_inference import FakeResponse
from tests.test_provider_routing import RouteRecorder
from tests.test_task_runner import (
    BLOCK_VERDICT, PASS_VERDICT, VALIDATION, ScriptedInference,
    implementer_json, make_task, make_workspace,
)


def settings_for(tmp_path, monkeypatch, **overrides):
    monkeypatch.setenv("OPENROUTER_TEST_API_KEY", "primary-test-key")
    monkeypatch.setenv("QUALITY_TEST_API_KEY", "quality-test-key")
    values = dict(
        state_dir=str(tmp_path), model_provider="openrouter", model_name="z-ai/glm-5.3-flash",
        provider_base_url="https://openrouter.ai/api", provider_api_key_env="OPENROUTER_TEST_API_KEY",
        fallback_provider="openrouter", fallback_model_name="deepseek/deepseek-v4.1-flash-1017",
        fallback_base_url="https://openrouter.ai/api", fallback_api_key_env="QUALITY_TEST_API_KEY",
        provider_only="z-ai/fp8,siliconflow/fp8", fallback_provider_only="deepinfra/fp8",
        openrouter_reasoning_effort="high", openrouter_max_tokens=65536,
        openrouter_json_mode=True, quality_model_route="fallback",
    )
    values.update(overrides)
    return ConfigLoader().load(**values)


def test_both_routes_enable_reasoning_and_require_compatible_endpoints(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, monkeypatch)
    client = build_inference_client(settings)
    assert isinstance(client, RoutedInferenceClient)
    for route, only in ((client._primary, ["z-ai/fp8", "siliconflow/fp8"]),
                        (client._fallback, ["deepinfra/fp8"])):
        recorder = RouteRecorder("success", route._model)
        route._urlopen = recorder
        route.complete([{"role": "user", "content": "Return JSON"}])
        payload = recorder.body()
        assert payload["reasoning"] == {"enabled": True, "effort": "high", "exclude": True}
        assert payload["max_tokens"] == 65536
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["provider"]["only"] == only
        assert payload["provider"]["require_parameters"] is True
        assert "reasoning_effort" not in payload


def test_quality_route_swaps_whole_routes_and_fails_over_only_once(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, monkeypatch)
    client = build_quality_inference_client(settings)
    assert isinstance(client, RoutedInferenceClient)
    first = RouteRecorder("429", settings.fallback_model_name)
    second = RouteRecorder("success", settings.model_name)
    client._primary._urlopen, client._fallback._urlopen = first, second
    result = client.complete([{"role": "user", "content": "Review JSON"}])
    assert result.model == settings.model_name
    assert len(first.requests) == len(second.requests) == 1
    assert first.auth() == "Bearer quality-test-key"
    assert second.auth() == "Bearer primary-test-key"
    assert first.body()["provider"]["only"] == ["deepinfra/fp8"]
    assert second.body()["provider"]["only"] == ["z-ai/fp8", "siliconflow/fp8"]
    assert first.body()["reasoning"] == second.body()["reasoning"]


@pytest.mark.parametrize("field,value", [
    ("openrouter_reasoning_effort", "none"), ("openrouter_reasoning_effort", "High"),
    ("openrouter_reasoning_effort", "medium"), ("openrouter_max_tokens", True),
    ("openrouter_max_tokens", 0), ("openrouter_max_tokens", 1023),
    ("openrouter_max_tokens", 262145), ("openrouter_max_tokens", 8192.5),
    ("openrouter_json_mode", "maybe"), ("openrouter_json_mode", 1),
    ("provider_only", "unsafe endpoint"), ("fallback_provider_only", [""]),
    ("quality_model_route", "automatic"),
])
def test_new_profile_settings_fail_closed(tmp_path, monkeypatch, field, value):
    with pytest.raises(ConfigError):
        settings_for(tmp_path, monkeypatch, **{field: value})


def test_new_profile_resolves_environment_and_config_precedence(tmp_path, monkeypatch):
    cfg = tmp_path / "profile.json"
    cfg.write_text(json.dumps({"openrouter_reasoning_effort": "low", "openrouter_max_tokens": 32768,
                               "provider_only": ["z-ai/fp8"], "openrouter_json_mode": True}))
    monkeypatch.setenv("DOOM_OPENROUTER_REASONING_EFFORT", "max")
    settings = settings_for(tmp_path, monkeypatch, config_file=cfg,
                            openrouter_reasoning_effort=None, openrouter_max_tokens=None,
                            openrouter_json_mode=None, provider_only=None)
    assert settings.openrouter_reasoning_effort == "max"
    assert settings.openrouter_max_tokens == 32768
    assert settings.openrouter_json_mode is True
    assert settings.provider_only == ("z-ai/fp8",)
    settings = settings_for(tmp_path, monkeypatch, config_file=cfg, openrouter_reasoning_effort="high")
    assert settings.openrouter_reasoning_effort == "high"
    assert "primary-test-key" not in json.dumps(settings.as_evidence_dict())
    assert "quality-test-key" not in settings.log_line()


def test_quality_fallback_requires_a_real_configured_route(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="require a fallback route"):
        settings_for(tmp_path, monkeypatch, fallback_provider="", fallback_model_name="",
                     fallback_base_url=None, fallback_api_key_env="", fallback_provider_only="")


def test_openrouter_settings_do_not_change_the_nvidia_profile(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, monkeypatch, model_provider="nvidia")
    client = build_inference_client(settings)
    assert isinstance(client, RoutedInferenceClient)
    assert client._primary._extra_body == NVIDIA_REQUEST_PROFILE
    assert "reasoning" in client._fallback._extra_body


@pytest.mark.parametrize("qa,expected,quality_calls", [
    (PASS_VERDICT, "accepted", 2), (BLOCK_VERDICT, "qa_blocked", 1),
])
def test_actual_task_gates_use_the_separate_quality_client(tmp_path, qa, expected, quality_calls):
    task = make_task()
    decision = route_task(task)
    _, workspace = make_workspace(tmp_path)
    implementation = ScriptedInference(implementer_json())
    quality = ScriptedInference(qa, PASS_VERDICT)
    runner = ProductionTaskRunner(implementation, validation_commands=VALIDATION, quality_inference=quality)
    result = runner.run_task(task, decision, specialist_by_id(decision.specialist_id), workspace)
    assert result.status == expected
    assert len(implementation.calls) == 1
    assert len(quality.calls) == quality_calls
    assert "independent QA" in quality.calls[0][0]["content"]
    if quality_calls == 2:
        assert "final reviewer" in quality.calls[1][0]["content"]


def test_usage_records_metrics_without_reasoning_text(tmp_path, monkeypatch):
    from doom_team.observability import DurableEventSink

    settings = settings_for(tmp_path, monkeypatch)
    client = build_inference_client(settings, event_sink=DurableEventSink(tmp_path))
    assert isinstance(client, RoutedInferenceClient)
    payload = {"model": "served-model", "provider": "Z.AI",
               "choices": [{"message": {"content": "{}", "reasoning": "PRIVATE_THOUGHT"},
                            "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 12, "completion_tokens": 40, "total_tokens": 52,
                         "cost": .0001, "completion_tokens_details": {"reasoning_tokens": 30}}}
    client._primary._urlopen = lambda *a, **kw: FakeResponse(200, json.dumps(payload).encode())
    assert client.complete([{"role": "user", "content": "Return JSON"}]).content == "{}"
    text = (tmp_path / "events/events.jsonl").read_text()
    assert "PRIVATE_THOUGHT" not in text
    event = json.loads(text)
    assert event["kind"] == "PROVIDER_USAGE"
    assert event["details"]["reasoning_tokens"] == 30
    assert event["details"]["cost_usd"] == .0001


def test_bad_usage_metadata_does_not_turn_success_into_failure(tmp_path, monkeypatch):
    from doom_team.observability import DurableEventSink

    client = build_inference_client(settings_for(tmp_path, monkeypatch), event_sink=DurableEventSink(tmp_path))
    payload = {"choices": [{"message": {"content": "{}"}}],
               "usage": {"completion_tokens_details": ["malformed metadata"]}}
    client._primary._urlopen = lambda *a, **kw: FakeResponse(200, json.dumps(payload).encode())
    assert client.complete([{"role": "user", "content": "Return JSON"}]).content == "{}"


def test_operator_launcher_strips_unrelated_credentials_and_old_settings():
    from runbooks.run_openrouter import build_environment

    env = build_environment({"DOOM_MODEL_PROVIDER": "openrouter"}, {
        "HOME": "/tmp", "OPENROUTER_API_KEY": "test-router-key", "NVIDIA_API_KEY": "old-test-key",
        "PYTHONPATH": "unsafe", "DOOM_MODEL_PROVIDER": "nvidia", "DOOM_STATE_DIR": "/tmp/state",
    })
    assert env["DOOM_MODEL_PROVIDER"] == "openrouter"
    assert env["DOOM_STATE_DIR"] == "/tmp/state"
    assert "NVIDIA_API_KEY" not in env and "PYTHONPATH" not in env
    with pytest.raises(ValueError):
        build_environment({"DOOM_API_KEY": "forbidden"}, {"OPENROUTER_API_KEY": "test"})
