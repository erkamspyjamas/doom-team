# Dependency Management

Python version is pinned to 3.11 (minor version). See `pyproject.toml`:
`requires-python = ">=3.11,<3.12"`.

## Locking strategy

We commit a `constraints.txt` file that pins the exact versions of the
top-level runtime and development dependencies. Runtime has no third-party
dependencies; the constraints cover the `dev` extras.

## Install

The documented install path (see `runbooks/INSTALL.md`) and the `Makefile`
`install` target both use:

```bash
python -m pip install -e ".[dev]" -c constraints.txt
```

This ensures every environment gets the same dependency versions.

## Upgrading dependencies

To upgrade a dependency deliberately:

1. Edit the version specifier in `pyproject.toml` (or the constraints).
2. Regenerate the constraints with:

   ```bash
   pip install pip-tools
   pip-compile -o constraints.txt pyproject.toml
   ```

   or simply `make lock` (requires `pip-tools`).
3. Review the diff and run `make quality` in a clean environment.
4. Commit the new `constraints.txt` and any `pyproject.toml` changes.

## Clean-environment verification

CI runs a fresh virtual environment from the lock and then the full quality
gate (`make quality`). This catches missing pins or installation drift.
