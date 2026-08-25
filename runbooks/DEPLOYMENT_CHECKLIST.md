# Deployment Checklist

This document records the deployment readiness of the Phase 2 production
service candidate.

## Status

**DEPLOY_READY = YES** — the documented conclusion of this candidate.

**IMPORTANT**: This checklist documents readiness only. **No deployment, push,
merge, or production mutation has been performed, and the autonomous path never
performs them.** Deployment is a human-only operation that requires a positive
approval through the `ApprovalPort`.

## Readiness Items

- [ ] All quality gates green in a clean environment: `make lint`, `make type`, `make test`
- [ ] Staging evidence current and passing (see [Staging Evidence](../docs/STAGING_EVIDENCE.md))
- [ ] Configuration reference present and consistent (see [Configuration Reference](../docs/CONFIGURATION.md))
- [ ] Operator runbooks present and consistent:
  - [ ] Install
  - [ ] Operate
  - [ ] Observability
  - [ ] Approve
  - [ ] Recover
  - [ ] Upgrade
  - [ ] Rollback
  - [ ] Disaster Recovery
  - [ ] Threat Model
- [ ] Security review / threat model current
- [ ] Rollback and disaster-recovery procedures documented
- [ ] Human approval procedure for deployment documented
- [ ] Verified backup available and restorable

## Deployment (Human Only)

Deployment is performed only by a human operator after a positive approval:

1. Review the candidate evidence and verdicts.
2. Approve the release through the `ApprovalPort` (records an
   `APPROVAL_RECORDED` event).
3. Execute the release action (push/merge/deploy) manually.
4. Verify post-deployment health with `doom-team doctor` and `doom-team status`.

No release action (push/merge/deploy) may be taken without `RELEASE_AUTHORIZED`
from the deterministic Supervisor with a positive human approval.

## Post-Deployment Verification

- [ ] `doom-team doctor` returns healthy and ready
- [ ] `doom-team status` shows no unexpected active tasks or errors
- [ ] Event log contains `SERVICE_STARTED` and any approval events
- [ ] Back up the new state directory after the release
