"""Real src-layout candidates must never be tested through the host install."""
from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

import pytest

from doom_team.transport import VALIDATION_POLICIES
from doom_team.validation import run_validation_commands


def packaged_candidate(root: Path, *, broken: bool = False) -> None:
    (root / "src/doom_team").mkdir(parents=True)
    (root / "src/doom_team/__init__.py").write_text("")
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "doom-team"\nversion = "0.0.0"\n'
        '[project.scripts]\ncandidate-usage = "doom_team.usage:main"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
    )
    if broken:
        (root / "src/doom_team/domain.py").write_text(
            "def task_id_is_valid(value):\n    return False\n"
        )
        (root / "test_candidate.py").write_text(
            "from doom_team.domain import task_id_is_valid\n"
            "def test_candidate():\n    assert task_id_is_valid('valid-task')\n"
        )
    else:
        (root / "src/doom_team/usage.py").write_text(
            "CANDIDATE_MARKER = 'new-candidate'\ndef main():\n    print(CANDIDATE_MARKER)\n"
        )
        (root / "test_candidate.py").write_text(
            "from pathlib import Path\nimport sys\nimport os\nimport subprocess\nimport doom_team.usage as usage\n"
            "def test_candidate():\n"
            "    assert usage.CANDIDATE_MARKER == 'new-candidate'\n"
            "    assert Path(usage.__file__).is_relative_to(Path(sys.prefix))\n"
            "    assert sys.prefix != sys.base_prefix\n"
            "    assert not any(key.startswith('DOOM_') for key in os.environ)\n"
            "    child = subprocess.run([sys.executable, '-c', 'from doom_team.usage import main; main()'],\n"
            "                           capture_output=True, text=True, check=True)\n"
            "    assert child.stdout.strip() == 'new-candidate'\n"
            "    cli = subprocess.run(['candidate-usage'], capture_output=True, text=True, check=True)\n"
            "    assert cli.stdout.strip() == 'new-candidate'\n"
        )


def test_src_candidate_installed_separately_from_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged_candidate(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "/irrelevant/inherited/source")
    monkeypatch.setenv("DOOM_STATE_DIR", "/mission/state/must-not-leak")
    monkeypatch.setenv("DOOM_MODEL_PROVIDER", "openrouter")
    results = run_validation_commands(tmp_path, VALIDATION_POLICIES["default"])
    assert all(r.passed for r in results), [r.as_evidence_dict() for r in results]
    assert "1 passed" in results[-1].command.stdout
    assert results[-1].command.argv[0] != sys.executable


def test_broken_candidate_cannot_pass_using_installed_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged_candidate(tmp_path, broken=True)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    from doom_team.domain import task_id_is_valid

    assert task_id_is_valid("valid-task")  # the installed baseline would falsely pass
    results = run_validation_commands(tmp_path, VALIDATION_POLICIES["default"])
    assert not results[-1].passed
    assert "AssertionError" in results[-1].command.stdout
    assert "1 failed" in results[-1].command.stdout


def test_candidate_build_failure_never_falls_back_to_host(tmp_path: Path) -> None:
    packaged_candidate(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="nonexistent_doom_backend"\n'
    )
    results = run_validation_commands(tmp_path, VALIDATION_POLICIES["default"])
    assert any(not r.passed for r in results)
    assert results[-1].label == "package-build"
    assert not any(r.label == "tests" for r in results)


def test_deleted_candidate_package_cannot_reappear_from_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import doom_team.candidate_python as candidate_python

    project = tmp_path / "project"
    packaged_candidate(project, broken=True)
    (project / "src/doom_team/domain.py").unlink()
    (project / "src/doom_team/__init__.py").unlink()
    (project / "src/doom_team").rmdir()
    dependencies = tmp_path / "host-dependencies"
    dependencies.mkdir()
    for entry in Path(sysconfig.get_path("purelib")).iterdir():
        if entry.name == "doom_team":
            continue  # also exercise this fixture from an installed wheel
        (dependencies / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    stale = dependencies / "doom_team"
    stale.mkdir()
    (stale / "__init__.py").write_text("")
    (stale / "domain.py").write_text("def task_id_is_valid(value):\n    return True\n")
    original = sysconfig.get_path

    def dependency_directory(name: str, *args: object, **kwargs: object) -> str:
        if name in ("purelib", "platlib") and not args and not kwargs:
            return str(dependencies)
        return original(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(candidate_python.sysconfig, "get_path", dependency_directory)
    results = run_validation_commands(project, VALIDATION_POLICIES["default"])
    assert not results[-1].passed
    assert results[-1].label == "tests"
    assert "ModuleNotFoundError" in results[-1].command.stdout
