# ADR-0005: Approval Boundary and Human-Gated Operations

## Status

Accepted

## Context

Doom Team must ensure that irreversible or shared-state operations (push, protected merge, deploy, destructive/live DB actions) are only performed after explicit human authorization. Autonomous reversible local work must proceed without human intervention.

## Decision

We define an explicit `ApprovalPort` that is required for any operation marked as irreversible or shared-state. The domain model includes an `ApprovalBoundary` concept. All such operations must be gated behind a positive human approval. The `ApprovalPort` is implemented by a human-gated adapter (e.g., manual confirmation, review workflow). No production mutation capability is embedded in the autonomous path.

## Consequences

- Human oversight is preserved for irreversible actions.
- Autonomous reversible engineering is unblocked.
- Adapters for approval must be robust and auditable.
- Any future automation must explicitly route through this boundary.
