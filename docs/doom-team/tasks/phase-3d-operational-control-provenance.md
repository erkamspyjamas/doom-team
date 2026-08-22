# Phase 3D — Operational Control Provenance

## Goal

Make operational actions such as pause, resume, retry, reassign, replan,
cancel and kill explicitly attributable and distinguish manual versus
autonomous control.

## Required behavior

Relevant operational actions must preserve:

- action type
- source/actor class
- reason
- target identity
- prior state
- resulting state
- recovery/attempt relation where applicable
- timestamp/event provenance

Manual operator action and autonomous recovery must not become
indistinguishable in durable history.

Unsupported or ambiguous transitions must fail closed.

## Safety boundaries

Do not add unnecessary UI/control-plane features.

Implement only product-level provenance required by demonstrated behavior.

Do not weaken existing authorization or recovery boundaries.

## Validation

Demonstrate a current provenance ambiguity.

Add positive and negative regression coverage including invalid transition or
ambiguous-source behavior.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
