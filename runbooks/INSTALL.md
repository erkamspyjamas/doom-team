# Install

Reproducible installation requires Python 3.11 (minor version pinned in
`pyproject.toml` as `>=3.11,<3.12`) and git.

1. Clone the repository.
2. Create a virtual environment (optional but recommended).
3. Install the package with dev dependencies and the committed constraint file:

   ```bash
   python -m pip install -e ".[dev]" -c constraints.txt
   ```

4. Verify the installation:

   ```bash
   python -c "import doom_team; print(doom_team.__version__)"
   ```

5. Run the quality gates:

   ```bash
   make lint
   make type
   make test
   ```

See `docs/DEPENDENCIES.md` for the locking strategy and how to upgrade
dependencies deliberately.
