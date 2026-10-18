# RC3 planner task decomposition

Authorized on 2026-10-19 following the RC2 dual-provider campaign's repeated
G2 failures. Scope: the planner's task execution instructions only.

Every task already needs a non-empty candidate commit, passing executable
validation, independent QA, and independent Reviewer acceptance before it
can integrate. The RC2 planner did not explain that contract. It emitted
standalone inspection, reproduction, verification, and commit tasks. In the
preserved NVIDIA-NONE B06 mission, reproduction added a correctly failing
regression test, which could not pass validation before the separately
planned fix. In B09, a final verification task exhausted attempts with
"nothing to commit" after three earlier tasks had integrated.

The planner now receives explicit guidance to:

- Describe a substantive required file change and its checkable outcome.
- Include inspection and diagnosis in the task making the corresponding change.
- Combine a bug fix with its regression test when a separate red-test task
  could not pass the executor's gates.
- Reserve separate test tasks for new passing coverage, and documentation
  tasks for required documentation changes.
- Leave distinct work for each dependent task and avoid placeholder or
  cosmetic changes manufactured to satisfy the non-empty-commit requirement.
- Let the executor perform committing, validation, review, and integration.

This is a semantic planning instruction, not a new deterministic validator.
Existing schema validation, routing, task ids, task/criteria bounds, retry
budgets, provider settings, implementer instructions, and acceptance gates
are unchanged. No task-id prefix blacklist is introduced: an inspection
feature can legitimately change source files. The model may still ignore
the instructions or over-complete a task; the external benchmark measures
that behavior. Prompt regression checks establish that the contract is sent,
not that any real model must obey it.

Verification uses the repository quality gates and a fresh sequential
NVIDIA-NONE run of the unchanged ten-case DOOM-BENCH-v1 suite, retaining the
historical four base attempts (one initial plus three semantic retries), the
existing one-shot quality reserve, 900-second worker limit, 300-second request
limit, and 5400-second mission policy. RC1 and RC2 tags remain frozen.
Campaign tooling and results are kept in the independent benchmark repository
under `experiments/rc3-none-full-20261019/`.
