"""Offline, disposable Python installations for packaged src-layout candidates.

Host test/build dependencies are reused without executing host .pth files or
exposing the host installation of the candidate's own package. This is import
isolation, not a sandbox for the untrusted build backend or test suite.
"""
from __future__ import annotations

import os
import re
import sys
import sysconfig
import tempfile
import venv
import zipfile
from importlib.metadata import packages_distributions
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from doom_team.subprocess_guard import sanitize_env

if TYPE_CHECKING:
    from doom_team.validation import ValidationResult


@dataclass(frozen=True)
class CandidatePython:
    interpreter: Path | None
    environment: dict[str, str]
    preparation: tuple[ValidationResult, ...]


def needs_candidate_install(root: Path, argv: tuple[str, ...]) -> bool:
    """Only the trusted Python pytest policy needs a project installation."""
    return (
        argv[:3] == (sys.executable, "-m", "pytest")
        and (root / "src").is_dir()
        and any((root / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg"))
    )


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _wheel_names(wheel: Path) -> tuple[set[str], set[str]]:
    """Names to exclude from the reused host dependency directory."""
    with zipfile.ZipFile(wheel) as archive:
        names = {name.split("/")[0] for name in archive.namelist()}
    packages = {name.split(".")[0] for name in names if not name.endswith((".dist-info", ".data"))}
    distributions = {
        _normalized(name.removesuffix(".dist-info").rsplit("-", 1)[0])
        for name in names if name.endswith(".dist-info")
    }
    return packages, distributions


def _reuse_dependencies(wheel: Path, target: Path) -> None:
    packages, distributions = _wheel_names(wheel)
    # A candidate may DELETE a formerly installed package. The old module
    # must remain excluded even when it is absent from the new wheel.
    packages.update(
        name for name, owners in packages_distributions().items()
        if any(_normalized(owner) in distributions for owner in owners)
    )
    for directory in {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}:
        for entry in Path(directory).iterdir():
            name = entry.name
            if name.endswith(".pth") or name.startswith(("__editable__", "__pycache__")):
                continue
            if name.split(".")[0] in packages:
                continue
            distribution = _normalized(name.removesuffix(".dist-info").rsplit("-", 1)[0])
            if name.endswith(".dist-info") and distribution in distributions:
                continue
            destination = target / name
            if not destination.exists():
                destination.symlink_to(entry, target_is_directory=entry.is_dir())


@contextmanager
def installed_candidate(root: Path, timeout: float) -> Iterator[CandidatePython]:
    """Build/install once per validation; retain real preparation evidence."""
    from doom_team.validation import ValidationResult, execute_command

    environment = {
        key: value for key, value in sanitize_env().items()
        if not key.startswith(("PYTHON", "PIP_", "DOOM_"))
    }
    # Interpreter overrides are never inputs to the candidate installation.
    environment.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": os.devnull})
    with tempfile.TemporaryDirectory(prefix="doom-candidate-python-") as temporary:
        scratch = Path(temporary)
        wheels = scratch / "wheels"
        wheels.mkdir()
        build = execute_command(
            (sys.executable, "-I", "-m", "pip", "wheel", "--no-index", "--no-deps",
             "--no-build-isolation", "--no-cache-dir", "--wheel-dir", str(wheels), str(root)),
            workspace_root=root, timeout=timeout, environment=environment,
        )
        preparation = [ValidationResult(label="package-build", command=build)]
        if not preparation[-1].passed:
            yield CandidatePython(None, environment, tuple(preparation))
            return
        built = list(wheels.glob("*.whl"))
        if len(built) != 1:
            from doom_team.validation import ValidationExecutionError

            raise ValidationExecutionError("candidate build must produce exactly one wheel")
        prefix = scratch / "venv"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
        site_packages = prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        _reuse_dependencies(built[0], site_packages)
        interpreter = prefix / "bin/python"
        install = execute_command(
            (str(interpreter), "-I", "-m", "pip", "install", "--no-index", "--no-deps",
             "--no-cache-dir", str(built[0])),
            workspace_root=root, timeout=timeout, environment=environment,
        )
        preparation.append(ValidationResult(label="package-install", command=install))
        environment["PATH"] = str(prefix / "bin") + os.pathsep + environment.get("PATH", os.defpath)
        environment["VIRTUAL_ENV"] = str(prefix)
        yield CandidatePython(interpreter if preparation[-1].passed else None, environment, tuple(preparation))
