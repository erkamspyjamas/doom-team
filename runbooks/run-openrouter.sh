#!/bin/bash
set -e
set +x
task_repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    set -a
    if ! source "$task_repo_dir/../../.env" >/dev/null 2>&1; then
        printf 'Could not load the OpenRouter credential environment.\n' >&2
        exit 1
    fi
    set +a
fi
exec "$task_repo_dir/.venv/bin/python" "$task_repo_dir/runbooks/run_openrouter.py" "$@"
