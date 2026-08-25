# Doom Team Staging Harness

This harness exercises the real Doom Team service end-to-end against controlled
(fake) dependencies and records repo-resident evidence with a mode label.

## Scenarios

1. Clean startup
2. Doctor check
3. Health and readiness
4. Gas City connection
5. Inference request
6. Complete mission lifecycle
7. Recording a human approval
8. Restart during active work
9. Inference provider outage
10. Gas City outage
11. Corrupted durable state (fails closed)
12. Backup
13. Restore
14. Recovery
15. Clean shutdown

## Running

```bash
python -m pip install -e ".[dev]" -c constraints.txt
python staging/run_staging.py --base-dir staging/run --output staging/results/latest.json
```

The harness defaults to `--mode fake`, meaning it uses high-fidelity local fakes
for Gas City and the inference provider. To run against real dedicated
non-production endpoints, set `--mode real` and provide the real configuration
via `DOOM_*` environment variables (never commit credentials).

Results are written as JSON to the `--output` path with the mode label and a
pass/fail status for each scenario.
