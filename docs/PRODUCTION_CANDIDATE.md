# Production Candidate

This document summarizes the readiness of the Doom Team production service
candidate (Phase 2).

## Status

**DEPLOY_READY = YES** (documentation conclusion). **No deployment, push,
merge, or production mutation has been performed, and none will be performed by
the autonomous path.** Deployment is a human-only operation that requires a
positive approval through the ApprovalPort (see
[Approval](../runbooks/APPROVAL.md) and
[Deployment Checklist](../runbooks/DEPLOYMENT_CHECKLIST.md)).

## Install

Reproducible install: `python -m pip install -e ".[dev]"` on Python 3.11+.
See [Install](../runbooks/INSTALL.md).

## Configuration

See the [Configuration Reference](../docs/CONFIGURATION.md).

## Operator Runbooks

- [Install](../runbooks/INSTALL.md)
- [Operate](../runbooks/OPERATE.md)
- [Observability](../runbooks/OBSERVABILITY.md)
- [Approve](../runbooks/APPROVAL.md)
- [Recover](../runbooks/RECOVERY.md)
- [Upgrade](../runbooks/UPGRADE.md)
- [Rollback](../runbooks/ROLLBACK.md)
- [Disaster Recovery](../runbooks/DISASTER_RECOVERY.md)
- [Threat Model](../runbooks/THREAT_MODEL.md)
- [Deployment Checklist](../runbooks/DEPLOYMENT_CHECKLIST.md)

## Risks

See [RISKS.md](RISKS.md) for explicit risks and mitigations.

## Staging Evidence

See [Staging Evidence](STAGING_EVIDENCE.md) for the end-to-end verification
summary (15 scenarios) with repo-resident, mode-labeled evidence.

## Readiness

The `test_production_candidate.py` test runs a representative 3-task mission
through the full pipeline and asserts that the Supervisor returns
`READY_FOR_FINAL_APPROVAL` for each candidate when no approval port is
provided. No release action is taken without a positive human approval.

## Representative Mission Evidence

A 21-task endurance mission
(`tests/test_reliability.py::test_20_plus_task_endurance`) completes with zero
stuck work, no workspace escape, fresh candidate-bound evidence, and no
duplicate evidence. Each candidate is ready for final approval.
