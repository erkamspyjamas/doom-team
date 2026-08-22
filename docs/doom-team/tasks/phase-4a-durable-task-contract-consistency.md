# Phase 4A — Durable Task Contract Consistency

## Goal

Ensure task contract semantics remain consistent from planning through
durable storage, API representation and runtime execution.

## Required behavior

At minimum verify consistency for:

- instruction
- expectedOutcome
- acceptanceCriteria
- workMode

Fields must not silently disappear, change meaning or receive incompatible
defaults between plan, DB, API and runtime layers.

Legacy records must remain readable where required.

## Safety boundaries

Prefer additive/backward-compatible changes.

Do not perform destructive migrations.

Do not infer dangerous mutation semantics from missing contract data.

Ambiguous unsafe contract state must fail closed.

## Validation

Demonstrate an actual contract propagation inconsistency or uncovered edge.

Add positive end-to-end propagation regression and negative/safety regression
for missing/invalid/incompatible contract state.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
