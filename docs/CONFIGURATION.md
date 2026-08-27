# Configuration Reference

Typed, validated, secret-safe production configuration. Implemented in
`src/doom_team/config.py` and pinned by `tests/test_config.py`.

## Precedence

Settings are resolved from layered sources; higher precedence wins:

1. Explicit arguments to `ConfigLoader.load(...)`
2. Environment variables (`DOOM_*`)
3. Configuration file (JSON object)
4. Safe defaults (optional settings only)

For the CLI, the `--state-dir` flag is an explicit argument and always wins
over the `DOOM_STATE_DIR` environment variable for every command that reads
or mutates state (`backup`, `restore`, `approve`, `status`, `doctor`,
`serve`). It may be given in either the global position
(`doom-team --state-dir /x backup ...`) or the subcommand position
(`doom-team backup --state-dir /x ...`). The environment variable is used only
when no CLI value is supplied.

## Required settings (fail closed)

Startup fails with `ConfigError` when any required setting or the provider
credential is missing. There are no defaults for required values.

| Setting | Required | Environment | Description |
|---------|----------|-------------|-------------|
| `state_dir` | yes | `DOOM_STATE_DIR` | Durable state base directory |
| `model_provider` | yes | `DOOM_MODEL_PROVIDER` | Provider name (e.g. `nvidia`) |
| `model_name` | yes | `DOOM_MODEL_NAME` | Model name (e.g. `deepseek-v4`) |
| `runtime` | no | `DOOM_RUNTIME` | Runtime id (default `local`) |
| `semantic_retries` | no | `DOOM_SEMANTIC_RETRIES` | Semantic retry budget (default `3`) |
| `provider_api_key_env` | no | `DOOM_PROVIDER_API_KEY_ENV` | Env var name holding the credential (default `DOOM_API_KEY`) |
| `provider_order` | no | `DOOM_PROVIDER_ORDER` | Comma-separated preferred provider order for OpenRouter (default `CoreWeave,DeepInfra,SiliconFlow`) |
| `forbidden_providers` | no | `DOOM_FORBIDDEN_PROVIDERS` | Comma-separated forbidden providers (default `Relace`) |
| `allow_fallbacks` | no | `DOOM_ALLOW_FALLBACKS` | Allow provider fallbacks (default `true`) |
| `request_timeout` | no | `DOOM_REQUEST_TIMEOUT` | Per-request timeout in seconds (default `60.0`) |
| `provider_base_url` | no | `DOOM_PROVIDER_BASE_URL` | Inference provider base URL, `http(s)` only, no embedded credentials (default unset; required to construct the inference client) |
| `task_transport` | no | `DOOM_TASK_TRANSPORT` | Where task attempts execute: `in_process` (default) or `gas_city` |
| `gas_city_binary` | when `gas_city` | `DOOM_GAS_CITY_BINARY` | Absolute path to the `gc` executable |
| `gas_city_city` | when `gas_city` | `DOOM_GAS_CITY_CITY` | Absolute path to the Doom Team-owned city (created on first use) |
| `gas_city_agent` | no | `DOOM_GAS_CITY_AGENT` | Gas City agent that claims work items (default `mayor`) |
| `gas_city_attempt_timeout` | no | `DOOM_GAS_CITY_ATTEMPT_TIMEOUT` | Per-attempt timeout in seconds (default `900.0`) |

## Task execution transport

`task_transport` selects where a task attempt runs and is never inferred from
ambient state — a configured Gas City is never silently downgraded to running
work in this process, and `gc` being installed never implies `gas_city`.

- `in_process` (default): attempts run inside the service process.
- `gas_city`: each attempt is one real Gas City work item, executed by a
  separate Doom Team worker process.

Choosing `gas_city` fails closed unless both `gas_city_binary` and
`gas_city_city` are set, both are absolute paths, and the binary is executable.
`gas_city_agent` must be a simple name. The city is initialized once,
non-interactively, if the configured path does not exist; an existing city is
used as-is. Job artifacts live under `<state_dir>/gas-city-jobs/`.

The `gc` executable may physically live under any directory, including a tools
directory belonging to another product. Only the path matters: it is operator
configuration, and pointing at such a binary does not make that other product a
Doom Team dependency. Doom Team imports no code from it and uses no part of its
runtime.

### Interpreter requirement

Gas City workers are launched as `sys.executable -m doom_team.worker`, so the
interpreter running the CLI must have the `doom_team` package installed, and
each worker re-reads this same configuration from the environment it inherits.
Workers run from a per-attempt job directory, so importability that depends on
the current directory — or on a relative `PYTHONPATH` — fails there. `PYTHONPATH`
is not a Doom Team setting and is not required by a properly installed runtime;
see [Install](../runbooks/INSTALL.md).

## Provider routing

`provider_order`, `forbidden_providers`, and `allow_fallbacks` are sent to
OpenRouter as request-body routing preferences, and only when `model_provider`
is `openrouter`; for any other provider they are omitted so a plain
OpenAI-compatible endpoint is not sent vocabulary it cannot read.

Doom Team performs no client-side provider failover of its own. Each inference
is a single request to `<provider_base_url>/v1/chat/completions`; a failure is
normalized (`timeout`, `overload`, `rejection`, `malformed`) and raised. Any
fallback between providers is performed by OpenRouter, not by Doom Team.

## Credentials and secrets

- Credentials are referenced by environment variable name
  (`provider_api_key_env`); values are never stored in files or code.
- The referenced environment variable must be present and non-empty at
  startup; otherwise startup fails closed.
- Secret values are redacted (`[REDACTED]`) from reprs, `log_line()`, error
  messages, and evidence views (`as_evidence_dict()`).
- Private reasoning contents from inference responses are never persisted
  into evidence or logs; only token counts and provenance are recorded.

## Example

```bash
export DOOM_[REDACTED]
export DOOM_MODEL_PROVIDER='nvidia'
python -c "from doom_team.config import ConfigLoader; s = ConfigLoader().load(state_dir='/var/lib/doom', model_name='deepseek-v4'); print(s.log_line())"
```
