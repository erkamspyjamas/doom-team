# Phase 4B — DAG Canonicalization & Dependency Safety

## Goal

Make plan/task DAG canonicalization deterministic while preserving dependency
and role semantics.

## Required behavior

Verify and harden:

- acyclic dependency graph
- missing dependency detection
- dependency ordering
- QA/Reviewer synthesis where contract requires it
- semantics-preserving role repair
- semantics-preserving workMode repair
- refusal of ambiguous unsafe canonicalization

Canonicalization must not manufacture duplicate semantic work.

## Safety boundaries

Do not bypass DEV → QA → Reviewer lineage.

Do not silently delete dependencies.

Do not convert validation/read-only work into mutation work.

Cycles and unresolved unsafe dependencies must fail closed.

## Validation

Provide positive coverage for safely repairable DAG input.

Provide negative/safety coverage for cycle, broken lineage or semantic change.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
