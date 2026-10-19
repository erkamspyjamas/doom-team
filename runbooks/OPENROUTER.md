# Reasoning-enabled two-model operation

The measured runtime profile lives outside the frozen product checkout at
`/home/eko/doom/runtime/doom-team-openrouter/selected-profile.json`.
Use the helper from this repository; it executes the installed CLI:

```bash
bash runbooks/run-openrouter.sh --state-dir /absolute/fresh/state run \
  "<objective>" --destination /absolute/project
bash runbooks/run-openrouter.sh --state-dir /absolute/state status
bash runbooks/run-openrouter.sh --state-dir /absolute/state approve \
  --mission-id <id> --operator <identity> --decision approve
```

State selection is still the CLI's explicit `--state-dir` or caller's
`DOOM_STATE_DIR`. Use fresh state/destination paths for a new mission and
the same state for status, resume, and approval. There is no deployment or
automatic human approval in this helper.

The launcher loads the OpenRouter credential from the existing environment
or, when absent, sources the existing `/home/eko/doom/.env` privately. It
never prints the file. A Python launcher then allowlists the child
environment, dropping unrelated credentials, stale NVIDIA/DOOM settings,
and `PYTHONPATH`. Secrets are never put in argv or the public profile.

## Profiles and model roles

Versioned public candidates:

- `profiles/openrouter-glm-author.json`: GLM 5.3 Flash plans and implements;
  DeepSeek V4.1 Flash 1017 independently performs QA and Reviewer calls.
- `profiles/openrouter-deepseek-author.json`: the same arrangement reversed.

`DOOM_QUALITY_MODEL_ROUTE=fallback` selects the second route for both quality
roles. Each role still has its own independent prompt/call and unchanged
fail-closed gates. The entire route is swapped, including its credential,
model, and allowed endpoints. Availability-only failover remains bounded to
one alternate call; a bad JSON response or quality block cannot trigger
model shopping. A temporary availability failover may consequently use the
author's model for a quality call; durable route events reveal that fact.

The selected profile is copied from one of these candidates after the
predeclared B01/B06/B07/B09 comparison. Its provenance and any provisional
selection are recorded in the independent benchmark repository. It is not
silently switched on every run.

## Reasoning and output budget

Both profiles explicitly send `reasoning.enabled=true`, `effort=high`, and
`exclude=true` through OpenRouter. Excluding reasoning hides the private
text; it does not turn reasoning off. JSON object output is requested for
the product's existing strict parser contracts. Total completion budget is
65,536 tokens, shared by reasoning and visible output; temperature is 1.0,
top_p is 0.95, and streaming is disabled. There is no unsupported seed
parameter sent to the selected Z.ai endpoint.

`provider.require_parameters=true` avoids routing to endpoints that silently
ignore the requested controls. Provider restrictions deliberately avoid
FP4/NVFP4 and slow Flex endpoints: GLM uses `z-ai/fp8` with `siliconflow/fp8`
as an eligible provider alternate; DeepSeek uses `deepinfra/fp8`.
The account's current privacy policy excludes DeepSeek's first-party
endpoint. That policy is respected, never relaxed by these profiles.

Requests are bounded at 600 seconds. A worker gets 4,500 seconds. Three
logical calls with one availability fallback each can use 6 x 600 seconds;
flat fixtures also have two validations bounded at 300 seconds each.
Packaged src-layout candidates additionally build/install offline in a fresh
environment, with separate 300-second command bounds. These are ceilings,
not a guarantee that every stage can consume its maximum within the worker
budget. Worker and mission caps remain authoritative for larger projects.
Canaries retain the frozen 5,400-second mission policy, four base attempts,
and existing one-shot quality reserve. Long tasks can still hit that mission
limit; larger token budgets are ceilings, not promises to spend them.

## New settings (explicit and opt-in)

| Setting | Values / behavior |
|---|---|
| `DOOM_OPENROUTER_REASONING_EFFORT` | unset preserves old payload; explicit `low`, `high`, or `max` enables reasoning |
| `DOOM_OPENROUTER_MAX_TOKENS` | integer 1024..262144; default 65536, sent when reasoning or JSON mode is enabled |
| `DOOM_OPENROUTER_JSON_MODE` | boolean; default false |
| `DOOM_PROVIDER_ONLY` | CSV of allowed primary OpenRouter endpoint tags |
| `DOOM_FALLBACK_PROVIDER_ONLY` | CSV of allowed alternate OpenRouter endpoint tags |
| `DOOM_QUALITY_MODEL_ROUTE` | `primary` (default, legacy behavior) or `fallback` |

NVIDIA reasoning settings remain independent. Old configurations retain
their request bodies and same-model quality behavior. Primary/fallback
configuration errors still fail visibly; HTTP 410 has not been added to the
availability fallback set.

When explicit OpenRouter reasoning is configured, `PROVIDER_USAGE` events
record numeric prompt/completion/reasoning tokens, reported cost, duration,
finish reason, actual model, and provider. Quality calls have a `/quality`
producer suffix. No reasoning text, prompts, response bodies, or credentials
are recorded by this telemetry. Missing cost remains unknown. Failed calls
may have unreported charges, so summed usage is reported known cost rather
than a guaranteed complete account invoice.

Compatibility was checked against OpenRouter's live catalog and successful
reasoning/JSON probes. General references:

- <https://openrouter.ai/docs/guides/best-practices/reasoning-tokens>
- <https://openrouter.ai/z-ai/glm-5.3-flash>
- <https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017>

Frozen release tags and prior mission evidence remain unchanged. No profile
selection authorizes a push, deployment, or approval of a preserved mission.
