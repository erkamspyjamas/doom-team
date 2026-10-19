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
| `nvidia_reasoning_effort` | no | `DOOM_NVIDIA_REASONING_EFFORT` | Reasoning effort for the NVIDIA route's request profile: `max` (default), `high`, or `none`; exact lowercase vocabulary, invalid values fail closed |
| `openrouter_reasoning_effort` | no | `DOOM_OPENROUTER_REASONING_EFFORT` | Unset preserves the old OpenRouter payload; explicit `low`, `high`, or `max` enables internal reasoning on both OpenRouter routes and excludes reasoning text from responses |
| `openrouter_max_tokens` | no | `DOOM_OPENROUTER_MAX_TOKENS` | Total reasoning plus visible output budget, integer 1024..262144, default 65536; sent with explicit OpenRouter reasoning or JSON mode |
| `openrouter_json_mode` | no | `DOOM_OPENROUTER_JSON_MODE` | Boolean, default false; request JSON object output and require endpoint support |
| `provider_only` | no | `DOOM_PROVIDER_ONLY` | CSV or JSON list of allowed primary OpenRouter endpoint tags; empty means unrestricted |
| `fallback_provider_only` | with fallback | `DOOM_FALLBACK_PROVIDER_ONLY` | Allowed alternate OpenRouter endpoint tags, independent of the primary restriction |
| `quality_model_route` | no | `DOOM_QUALITY_MODEL_ROUTE` | `primary` (default) or `fallback`; chooses the initial model for independent QA/Reviewer calls, swapping whole routes when needed |
| `fallback_provider` | with fallback | `DOOM_FALLBACK_PROVIDER` | Provider name of the single fallback inference route (e.g. `openrouter`) |
| `fallback_model_name` | with fallback | `DOOM_FALLBACK_MODEL_NAME` | Model served by the fallback route |
| `fallback_base_url` | with fallback | `DOOM_FALLBACK_BASE_URL` | Fallback route base URL, same rules as `provider_base_url` |
| `fallback_api_key_env` | with fallback | `DOOM_FALLBACK_API_KEY_ENV` | Env var name holding the fallback route's own credential |
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

Explicit OpenRouter reasoning is independent of the NVIDIA knob. A configured
OpenRouter reasoning profile sends `reasoning.enabled=true`, the selected
effort, `reasoning.exclude=true`, the total output ceiling, temperature 1.0,
top_p 0.95, and non-streaming output. JSON mode sends a JSON object response
format. These opt-in controls require compatible endpoints rather than being
silently dropped. The default OpenRouter payload is unchanged when neither
option is enabled. Endpoint `only` restrictions follow each route when quality
routing swaps primary and alternate. See [two-model operation](../runbooks/OPENROUTER.md)
for the measured GLM/DeepSeek profiles, usage telemetry, and safe launcher.

Request options are route-specific. `provider_order`, `forbidden_providers`,
and `allow_fallbacks` are sent as request-body routing preferences only to an
`openrouter` route; an `nvidia` route instead carries the canonical NVIDIA
request profile (`temperature=1.0`, `top_p=0.95`, `max_tokens=16384`,
`seed=42`, `stream=false`, plus `reasoning_effort` from
`nvidia_reasoning_effort` — `max` by default, the rc1 historical profile;
`high` and `none` are supported operational profiles whose relative merits
are measured, not assumed). The setting applies only to `nvidia` routes;
an OpenRouter route — including the Flash fallback — never carries it. No route is ever sent another
route's vocabulary. Requests go to `<base_url>/v1/chat/completions`; a base
URL already ending in `/v1` is used as-is, never doubled.

### Provider failover

The canonical production policy is a two-route failover:

- **Primary**: `nvidia` / `deepseek-ai/deepseek-v4-pro-0813` at
  `https://integrate.api.nvidia.com`, maximum reasoning.
- **Fallback**: `openrouter` / `deepseek/deepseek-v4-flash-0731` at
  `https://openrouter.ai/api`, with the OpenRouter preferences above.

When the four `fallback_*` settings are configured (they are required as a
unit, with an independent credential), one logical inference call tries the
primary once and, only for a provider-availability failure — a timeout, a
connection failure, HTTP 408/429/500/502/503/504 — the fallback exactly once.
Never more than two route calls, and never back to the primary. A 400, 401,
403, 404 or 422 from the primary raises unchanged so a misconfigured
credential or model name stays visible, and a successful response never falls
back however its content fares in later validation, QA, or review — fallback
is about reachability, not model quality. A fallback that is used records a
`PROVIDER_FALLBACK` event, and the response's recorded provider/model always
name the route that actually answered. OpenRouter may still select among its
own upstream hosts; that is internal to OpenRouter and separate from this
two-route policy. Without the `fallback_*` settings there is a single route
and failures raise directly.

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
