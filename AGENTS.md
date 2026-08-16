# Doom Core — Agent Quick Reference

## Commands
```bash
# Setup (run once)
./setup.sh         # installs deps, creates .env, runs typecheck

# Development
./run.sh           # loads .env, runs npm start (tsx src/server.ts)
npm run dev        # tsx watch src/server.ts

# Verification
npm run typecheck  # tsc --noEmit
npm run test       # node --import tsx --test tests/*.test.ts
npm run db:reset   # tsx src/db/reset.ts
```

## Environment
- **Node**: >=22 required (enforced in setup.sh)
- **Config**: Loaded from `.env` (created from `.env.example` on setup)
- **DB**: SQLite at `DOOM_DB` (default: `/home/eko/doom-team/data/doom-v02.sqlite`)
- **Workspace**: `DOOM_DEFAULT_WORKSPACE` (default: `/home/eko/doom-team/projects/doom-test`)
- **Worktrees**: `DOOM_WORKTREE_ROOT` (default: `/home/eko/doom-team/worktrees`)
- **Port**: `DOOM_PORT` (default: 8780)

## Key Architecture
- **Entry**: `src/server.ts` — Fastify HTTP API on `DOOM_HOST:DOOM_PORT`
- **DB**: `src/db/schema.ts` — SQLite schema (missions, tasks, agents, models, skills, worktrees, approvals)
- **Runtime**: `src/runtime/` — qwen-code SDK + generic OpenAI-compatible adapter
- **Missions**: `src/missions.ts` — mission lifecycle (create, plan, replan, run, pause, cancel, recovery)
- **Tasks**: `src/task-contracts.ts` — deterministic work-mode inference from `instruction` field only

## Testing
- Uses Node's built-in test runner via `tsx` loader (`npm test`)
- Tests in `tests/*.test.ts` — import from `../src/*.js` with `.js` extension
- No external test framework dependencies

## Gotchas
- **TypeScript**: ESM only (`"type": "module"`), imports need `.js` extension
- **DB migrations**: Additive column migration runs at startup (see `src/db/index.ts`)
- **Worktrees**: Isolated per-task workspaces under `DOOM_WORKTREE_ROOT`
- **Recovery**: Autonomous mission recovery on startup (`resumeAutonomousMissions()`)
- **Locales**: Default `tr` (Turkish), configurable via `DOOM_DEFAULT_LOCALE`