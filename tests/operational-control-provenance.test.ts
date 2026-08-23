import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test, { after } from 'node:test';

// ── scratch DB ──────────────────────────────────────────────────────────────
const scratchDir = mkdtempSync(join(tmpdir(), 'doom-phase3d-provenance-'));
process.env.DOOM_DB = join(scratchDir, 'doom-phase3d.sqlite');

const { db, now, id, seed } = await import('../src/db/index.js');
seed();

const defaultWorkspace = join(scratchDir, 'workspace');
import { mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
mkdirSync(defaultWorkspace, { recursive: true });
execFileSync('git', ['init', '--quiet', '--initial-branch=main'], { cwd: defaultWorkspace });
execFileSync('git', ['config', 'user.email', 'doom-test@example.invalid'], { cwd: defaultWorkspace });
execFileSync('git', ['config', 'user.name', 'Doom Test'], { cwd: defaultWorkspace });
writeFileSync(join(defaultWorkspace, 'README.md'), '# fixture\n');
execFileSync('git', ['add', 'README.md'], { cwd: defaultWorkspace });
execFileSync('git', ['commit', '--quiet', '--message', 'seed'], { cwd: defaultWorkspace });

const baseCommit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: defaultWorkspace }).toString().trim();

// ── Seeded IDs ───────────────────────────────────────────────────────────────
const AGENT_IDS: Record<string, string> = {};
for (const key of ['lead', 'qa', 'reviewer', 'backend']) {
  const row: any = db.prepare(`SELECT id FROM agents WHERE id = ?`).get(`agent_default_${key}`);
  AGENT_IDS[key] = String(row.id);
}
const teamRow: any = db.prepare("SELECT id FROM teams WHERE id = 'team_default_software'").get();

function baseRefs() {
  const agent: any = db.prepare(`
    SELECT
      a.id AS agent_id,
      av.id AS agent_version_id
    FROM agents a
    JOIN agent_versions av ON av.agent_id=a.id
    WHERE a.enabled=1
    ORDER BY a.id,av.version_no DESC
    LIMIT 1
  `).get();

  const runtime: any = db.prepare(`
    SELECT id FROM runtimes WHERE enabled=1 ORDER BY id LIMIT 1
  `).get();

  const model: any = db.prepare(`
    SELECT id FROM models WHERE enabled=1 ORDER BY id LIMIT 1
  `).get();

  return {
    agentId: String(agent.agent_id),
    agentVersionId: String(agent.agent_version_id),
    runtimeId: String(runtime.id),
    modelId: String(model.id),
  };
}

function createMissionFixture(overrides: Partial<{
  status: string;
  taskStatus: string;
  taskErrorText: string;
  withExecution: boolean;
}> = {}) {
  const refs = baseRefs();
  const n = Date.now() + Math.floor(Math.random() * 10000);
  const t = now();

  const teamId = `phase3d_team_${n}`;
  const missionId = `phase3d_mission_${n}`;
  const taskId = `phase3d_task_${n}`;
  const taskKey = `T${n}`;
  const executionId = overrides.withExecution ? `phase3d_execution_${n}` : null;

  db.prepare(`
    INSERT INTO teams (id, name, description, lead_agent_id, enabled, created_at, updated_at)
    VALUES (?,?,?,?,?,?,?)
  `).run(teamId, `Phase3D Team ${n}`, 'Phase3D provenance fixture', refs.agentId, 1, t, t);

  db.prepare(`
    INSERT INTO missions (id, title, objective, team_id, lead_agent_id, status, planning_execution_id, plan_json, error_text, created_at, updated_at, started_at, ended_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(missionId, `Phase3D Mission ${n}`, 'Provenance regression', teamId, refs.agentId, overrides.status ?? 'running', null, '{}', null, t, t, t, null);

  // Add agent as team member so reassign can work
  db.prepare(`
    INSERT OR IGNORE INTO team_members (team_id, agent_id, role_label, priority, can_delegate)
    VALUES (?, ?, ?, ?, ?)
  `).run(teamId, refs.agentId, 'developer', 100, 0);

  // Create initial epoch reset event so recovery_attempts have a valid epoch_event_id
  db.prepare(`
    INSERT INTO mission_events (mission_id, task_id, event_type, payload_json, created_at)
    VALUES (?, NULL, 'lead.recovery_budget_epoch_reset', '{}', ?)
  `).run(missionId, t);

  if (executionId) {
    db.prepare(`
      INSERT INTO executions (id, agent_id, agent_version_id, runtime_id, model_id, external_session_id, task_text, status, result_text, error_text, started_at, ended_at, snapshot_json)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(executionId, refs.agentId, refs.agentVersionId, refs.runtimeId, refs.modelId, null, 'Phase3D provenance execution', 'done', null, null, t, t, '{}');
  }

  db.prepare(`
    INSERT INTO mission_tasks (id, mission_id, task_key, sequence_no, title, instruction, expected_outcome, work_mode, description, acceptance_criteria_json, assigned_agent_id, status, priority, execution_id, attempt_no, result_text, error_text, created_at, updated_at, started_at, ended_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(taskId, missionId, taskKey, 0, `Phase3D Task ${n}`, 'Do work', '', 'implement', 'Provenance task', '[]', refs.agentId, overrides.taskStatus ?? 'failed', 100, executionId, 1, null, overrides.taskErrorText ?? 'prior failure', t, t, t, t);

  return { missionId, taskId, taskKey, teamId, assignedAgentId: refs.agentId, executionId };
}

after(() => {
  try { db.close(); } catch {}
  try { rmSync(scratchDir, { recursive: true, force: true }); } catch {}
});

// ══════════════════════════════════════════════════════════════════════════════
// Tests
// ══════════════════════════════════════════════════════════════════════════════

test('pauseMission persists source and reason in mission_events', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { pauseMission } = await import('../src/missions.js');
  pauseMission(missionId, 'manual', 'Operator requested pause');

  const evt = db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? AND event_type='mission.paused' ORDER BY id DESC LIMIT 1
  `).get(missionId) as any;

  assert.ok(evt, 'mission.paused event must be recorded');
  const payload = JSON.parse(evt.payload_json);
  assert.equal(payload.source, 'manual', 'source must be manual');
  assert.equal(payload.reason, 'Operator requested pause', 'reason must be preserved');
});

test('pauseMission with autonomous source preserves autonomous provenance', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { pauseMission } = await import('../src/missions.js');
  pauseMission(missionId, 'autonomous', 'Autonomous budget exhausted');

  const evt = db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? AND event_type='mission.paused' ORDER BY id DESC LIMIT 1
  `).get(missionId) as any;

  const payload = JSON.parse(evt.payload_json);
  assert.equal(payload.source, 'autonomous', 'source must be autonomous');
  assert.equal(payload.reason, 'Autonomous budget exhausted', 'reason must be preserved');
});

test('retryMissionTask writes recovery_attempts with correct source', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { retryMissionTask } = await import('../src/missions.js');
  retryMissionTask(missionId, taskId, 'manual', 'User requested retry');

  const attempt = db.prepare(`
    SELECT * FROM recovery_attempts WHERE mission_id=? AND task_id=? ORDER BY id DESC LIMIT 1
  `).get(missionId, taskId) as any;

  assert.ok(attempt, 'recovery_attempts row must exist');
  assert.equal(attempt.source, 'manual:lead.retry_task', 'source must include manual prefix');
  assert.equal(attempt.reason, 'User requested retry', 'reason must be preserved');
});

test('retryMissionTask with autonomous source writes correct source', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { retryMissionTask } = await import('../src/missions.js');
  retryMissionTask(missionId, taskId, 'autonomous', 'Autonomous recovery');

  const attempt = db.prepare(`
    SELECT * FROM recovery_attempts WHERE mission_id=? AND task_id=? ORDER BY id DESC LIMIT 1
  `).get(missionId, taskId) as any;

  assert.equal(attempt.source, 'autonomous:lead.retry_task', 'source must include autonomous prefix');
});

test('reassignMissionTask writes recovery_attempts with correct source', async () => {
  const { missionId, taskId, assignedAgentId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { reassignMissionTask } = await import('../src/missions.js');
  reassignMissionTask(missionId, taskId, assignedAgentId, 'manual', 'User reassigned');

  const attempt = db.prepare(`
    SELECT * FROM recovery_attempts WHERE mission_id=? AND task_id=? ORDER BY id DESC LIMIT 1
  `).get(missionId, taskId) as any;

  assert.ok(attempt, 'recovery_attempts row must exist');
  assert.equal(attempt.source, 'manual:lead.reassign_task', 'source must include manual prefix');
  assert.equal(attempt.reason, 'User reassigned', 'reason must be preserved');
});

test('reassignMissionTask with autonomous source writes correct source', async () => {
  const { missionId, taskId, assignedAgentId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { reassignMissionTask } = await import('../src/missions.js');
  reassignMissionTask(missionId, taskId, assignedAgentId, 'autonomous', 'Autonomous reassign');

  const attempt = db.prepare(`
    SELECT * FROM recovery_attempts WHERE mission_id=? AND task_id=? ORDER BY id DESC LIMIT 1
  `).get(missionId, taskId) as any;

  assert.equal(attempt.source, 'autonomous:lead.reassign_task', 'source must include autonomous prefix');
});

test('cancelMissionTask records source and reason in task.cancelled event', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { cancelMissionTask } = await import('../src/missions.js');
  cancelMissionTask(missionId, taskId, 'cancel', 'manual', 'User cancelled');

  const evt = db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? AND event_type='task.cancelled' ORDER BY id DESC LIMIT 1
  `).get(missionId) as any;

  assert.ok(evt, 'task.cancelled event must be recorded');
  const payload = JSON.parse(evt.payload_json);
  assert.equal(payload.mode, 'cancel', 'mode must be cancel');
  assert.equal(payload.source, 'manual', 'source must be manual');
  assert.equal(payload.reason, 'User cancelled', 'reason must be preserved');
});

test('cancelMissionTask kill mode records source and reason', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { cancelMissionTask } = await import('../src/missions.js');
  cancelMissionTask(missionId, taskId, 'kill', 'autonomous', 'Autonomous kill');

  const evt = db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? AND event_type='task.killed' ORDER BY id DESC LIMIT 1
  `).get(missionId) as any;

  assert.ok(evt, 'task.killed event must be recorded');
  const payload = JSON.parse(evt.payload_json);
  assert.equal(payload.mode, 'kill', 'mode must be kill');
  assert.equal(payload.source, 'autonomous', 'source must be autonomous');
  assert.equal(payload.reason, 'Autonomous kill', 'reason must be preserved');
});

test('stopMission records source and reason in mission.cancelled event', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { stopMission } = await import('../src/missions.js');
  stopMission(missionId, 'cancel', 'manual', 'User stopped mission');

  const evt = db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? AND event_type='mission.cancelled' ORDER BY id DESC LIMIT 1
  `).get(missionId) as any;

  assert.ok(evt, 'mission.cancelled event must be recorded');
  const payload = JSON.parse(evt.payload_json);
  assert.equal(payload.mode, 'cancel', 'mode must be cancel');
  assert.equal(payload.source, 'manual', 'source must be manual');
  assert.equal(payload.reason, 'User stopped mission', 'reason must be preserved');
});

test('stopMission kill mode records source and reason', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { stopMission } = await import('../src/missions.js');
  stopMission(missionId, 'kill', 'autonomous', 'Autonomous kill');

  const evt = db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? AND event_type='mission.killed' ORDER BY id DESC LIMIT 1
  `).get(missionId) as any;

  assert.ok(evt, 'mission.killed event must be recorded');
  const payload = JSON.parse(evt.payload_json);
  assert.equal(payload.mode, 'kill', 'mode must be kill');
  assert.equal(payload.source, 'autonomous', 'source must be autonomous');
  assert.equal(payload.reason, 'Autonomous kill', 'reason must be preserved');
});

test('retryMissionTask fails closed when source is missing', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { retryMissionTask } = await import('../src/missions.js');
  // @ts-expect-error - intentionally passing invalid source
  assert.throws(() => retryMissionTask(missionId, taskId, 'invalid', 'reason'), /INVALID_SOURCE|expected/);
});

test('reassignMissionTask fails closed when source is missing', async () => {
  const { missionId, taskId, assignedAgentId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { reassignMissionTask } = await import('../src/missions.js');
  // @ts-expect-error - intentionally passing invalid source
  assert.throws(() => reassignMissionTask(missionId, taskId, assignedAgentId, 'invalid', 'reason'), /INVALID_SOURCE|expected/);
});

test('cancelMissionTask fails closed when source is invalid', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { cancelMissionTask } = await import('../src/missions.js');
  // @ts-expect-error - intentionally passing invalid source
  await assert.rejects(cancelMissionTask(missionId, taskId, 'cancel', 'invalid', 'reason'), /INVALID_SOURCE/);
});

test('stopMission fails closed when source is invalid', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { stopMission } = await import('../src/missions.js');
  // @ts-expect-error - intentionally passing invalid source
  await assert.rejects(stopMission(missionId, 'cancel', 'invalid', 'reason'), /INVALID_SOURCE/);
});

test('pauseMission fails closed when source is invalid', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { pauseMission } = await import('../src/missions.js');
  // @ts-expect-error - intentionally passing invalid source
  assert.throws(() => pauseMission(missionId, 'invalid', 'reason'), /INVALID_SOURCE/);
});

test('retryMissionTask does not mutate task when execution is still running', async () => {
  const { missionId, taskId, executionId } = createMissionFixture({ taskStatus: 'running', withExecution: true });

  const { retryMissionTask } = await import('../src/missions.js');
  assert.throws(() => retryMissionTask(missionId, taskId, 'manual', 'reason'), /MISSION_TASK_NOT_RETRYABLE/);

  const task: any = db.prepare(`SELECT status, execution_id FROM mission_tasks WHERE id=?`).get(taskId);
  assert.equal(task.status, 'running', 'task status must not change');
  assert.equal(task.execution_id, executionId, 'execution_id must not change');
});

test('reassignMissionTask fails closed when task is running', async () => {
  const { missionId, taskId, assignedAgentId, executionId } = createMissionFixture({ taskStatus: 'running', withExecution: true });

  const { reassignMissionTask } = await import('../src/missions.js');
  assert.throws(() => reassignMissionTask(missionId, taskId, assignedAgentId, 'manual', 'reason'), /MISSION_TASK_RUNNING/);
});

test('cancelMissionTask fails closed when task is done', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'done', withExecution: true });

  const { cancelMissionTask } = await import('../src/missions.js');
  await assert.rejects(cancelMissionTask(missionId, taskId, 'cancel', 'manual', 'reason'), /MISSION_TASK_ALREADY_DONE/);
});

test('stopMission fails closed when mission is done', async () => {
  const { missionId } = createMissionFixture({ status: 'done' });

  const { stopMission } = await import('../src/missions.js');
  await assert.rejects(stopMission(missionId, 'cancel', 'manual', 'reason'), /MISSION_ALREADY_TERMINAL/);
});

test('runMission throws when failed tasks exist', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { pauseMission, runMission } = await import('../src/missions.js');
  pauseMission(missionId, 'manual', 'pause');

  // runMission should throw because there's a failed task
  assert.throws(() => runMission(missionId), /MISSION_HAS_FAILED_TASKS_RETRY_FIRST/);
});

test('recovery_attempts ledger distinguishes manual from autonomous for retry/reassign', async () => {
  const { missionId: m1, taskId: t1, refs: r1 } = createMissionFixture({ taskStatus: 'failed', withExecution: false });
  const { missionId: m2, taskId: t2, refs: r2 } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { retryMissionTask, reassignMissionTask } = await import('../src/missions.js');

  retryMissionTask(m1, t1, 'manual', 'User retry');
  retryMissionTask(m2, t2, 'autonomous', 'Auto retry');

  const manualAttempt = db.prepare(`SELECT source FROM recovery_attempts WHERE mission_id=? AND task_id=?`).get(m1, t1) as any;
  const autoAttempt = db.prepare(`SELECT source FROM recovery_attempts WHERE mission_id=? AND task_id=?`).get(m2, t2) as any;

  assert.ok(manualAttempt.source.startsWith('manual:'), 'manual attempt source must start with manual:');
  assert.ok(autoAttempt.source.startsWith('autonomous:'), 'autonomous attempt source must start with autonomous:');
});

test('pauseMission creates mission_events with prior state context', async () => {
  const { missionId } = createMissionFixture({ status: 'running' });

  const { pauseMission } = await import('../src/missions.js');
  pauseMission(missionId, 'manual', 'Investigation needed');

  const evt = db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? AND event_type='mission.paused' ORDER BY id DESC LIMIT 1
  `).get(missionId) as any;

  assert.ok(evt.created_at, 'timestamp must be present');
  const payload = JSON.parse(evt.payload_json);
  assert.equal(payload.source, 'manual');
  assert.equal(payload.reason, 'Investigation needed');
});

test('retryMissionTask ledger entry includes budget and scope info', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { retryMissionTask } = await import('../src/missions.js');
  retryMissionTask(missionId, taskId, 'manual', 'Test retry');

  const attempt = db.prepare(`
    SELECT * FROM recovery_attempts WHERE mission_id=? AND task_id=? ORDER BY id DESC LIMIT 1
  `).get(missionId, taskId) as any;

  assert.ok(attempt.mission_budget > 0, 'mission_budget must be set');
  assert.ok(attempt.scope_budget > 0, 'scope_budget must be set');
  assert.equal(attempt.kind, 'task');
  assert.equal(attempt.scope_type, 'task');
  assert.equal(attempt.terminal_outcome, null, 'initial state must be non-terminal');
});

test('cancelMissionTask and stopMission are not recovery attempts', async () => {
  const { missionId, taskId } = createMissionFixture({ taskStatus: 'failed', withExecution: false });

  const { cancelMissionTask, stopMission } = await import('../src/missions.js');
  cancelMissionTask(missionId, taskId, 'cancel', 'manual', 'Not a recovery');
  stopMission(missionId, 'cancel', 'manual', 'Not a recovery');

  // Verify no recovery_attempts rows were created for cancel/kill
  const attempts = db.prepare(`SELECT COUNT(*) as c FROM recovery_attempts WHERE mission_id=?`).get(missionId) as any;
  assert.equal(attempts.c, 0, 'cancel/kill must not create recovery_attempts rows');
});