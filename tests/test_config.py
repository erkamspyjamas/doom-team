"""Tests for typed, validated, secret-safe production configuration."""

import json
import logging
from pathlib import Path

import pytest

from doom_team.config import ConfigError, ConfigLoader


class _RecordHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _valid_kwargs(tmp_path: Path) -> dict[str, str]:
    return {
        "state_dir": str(tmp_path / "state"),
        "model_provider": "nvidia",
        "model_name": "deepseek-v4",
    }


def test_optional_defaults_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    settings = ConfigLoader().load(**_valid_kwargs(tmp_path))
    assert settings.runtime == "local"
    assert settings.semantic_retries == 3
    assert settings.provider_api_key_env == "DOOM_API_KEY"


def test_precedence_explicit_over_env_over_file_over_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "model_provider": "from-file",
                "model_name": "from-file",
                "runtime": "from-file",
                "semantic_retries": 1,
            }
        )
    )
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    monkeypatch.setenv("DOOM_MODEL_PROVIDER", "from-env")
    monkeypatch.setenv("DOOM_RUNTIME", "from-env")
    settings = ConfigLoader().load(
        state_dir=str(tmp_path / "state"),
        model_name="from-args",
        config_file=config_file,
    )
    assert settings.model_provider == "from-env"
    assert settings.model_name == "from-args"
    assert settings.runtime == "from-env"
    assert settings.semantic_retries == 1


def test_config_file_used_for_required_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "state_dir": str(tmp_path / "state"),
                "model_provider": "nvidia",
                "model_name": "deepseek-v4",
            }
        )
    )
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    settings = ConfigLoader().load(config_file=config_file)
    assert settings.state_dir == tmp_path / "state"
    assert settings.model_provider == "nvidia"
    assert settings.model_name == "deepseek-v4"


def test_fail_closed_when_required_setting_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    with pytest.raises(ConfigError, match="model_name"):
        ConfigLoader().load(
            state_dir=str(tmp_path / "state"), model_provider="nvidia"
        )


def test_fail_closed_when_credential_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOOM_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="DOOM_API_KEY"):
        ConfigLoader().load(**_valid_kwargs(tmp_path))


def test_fail_closed_when_credential_env_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "")
    with pytest.raises(ConfigError, match="DOOM_API_KEY"):
        ConfigLoader().load(**_valid_kwargs(tmp_path))


def test_invalid_config_file_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text("not json")
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    with pytest.raises(ConfigError, match="invalid config file"):
        ConfigLoader().load(**_valid_kwargs(tmp_path), config_file=config_file)


def test_invalid_semantic_retries_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    with pytest.raises(ConfigError):
        ConfigLoader().load(**_valid_kwargs(tmp_path), semantic_retries=-1)


def test_invalid_semantic_retries_env_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    monkeypatch.setenv("DOOM_SEMANTIC_RETRIES", "abc")
    with pytest.raises(ConfigError, match="must be an integer"):
        ConfigLoader().load(**_valid_kwargs(tmp_path))


def test_secret_redacted_from_repr_and_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "super-secret-value")
    settings = ConfigLoader().load(**_valid_kwargs(tmp_path))
    assert "super-secret-value" not in repr(settings)
    assert "super-secret-value" not in settings.log_line()
    assert "[REDACTED]" in repr(settings)


def test_secret_not_in_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "hunter2")
    settings = ConfigLoader().load(**_valid_kwargs(tmp_path))
    handler = _RecordHandler()
    logger = logging.getLogger("doom_team.config")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("settings: %s", settings)
        logger.info("log_line: %s", settings.log_line())
    finally:
        logger.removeHandler(handler)
    text = " ".join(record.getMessage() for record in handler.records)
    assert "hunter2" not in text


def test_evidence_dict_contains_no_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "super-secret-value")
    settings = ConfigLoader().load(**_valid_kwargs(tmp_path))
    evidence = settings.as_evidence_dict()
    assert "super-secret-value" not in json.dumps(evidence)
    assert evidence["provider_api_key_env"] == "DOOM_API_KEY"


def test_redact_method_replaces_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "super-secret-value")
    settings = ConfigLoader().load(**_valid_kwargs(tmp_path))
    assert settings.redact("leak super-secret-value") == "leak [REDACTED]"


def test_unknown_env_vars_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    monkeypatch.setenv("DOOM_UNRELATED", "value")
    settings = ConfigLoader().load(**_valid_kwargs(tmp_path))
    assert settings.model_provider == "nvidia"


def test_configuration_reference_document_exists() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    doc = repo_root / "docs" / "CONFIGURATION.md"
    assert doc.is_file()
    content = doc.read_text()
    assert "Precedence" in content
    assert "DOOM_API_KEY" in content
