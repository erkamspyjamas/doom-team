import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

// ── Test DB bootstrap ────────────────────────────────────────────────────────
// Build a fresh in-memory database and inject it as the singleton.  This
// avoids scratch-DB path issues and gives us full control over the DB
// lifecycle within each test worker. ───────────────────────────────────────────

const scratchDir = mkdtempSync(join(tmpdir(), 'doom-phase3c-'));
const scratchDb  = join(scratchDir, 'doom-phase3c.sqlite');
process.env.DOOM_DB = scratchDb;

// DOOM_DB was set above before any application module is evaluated.
// Import the real DB module against that disposable scratch path.
// Do not add test-only APIs to production src/db/index.ts.
const dbApi = await import('../src/db/index.js');
const db = dbApi.db;
const now = dbApi.now;
const id = dbApi.id;

const SCHEMA = (await import('../src/db/schema.js')).SCHEMA;

// ─── In-memory standalone DB helpers (for isolated ledger tests) ─────────────

import Database from 'better-sqlite3';

function standaloneDb() {
  const d = new Database(':memory:');
  d.pragma('foreign_keys = OFF');
  d.exec(SCHEMA);
  return d;
}

// ─── Module under test ────────────────────────────────────────────────────────

const {
  isMissionTerminallyExhausted,
  terminalExhaustionReason,
  ledgerInitFromAttemptStarted,
  overrideRecoveryEpoch,
  replanMission,
  runMission,
  recoveryEpochStartId,
  appliedTaskRecoveryCount,
  appliedMissionRecoveryCount,
  appliedFinalGateRecoveryCount,
} = await import('../src/missions.js');

// ─── Fixture helpers ──────────────────────────────────────────────────────────

const MISSION_PREFIX = 'p3c_';

function uid() {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

function missionId_(suffix = ''): string {
  return `${MISSION_PREFIX}${suffix || uid()}`;
}

/**
 * Insert a mission row into the primary scratch database.
 * Mirrors the test helper in final-gate-status.test.ts.
 *
 * @param errorText - error_text to set; empty string means NULL.
 * @param status - mission status
 * @param taskCountHint - if > 0, mode becomes 'shared'.
 */
function makeMission(
  errorText: string = '',
  status: string = 'new',
  taskCountHint = 0,
): string {
  const id = missionId_();
  const agentId = 'agent_default_lead';
  const teamId = 'team_default_software';
  const t = now();
  const mode = taskCountHint > 0 ? 'shared' : 'isolated';
  const wsPath = join('/tmp', 'ws', id);

  mkdirSync(wsPath, { recursive: true });

  // Insert missions FIRST so mission_workspaces FK (mission_id → missions.id) is satisfied.
  db.prepare(
    `INSERT INTO missions
       (id, title, objective, team_id, lead_agent_id, status,
        plan_json, error_text, created_at, updated_at)
     VALUES (?, 'm', 'obj', ?, ?, ?, '{}', ?, ?, ?)`
  ).run(id, teamId, agentId, status,
        errorText || null,
        t, t);

  db.prepare(
    `INSERT OR REPLACE INTO mission_workspaces
       (mission_id, mode, base_workspace, status, created_at, updated_at)
     VALUES (?, ?, ?, 'ready', ?, ?)`
  ).run(id, mode, wsPath, t, t);

  return id;
}

/**
 * exhaustMission writes an epoch event and an exhausted ledger row for
 * the mission so that count-based checks reflect consumption.
 */
function exhaustMission(missionId: string, errorText: string): void {
  const t = now();
  const epochEvtId = Number(
    db.prepare(
      `INSERT INTO mission_events (mission_id, event_type, payload_json, created_at)
       VALUES (?, 'lead.recovery_budget_epoch_reset', '{}', ?)`
    ).run(missionId, t).lastInsertRowid
  );

  db.prepare(
    `INSERT OR REPLACE INTO recovery_attempts
       (mission_id, task_id, kind, scope_type, epoch_event_id,
        attempt_no, mission_budget, scope_budget, source, reason,
        terminal_outcome, terminal_reason, terminal_event_id,
        created_at, terminalized_at)
     VALUES (?, NULL, 'task', 'task', ?, 1, 3, 3,
             'lead.recovery_attempt_started', 'RECOVERY_BUDGET_EXHAUSTED',
             'exhausted', 'RECOVERY_BUDGET_EXHAUSTED', ?, ?, ?)`
  ).run(missionId, epochEvtId, epochEvtId, t, t);

  db.prepare(
    `UPDATE missions SET status='blocked', error_text=?, updated_at=? WHERE id=?`
  ).run(errorText, now(), missionId);
}

/** Best-effort cleanup between tests. */
function cleanupMission(missionId: string): void {
  try {
    db.pragma('foreign_keys = OFF');
    db.prepare('DELETE FROM mission_task_workspaces WHERE mission_id=?').run(missionId);
    db.prepare('DELETE FROM mission_tasks WHERE mission_id=?').run(missionId);
    db.prepare('DELETE FROM mission_gates WHERE mission_id=?').run(missionId);
    db.prepare('DELETE FROM recovery_attempts WHERE mission_id=?').run(missionId);
    db.prepare('DELETE FROM mission_events WHERE mission_id=?').run(missionId);
    db.prepare('DELETE FROM mission_workspaces WHERE mission_id=?').run(missionId);
    db.prepare('DELETE FROM missions WHERE id=?').run(missionId);
    db.pragma('foreign_keys = ON');
  } catch { /* best-effort */ }
}

/** Ensure no stray phase-3c missions remain from prior test failures. */
function cleanupAllPhase3c(): number {
  let rows: { id: string }[] = [];
  try {
    rows = db.prepare(
      `SELECT id FROM missions WHERE id LIKE ?`
    ).all(`${MISSION_PREFIX}%`) as { id: string }[];
  } catch (e: any) {
    console.error('[cleanupAllPhase3c SELECT ERROR]', e.message, e.stack);
    return -1;
  }
  for (const id of rows.map(r => r.id)) {
    try { cleanupMission(id); } catch (e: any) {
      // Non-fatal: log but don't rethrow (avoid breaking module evaluation)
      console.error('[cleanupMission err]', id, e.message);
    }
  }
  return rows.length;
}

// ─── Module-level pre-test cleanup ───────────────────────────────────────────

cleanupAllPhase3c();

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-1: replanMission must not reset terminal exhaustion
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C1-a: replanMission rejects AUTONOMOUS_RECOVERY_EXHAUSTED (terminal guard)',
  async () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      let threw = null;
      try {
        await replanMission(id);
      } catch (err: any) {
        threw = String(err?.message ?? err);
      }
      assert.ok(
        /MISSION_TERMINALLY_EXHAUSTED/.test(threw),
        `expected MISSION_TERMINALLY_EXHAUSTED, got: ${threw}`
      );
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C1-b: replanMission rejects FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:qa',
  async () => {
    const id = makeMission('FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:qa', 'blocked');
    try {
      let threw = null;
      try {
        await replanMission(id);
      } catch (err: any) {
        threw = String(err?.message ?? err);
      }
      assert.ok(
        /MISSION_TERMINALLY_EXHAUSTED/.test(threw),
        `expected MISSION_TERMINALLY_EXHAUSTED, got: ${threw}`
      );
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C1-c: replanMission rejects FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:reviewer',
  async () => {
    const id = makeMission('FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:reviewer', 'blocked');
    try {
      let threw = null;
      try {
        await replanMission(id);
      } catch (err: any) {
        threw = String(err?.message ?? err);
      }
      assert.ok(
        /MISSION_TERMINALLY_EXHAUSTED/.test(threw),
        `expected MISSION_TERMINALLY_EXHAUSTED, got: ${threw}`
      );
    } finally {
      cleanupMission(id);
    }
  }
);

async function waitForRunMissionLoopToSettle(
  missionId: string,
  timeoutMs = 2000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const row = db.prepare(
      `SELECT status, error_text FROM missions WHERE id=?`
    ).get(missionId) as any;

    // These positive runMission fixtures intentionally contain no tasks.
    // missionLoop therefore deterministically reaches this terminal fixture
    // state once its asynchronous startup has finished.
    if (
      row?.status === 'blocked' &&
      row?.error_text === 'MISSION_HAS_NO_TASKS'
    ) {
      // Let missionLoop's finally block run before fixture deletion.
      await new Promise<void>((resolve) => setImmediate(resolve));
      return;
    }

    await new Promise<void>((resolve) => setTimeout(resolve, 10));
  }

  throw new Error(
    `TEST_MISSION_LOOP_DID_NOT_SETTLE:${missionId}`
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-2: runMission must not open replacement execution
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C2-a: runMission rejects AUTONOMOUS_RECOVERY_EXHAUSTED (restart guard)',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      assert.throws(
        () => runMission(id),
        /MISSION_TERMINALLY_EXHAUSTED/,
      );
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C2-b: runMission does NOT throw for non-exhaustion blocked state (LEAD_ESCALATION_REQUIRED)',
  async () => {
    // taskCountHint>0 selects the already-ready shared workspace fixture.
    // No real worktree preparation is needed for this guard regression.
    const id = makeMission(
      'LEAD_ESCALATION_REQUIRED:needs_human',
      'blocked',
      1,
    );

    try {
      let threwExhaustion = false;
      let started: any = null;

      try {
        started = runMission(id);
      } catch (err: any) {
        if (
          /MISSION_TERMINALLY_EXHAUSTED/.test(
            String(err?.message ?? err)
          )
        ) {
          threwExhaustion = true;
        }
      }

      assert.equal(
        threwExhaustion,
        false,
        'ESCALATION_REQUIRED must not trigger MISSION_TERMINALLY_EXHAUSTED',
      );

      assert.equal(
        started?.status,
        'running',
        'runMission must synchronously enter running state',
      );

      // runMission intentionally fires missionLoop in the background.
      // Wait for this zero-task fixture to quiesce before deleting its DB rows.
      await waitForRunMissionLoopToSettle(id);
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-3: overrideRecoveryEpoch clears exhaustion and creates new epoch
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C3-a: overrideRecoveryEpoch clears AUTONOMOUS_RECOVERY_EXHAUSTED',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      const epochBefore = recoveryEpochStartId(db, id);

      const result = overrideRecoveryEpoch({
        missionId: id,
        reason: 'operator restart',
        authorizedBy: 'human:ops',
      });

      // 1. error_text cleared.
      const row = db.prepare(
        `SELECT error_text FROM missions WHERE id=?`
      ).get(id) as any;
      assert.equal(row.error_text, null,
        'error_text must be null after override');

      // 2. New epoch event was recorded (advances from epochBefore).
      const epochAfter = recoveryEpochStartId(db, id);
      assert.ok(
        typeof epochAfter === 'number' && epochAfter > 0,
        'epoch event id must be a positive number after override'
      );

      // 3. Epoch reset event with override attribution.
      const resetEvt = db.prepare(
        `SELECT payload_json FROM mission_events
         WHERE mission_id=? AND event_type='lead.recovery_budget_epoch_reset'
         ORDER BY id DESC LIMIT 1`
      ).get(id) as any;
      assert.ok(resetEvt, 'epoch reset event must exist');
      const resetPayload = JSON.parse(resetEvt.payload_json);
      assert.equal(resetPayload.overridden, true);
      assert.equal(resetPayload.authorizedBy, 'human:ops');
      assert.equal(resetPayload.reason, 'operator restart');
      assert.equal(resetPayload.previousError, 'AUTONOMOUS_RECOVERY_EXHAUSTED');

      // 4. Override audit event.
      const overrides = db.prepare(
        `SELECT payload_json FROM mission_events
         WHERE mission_id=? AND event_type='lead.recovery_epoch_override'`
      ).all(id) as any[];
      assert.equal(overrides.length, 1, 'exactly one override event');
      assert.equal(
        JSON.parse(overrides[0].payload_json).authorizedBy,
        'human:ops',
      );

      // 5. Return value.
      assert.equal(result.missionId, id);
      assert.equal(result.previousError, 'AUTONOMOUS_RECOVERY_EXHAUSTED');
      assert.ok(typeof result.eventId === 'number' && result.eventId > 0);
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C3-b: overrideRecoveryEpoch clears FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:reviewer',
  () => {
    const id = makeMission('FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:reviewer', 'blocked');
    try {
      overrideRecoveryEpoch({
        missionId: id,
        reason: 'reviewer override',
        authorizedBy: 'reviewer-lead',
      });

      const row = db.prepare(
        `SELECT error_text FROM missions WHERE id=?`
      ).get(id) as any;
      assert.equal(row.error_text, null,
        'error_text must be null after override clears final-gate exhaustion');
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C3-c: overrideRecoveryEpoch preserves non-exhaustion error (LEAD_ESCALATION_REQUIRED)',
  () => {
    const prior = 'LEAD_ESCALATION_REQUIRED:decision_needed';
    const id = makeMission(prior, 'blocked');
    try {
      overrideRecoveryEpoch({
        missionId: id,
        reason: 'manual reset',
        authorizedBy: 'ops',
      });

      const row = db.prepare(
        `SELECT error_text FROM missions WHERE id=?`
      ).get(id) as any;
      assert.equal(row.error_text, prior,
        'non-exhaustion error must not be modified by overrideRecoveryEpoch');
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-4: duplicate recovery entry refused (in-progress scope)
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C4-a: ledgerInitFromAttemptStarted refuses second call for same in-progress scope',
  () => {
    const missionId = `ledger_dup_${uid()}`;
    const taskId = `task_${uid()}`;
    const teamId = 'team_default_software';
    const agentId = 'agent_default_lead';
    const t = now();

    try {
      db.prepare(
        `INSERT INTO missions
           (id, title, objective, team_id, lead_agent_id, status, plan_json, created_at, updated_at)
         VALUES (?, 'm', 'obj', ?, ?, 'blocked', '{}', ?, ?)`
      ).run(missionId, teamId, agentId, t, t);

      // Create a mission task so FK constraint passes
      db.prepare(
        `INSERT INTO mission_tasks
           (id, mission_id, task_key, title, description, assigned_agent_id, status, created_at, updated_at)
         VALUES (?, ?, 'tk', 'Test Task', 'desc', ?, 'pending', ?, ?)`
      ).run(taskId, missionId, agentId, t, t);

      // Create an epoch reset event first so epochEventId is valid
      const epochEvtId = Number(
        db.prepare(
          `INSERT INTO mission_events (mission_id, event_type, payload_json, created_at)
           VALUES (?, 'lead.recovery_budget_epoch_reset', '{}', ?)`
        ).run(missionId, t).lastInsertRowid
      );

      const evtId = Number(
        db.prepare(
          `INSERT INTO mission_events (mission_id, event_type, payload_json, created_at)
           VALUES (?, 'start', '{}', ?)`
        ).run(missionId, t).lastInsertRowid
      );

      // First call: creates in-progress row.
      ledgerInitFromAttemptStarted({
        missionId, kind: 'task', scopeTaskId: taskId,
        payload: {}, eventId: evtId, missionBudget: 3, scopeBudget: 3,
        source: 'start',
      });

      // Second call: refused — same scope already in-progress.
      ledgerInitFromAttemptStarted({
        missionId, kind: 'task', scopeTaskId: taskId,
        payload: {}, eventId: evtId, missionBudget: 3, scopeBudget: 3,
        source: 'start',
      });

      // The in-progress row was terminalized as failed by the duplicate check.
      // So there are 0 in-progress rows and 1 failed row.
      const inProg = db.prepare(
        `SELECT COUNT(*) AS c FROM recovery_attempts
         WHERE mission_id=? AND kind='task' AND task_id=? AND terminal_outcome IS NULL`
      ).get(missionId, taskId) as { c: number };
      assert.equal(inProg.c, 0, 'no in-progress row after duplicate was refused');

      // A refused/failed record for the duplicate attempt (the original in-progress row was terminalized).
      const dup = db.prepare(
        `SELECT COUNT(*) AS c FROM recovery_attempts
         WHERE mission_id=? AND kind='task' AND task_id=?
           AND terminal_outcome='failed'
           AND terminal_reason='DUPLICATE_RECOVERY_ENTRY_REFUSED: scope already in-progress'`
      ).get(missionId, taskId) as { c: number };
      assert.equal(dup.c, 1,
        'duplicate attempt must be recorded as failed with DUPLICATE_RECOVERY_ENTRY_REFUSED');
    } finally {
      cleanupMission(missionId);
    }
  }
);

test(
  'C4-b: ledgerInitFromAttemptStarted refuses when scope is already terminalized as exhausted',
  () => {
    const missionId = `ledger_exh_${uid()}`;
    const taskId = `task_${uid()}`;
    const teamId = 'team_default_software';
    const agentId = 'agent_default_lead';
    const t = now();

    try {
      db.prepare(
        `INSERT INTO missions
           (id, title, objective, team_id, lead_agent_id, status, plan_json, created_at, updated_at)
         VALUES (?, 'm', 'obj', ?, ?, 'blocked', '{}', ?, ?)`
      ).run(missionId, teamId, agentId, t, t);

      // Create a mission task so FK constraint passes
      db.prepare(
        `INSERT INTO mission_tasks
           (id, mission_id, task_key, title, description, assigned_agent_id, status, created_at, updated_at)
         VALUES (?, ?, 'tk', 'Test Task', 'desc', ?, 'pending', ?, ?)`
      ).run(taskId, missionId, agentId, t, t);

      // Create an epoch reset event first so epochEventId is valid
      const epochEvtId = Number(
        db.prepare(
          `INSERT INTO mission_events (mission_id, event_type, payload_json, created_at)
           VALUES (?, 'lead.recovery_budget_epoch_reset', '{}', ?)`
        ).run(missionId, t).lastInsertRowid
      );

      const evtExhaust = Number(
        db.prepare(
          `INSERT INTO mission_events (mission_id, event_type, payload_json, created_at)
           VALUES (?, 'exhausted', '{}', ?)`
        ).run(missionId, t).lastInsertRowid
      );

      // Pre-existing exhausted row in the CURRENT epoch.
      db.prepare(
        `INSERT INTO recovery_attempts
           (mission_id, task_id, kind, scope_type, epoch_event_id, source_event_id,
            attempt_no, mission_budget, scope_budget, source, reason,
            terminal_outcome, terminal_reason, terminal_event_id, created_at, terminalized_at)
         VALUES (?, ?, 'task', 'task', ?, ?, 1, 3, 3,
                 'start', 'BUDGET_EXHAUSTED',
                 'exhausted', 'BUDGET_EXHAUSTED', ?, ?, ?)`
      ).run(missionId, taskId, epochEvtId, evtExhaust, evtExhaust, t, t);

      // Try to start a new attempt — should be refused (terminal row blocks insert).
      ledgerInitFromAttemptStarted({
        missionId, kind: 'task', scopeTaskId: taskId,
        payload: {}, eventId: evtExhaust, missionBudget: 3, scopeBudget: 3,
        source: 'budget_exhausted',
      });

      const total = db.prepare(
        `SELECT COUNT(*) AS c FROM recovery_attempts
         WHERE mission_id=? AND kind='task' AND task_id=?`
      ).get(missionId, taskId) as { c: number };
      assert.equal(total.c, 1,
        'no new attempt must be inserted when scope is already exhausted');
    } finally {
      cleanupMission(missionId);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-5: override + subsequent recovery action
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C5-a: replanMission proceeds after override clears exhaustion',
  async () => {
    const id = makeMission(
      'AUTONOMOUS_RECOVERY_EXHAUSTED',
      'blocked',
      1,
    );

    try {
      overrideRecoveryEpoch({
        missionId: id,
        reason: 'operator restart',
        authorizedBy: 'ops',
      });

      // Install a deterministic post-exhaustion replan blocker.
      //
      // replanMission checks terminal exhaustion FIRST and integrated
      // changes SECOND. Therefore receiving
      // MISSION_REPLAN_INTEGRATED_CHANGES_EXIST proves that the explicit
      // override removed the exhaustion guard without allowing the test
      // to enter the real asynchronous planning/replan pipeline.
      const taskId = `task_c5a_${uid()}`;
      const workspaceId = `mtws_c5a_${uid()}`;
      const t = now();

      db.prepare(`
        INSERT INTO mission_tasks (
          id,
          mission_id,
          task_key,
          sequence_no,
          title,
          instruction,
          expected_outcome,
          work_mode,
          description,
          acceptance_criteria_json,
          assigned_agent_id,
          status,
          priority,
          attempt_no,
          created_at,
          updated_at
        )
        VALUES (
          ?, ?, ?, 100,
          'C5-a integrated fixture',
          '',
          '',
          '',
          'Deterministic post-override replan guard fixture',
          '[]',
          'agent_default_lead',
          'done',
          100,
          1,
          ?,
          ?
        )
      `).run(
        taskId,
        id,
        `c5a_${uid()}`,
        t,
        t,
      );

      db.prepare(`
        INSERT INTO mission_task_workspaces (
          id,
          mission_id,
          task_id,
          attempt_no,
          workspace_path,
          branch_name,
          base_commit,
          status,
          commit_hash,
          created_at,
          updated_at
        )
        VALUES (
          ?, ?, ?, 1,
          '/tmp/ws',
          'test/c5a',
          'baseline',
          'integrated',
          'c5a-integrated-proof',
          ?,
          ?
        )
      `).run(
        workspaceId,
        id,
        taskId,
        t,
        t,
      );

      let errorText = '';

      try {
        await replanMission(id);
      } catch (err: any) {
        errorText = String(err?.message ?? err);
      }

      assert.equal(
        /MISSION_TERMINALLY_EXHAUSTED/.test(errorText),
        false,
        'override must remove terminal exhaustion as the replan blocker',
      );

      assert.match(
        errorText,
        /MISSION_REPLAN_INTEGRATED_CHANGES_EXIST/,
        'replan must advance past exhaustion guard to the next deterministic guard',
      );

      const row = db.prepare(
        `SELECT error_text FROM missions WHERE id=?`
      ).get(id) as any;

      assert.equal(
        row.error_text,
        null,
        'override must keep terminal exhaustion marker cleared',
      );
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C5-b: runMission starts mission after override clears exhaustion',
  async () => {
    // Shared/ready workspace keeps this a pure mission-state regression and
    // avoids creating an integration worktree for the test fixture.
    const id = makeMission(
      'AUTONOMOUS_RECOVERY_EXHAUSTED',
      'blocked',
      1,
    );

    try {
      overrideRecoveryEpoch({
        missionId: id,
        reason: 'operator restart',
        authorizedBy: 'ops',
      });

      let threwExhaustion = false;
      let started: any = null;

      try {
        started = runMission(id);
      } catch (err: any) {
        if (
          /MISSION_TERMINALLY_EXHAUSTED/.test(
            String(err?.message ?? err)
          )
        ) {
          threwExhaustion = true;
        }
      }

      assert.equal(
        threwExhaustion,
        false,
        'runMission must not throw MISSION_TERMINALLY_EXHAUSTED after override',
      );

      assert.equal(
        started?.status,
        'running',
        'runMission must synchronously restart the overridden mission',
      );

      assert.equal(
        started?.error_text,
        null,
        'override + runMission must clear the exhaustion marker',
      );

      // Do not delete fixture rows while runMission's detached missionLoop
      // is still using them.
      await waitForRunMissionLoopToSettle(id);
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-6: isMissionTerminallyExhausted helper
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C6-a: isMissionTerminallyExhausted false for new / non-exhausted mission',
  () => {
    const id = makeMission('', 'new');
    try {
      const row = db.prepare(`SELECT * FROM missions WHERE id=?`).get(id) as any;
      assert.equal(isMissionTerminallyExhausted(row), false);
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C6-b: isMissionTerminallyExhausted true for AUTONOMOUS_RECOVERY_EXHAUSTED',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      const row = db.prepare(`SELECT * FROM missions WHERE id=?`).get(id) as any;
      assert.equal(isMissionTerminallyExhausted(row), true);
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C6-c: isMissionTerminallyExhausted true for FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:qa',
  () => {
    const id = makeMission('FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:qa', 'blocked');
    try {
      const row = db.prepare(`SELECT * FROM missions WHERE id=?`).get(id) as any;
      assert.equal(isMissionTerminallyExhausted(row), true);
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C6-d: isMissionTerminallyExhausted false after overrideRecoveryEpoch',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      overrideRecoveryEpoch({ missionId: id, reason: 'reset', authorizedBy: 'test' });
      const row = db.prepare(`SELECT * FROM missions WHERE id=?`).get(id) as any;
      assert.equal(isMissionTerminallyExhausted(row), false,
        'override must clear the terminal exhaustion state');
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C6-e: terminalExhaustionReason null for non-exhaustion blocked states',
  () => {
    const id = makeMission('LEAD_ESCALATION_REQUIRED:decision_needed', 'blocked');
    try {
      const row = db.prepare(`SELECT * FROM missions WHERE id=?`).get(id) as any;
      assert.equal(terminalExhaustionReason(row), null,
        'escalation state must not produce an exhaustion reason');
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C6-f: terminalExhaustionReason includes gate type for final-gate exhaustion',
  () => {
    const id = makeMission('FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:reviewer', 'blocked');
    try {
      const row = db.prepare(`SELECT * FROM missions WHERE id=?`).get(id) as any;
      assert.equal(
        terminalExhaustionReason(row),
        'Final-gate recovery budget exhausted (reviewer).',
      );
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C6-g: terminalExhaustionReason generic for AUTONOMOUS_RECOVERY_EXHAUSTED',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      const row = db.prepare(`SELECT * FROM missions WHERE id=?`).get(id) as any;
      assert.equal(
        terminalExhaustionReason(row),
        'Autonomous recovery budget exhausted.',
      );
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-7: epoch isolation – ledger is append-only across override
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C7: overrideRecoveryEpoch does not alter ledger row count (append-only)',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      exhaustMission(id, 'AUTONOMOUS_RECOVERY_EXHAUSTED');

      // Use mission-level count since exhaustMission creates task_id=NULL rows
      const totalBefore = appliedMissionRecoveryCount(id);
      const epochBefore = recoveryEpochStartId(db, id);

      overrideRecoveryEpoch({
        missionId: id,
        reason: 'epoch reset test',
        authorizedBy: 'test',
      });

      const totalAfter = appliedMissionRecoveryCount(id);
      const epochAfter = recoveryEpochStartId(db, id);

      assert.equal(totalAfter, totalBefore,
        'ledger count must not change after override (append-only invariant)');
      assert.ok(typeof epochAfter === 'number' && epochAfter > 0,
        'new epoch event must exist after override');
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-8: double-override idempotency
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C8: second overrideRecoveryEpoch does not restore the error_text',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      overrideRecoveryEpoch({ missionId: id, reason: 'first', authorizedBy: 'ops' });
      const epoch1 = recoveryEpochStartId(db, id);

      overrideRecoveryEpoch({ missionId: id, reason: 'second', authorizedBy: 'ops' });
      const epoch2 = recoveryEpochStartId(db, id);

      assert.ok(typeof epoch2 === 'number' && epoch2 > 0,
        'second override must produce a valid epoch event id');

      const row = db.prepare(
        `SELECT error_text FROM missions WHERE id=?`
      ).get(id) as any;
      assert.equal(row.error_text, null,
        'error_text must remain null after double override (idempotent)');
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-9: override attribution in event payloads
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C9: overrideRecoveryEpoch records authorizedBy in all events',
  () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      overrideRecoveryEpoch({
        missionId: id,
        reason: 'emergency restart',
        authorizedBy: 'admin:supervisor',
      });

      const resets = db.prepare(
        `SELECT payload_json FROM mission_events
         WHERE mission_id=? AND event_type='lead.recovery_budget_epoch_reset'
         ORDER BY id DESC`
      ).all(id) as any[];

      assert.ok(resets.length >= 1, 'at least one epoch reset event must exist');
      const resetPayload = JSON.parse(resets[0].payload_json);
      assert.equal(resetPayload.authorizedBy, 'admin:supervisor');
      assert.equal(resetPayload.overridden, true);
      assert.equal(resetPayload.previousError, 'AUTONOMOUS_RECOVERY_EXHAUSTED');

      const overrides = db.prepare(
        `SELECT payload_json FROM mission_events
         WHERE mission_id=? AND event_type='lead.recovery_epoch_override'`
      ).all(id) as any[];
      assert.equal(overrides.length, 1, 'exactly one override audit event');
      assert.equal(
        JSON.parse(overrides[0].payload_json).authorizedBy,
        'admin:supervisor',
      );
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-10: resumeAutonomousMissions skips exhausted mission
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C10: resumeAutonomousMissions skips terminally exhausted mission',
  async () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      const { resumeAutonomousMissions } = await import('../src/missions.js');
      const result = resumeAutonomousMissions();

      assert.ok(
        Array.isArray(result.skipped) && result.skipped.includes(id),
        `mission ${id} must appear in skipped list`,
      );
      assert.ok(
        !(Array.isArray(result.resumed) && result.resumed.includes(id)),
        `mission ${id} must NOT appear in resumed list`,
      );
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// PHASE-3C-11: replan exhaustion rejection is durable (no silent mutation)
// ─────────────────────────────────────────────────────────────────────────────

test(
  'C11: replanMission persists its exhaustion rejection — does not silently clear status',
  async () => {
    const id = makeMission('AUTONOMOUS_RECOVERY_EXHAUSTED', 'blocked');
    try {
      let threw = false;
      try {
        await replanMission(id);
      } catch (err: any) {
        if (/MISSION_TERMINALLY_EXHAUSTED/.test(String(err?.message ?? err))) {
          threw = true;
        } else {
          throw err;
        }
      }
      assert.ok(threw, 'replanMission must throw MISSION_TERMINALLY_EXHAUSTED');

      const row = db.prepare(
        `SELECT status, error_text FROM missions WHERE id=?`
      ).get(id) as any;
      assert.equal(row.status, 'blocked', 'status must remain blocked');
      assert.equal(row.error_text, 'AUTONOMOUS_RECOVERY_EXHAUSTED',
        'error_text must remain unchanged after rejected replan');
    } finally {
      cleanupMission(id);
    }
  }
);

test(
  'C12: runMission persists its exhaustion rejection — does not silently set status=running',
  () => {
    const id = makeMission('FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:qa', 'blocked');
    try {
      assert.throws(
        () => runMission(id),
        /MISSION_TERMINALLY_EXHAUSTED/,
      );

      const row = db.prepare(
        `SELECT status FROM missions WHERE id=?`
      ).get(id) as any;
      assert.equal(row.status, 'blocked',
        'runMission must not change status of exhausted mission to running');
    } finally {
      cleanupMission(id);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// Cleanup
// ─────────────────────────────────────────────────────────────────────────────

test.after(() => {
  try { rmSync(scratchDir, { recursive: true, force: true }); } catch { /* ignore */ }
});