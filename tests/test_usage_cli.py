"""CLI tests for the read-only offline ``usage`` command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from doom_team import cli


EMPTY_REPORT = {
    "schema_version": 1,
    "successful_calls": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "reasoning_tokens": None,
    "known_cost_usd": None,
    "calls_without_cost": 0,
    "calls_without_reasoning_count": 0,
    "provider_errors": 0,
    "fallback_events": 0,
    "length_finishes": 0,
    "by_model": [],
}


def _usage_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "kind": "PROVIDER_USAGE",
        "provider": "alpha",
        "model": "a",
        "prompt_tokens": 1,
        "completion_tokens": 2,
    }
    event.update(overrides)
    return event


def _write_state(root: Path, events: list[dict[str, Any]] | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if events is not None:
        events_dir = root / "events"
        events_dir.mkdir()
        (events_dir / "events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
    return root


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _snapshot(root: Path) -> dict[str, tuple[str, int | None, bytes | None]]:
    snapshot: dict[str, tuple[str, int | None, bytes | None]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            path = Path(dirpath) / name
            snapshot[str(path.relative_to(root))] = ("dir", None, None)
        for name in filenames:
            path = Path(dirpath) / name
            snapshot[str(path.relative_to(root))] = (
                "file",
                path.stat().st_mtime_ns,
                path.read_bytes(),
            )
    return snapshot


def test_usage_json_aggregates_and_sorts_by_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _write_state(
        tmp_path / "state",
        [
            {"kind": "LEGACY_EVENT", "details": {"ignored": True}},
            _usage_event(
                provider="zeta",
                model="m2",
                prompt_tokens=3,
                completion_tokens=4,
                reasoning_tokens=1,
                cost_usd=0.5,
                finish_reason="length",
            ),
            {"kind": "PROVIDER_ERROR", "details": {"error": "boom"}},
            _usage_event(
                provider="alpha", model="b", prompt_tokens=1, completion_tokens=2
            ),
            {"kind": "PROVIDER_FALLBACK", "details": {"from": "x", "to": "y"}},
            _usage_event(
                provider="alpha",
                model="a",
                prompt_tokens=5,
                completion_tokens=6,
                reasoning_tokens=0,
                cost_usd=0.0,
                finish_reason="length",
            ),
        ],
    )

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)

    assert code == 0
    assert err == ""
    assert len(out.strip().splitlines()) == 1
    data = json.loads(out)
    assert data["schema_version"] == 1
    assert set(data) == {
        "schema_version",
        "successful_calls",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "known_cost_usd",
        "calls_without_cost",
        "calls_without_reasoning_count",
        "provider_errors",
        "fallback_events",
        "length_finishes",
        "by_model",
    }
    assert data["successful_calls"] == 3
    assert data["prompt_tokens"] == 9
    assert data["completion_tokens"] == 12
    assert data["reasoning_tokens"] == 1
    assert data["known_cost_usd"] == pytest.approx(0.5)
    assert data["calls_without_cost"] == 1
    assert data["calls_without_reasoning_count"] == 1
    assert data["provider_errors"] == 1
    assert data["fallback_events"] == 1
    assert data["length_finishes"] == 2
    assert [(row["provider"], row["model"]) for row in data["by_model"]] == [
        ("alpha", "a"),
        ("alpha", "b"),
        ("zeta", "m2"),
    ]
    assert set(data["by_model"][0]) == {
        "provider",
        "model",
        "successful_calls",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "known_cost_usd",
        "calls_without_cost",
        "calls_without_reasoning_count",
        "length_finishes",
    }
    assert data["by_model"][1]["reasoning_tokens"] is None
    assert data["by_model"][1]["known_cost_usd"] is None
    assert data["by_model"][1]["calls_without_cost"] == 1
    assert data["by_model"][1]["calls_without_reasoning_count"] == 1


def test_state_dir_before_subcommand(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _write_state(tmp_path / "state", [_usage_event()])
    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)
    assert code == 0
    assert err == ""
    assert json.loads(out)["successful_calls"] == 1


def test_state_dir_after_subcommand(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _write_state(tmp_path / "state", [_usage_event()])
    code, out, err = _run(["usage", "--state-dir", str(state), "--json"], capsys)
    assert code == 0
    assert err == ""
    assert json.loads(out)["successful_calls"] == 1


def test_explicit_state_dir_overrides_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    explicit = _write_state(
        tmp_path / "explicit", [_usage_event(provider="explicit-provider")]
    )
    environment = _write_state(
        tmp_path / "environment", [_usage_event(provider="environment-provider")]
    )
    monkeypatch.setenv("DOOM_STATE_DIR", str(environment))

    code, out, err = _run(["--state-dir", str(explicit), "usage", "--json"], capsys)

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert [row["provider"] for row in data["by_model"]] == ["explicit-provider"]


def test_state_dir_after_subcommand_overrides_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    explicit = _write_state(
        tmp_path / "explicit", [_usage_event(provider="explicit-provider")]
    )
    environment = _write_state(
        tmp_path / "environment", [_usage_event(provider="environment-provider")]
    )
    monkeypatch.setenv("DOOM_STATE_DIR", str(environment))

    code, out, err = _run(["usage", "--state-dir", str(explicit), "--json"], capsys)

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert [row["provider"] for row in data["by_model"]] == ["explicit-provider"]


def test_environment_state_dir_is_used_when_cli_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment = _write_state(
        tmp_path / "environment", [_usage_event(provider="environment-provider")]
    )
    monkeypatch.setenv("DOOM_STATE_DIR", str(environment))

    code, out, err = _run(["usage", "--json"], capsys)

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert [row["provider"] for row in data["by_model"]] == ["environment-provider"]


def test_missing_state_selection_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DOOM_STATE_DIR", raising=False)

    code, out, err = _run(["usage", "--json"], capsys)

    assert code == 1
    assert out == ""
    assert "state_dir" in err.lower()


def test_missing_state_directory_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"

    code, out, err = _run(["--state-dir", str(missing), "usage", "--json"], capsys)

    assert code == 2
    assert out == ""
    assert "state directory" in err
    assert not missing.exists()


def test_non_directory_state_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_file = tmp_path / "state-file"
    state_file.write_text("not a directory", encoding="utf-8")

    code, out, err = _run(["--state-dir", str(state_file), "usage", "--json"], capsys)

    assert code == 2
    assert out == ""
    assert "state directory" in err


def test_existing_state_without_event_log_is_empty_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    state.mkdir()

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)

    assert code == 0
    assert err == ""
    assert json.loads(out) == EMPTY_REPORT


def test_empty_report_text_marks_unknown_cost_and_reasoning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    state.mkdir()

    code, out, err = _run(["--state-dir", str(state), "usage"], capsys)

    assert code == 0
    assert err == ""
    assert "known_cost_usd: unknown" in out
    assert "reasoning_tokens: unknown" in out
    assert "calls_without_cost: 0" in out
    assert "calls_without_reasoning_count: 0" in out
    assert "by_model: 0" in out


def test_malformed_event_json_exits_2_without_echoing_contents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    events_dir = state / "events"
    events_dir.mkdir(parents=True)
    secret = "RAW-SECRET-CONTENT"
    (events_dir / "events.jsonl").write_text(
        '{"kind": "PROVIDER_USAGE", "prompt_tokens": 1, "secret": "'
        + secret
        + '"',
        encoding="utf-8",
    )

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)

    assert code == 2
    assert out == ""
    assert "line 1" in err
    assert secret not in err
    assert "{" not in err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_tokens", -1),
        ("completion_tokens", True),
        ("reasoning_tokens", 1.5),
        ("cost_usd", -0.1),
        ("cost_usd", True),
    ],
)
def test_invalid_usage_values_exit_2_without_echoing_contents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: Any,
) -> None:
    state = _write_state(tmp_path / "state", [_usage_event(**{field: value})])

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)

    assert code == 2
    assert out == ""
    assert field in err
    assert "{" not in err


def test_unknown_cost_and_reasoning_remain_null_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _write_state(
        tmp_path / "state",
        [
            _usage_event(),
            _usage_event(prompt_tokens=3, completion_tokens=4),
        ],
    )

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["known_cost_usd"] is None
    assert data["reasoning_tokens"] is None
    assert data["calls_without_cost"] == 2
    assert data["calls_without_reasoning_count"] == 2
    row = data["by_model"][0]
    assert row["known_cost_usd"] is None
    assert row["reasoning_tokens"] is None


def test_explicit_zero_cost_and_reasoning_are_known(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _write_state(
        tmp_path / "state", [_usage_event(cost_usd=0, reasoning_tokens=0)]
    )

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["known_cost_usd"] == 0.0
    assert data["reasoning_tokens"] == 0
    assert data["calls_without_cost"] == 0
    assert data["calls_without_reasoning_count"] == 0


def test_text_output_labels_and_does_not_leak_arbitrary_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets = [
        "SECRET-PROMPT",
        "SECRET-REASONING",
        "SECRET-KEY",
        "SECRET-RAW",
        "SHOULD-NOT-APPEAR",
    ]
    state = _write_state(
        tmp_path / "state",
        [
            _usage_event(
                provider="alpha",
                model="a",
                prompt_tokens=1,
                completion_tokens=2,
                reasoning_tokens=3,
                cost_usd=0.25,
                details={
                    "prompt": secrets[0],
                    "reasoning": secrets[1],
                    "api_key": secrets[2],
                    "raw_response": secrets[3],
                    "arbitrary": secrets[4],
                },
            ),
            {"kind": "PROVIDER_ERROR", "details": {"error": secrets[3]}},
            {"kind": "PROVIDER_FALLBACK", "details": {"reason": secrets[1]}},
        ],
    )

    code, out, err = _run(["--state-dir", str(state), "usage"], capsys)

    assert code == 0
    assert err == ""
    for label in (
        "known_cost_usd: 0.250000",
        "calls_without_cost: 0",
        "calls_without_reasoning_count: 0",
        "provider_errors: 1",
        "fallback_events: 1",
        "by_model: 1",
        "provider=alpha",
        "model=a",
        "successful_calls=1",
        "prompt_tokens=1",
        "completion_tokens=2",
        "reasoning_tokens=3",
    ):
        assert label in out
    for secret in secrets:
        assert secret not in out
        assert secret not in err


def test_usage_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _write_state(
        tmp_path / "state", [_usage_event(cost_usd=0.1, reasoning_tokens=2)]
    )
    before = _snapshot(state)

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)
    assert code == 0
    assert err == ""
    assert _snapshot(state) == before

    code, out, err = _run(["--state-dir", str(state), "usage"], capsys)
    assert code == 0
    assert err == ""
    assert _snapshot(state) == before


def test_usage_does_not_load_provider_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _write_state(tmp_path / "state", [_usage_event()])

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("usage must not load provider settings")

    monkeypatch.setattr(cli, "_load_settings", boom)
    monkeypatch.setattr(cli, "build_inference_client", boom)
    monkeypatch.delenv("DOOM_STATE_DIR", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    code, out, err = _run(["--state-dir", str(state), "usage", "--json"], capsys)

    assert code == 0
    assert err == ""
    assert json.loads(out)["successful_calls"] == 1
