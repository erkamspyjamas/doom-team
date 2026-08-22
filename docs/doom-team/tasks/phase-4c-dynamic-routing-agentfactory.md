# Phase 4C — Dynamic Routing & AgentFactory

## Goal

Resolve task routing from durable role/capability/tool/team contracts rather
than production hard-coded agent names or enums.

## Required behavior

Routing should consider relevant durable constraints such as:

- role
- capability
- tool grants
- team membership
- task contract
- workMode

When no suitable persistent agent exists, scoped specialist creation may be
used only if the product contract and demonstrated requirement justify it.

Existing DEV/QA/Reviewer safety boundaries must remain intact.

## Safety boundaries

Do not create agents merely to demonstrate AgentFactory.

Do not broaden tool grants unnecessarily.

QA/Reviewer remain repository read-only.

Missing safe route must fail closed instead of selecting an arbitrary agent.

## Validation

Demonstrate a real hard-coded/dynamic-routing gap.

Add positive routing regression and negative coverage for no-safe-agent,
wrong-role or insufficient-capability cases.

Run targeted tests, full safe suite, typecheck/build where applicable and
git diff --check.
