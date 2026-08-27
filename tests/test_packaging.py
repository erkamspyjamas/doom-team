"""Packaging and runtime-contract invariants.

Doom Team runs from an INSTALLED package. The Gas City transport launches each
task attempt as ``sys.executable -m doom_team.worker`` from a per-attempt job
directory, so importability that depends on the current directory - or on a
relative ``PYTHONPATH`` - works for the CLI and then fails in the worker. These
tests pin the declarations that make an installed runtime correct, and the
documentation that tells an operator how to get one.

These tests are deliberately static: they read repository files and resolve
declared targets in the current interpreter. They never build, install,
download, or look at any virtual environment, so they behave identically in a
developer checkout, in CI, and under the quality toolchain (which ships no
``pip``). A temporary real install is not attempted for exactly that reason:
it would require either network access for build isolation or a build backend
present in whatever interpreter happens to run the suite. The operational proof
that an install materializes the console script belongs to
``runbooks/INSTALL.md`` step 4, not to the offline test suite.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import sys
import tomllib
from pathlib import Path

from doom_team.config import Settings, _env_name

REPO_ROOT = Path(__file__).resolve().parents[1]

CONSOLE_SCRIPT = "doom-team"


def _pyproject() -> dict[str, object]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def _scripts() -> dict[str, str]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    scripts = project.get("scripts")
    assert isinstance(scripts, dict), "pyproject declares no [project.scripts]"
    return {str(k): str(v) for k, v in scripts.items()}


def test_pyproject_declares_the_console_script() -> None:
    """The operator entrypoint is a console script, not a module path."""
    assert CONSOLE_SCRIPT in _scripts()


def test_declared_console_script_target_resolves() -> None:
    """A renamed or deleted target must fail here, not at operator install time."""
    target = _scripts()[CONSOLE_SCRIPT]
    module_name, _, attribute = target.partition(":")
    assert module_name and attribute, f"malformed entry point: {target!r}"
    module = importlib.import_module(module_name)
    entry = getattr(module, attribute, None)
    assert callable(entry), f"{target} is not callable"


def test_worker_module_entrypoint_resolves() -> None:
    """`-m doom_team.worker` is a launch contract, so its target must exist."""
    worker = importlib.import_module("doom_team.worker")
    assert callable(getattr(worker, "main", None))


def test_gas_city_worker_runs_under_the_current_interpreter() -> None:
    """The worker must inherit the CLI's interpreter, which holds the package.

    Pinning this default is what makes "install the package" a sufficient
    operator contract: any change to a bare `python` or a hard-coded path would
    let the worker resolve to an interpreter without doom_team installed.
    """
    from doom_team.gas_city_transport import GasCityTaskTransport

    default = inspect.signature(GasCityTaskTransport.__init__).parameters[
        "worker_argv"
    ].default
    assert default == (sys.executable, "-m", "doom_team.worker")


def test_product_source_never_reads_pythonpath() -> None:
    """PYTHONPATH is not product configuration and must not become behavior."""
    offenders = [
        path.relative_to(REPO_ROOT)
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "PYTHONPATH" in path.read_text()
    ]
    assert offenders == [], f"PYTHONPATH referenced in product source: {offenders}"


def test_every_env_mapped_setting_is_documented() -> None:
    """Drift guard: a new setting must reach the Configuration Reference.

    The Gas City settings were shipped without being documented, which left the
    standalone runtime configurable only by reading source.
    """
    reference = (REPO_ROOT / "docs" / "CONFIGURATION.md").read_text()
    undocumented = sorted(
        _env_name(field.name)
        for field in dataclasses.fields(Settings)
        if not field.name.startswith("_") and _env_name(field.name) not in reference
    )
    assert undocumented == [], f"settings missing from CONFIGURATION.md: {undocumented}"


def test_install_runbook_documents_the_installed_runtime() -> None:
    """A fresh operator must learn which environment runs the product."""
    install = (REPO_ROOT / "runbooks" / "INSTALL.md").read_text()
    assert "pip install -e" in install
    assert CONSOLE_SCRIPT in install
    assert "PYTHONPATH" in install, "the runbook must say PYTHONPATH is not required"


def test_operate_runbook_documents_the_mission_lifecycle() -> None:
    """run/resume/approve are the product; they must not be source-only knowledge."""
    operate = (REPO_ROOT / "runbooks" / "OPERATE.md").read_text()
    for subcommand in ("run", "resume", "approve"):
        assert f"{CONSOLE_SCRIPT} {subcommand}" in operate, subcommand
