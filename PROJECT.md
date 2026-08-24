# Doom Team — Project Source of Truth

**Project ID:** `doom-team`  
**Mode:** Greenfield  
**Builder:** Doom Autopilot

## Mission

Build Doom Team as a self-hosted autonomous software-engineering platform that turns a human business objective into safe, evidenced, independently verified software progress with minimal routine human intervention.

Canonical lifecycle:

```text
Human / Business Objective
        ↓
Objective + Task Contract
        ↓
planning / decomposition
        ↓
dynamic capabilities / roles
        ↓
isolated implementation
        ↓
repository-derived evidence
        ↓
independent QA
        ↓
independent Reviewer
        ↓
deterministic Supervisor / acceptance
        ↓
READY_FOR_FINAL_APPROVAL
        ↓
human approval
        ↓
protected merge / push / deploy only when separately authorized
```

## Product value

Doom Team is not merely a multi-agent scheduler. Its core product value is the engineering trust layer:

- objective/task contracts
- capability and permission policy
- mutation boundaries
- candidate identity and freshness
- evidence/provenance
- objective acceptance
- independent QA/Reviewer
- bounded semantic recovery
- duplicate/loop detection
- deterministic Supervisor
- human approval boundary
- forensic workspace safety

Generic orchestration should be delegated through a replaceable adapter where appropriate.

## Core invariants

1. Repository evidence outranks agent prose.
2. Generic workflow `done` does not imply Doom acceptance.
3. Acceptance proof binds to the exact attempt/candidate.
4. QA and Reviewer are validators, not implementers.
5. Runtime retry and semantic remediation are separate.
6. Semantic recovery is bounded.
7. Dirty/failed/uncertain workspaces are preserved.
8. Reversible local work proceeds autonomously.
9. Push, protected merge, deploy and destructive/live DB actions require explicit authorization.
10. Model/provider and orchestration substrate must remain replaceable.

## Initial provider preference

1. NVIDIA DeepSeek V4 Flash
2. OpenRouter DeepSeek V4 Flash
3. NVIDIA Nemotron 3 Ultra

Actual provider/model/runtime route must be recorded as evidence.

## First-release non-goals

Do not prioritize polished UI, deployment automation, cloud multi-tenancy, enterprise RBAC, notification integrations, or custom generic scheduling infrastructure before reliability/evidence/safety.
