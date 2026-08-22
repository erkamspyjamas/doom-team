import assert from 'node:assert/strict';
import test from 'node:test';
import Database from 'better-sqlite3';
import {
  listRecoveryAttempts,
  listRecoveryAttemptsEnriched,
  getRecoveryAttempt,
  getRecoveryAttemptCount,
  getTaskRecoveryCount,
  getFinalGateRecoveryCount,
  getMissionRecoveryCount,
} from '../src/recovery-attempts.js';

/** Build an in-memory DB with both tables needed for ledger testing. */
function buildLedgerDb() {
  const database = new Database(':memory:');
  database.pragma('foreign_keys = OFF');
  database.exec(`
    CREATE TABLE mission_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      mission_id TEXT NOT NULL,
      task_id TEXT,
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    )
  `);
  database.exec(`
    CREATE TABLE recovery_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      mission_id TEXT NOT NULL,
      task_id TEXT,
      kind TEXT NOT NULL CHECK (kind IN ('task','final_gate','planning')),
      scope_type TEXT NOT NULL DEFAULT 'task'
        CHECK (scope_type IN ('task','final_gate','planning','mission')),
      epoch_event_id INTEGER NOT NULL,
      source_event_id INTEGER,
      attempt_no INTEGER NOT NULL,
      mission_budget INTEGER NOT NULL,
      scope_budget INTEGER NOT NULL,
      source TEXT,
      reason TEXT,
      terminal_outcome TEXT
        CHECK (terminal_outcome IN ('applied','failed','exhausted','refused','escalated',null)),
      terminal_reason TEXT,
      terminal_event_id INTEGER,
      created_at TEXT NOT NULL,
      terminalized_at TEXT,
      UNIQUE(mission_id, kind, scope_type, task_id, attempt_no)
    )
  `);
  return { db: database };
}

/** Directly insert a row into recovery_attempts (bypassing event trigger). */
function insertAttempt(
  db: Database,
  input: {
    missionId: string;
    taskId: string | null;
    kind: 'task' | 'final_gate' | 'planning';
    scopeType: 'task' | 'final_gate' | 'planning' | 'mission';
    attemptNo: number;
    missionBudget?: number;
    scopeBudget?: number;
    source?: string;
    reason?: string;
    terminalOutcome?: 'applied' | 'failed' | 'exhausted' | 'refused' | 'escalated' | null;
    terminalReason?: string | null;
    epochEventId?: number;
    sourceEventId?: number | null;
  },
) {
  return db.prepare(`
    INSERT INTO recovery_attempts
      (mission_id, task_id, kind, scope_type, epoch_event_id, source_event_id,
       attempt_no, mission_budget, scope_budget, source, reason,
       terminal_outcome, terminal_reason, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    input.missionId,
    input.taskId,
    input.kind,
    input.scopeType,
    input.epochEventId ?? 0,
    input.sourceEventId ?? null,
    input.attemptNo,
    input.missionBudget ?? 8,
    input.scopeBudget ?? 3,
    input.source ?? 'manual_test',
    input.reason ?? null,
    input.terminalOutcome ?? null,
    input.terminalReason ?? null,
    new Date().toISOString(),
  );
}

// ─── Tests ────────────────────────────────────────────────────────────────

test('ledger records one row per task recovery attempt', () => {
  const { db } = buildLedgerDb();

  insertAttempt(db, {
    missionId: 'mission_1',
    taskId: 'task_1',
    kind: 'task',
    scopeType: 'task',
    attemptNo: 1,
    source: 'ledgerInitFromAttemptStarted',
    sourceEventId: 1,
  });
  insertAttempt(db, {
    missionId: 'mission_1',
    taskId: 'task_1',
    kind: 'task',
    scopeType: 'task',
    attemptNo: 2,
    source: 'ledgerInitFromAttemptStarted',
    sourceEventId: 3,
  });

  const attempts = listRecoveryAttempts(db, 'mission_1', { kind: 'task' });
  assert.equal(attempts.length, 2, 'exactly 2 task attempts');
  assert.equal(attempts[0].attemptNo, 1);
  assert.equal(attempts[1].attemptNo, 2);
  assert.equal(attempts[0].kind, 'task');
  assert.equal(attempts[0].scopeType, 'task');
  assert.equal(attempts[0].taskId, 'task_1');
  assert.equal(attempts[0].missionBudget, 8);
  assert.equal(attempts[0].scopeBudget, 3);
  assert.equal(attempts[0].source, 'ledgerInitFromAttemptStarted');
});

test('ledger distinguishes final_gate attempts from task attempts', () => {
  const { db } = buildLedgerDb();

  // 2 task attempts for task_1
  insertAttempt(db, { missionId: 'mission_2', taskId: 'task_1', kind: 'task', scopeType: 'task', attemptNo: 1 });
  insertAttempt(db, { missionId: 'mission_2', taskId: 'task_1', kind: 'task', scopeType: 'task', attemptNo: 2 });

  // 1 final_gate attempt (row exists with task_id = null)
  insertAttempt(db, { missionId: 'mission_2', taskId: null, kind: 'final_gate', scopeType: 'final_gate', attemptNo: 1 });

  assert.equal(getTaskRecoveryCount(db, 'mission_2', 'task_1'), 2, 'task scope count');
  assert.equal(getFinalGateRecoveryCount(db, 'mission_2'), 1, 'final_gate scope count');
  assert.equal(getMissionRecoveryCount(db, 'mission_2'), 3, 'total mission count');

  // Separately: final_gate attempt with non-null task_id counts in task scope, not final_gate
  insertAttempt(db, { missionId: 'mission_2', taskId: 'task_1', kind: 'final_gate', scopeType: 'final_gate', attemptNo: 1 });
  assert.equal(getFinalGateRecoveryCount(db, 'mission_2'), 2, 'final_gate attempts with specific task_id');
});

test('detached task events do not count as final_gate after replan', () => {
  // After `replanMission()`, task-scoped events get task_id=NULL in mission_events.
  // In the legacy dual-path system this caused them to be misidentified as
  // final_gate attempts (the final_gate path counts task_id IS NULL events).
  // The unified ledger is immune because kind is stored explicitly per row.
  const { db } = buildLedgerDb();

  // Simulate 2 task-type attempts whose task_id is NULL after attachment detachment
  insertAttempt(db, { missionId: 'mission_3', taskId: null, kind: 'task', scopeType: 'task', attemptNo: 1 });
  insertAttempt(db, { missionId: 'mission_3', taskId: null, kind: 'task', scopeType: 'task', attemptNo: 2 });

  // No actual final_gate type attempts
  assert.equal(getTaskRecoveryCount(db, 'mission_3', null), 2, 'kind=task rows still counted correctly despite NULL task_id');
  assert.equal(getFinalGateRecoveryCount(db, 'mission_3'), 0, 'kind=final_gate count is 0');
});

test('listRecoveryAttempts supports kind and outcome filters', () => {
  const { db } = buildLedgerDb();
  const nowIso = new Date().toISOString();

  const add = (input: any) =>
    db.prepare(`
      INSERT INTO recovery_attempts
        (mission_id, task_id, kind, scope_type, epoch_event_id, source_event_id,
         attempt_no, mission_budget, scope_budget, source, reason,
         terminal_outcome, terminal_reason, created_at)
      VALUES (?, ?, ?, ?, 0, ?, ?, 8, 3, ?, ?, ?, ?, ?)
    `).run(
      input.missionId,
      input.taskId,
      input.kind,
      input.scopeType ?? input.kind,
      input.sourceEventId,
      input.attemptNo,
      input.source,
      input.reason,
      input.terminalOutcome ?? null,
      input.terminalReason ?? null,
      nowIso,
    );

  add({ missionId: 'mission_4', taskId: 'task_a', kind: 'task', attemptNo: 1, sourceEventId: 1, source: 'started', reason: 'first', terminalOutcome: 'applied', terminalReason: 'retry_task applied' });
  add({ missionId: 'mission_4', taskId: 'task_a', kind: 'task', attemptNo: 2, sourceEventId: 2, source: 'started', reason: 'transient error', terminalOutcome: 'failed', terminalReason: 'execution error' });
  add({ missionId: 'mission_4', taskId: null,  kind: 'final_gate', attemptNo: 1, sourceEventId: 3, source: 'started', reason: 'qa failed', terminalOutcome: 'applied', terminalReason: 'remediation_created' });
  add({ missionId: 'mission_4', taskId: 'task_b', kind: 'task', attemptNo: 1, sourceEventId: 4, source: 'started', reason: 'pre-check refusal', terminalOutcome: 'refused', terminalReason: 'RECOVERY_REFUSED_INTEGRATED_TASK' });
  add({ missionId: 'mission_4', taskId: null,  kind: 'planning', attemptNo: 1, sourceEventId: 5, source: 'started', reason: 'plan error' });

  assert.equal(getMissionRecoveryCount(db, 'mission_4'), 5);

  const taskOnly = listRecoveryAttempts(db, 'mission_4', { kind: 'task' });
  assert.equal(taskOnly.length, 3, 'only task kind');

  const applied = listRecoveryAttempts(db, 'mission_4', { terminalOutcome: 'applied' });
  assert.equal(applied.length, 2, 'two applied outcomes');
  assert.equal(applied[0].terminalReason, 'retry_task applied');

  const task_a = listRecoveryAttempts(db, 'mission_4', { kind: 'task', taskId: 'task_a' });
  assert.equal(task_a.length, 2, 'task_a only');

  const firstAttempt = getRecoveryAttempt(db, 1);
  assert.ok(firstAttempt);
  assert.equal(firstAttempt!.kind, 'task');
  assert.equal(firstAttempt!.taskId, 'task_a');
  assert.equal(firstAttempt!.attemptNo, 1);
  assert.equal(firstAttempt!.terminalOutcome, 'applied');
});

test('replan cannot reset or double-create existing attempt state', () => {
  // When a recovery_budget_epoch_reset event fires, the ledger partitions
  // attempts by epoch_event_id (the reset event's id). The attempt_no sequence
  // for each scope must continue; an epoch reset must not collapse prior rows
  // or restart at attempt_no=1.
  const { db } = buildLedgerDb();
  const nowIso = new Date().toISOString();

  const add = (input: any) =>
    db.prepare(`
      INSERT INTO recovery_attempts
        (mission_id, task_id, kind, scope_type, epoch_event_id, source_event_id,
         attempt_no, mission_budget, scope_budget, source, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      input.missionId,
      input.taskId,
      input.kind,
      input.scopeType ?? input.kind,
      input.epochEventId,
      input.sourceEventId,
      input.attemptNo,
      input.missionBudget ?? 8,
      input.scopeBudget ?? 3,
      input.source,
      nowIso,
    );

  // Two task attempts recorded in epoch 0
  add({ missionId: 'mission_5', taskId: 'task_1', kind: 'task', scopeType: 'task', attemptNo: 1, epochEventId: 0, sourceEventId: 1 });
  add({ missionId: 'mission_5', taskId: 'task_1', kind: 'task', scopeType: 'task', attemptNo: 2, epochEventId: 0, sourceEventId: 2 });

  const beforeReset = db.prepare(
    `SELECT COUNT(*) c FROM recovery_attempts WHERE mission_id = ? AND kind = 'task'`
  ).get('mission_5') as { c: number };
  assert.equal(beforeReset.c, 2, 'two pre-epoch-reset attempt rows');

  // Simulate epoch reset with event id=3 → adds epoch_event_id=3 rows going forward
  add({ missionId: 'mission_5', taskId: 'task_1', kind: 'task', scopeType: 'task', attemptNo: 3, epochEventId: 3, sourceEventId: 3 });

  const afterReset = db.prepare(
    `SELECT COUNT(*) c FROM recovery_attempts WHERE mission_id = ? AND kind = 'task'`
  ).get('mission_5') as { c: number };
  assert.equal(afterReset.c, 3, 'three total attempts — not reset to 1');

  const histRows = db.prepare(
    `SELECT id, attempt_no, epoch_event_id FROM recovery_attempts WHERE mission_id = ? ORDER BY id`
  ).all('mission_5') as any[];
  assert.deepEqual(
    histRows.map((r: any) => ({ id: r.id, attempt_no: r.attempt_no, epoch_event_id: r.epoch_event_id })),
    [
      { id: 1, attempt_no: 1, epoch_event_id: 0 },
      { id: 2, attempt_no: 2, epoch_event_id: 0 },
      { id: 3, attempt_no: 3, epoch_event_id: 3 },
    ],
  );
  assert.equal(histRows.length, 3, 'no duplicate rows added during epoch reset');
});

test('restart/replan cannot silently reset consumed budget', () => {
  // On restart, `decideStartupRecovery` uses counts from
  // appliedTaskRecoveryCount (→ getTaskRecoveryCount) to determine if
  // blockedTaskRecoveryAllowed and whether to run/replan. The ledger
  // must preserve those counts across restart/replan boundaries.
  const { db } = buildLedgerDb();
  const nowIso = new Date().toISOString();

  const add = (input: any) =>
    db.prepare(`
      INSERT INTO recovery_attempts
        (mission_id, task_id, kind, scope_type, epoch_event_id, source_event_id,
         attempt_no, mission_budget, scope_budget, source, reason,
         terminal_outcome, created_at)
      VALUES (?, ?, ?, ?, 0, ?, ?, 8, 3, ?, ?, ?, ?)
    `).run(
      input.missionId,
      input.taskId,
      input.kind,
      input.scopeType ?? input.kind,
      input.sourceEventId,
      input.attemptNo,
      input.source,
      input.reason,
      input.terminalOutcome ?? null,
      nowIso,
    );

  // simulate 2 prior task attempts for task_1 (already exhausted the first part of budget)
  add({ missionId: 'mission_6', taskId: 'task_1', kind: 'task', attemptNo: 1, sourceEventId: 1, source: 'started', reason: 'first', terminalOutcome: 'failed' });
  add({ missionId: 'mission_6', taskId: 'task_1', kind: 'task', attemptNo: 2, sourceEventId: 2, source: 'started', reason: 'second', terminalOutcome: 'failed' });

  // A third attempt ought to be attempt_no=3, not "attempt_no=1 (new attempt)"
  const nextExpected = db.prepare(
    `SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_no FROM recovery_attempts WHERE mission_id = ? AND kind = 'task'`
  ).get('mission_6') as { next_no: number };

  assert.equal(nextExpected.next_no, 3, 'next attempt_no must continue from prior max, not restart');

  // Now add a third: it must be 3
  add({ missionId: 'mission_6', taskId: 'task_1', kind: 'task', attemptNo: 3, sourceEventId: 3, source: 'started', reason: 'third' });

  assert.equal(getTaskRecoveryCount(db, 'mission_6', 'task_1'), 3, 'restart must not drop historic attempts');

  const histRows = db.prepare(
    `SELECT attempt_no, terminal_outcome FROM recovery_attempts WHERE mission_id = ? AND kind = 'task' ORDER BY attempt_no`
  ).all('mission_6') as any[];
  assert.deepEqual(
    histRows.map((r: any) => ({ attempt_no: r.attempt_no, terminal_outcome: r.terminal_outcome })),
    [
      { attempt_no: 1, terminal_outcome: 'failed' },
      { attempt_no: 2, terminal_outcome: 'failed' },
      { attempt_no: 3, terminal_outcome: null },
    ],
  );
});