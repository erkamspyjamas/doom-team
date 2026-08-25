# Staging Evidence

This document summarizes the end-to-end staging evidence for the Doom Team
service. The staging harness exercises the real service against controlled
(fake) dependencies and records repo-resident evidence with a mode label.

## Location

- Harness: `staging/run_staging.py`
- Latest report: `staging/results/latest.json`

## Running

```bash
python -m pip install -e ".[dev]" -c constraints.txt
python staging/run_staging.py --base-dir staging/run --output staging/results/latest.json
```

The harness runs in **controlled/fake staging mode only** (the default
`--mode fake`). Only `--mode fake` is supported; `--mode real` (or any other
mode) is rejected with a clear error so evidence can never be labeled real.
There is no real-mode connectivity. Real external staging is not implemented
(see [Production Candidate](PRODUCTION_CANDIDATE.md)).

## Scenarios

| # | Scenario | Meaning |
|---|----------|---------|
| 1 | `clean_startup` | Service starts and shuts down gracefully |
| 2 | `doctor_check` | `doctor` returns healthy |
| 3 | `health_readiness` | Health and readiness are distinct and true |
| 4 | `gas_city_connection` | Gas City adapter enqueue/observe works |
| 5 | `inference_request` | Inference adapter returns a normalized response |
| 6 | `mission_lifecycle` | Full DEV → QA → Reviewer mission completes |
| 7 | `record_approval` | ApprovalPort records and verifies approval |
| 8 | `restart_during_work` | Recovery state and run-id mapping survive restart |
| 9 | `inference_provider_outage` | Runtime failure does not consume semantic budget |
| 10 | `gas_city_outage` | Transient Gas City failure is normalized |
| 11 | `corrupted_state_fails_closed` | Corrupt durable state is rejected |
| 12 | `backup` | Verified backup is created |
| 13 | `restore` | State restores from a verified backup |
| 14 | `recovery` | Semantic exhaustion survives restart |
| 15 | `clean_shutdown` | Service records a clean shutdown event |

## Current Result

`staging/results/latest.json` reports all 15 scenarios as `pass` with
`all_passed: true` in `fake` mode. Evidence produced by the harness is
repo-resident and mode-labeled (`fake`) so it can be audited.
