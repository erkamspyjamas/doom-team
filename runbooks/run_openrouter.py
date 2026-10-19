"""Launch the installed CLI using the measured public profile and a clean env."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from collections.abc import Mapping

REPO = Path(__file__).resolve().parents[1]
SELECTED_PROFILE = REPO.parents[1] / "runtime/doom-team-openrouter/selected-profile.json"
ALLOWED_PROFILE_KEYS = frozenset({
    "DOOM_MODEL_PROVIDER", "DOOM_MODEL_NAME", "DOOM_PROVIDER_BASE_URL", "DOOM_PROVIDER_API_KEY_ENV",
    "DOOM_PROVIDER_ORDER", "DOOM_PROVIDER_ONLY", "DOOM_FORBIDDEN_PROVIDERS", "DOOM_ALLOW_FALLBACKS",
    "DOOM_FALLBACK_PROVIDER", "DOOM_FALLBACK_MODEL_NAME", "DOOM_FALLBACK_BASE_URL", "DOOM_FALLBACK_API_KEY_ENV",
    "DOOM_FALLBACK_PROVIDER_ONLY", "DOOM_QUALITY_MODEL_ROUTE", "DOOM_OPENROUTER_REASONING_EFFORT",
    "DOOM_OPENROUTER_MAX_TOKENS", "DOOM_OPENROUTER_JSON_MODE", "DOOM_REQUEST_TIMEOUT",
    "DOOM_SEMANTIC_RETRIES", "DOOM_TASK_TRANSPORT", "DOOM_GAS_CITY_BINARY", "DOOM_GAS_CITY_CITY",
    "DOOM_GAS_CITY_ATTEMPT_TIMEOUT",
})


def build_environment(profile: dict[str, str], inherited: Mapping[str, str]) -> dict[str, str]:
    if set(profile) - ALLOWED_PROFILE_KEYS or any(not isinstance(v, str) for v in profile.values()):
        raise ValueError("profile contains unexpected fields; credentials belong only in the environment")
    env = {k: inherited[k] for k in ("HOME", "USER", "LOGNAME", "LANG", "TZ", "XDG_RUNTIME_DIR",
                                    "DBUS_SESSION_BUS_ADDRESS", "OPENROUTER_API_KEY", "DOOM_STATE_DIR")
           if k in inherited}
    if not env.get("OPENROUTER_API_KEY"):
        raise ValueError("OPENROUTER_API_KEY must be set")
    env["PATH"] = str(REPO / ".venv/bin") + ":/usr/bin:/bin"
    env.update(profile)
    return env


def main() -> None:
    try:
        profile = json.loads(SELECTED_PROFILE.read_text())["environment"]
        environment = build_environment(profile, os.environ)
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("OpenRouter profile or credential unavailable; see runbooks/OPENROUTER.md") from None
    # No credential in argv, and no shell interpreting profile contents.
    cli = str(REPO / ".venv/bin/doom-team")
    os.execve(cli, [cli, *sys.argv[1:]], environment)


if __name__ == "__main__":
    main()
