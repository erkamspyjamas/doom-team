# Phase 3B — Versioned Recovery Policy

## Goal

Move recovery allowlists, budgets, retry/backoff decisions and terminal
cutover semantics behind an explicit versioned recovery policy.

## Required behavior

Planning, task, mission and final-gate recovery decisions must be attributable
to a concrete policy version.

Policy must define or resolve:

- recovery eligibility
- bounded attempt budgets
- retry/backoff semantics
- terminal cutover
- allowlisted planning recovery
- refusal behavior

Existing accepted behavior must remain backward compatible unless a concrete
bug demonstrates otherwise.

Changing policy version must not silently reset consumed recovery history.

## Safety boundaries

No unlimited retries.

No implicit fallback that bypasses terminal exhaustion.

No provider-specific redesign without demonstrated requirement.

No live DB mutation or control-plane modification.

## Validation

Show a reproducible policy ambiguity or duplicated hard-coded policy path.

Add positive tests for versioned policy resolution and negative tests proving
unknown/unsafe policy state fails closed.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
