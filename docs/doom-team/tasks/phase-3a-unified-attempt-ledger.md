# Phase 3A — Unified Attempt Ledger

## Goal

Unify planning, task, mission and final-gate recovery attempts under one
durable, provenance-aware attempt ledger.

The current system has multiple recovery paths and counters. This phase must
make recovery identity and budget consumption explicit without weakening
existing bounded-recovery safety.

## Required behavior

Each recovery attempt must expose enough durable information to identify:

- recovery identity/kind
- mission/task/execution relation where applicable
- event epoch or event id
- attempt number
- configured budget
- source
- reason
- result
- terminal/non-terminal outcome

Retry/replan/reassign/recovery paths must not silently create unrelated
attempt histories for the same semantic recovery sequence.

Restart/replan must not erase already-consumed recovery budget.

## Safety boundaries

Do not redesign provider/model/runtime architecture.

Do not weaken Phase 1F bounded-recovery behavior.

Do not reset historical attempts.

Do not mutate live DB or OpenClaw/control-plane.

Preserve existing untracked forensic evidence.

## Validation

Demonstrate a real existing split/inconsistency or missing provenance path.

Add positive regression coverage for valid ledger progression.

Add negative/safety regression coverage proving restart/replan/duplicate paths
cannot silently reset or double-create attempt state.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
