# Install

Reproducible installation requires Python 3.11 (minor version pinned in
`pyproject.toml` as `>=3.11,<3.12`) and git.

## The product runtime is an installed package

Doom Team runs from an **installed** package, never from a source tree that
happens to be the current directory. The virtual environment you install into
*is* the product runtime: it owns the `doom-team` console script, and it is the
same interpreter that Gas City task workers run under (see
[Operate](OPERATE.md#gas-city-task-execution)).

`PYTHONPATH` is not part of the runtime contract. Do not set it to make
`doom_team` importable — an installed package needs no import path, and a
relative `PYTHONPATH` silently breaks Gas City workers, which execute from a
per-attempt job directory rather than the repository.

## Steps

1. Clone the repository and change into it.

2. Create the runtime virtual environment:

   ```bash
   python3.11 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip
   ```

3. Install the package. The package itself has **no runtime dependencies**
   (`dependencies = []`), so an operator runtime needs only:

   ```bash
   .venv/bin/python -m pip install -e .
   ```

   To also run the quality gates, install the dev extras against the committed
   constraint file:

   ```bash
   .venv/bin/python -m pip install -e ".[dev]" -c constraints.txt
   ```

4. Verify the installation. Both checks must pass, and neither may require
   `PYTHONPATH`:

   ```bash
   .venv/bin/python -c "import doom_team; print(doom_team.__version__)"
   .venv/bin/doom-team --help
   ```

   If `.venv/bin/doom-team` is missing, the console script was not generated —
   reinstall (step 3). An installation performed before the console script was
   declared stays stale until it is reinstalled.

5. Run the quality gates (dev install only):

   ```bash
   make lint
   make type
   make test
   ```

## Upgrading an existing checkout

An editable install tracks the working tree for *source*, but console scripts
and metadata are written at install time. After pulling changes that touch
`pyproject.toml`, reinstall:

```bash
.venv/bin/python -m pip install -e ".[dev]" -c constraints.txt
```

See `docs/DEPENDENCIES.md` for the locking strategy and how to upgrade
dependencies deliberately.
