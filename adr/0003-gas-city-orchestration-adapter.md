# ADR-0003: Gas City Orchestration Adapter

## Status

Proposed

## Context

Gas City is the preferred initial substrate for durable work, DAG/dependencies, concurrency, and runtime recovery. Doom must not be locked into it.

## Decision

We will integrate Gas City through an adapter implementing `OrchestrationPort`. The adapter will translate Doom domain types to Gas City concepts and map runtime failures to recovery policies. All orchestration-specific details remain behind the port.

## Consequences

- Gas City can be replaced with another engine.
- Runtime failures are isolated from semantic recovery.
- Adapter must be kept thin.
