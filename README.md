# Doom Core v0.12.19 — Bounded Plan Recovery and Task-Action Semantics

This build completes the deterministic plan-contract repair across both planning and execution. A shared task-action classifier now reads the executable `instruction` only, understands explicit read-only/negated wording, and never lets a completed-state `expectedOutcome` silently reroute QA or Reviewer from `validate` to `modify`.

Startup recovery for planning failures is fail-closed and bounded. Only allowlisted planning/plan-quality failures with zero tasks are replanned, semantic and zero-task attempts share a durable three-attempt mission budget, and exhaustion emits `mission.autonomous_replan_budget_exhausted`. Semantic replan detaches task-scoped mission events before deleting the old DAG, preserving recovery history and budgets across task replacement.

Task-local mutation targets now distinguish the artifact being created from inspected inputs and technology names. Git evidence enumerates every untracked file instead of collapsing a new directory, preventing valid artifacts such as `scripts/repo-health-check.mjs` from failing exact-target acceptance. A one-time bounded policy epoch recovers legacy missions whose archived gate proves this collapsed-untracked false negative.

Regression coverage uses Node's built-in test runner through the existing `tsx` loader (`npm test`). No dependency or destructive database migration is introduced.

# Doom Core v0.12.18 — Deterministic Plan Contract Repair

This build fixes a planning deadlock where QA/Reviewer validation tasks could be misclassified as mutation tasks because `expectedOutcome` mentioned created/updated files. Mutation/work-mode intent is now derived from the executable `instruction` contract only. Before an LLM repair is attempted, Doom Core deterministically canonicalizes safe plan errors: mutation work is moved away from read-only validation roles when a write-capable roster member exists, work modes are corrected, QA is made dependent on the full implementation set, and Reviewer is made dependent on QA. These repairs emit `mission.plan_contract_canonicalized`. A mission that previously failed planning and is left `blocked` with zero tasks is also eligible for autonomous replanning on the next Core restart, so routine planning-format failures no longer require the Boss to press Replan.

# Doom Core V0.12.17 - Durable Task Semantic Contract

V0.12.17 closes a durability gap discovered after V0.12.16 made task work mode first-class.

## Root cause

V0.12.16 required every Lead plan task to contain:

```text
instruction
expectedOutcome
workMode
```

but `mission_tasks` persisted only the legacy `description` plus acceptance criteria. The richer semantic contract lived primarily in `missions.plan_json`.

That created two recovery/restart hazards:

1. `refreshMissionPlanSnapshot()` rebuilt the plan after autonomous recovery without preserving `workMode`.
2. `revise_task` updated `description`, but an older `plan_json.instruction` could still win when the executor prompt was built.

So a task that Lead had autonomously repaired could execute with stale semantics or fall back to text inference again.

## 1. Task semantic contract is durable DB state

`mission_tasks` now stores:

```text
instruction
expected_outcome
work_mode
```

alongside the backward-compatible `description` alias.

Fresh plans write all three fields directly when the DAG is persisted.

## 2. Additive migration for existing databases

Startup performs additive SQLite column migration only when the columns are absent.

No DB reset is required.

Existing tasks are backfilled from, in order:

```text
mission_tasks durable field
mission.plan_json task spec
legacy description
Core work-mode inference
```

Backfill emits:

```text
mission.task_contract_backfilled
```

## 3. Recovery can repair the real semantic contract

`revise_task.patch` now supports:

```json
{
  "instruction": "imperative action",
  "expectedOutcome": "post-task state",
  "workMode": "modify|validate|analysis|general"
}
```

The repaired values are written to `mission_tasks`, not merely to a transient prompt or stale plan snapshot.

## 4. Remediation tasks also receive a complete contract

`add_remediation_task.newTask` accepts/persists:

```text
instruction
expectedOutcome
workMode
```

Legacy `description` remains accepted as a compatibility alias and is converted into an executable instruction when needed.

## 5. Plan snapshot no longer drops workMode

`refreshMissionPlanSnapshot()` reconstructs the visible plan from durable task rows and preserves:

```text
instruction
expectedOutcome
workMode
acceptanceCriteria
dependencies
assignedAgentId
```

This keeps UI, recovery context and execution state aligned.

## 6. Recovery reasoning sees the actual contract

Lead Supervisor snapshots now include each failed/current task's:

```text
instruction
expectedOutcome
workMode
```

The recovery policy explicitly tells Lead to revise these fields when the semantic contract itself is wrong instead of relying on description-only rewrites.

## Diagnostics

New diagnostic:

```text
missions.task_contract_durability
```

It guards:

- durable task columns,
- additive DB migration,
- initial plan persistence,
- recovery persistence,
- snapshot workMode preservation,
- structured recovery support.

## Compatibility

- Existing mission/task IDs are preserved.
- Existing databases do not require reset.
- Existing `description` readers continue to work.
- Legacy task rows are repaired on autonomous startup scan.
- Existing V0.12.16 mutation routing, evidence gates, worktree isolation and recovery budgets remain intact.
