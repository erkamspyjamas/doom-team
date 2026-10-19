# Technology-independent project execution

The operator may select `DOOM_VALIDATION_POLICY=project` for a new mission.
This leaves the historical `default` and `python-quality` policies unchanged.
The engine still runs on Python 3.11; the generated project may use another
language, version, framework, database or dependency stack.

The planner can route any native-stack implementation or testing task through
the explicit `software.engineering` capability. The console appends the native
execution contract automatically; its first task must include a working slice,
tests and a root `doom.project.json` file, for example:

```json
{
  "version": 1,
  "setup": [{"argv": ["npm", "install", "--no-audit", "--no-fund"]}],
  "build": [{"argv": ["npm", "run", "build"]}],
  "checks": [{"argv": ["npm", "run", "lint"]}],
  "test": [{"argv": ["npm", "test"]}]
}
```

This is an example, not a required stack. Commands are argv arrays, with an
optional repository-relative `cwd`. Setup may be empty. Build, checks and tests
must each contain real commands; missing phases, shell strings, known no-ops,
and escaping working directories fail closed. Native commands and their actual
outputs/exit codes are captured before independent QA/Reviewer checks. The same
four phases run again on the final candidate. The manifest, like all generated
code and tests, is untrusted project content that QA and Reviewer must assess;
passing a command alone does not prove adequate test coverage.

Dependency setup is explicitly allowed network access by the operator's policy;
every subprocess still receives a credential-scrubbed environment. Other phases
retain the existing best-effort network isolation. No filesystem sandbox is
provided. Only the trusted policy can set network permission; its phase argv and
network flags are fingerprinted into the mission and checked on workers/resume.

An isolated Python venv under the disposable validation workspace is available
for pip; it never installs into the engine's environment. A project's own root
`.venv` takes precedence. Other local toolchains may be installed in setup, with
their actual paths declared in commands. A missing executable or service is a
real validation failure, never a skipped gate. Ignore dependency/build/cache
directories in Git and document exact launch commands in README.

Existing missions keep their original objectives, evidence and pinned policy.
Do not change a running mission's policy to resume it under different gates.

Local verification: native Node tests (positive and negative) traverse the full
mission pipeline with offline inference fixtures; a separate React 18.3.1 fixture
downloaded its dependencies and passed build, static checks and two native tests.
The latter is a test fixture, not a production framework-version recommendation.
Evidence: `/home/eko/doom/runtime/doom-console/verification/native-react-20261019/proof.json`.
