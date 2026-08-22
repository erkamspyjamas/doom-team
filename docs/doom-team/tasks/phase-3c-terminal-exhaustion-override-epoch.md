# Phase 3C — Terminal Exhaustion & Override Epoch

## Goal

Guarantee that exhausted recovery state remains terminal across restart,
resume and replan unless an explicit authorized override creates a new
auditable recovery epoch.

## Required behavior

After recovery budget exhaustion:

- restart must not open replacement execution
- resume must not silently continue attempts
- replan must not reset exhaustion
- duplicate recovery entry must remain refused

If override capability exists, it must be explicit, attributable and
audit-visible, and must create a clearly distinguishable new epoch rather
than rewriting historical attempts.

## Safety boundaries

No automatic override.

No hidden budget reset.

No mutation of historical evidence.

No bypass of human-required terminal state.

No live DB or control-plane modification.

## Validation

Reproduce at least one exhaustion/restart or exhaustion/replan edge case.

Positive regression must prove legitimate bounded operation.

Negative regression must prove exhausted state cannot reopen autonomously.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
