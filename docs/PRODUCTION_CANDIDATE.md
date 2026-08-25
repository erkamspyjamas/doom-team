# Production Candidate

This document summarizes the readiness of the first Doom Team production candidate.

## Install

Reproducible install: `python -m pip install -e ".[dev]"` on Python 3.11+.

## Runbooks

- [Install](../runbooks/INSTALL.md)
- [Recovery](../runbooks/RECOVERY.md)
- [Observability](../runbooks/OBSERVABILITY.md)
- [Approval](../runbooks/APPROVAL.md)
- [Threat Model](../runbooks/THREAT_MODEL.md)

## Risks

See [RISKS.md](RISKS.md) for explicit risks and mitigations.

## Readiness

The `test_production_candidate.py` test runs a representative 3-task mission through the full pipeline and asserts that the Supervisor returns `READY_FOR_FINAL_APPROVAL` for each candidate when no approval port is provided. No release action is taken without a positive human approval.

## Representative Mission Evidence

A 21-task endurance mission (`tests/test_reliability.py::test_20_plus_task_endurance`) completes with zero stuck work, no workspace escape, fresh candidate-bound evidence, and no duplicate evidence. Each candidate is ready for final approval.
