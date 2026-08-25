# Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Provider outage | Runtime failure | Separate from semantic budget; retry via orchestration adapter. |
| Stale evidence | Acceptance of invalid candidate | Candidate-bound evidence and freshness checks. |
| QA/Reviewer mutation | Corruption | Read-only validators; no workspace access. |
| Workspace escape | Data leak | Escape detection and path confinement. |
| Unbounded retries | Resource exhaustion | Bounded semantic budget; duplicate/no-progress detection. |
| Unauthorized release | Production incident | Human approval boundary; no autonomous irreversible actions. |
| Loss of forensic data | Inability to audit | Preserved failed/dirty workspaces. |
