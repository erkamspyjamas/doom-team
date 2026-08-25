# ADR-0002: Domain Ports and Adapters

## Status

Accepted

## Context

The core must not depend on orchestration engines, file systems, or HTTP clients. It must be testable in isolation and allow replaceable infrastructure.

## Decision

We define hexagonal architecture with explicit ports (interfaces) for orchestration, workspace, repository, evidence, inference telemetry, approval, and clock. All infrastructure adapters depend on these ports. Domain types do not leak engine-specific types.

## Consequences

- Core is highly testable.
- Adding a new orchestration adapter requires implementing the port.
- Slight indirection overhead.
