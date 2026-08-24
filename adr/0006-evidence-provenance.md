# ADR-0006: Evidence and Provenance Model

## Status

Accepted

## Context

Doom Team must produce durable, candidate-bound evidence that identifies the objective, mission, task, attempt, recovery epoch, workspace, baseline commit, candidate commit, producer role/run, command/test artifacts, actual provider/model/runtime, timestamp and digest. This evidence is the basis for independent QA, Reviewer, and Supervisor acceptance.

## Decision

We define an `EvidenceStore` port that persists evidence as structured records. Each evidence record is immutable and bound to a specific candidate. The evidence must include all required fields listed in the architecture. The store must support retrieval by candidate id and must reject tampering (e.g., via digest). The domain model includes `Evidence` and `Candidate` types that enforce these fields.

## Consequences

- Acceptance decisions are based on repository-derived evidence, not agent prose.
- Provenance is traceable and auditable.
- The store must be durable and tamper-evident.
- Integration with orchestration requires mapping run IDs to evidence records.
