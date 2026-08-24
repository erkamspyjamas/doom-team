# Doom Team — Greenfield Roadmap

## Phase 0 — Foundation
Bootstrap repository/tooling, architecture ADRs and reproducible quality baseline.

## Phase 1 — Objective and Task Contracts
Implement Objective, Mission, TaskContract, validation, dependencies, acceptance criteria, capabilities and mutation intent.

## Phase 2 — Capability and Permission Engine
Implement deterministic tool/mutation authority and deny dangerous irreversible actions by default.

## Phase 3 — Workspace and Repository Layer
Implement isolated workspaces, baseline/candidate identity, exact-file staging, local commits, workspace escape checks and forensic retention.

## Phase 4 — Evidence and Provenance
Implement durable candidate-bound evidence, artifacts, commands/tests, freshness and actual provider/model/runtime telemetry.

## Phase 5 — Orchestration Adapter
Implement a Doom-owned orchestration port and initial Gas City adapter for durable DAG work, dependencies, run observation and runtime recovery.

## Phase 6 — DEV → QA → Reviewer
Implement DEV candidate execution plus independent read-only QA and Reviewer verification of the exact candidate.

## Phase 7 — Acceptance and Supervisor
Implement explicit acceptance state and deterministic Supervisor decisions above generic orchestration.

## Phase 8 — Semantic Recovery and Loop Detection
Implement RecoveryEpoch, semantic retry budget, blocker identity, duplicate/no-progress detection, loop detection and restart-safe exhaustion.

## Phase 9 — Parallelism and Dynamic Routing
Implement dynamic capabilities, at least three parallel independent tasks, dependency fan-out/fan-in and route telemetry.

## Phase 10 — Autonomous Mission Runner
Implement planning/next-runnable-work progression for a non-trivial human objective with minimal routine intervention.

## Phase 11 — Reliability and Security
Prove provider failure, crash/restart, stale-proof rejection, QA/Reviewer mutation detection, dirty workspace retention, duplicate/loop bounds and forbidden-operation gates.

## Phase 12 — Production Candidate
Produce reproducible install/runbooks, backup/recovery, observability, approval procedure, threat model, representative mission evidence and a final `READY_FOR_FINAL_APPROVAL`.
