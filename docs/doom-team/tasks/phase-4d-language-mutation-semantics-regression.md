# Phase 4D — Language & Mutation Semantics Regression

## Goal

Harden task interpretation and mutation semantics across Turkish and English
without relying on fragile keyword-only behavior.

## Required behavior

Regression coverage should include relevant distinctions such as:

- action versus observation
- affirmative versus negated mutation
- requested work versus already-completed state
- validation deliverable versus implementation deliverable
- explicit output path versus inspected/reference/input path
- technology/reference terms versus actual mutation target
- Turkish and English equivalent intent

Semantic normalization must preserve safety and task intent.

## Safety boundaries

Do not turn ambiguous language into irreversible mutation.

Do not treat referenced files as output targets without evidence.

Validation/read-only tasks must remain non-mutating.

Unsafe ambiguity must fail closed.

## Validation

Use representative reproducible cases from current parser/planner behavior.

Add positive bilingual/semantic regression and negative/safety regression.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
