"""Native-stack project validation, selected explicitly by the operator policy.

The engine fixes the four phases and captures their real exit codes. The
candidate declares its native commands; their contents and output are reviewed
by independent QA and Reviewer. This is execution of project code, not a sandbox.
Only dependency setup gets network access from the trusted policy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from doom_team.subprocess_guard import sanitize_env

MANIFEST = "doom.project.json"
PHASES = ("setup", "build", "checks", "test")

PROJECT_EXECUTION_CONTRACT = '''
Project execution contract (technology choice is yours):
Choose languages, versions, frameworks, databases and dependencies to fit the
objective. You are not restricted to Python, standard libraries or vanilla web
technologies. Respect any explicit user stack choice and the existing project.
Use capability software.engineering for any stack, including frontend and tests.
The first implementation task must create a working vertical slice, native tests
and doom.project.json at the repository root. Every task's candidate must pass
all four phases before the next task starts; do not defer a runnable setup or tests.
Manifest JSON schema:
{"version":1,"setup":[{"argv":["npm","install","--no-audit","--no-fund"]}],
 "build":[{"argv":["npm","run","build"]}],
 "checks":[{"argv":["npm","run","lint"]}],
 "test":[{"argv":["npm","test","--","--run"]}]}
This is a Node example, not a required stack. Each entry has argv (an array, not
a shell string) and optional cwd (a repository-relative directory, default '.').
Use the actual native setup/build/static-check/test commands for your chosen
stack. setup may be empty for projects without dependencies; build, checks and
test must each have at least one real command. Never substitute echo, no-op or
fabricated-success scripts for real build/check/test work. Native tools execute
automatically from the manifest; do not claim you executed them yourself.
Dependencies may be downloaded in setup. Pin dependency versions. The validator
provides an isolated workspace Python venv for pip when useful; you may create
your own .venv with a different Python version or install other local toolchains
as required. This does not constrain your application's language/version.
Ignore node_modules, .venv, .doom-runtime, build outputs and caches in Git.
Include exact install/run/test instructions in README. Do not invent credentials;
unavailable services or toolchains must produce useful real failures, not fake passes.
Do not push or deploy without a separate user instruction.
'''.strip()


class ProjectValidationError(ValueError):
    pass


def load_manifest(root: Path) -> dict[str, list[dict[str, Any]]]:
    path = root / MANIFEST
    if not path.is_file() or path.is_symlink():
        raise ProjectValidationError(f"Create a regular {MANIFEST} declaring the native validation commands.")
    if path.stat().st_size > 65536:
        raise ProjectValidationError(f"{MANIFEST} exceeds 65536 bytes")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ProjectValidationError("project manifest version must be integer 1")
    result: dict[str, list[dict[str, Any]]] = {}
    for phase in PHASES:
        commands = data.get(phase)
        if not isinstance(commands, list) or len(commands) > 12 or (phase != "setup" and not commands):
            raise ProjectValidationError(f"{phase} must declare 1..12 real commands (setup may be empty)")
        result[phase] = []
        for command in commands:
            if not isinstance(command, dict):
                raise ProjectValidationError(f"{phase} commands must be objects with argv arrays")
            argv = command.get("argv")
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 64
                    or any(not isinstance(x, str) or not x or len(x) > 4000 or "\x00" in x for x in argv)):
                raise ProjectValidationError(f"{phase}: argv must be 1..64 nonempty strings")
            if Path(argv[0]).name in {"true", "echo", "printf", "exit", ":"}:
                raise ProjectValidationError(f"{phase}: no-op commands cannot serve as validation")
            cwd = command.get("cwd", ".")
            if not isinstance(cwd, str) or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
                raise ProjectValidationError(f"{phase}: cwd must stay inside the project")
            target = (root / cwd).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ProjectValidationError(f"{phase}: cwd escapes project through a symlink")
            result[phase].append({"argv": argv, "cwd": cwd})
    return result


def command_environment(root: Path) -> dict[str, str]:
    env = {k: v for k, v in sanitize_env().items()
           if not k.startswith(("DOOM_", "PIP_", "PYTHON")) and k != "VIRTUAL_ENV"}
    managed = root / ".doom-runtime/python"
    local = root / ".venv"
    python_env = local if (local / "bin/python").is_file() else managed
    env.update(
        PATH=os.pathsep.join((str(root / "node_modules/.bin"), str(python_env / "bin"), env.get("PATH", os.defpath))),
        VIRTUAL_ENV=str(python_env), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
        PIP_CONFIG_FILE=os.devnull, PIP_REQUIRE_VIRTUALENV="true", CI="true",
    )
    return env


def run_phase(root: Path, phase: str) -> int:
    if phase not in PHASES:
        raise ProjectValidationError("unknown project validation phase")
    commands = load_manifest(root)
    scratch = root / ".doom-runtime"
    if scratch.is_symlink() or (scratch.exists() and not scratch.is_dir()):
        raise ProjectValidationError(".doom-runtime must be a local directory")
    if phase == "setup":
        scratch.mkdir(exist_ok=True)
        if not (scratch / "python/bin/python").exists():
            result = subprocess.run(
                [sys.executable, "-I", "-m", "venv", str(scratch / "python")],
                env=sanitize_env(), check=False,
            )
            if result.returncode:
                return result.returncode
        marker = scratch / "setup.ok"
        marker.unlink(missing_ok=True)
    elif not (scratch / "setup.ok").is_file():
        raise ProjectValidationError("Dependency setup did not complete successfully; inspect dependencies output.")
    for command in commands[phase]:
        cwd = (root / command["cwd"]).resolve()
        if not cwd.is_relative_to(root.resolve()):
            raise ProjectValidationError(f"{phase}: working directory changed to an outside symlink")
        if not cwd.is_dir():
            raise ProjectValidationError(f"{phase}: working directory does not exist: {command['cwd']}")
        print(json.dumps({"phase": phase, "argv": command["argv"], "cwd": command["cwd"]}), flush=True)
        try:
            result = subprocess.run(command["argv"], cwd=cwd, env=command_environment(root), check=False)
        except FileNotFoundError:
            raise ProjectValidationError(f"{phase}: executable not found: {command['argv'][0]}; "
                                         "declare its local installation in setup or provision the toolchain") from None
        if result.returncode:
            print(f"{phase}: native command exited {result.returncode}", flush=True)
            return result.returncode
    if phase == "setup":
        (scratch / "setup.ok").write_text("completed\n")
    print(f"{phase}: completed {len(commands[phase])} native command(s)", flush=True)
    return 0


def main() -> int:
    try:
        return run_phase(Path.cwd(), sys.argv[1] if len(sys.argv) == 2 else "")
    except (ProjectValidationError, ValueError, OSError) as exc:
        print(f"Project validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
