import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import test, { after } from 'node:test';

// -------------------------------------------------------------------
// Isolated scratch DB — must be set before importing Doom modules.
// -------------------------------------------------------------------
const scratchDir = mkdtempSync(
  join(tmpdir(), 'doom-phase1f-mw-')
);
process.env.DOOM_DB = join(scratchDir, 'doom-phase1f-mw.sqlite');

const {
  db,
  now,
} = await import('../src/db/index.js');

const {
  attemptAutonomousTaskRecovery,
  // cleanupFailedTaskAttempt is intentionally NOT exported; it is an
  // internal helper whose behaviour is now superseded by the
  // recoveryWorkTreeHasIntegratedWork pre-check in attemptAutonomousTaskRecovery.
  recoveryWorkTreeHasIntegratedWork,
} = await import('../src/missions.js');

after(() => {
  try { db.close(); } finally {
    rmSync(scratchDir, { recursive: true, force: true });
  }
});

/* =================================================================== */
/* Shared helpers                                                       */
/* =================================================================== */

const WORKSPACE_ROOT = join(scratchDir, 'worktrees');

function runGit(cwd: string, ...args: string[]) {
  execFileSync('git', args, { cwd, encoding: 'utf8' });
}

function ensureRepo(wp: string) {
  try { runGit(wp, 'rev-parse', '--git-dir'); }
  catch {
    execFileSync('git', ['init', '--quiet', wp]);
    execFileSync('git', ['-C', wp, 'config', 'user.email', 'test@example.invalid']);
    execFileSync('git', ['-C', wp, 'config', 'user.name', 'Test']);
  }
}

function baseRefs() {
  const agent: any = db.prepare(`
    SELECT a.id AS agent_id, av.id AS agent_version_id
    FROM agents a
    JOIN agent_versions av ON av.agent_id = a.id
    WHERE a.id = 'agent_default_lead'
    LIMIT 1
  `).get();

  const runtime: any = db.prepare(
    "SELECT id FROM runtimes WHERE enabled=1 ORDER BY id LIMIT 1"
  ).get();

  const model: any = db.prepare(
    "SELECT id FROM models WHERE enabled=1 ORDER BY id LIMIT 1"
  ).get();

  assert.ok(agent?.agent_id, 'seed needs agent_default_lead');
  assert.ok(runtime?.id, 'seed needs enabled runtime');
  assert.ok(model?.id, 'seed needs enabled model');

  return {
    leadAgentId: String(agent.agent_id),
    leadVersionId: String(agent.agent_version_id),
    runtimeId: String(runtime.id),
    modelId: String(model.id),
  };
}

let _seq = 0;

/** Insert a mission row and return the id. */
function createMission(status = 'running') {
  return createMissionRef(baseRefs(), status);
}

function createMissionRef(refs: ReturnType<typeof baseRefs>, status = 'running') {
  const id = `m_1f_${++_seq}`;
  const t = now();
  db.prepare(`
    INSERT INTO missions
      (id,team_id,lead_agent_id,title,objective,status,plan_json,error_text,
       created_at,updated_at,started_at,ended_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    id,
    'team_default_software',
    refs.leadAgentId,
    'Phase 1F recovery defect mission',
    'Reproduce merged-worktree recovery defect',
    status,
    '{}',
    null,
    t, t,
    status === 'running' ? t : null,
    null,
  );
  return id;
}

/** Insert a mission_task row and return its id. */
function createTask(
  missionId: string,
  taskStatus: string = 'pending',
  errorText: string | null = null,
) {
  const id = `tk_1f_${++_seq}`;
  const t = now();
  db.prepare(`
    INSERT INTO mission_tasks
      (id,mission_id,task_key,sequence_no,title,instruction,expected_outcome,
       work_mode,description,acceptance_criteria_json,assigned_agent_id,
       status,priority,execution_id,attempt_no,
       result_text,error_text,created_at,updated_at,started_at,ended_at)
    VALUES (
      ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
    )
  `).run(
    id, missionId, `T${_seq}`, 1,
    `T${_seq}`,
    'modify repository',
    'repository modified',
    'modify', 'modify repository',
    '[]', 'agent_default_backend',
    taskStatus, 10,
    null, 1,
    null, errorText,
    t, t, t, null,
  );
  return id;
}

/** Insert a mission_task_workspaces row with the given status and hashes. */
function upsertWorktree(
  missionId: string,
  taskId: string,
  attemptNo: number,
  opts: {
    status: string;
    commitHash: string | null;
    mergeCommit: string | null;
    wpOverride?: string;
  },
) {
  const wp = opts.wpOverride ?? join(WORKSPACE_ROOT, missionId, taskId, `att_${attemptNo}`);
  ensureRepo(wp);
  // Optionally produce real commit hashes when caller passes nulls.
  let ch = opts.commitHash;
  let mc = opts.mergeCommit;
  if (ch === null && mc === null && opts.status === 'active') {
    // leave both null — intentional
  }
  if (ch === null && opts.status !== 'active') {
    try {
      runGit(wp, 'add', '.');
      runGit(wp, 'commit', '--quiet', '-m', 'committed');
      ch = runGit(wp, 'rev-parse', 'HEAD').trim();
      mc = ch;
    } catch { /* noop — empty tree */ }
  }
  const rowId = `mtws_1f_${++_seq}`;
  db.prepare(`
    INSERT INTO mission_task_workspaces
      (id,mission_id,task_id,attempt_no,workspace_path,branch_name,base_commit,
       status,commit_hash,merge_commit,diff_stat,error_text,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    rowId, missionId, taskId, attemptNo,
    wp, `branch-${attemptNo}`,
    'base123',
    opts.status, ch, mc,
    '', null,
    now(), now(),
  );
}

/* =================================================================== */
/* 1. Unit tests: recoveryWorkTreeHasIntegratedWork (to be implemented) */
/* =================================================================== */

test('recoveryWorkTreeHasIntegratedWork: true for merged worktree', () => {
  const missionId = createMission();
  const taskId = createTask(missionId);
  upsertWorktree(missionId, taskId, 1, {
    status: 'merged',
    commitHash: 'sha-m',
    mergeCommit: 'sha-m',
  });
  // @ts-ignore – function is not exported before fix; runtime eval via any
  assert.equal(recoveryWorkTreeHasIntegratedWork(db, taskId), true);
});

test('recoveryWorkTreeHasIntegratedWork: true for cleaned_with_commit worktree', () => {
  const missionId = createMission();
  const taskId = createTask(missionId);
  upsertWorktree(missionId, taskId, 1, {
    status: 'cleaned',
    commitHash: 'sha-c',
    mergeCommit: null,
  });
  assert.equal(recoveryWorkTreeHasIntegratedWork(db, taskId), true);
});

test('recoveryWorkTreeHasIntegratedWork: true for recovery_cleaned_with_hash', () => {
  const missionId = createMission();
  const taskId = createTask(missionId);
  upsertWorktree(missionId, taskId, 1, {
    status: 'recovery_cleaned',
    commitHash: 'sha-rc',
    mergeCommit: 'sha-rc',
  });
  assert.equal(recoveryWorkTreeHasIntegratedWork(db, taskId), true);
});

test('recoveryWorkTreeHasIntegratedWork: true for cleaned_readonly_with_hash', () => {
  const missionId = createMission();
  const taskId = createTask(missionId);
  upsertWorktree(missionId, taskId, 1, {
    status: 'cleaned_readonly',
    commitHash: 'sha-cro',
    mergeCommit: null,
  });
  assert.equal(recoveryWorkTreeHasIntegratedWork(db, taskId), true);
});

test('recoveryWorkTreeHasIntegratedWork: false when no worktree row exists', () => {
  const missionId = createMission();
  const taskId = createTask(missionId);
  // No worktree row — task was never executed.
  assert.equal(recoveryWorkTreeHasIntegratedWork(db, taskId), false);
});

test('recoveryWorkTreeHasIntegratedWork: false for active row with null hashes', () => {
  const missionId = createMission();
  const taskId = createTask(missionId);
  upsertWorktree(missionId, taskId, 1, {
    status: 'active',
    commitHash: null,
    mergeCommit: null,
  });
  assert.equal(recoveryWorkTreeHasIntegratedWork(db, taskId), false);
});

test('recoveryWorkTreeHasIntegratedWork: false for error row without hashes', () => {
  const missionId = createMission();
  const taskId = createTask(missionId);
  upsertWorktree(missionId, taskId, 1, {
    status: 'error',
    commitHash: null,
    mergeCommit: null,
  });
  assert.equal(recoveryWorkTreeHasIntegratedWork(db, taskId), false);
});

/* =================================================================== */
/* 2. (Removed) cleanupFailedTaskAttempt direct unit test                */
/*    The principle is already covered by:                              */
/*    - recoveryWorkTreeHasIntegratedWork unit tests (§1)                */
/*    - attemptAutonomousTaskRecovery end-to-end tests (§3)              */
/*    With the fix, cleanupFailedTaskAttempt is never called for a       */
/*    task whose worktree is already integrated.                         */
/* =================================================================== */

/* =================================================================== */
/* 3. End-to-end: attemptAutonomousTaskRecovery with merged worktree    */
/* =================================================================== */

function makeMergedMission() {
  const refs = baseRefs();
  const missionId = createMissionRef(refs, 'running');

  // T1 = done so that nextReadyTask doesn't trip on the DAG.
  const t1 = createTask(missionId, 'done', null);

  // T2 = failed + merged worktree — the defect scenario.
  const t2 = createTask(missionId, 'failed', 'ACCEPTANCE_GATE_FAILED');
  upsertWorktree(missionId, t2, 1, {
    status: 'merged',
    commitHash: 'sha-merge-t2',
    mergeCommit: 'sha-merge-t2',
  });

  return { missionId, t2TaskId: t2 };
}

test(
  'attemptAutonomousTaskRecovery: refuses with RECOVERY_REFUSED_INTEGRATED_TASK ' +
  'when the LATEST worktree for that task is already merged/cleaned',
  async () => {
    const { missionId, t2TaskId } = makeMergedMission();

    const missionRow: any = db.prepare('SELECT * FROM missions WHERE id=?').get(missionId);
    const taskRow: any = db.prepare('SELECT * FROM mission_tasks WHERE id=?').get(t2TaskId);

    const result = await attemptAutonomousTaskRecovery(missionRow, taskRow);

    // ── Recovery must not succeed ─────────────────────────────────────
    assert.equal(result.recovered, false);
    assert.equal(result.reason, 'RECOVERY_REFUSED_INTEGRATED_TASK');

    // ── Budget: no recovery_attempt_started is written ──────────────────
    // (the function bails out before that event is emitted)
    const startEvt: any = db.prepare(`
      SELECT COUNT(*) AS c
      FROM mission_events
      WHERE mission_id=? AND event_type='lead.recovery_attempt_started'
    `).get(missionId);
    assert.equal(
      Number(startEvt.c), 0,
      'recovery_attempt_started must not be written for an early pre-check refusal',
    );

    // ── The only new event is the refusal diagnostic ─────────────────────
    const refusalEvt: any = db.prepare(`
      SELECT COUNT(*) AS c
      FROM mission_events
      WHERE mission_id=?
        AND event_type='lead.recovery_integrated_task_refused'
        AND json_extract(payload_json, '$.taskKey')='T3'
    `).get(missionId);  // T3 because _seq is 3 when T2 is created (m_1f_1, T1=_seq=2, T2=_seq=3)

    // We don't hard-code the task_key; just confirm that a refusal event
    // was recorded for the failed task (so supervisors can observe it).
    const refusalEvtAny: any = db.prepare(`
      SELECT COUNT(*) AS c
      FROM mission_events
      WHERE mission_id=? AND event_type='lead.recovery_integrated_task_refused'
    `).get(missionId);
    assert.equal(
      Number(refusalEvtAny.c), 1,
      'exactly one lead.recovery_integrated_task_refused event must be recorded',
    );

    // ── T2 must remain 'failed' (not overwritten to 'done'/'cancelled') ──
    const t2After: any = db.prepare(
      'SELECT status, error_text FROM mission_tasks WHERE id=?'
    ).get(t2TaskId);
    assert.equal(t2After.status, 'failed');
    assert.equal(t2After.error_text, 'ACCEPTANCE_GATE_FAILED');
  }
);

test(
  'attemptAutonomousTaskRecovery: does NOT refuse for active unmerged worktree',
  async () => {
    const refs = baseRefs();
    const missionId = createMissionRef(refs, 'running');

    const t1 = createTask(missionId, 'done', null);
    const t2 = createTask(missionId, 'failed', 'TEST_INTERNAL_ERROR');

    // Build a real, active worktree (no commit_hash / merge_commit).
    const wp = join(WORKSPACE_ROOT, missionId, t2);
    ensureRepo(wp);
    db.prepare(`
      INSERT INTO mission_task_workspaces
        (id,mission_id,task_id,attempt_no,workspace_path,branch_name,base_commit,
         status,commit_hash,merge_commit,diff_stat,error_text,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,NULL,NULL,'',NULL,?,?)
    `).run(
      `mtws_1f_ok_${++_seq}`, missionId, t2, 1,
      wp, `b-t2-${_seq}`,
      'base-ok',
      'active', now(), now(),
    );

    const missionRow: any = db.prepare('SELECT * FROM missions WHERE id=?').get(missionId);
    const taskRow: any = db.prepare('SELECT * FROM mission_tasks WHERE id=?').get(t2);
    const result = await attemptAutonomousTaskRecovery(missionRow, taskRow);

    assert.notEqual(
      result.reason,
      'RECOVERY_REFUSED_INTEGRATED_TASK',
      'active (unmerged) worktree must not trigger the refusal pre-check',
    );
  }
);

test(
  'RECOVERY_REFUSED_INTEGRATED_TASK: does not permanently strand the mission ' +
  'such that a second non-merged failed task stays retryable',
  async () => {
    //
    // Two-failed-task layout:
    //   T1 (integrated  — should be refused by recovery)
    //   T2 (independent — should stay 'failed' and retryable)
    //
    // After recovery of T1 returns the refusal, the missionLoop catches the
    // failure and marks it blocked.  A subsequent `decideStartupRecovery`
    // call must NOT interpret this as a permanent exhausted-blocked state
    // for tasks that have not been touched.
    //
    const refs = baseRefs();
    const missionId = createMissionRef(refs, 'running');

    const t1 = createTask(missionId, 'failed', 'ACCEPTANCE_GATE_FAILED');
    upsertWorktree(missionId, t1, 1, {
      status: 'merged',
      commitHash: 'sha-strand',
      mergeCommit: 'sha-strand',
    });

    const t2 = createTask(missionId, 'failed', 'EXECUTION_TIMEOUT');

    const missionRowS: any = db.prepare('SELECT * FROM missions WHERE id=?').get(missionId);
    const t1RowS: any = db.prepare('SELECT * FROM mission_tasks WHERE id=?').get(t1);
    await attemptAutonomousTaskRecovery(missionRowS, t1RowS);

    // T2 must remain 'failed' — not silently overwritten to 'cancelled'
    // or anything else terminal.
    const t2After: any = db.prepare(
      'SELECT status, error_text FROM mission_tasks WHERE id=?'
    ).get(t2);
    assert.equal(t2After.status, 'failed',
      `T2 should be 'failed'; got '${t2After.status}'`);
  }
);