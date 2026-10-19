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

## Validating packaged src-layout deliverables

The installed CLI and workers remain the trusted Doom runtime. When the
default pytest policy validates a deliverable containing `src/` and Python
packaging metadata, it builds a wheel offline and installs it in a fresh,
temporary virtual environment. Tests and the candidate's installed console
scripts use that environment; they cannot accidentally use the host's older
installation of the same package. Fresh final validation uses the same path.

The runtime therefore needs `pip`, `pytest`, and the candidate's build backend
and dependencies provisioned beforehand. No dependency or build-isolation
download is attempted. Available host dependency packages are reused without
host `.pth` hooks or the candidate's own host package/distribution. Doom
runtime settings, credentials and inherited interpreter overrides are removed
from candidate commands, so application tests supply their own configuration.
Missing
backends, build failures, or installation failures block validation. Other
third-party dependencies are reused, not version-resolved against the
candidate's requirements; this is not a reproducible dependency resolver.

Evidence includes the actual `package-build`, `package-install`, compile and
test commands with their exits and bounded output. Each command keeps its
existing 300-second default bound, configurable by the operator with
`DOOM_VALIDATION_COMMAND_TIMEOUT` (finite seconds, greater than zero, at most
3600). The setting applies to task and final validation; task requests cannot
choose a timeout. Setup adds commands, so worker and mission
limits can still stop a large repository. Temporary environments are removed
after validation, while command evidence and exported candidates remain.
Flat Python fixtures and compile-only policies retain their previous path.

This isolates package imports, not filesystem or process authority. Building
and testing candidate code still has the documented subprocess guard's
limitations; do not treat the virtual environment as a security sandbox.

## Runtime source protection

Deliverable destinations must remain outside the running Doom Team source
tree. In an editable installation that tree belongs to the runtime checkout;
in a wheel installation it belongs to the installed runtime. A symlink cannot
bypass that protection. An independent checkout containing a Doom Team
package may still be used as an explicitly selected deliverable.

Tests of this boundary must target the executing package's source location,
not assume the test file's checkout is the installed runtime. Before a paid
self-development pilot, run the complete baseline suite both in editable
mode and through the isolated installed-candidate validator. A failure in
either mode blocks the pilot before planning or semantic retries begin.

## Task workspace context

The implementer receives task-focused repository context. Task file references
(including package-relative suffixes) rank first, then filename terms from the
task identifier and dependency names, then task instruction terms. References and terms from
acceptance criteria, dependency results and retry feedback rank afterward, so
a failure's test path cannot displace the task's implementation source.
Generic test filename prefixes carry no weight. Ties use repository-relative
path order; file contents never influence ranking.

Files are provided whole, up to 50000 characters per file. The entire workspace
context, including its bounded inventory and section headers, stays within
60000 characters. Binary, symlinked, unreadable, oversized and over-budget
files are explicitly omitted; an oversized inventory reports its own omission
count. No file prefix is presented as complete source. All of this material
retains the existing untrusted-data framing.

An existing tracked file can be replaced only if its complete contents were
provided in that attempt. The entire response is checked before any file is
written. Unseen-file replacements fail with a named-file diagnostic that can
inform the next bounded retry. New files remain permitted. This selection is
a deterministic heuristic, not semantic repository search: a task whose files
cannot fit the bounds must be narrowed rather than reconstructed from guesses.
QA, Reviewer, validation, model profiles and human approval gates are unchanged.


## Optional Python quality validation profile

The trusted default remains compileall plus pytest. To require lint and strict
source typing before **each task's** QA/Reviewer and again on the fresh final
candidate, configure the operator environment before starting the mission:

```bash
export PATH="/absolute/path/to/quality-tools/bin:$PATH"
export DOOM_VALIDATION_POLICY=python-quality
export DOOM_PYTHON_TYPECHECK_TARGET=pocketledger
```

`python-quality` executes the existing compile/pytest checks, then
`ruff check --isolated --select E4,E7,E9,F .` and
`mypy --strict --no-incremental pocketledger` for this example target. Every
command's real exit code, stdout/stderr and label reach both reviewers and the
durable task/final-validation evidence. No command or success assertion comes
from generated code. A failing quality gate prevents review/advancement.

Ruff and mypy must already be executable on the operator PATH; their absolute
paths are resolved before planning. Missing tools or an unknown policy fail
closed. The target is one relative source file/directory (`.` by default),
not command arguments: absolute paths, parent traversal, options and shell
text are refused. Config file keys and explicit ConfigLoader overrides use
`validation_policy` and `python_typecheck_target` with normal precedence.

The chosen quality commands/target are fingerprinted in worker requests and
new mission records. A worker refuses a changed target/policy; resume refuses
an upgrade/downgrade or changed quality configuration. Start a new mission for
such a policy change, preserving old attempts. Existing default-policy
missions and their serialization remain compatible. This profile is optional
and does not change historical benchmark validation or approval requirements.
