# Risks

| Risk | Impact | Mitigation / Residual |
|------|--------|------------------------|
| Provider outage | Runtime failure | Separate from semantic budget; retry via orchestration adapter. |
| Stale evidence | Acceptance of invalid candidate | Candidate-bound evidence and freshness checks. |
| QA/Reviewer mutation | Corruption | Read-only validators; no workspace access. |
| Workspace escape | Data leak | `escape_check` is post-hoc detection only; TOCTOU/hard-link/side-effect residual risks remain. |
| Unbounded retries | Resource exhaustion | Bounded semantic budget; duplicate/no-progress detection. |
| Unauthorized release | Production incident | Human approval boundary; no autonomous irreversible actions. |
| Loss of forensic data | Inability to audit | Preserved failed/dirty workspaces. |
| Unsandboxed execution of model-authored code | Arbitrary code with worker privileges | Residual risk: no OS/container sandbox; commands/tests may access network and inherited credentials. |
| Post-hoc-only escape detection | Escape not prevented in time | Residual risk: TOCTOU window, hard links, already-executed side effects. |
| No evidence-at-rest integrity verification | Tampered evidence undetected | Residual risk: digest is self-asserted; no signing/MAC. |
| In-memory-only telemetry | Route records lost on restart | Residual risk: durable telemetry not implemented. |
