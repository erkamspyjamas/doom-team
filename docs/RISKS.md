# Risks

| Risk | Impact | Mitigation / Residual |
|------|--------|------------------------|
| Provider outage | Runtime failure | Separate from semantic budget; retry via orchestration adapter. |
| Stale evidence | Acceptance of invalid candidate | Candidate-bound evidence and freshness checks. |
| QA/Reviewer mutation | Corruption | Read-only validators; no workspace access. |
| Workspace escape | Data leak | `escape_check` is post-hoc detection only; pre-execution symlink component checks added. TOCTOU/hard-link/side-effect residual risks remain. |
| Unbounded retries | Resource exhaustion | Bounded semantic budget; duplicate/no-progress detection. |
| Unauthorized release | Production incident | Human approval boundary; no autonomous irreversible actions. |
| Loss of forensic data | Inability to audit | Preserved failed/dirty workspaces. |
| Unsandboxed execution of model-authored code | Arbitrary code with worker privileges | Mitigation: sanitized child environments (credentials stripped), argv-only subprocess execution, pre-execution path/symlink checks, network isolation via `unshare --net` when supported. Residual: no OS/container sandbox; arbitrary user callables not covered; network isolation may be unavailable; TOCTOU window remains. |
| Post-hoc-only escape detection | Escape not prevented in time | Mitigation: pre-execution symlink component checks added. Residual: TOCTOU window, hard links, already-executed side effects. |
| No evidence-at-rest integrity verification | Tampered evidence undetected | Residual risk: digest is self-asserted; no signing/MAC. |
| Telemetry persistence | Route records lost on restart if only the in-memory adapter is used | Mitigated: `DurableTelemetry` records `PROVIDER_ROUTE` events to the durable event log; the in-memory adapter is test-only. |
