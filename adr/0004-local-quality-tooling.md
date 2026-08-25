# ADR-0004: Local Quality Tooling and Reproducible Baseline

## Status

Accepted

## Context

The project requires a reproducible quality baseline that all contributors and CI can run locally. We already use `pyproject.toml` for dependencies, `ruff` for linting, `mypy` for strict type checking, `pytest` for tests, and a `Makefile` for convenience targets. We must codify the exact commands and expectations so anyone can verify the foundation.

## Decision

We adopt the existing `Makefile` targets as the canonical quality entry points:

- `make lint` -> `ruff check .`
- `make type` -> `mypy src`
- `make test` -> `pytest`
- `make quality` -> `lint type test`

These are enforced by CI (`.github/workflows/ci.yml`) and the pre-commit configuration (`.pre-commit-config.yaml`). The baseline must remain green on a clean checkout with only `pip install -e ".[dev]"`.

## Consequences

- Quality is reproducible and verifiable locally and in CI.
- No undocumented configuration drift; any change must keep all three gates green.
- The baseline is minimal but strict (mypy `strict`) to prevent regressions.
