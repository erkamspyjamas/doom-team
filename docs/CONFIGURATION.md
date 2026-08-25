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
