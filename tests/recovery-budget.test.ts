import assert from 'node:assert/strict';
import test from 'node:test';
import Database from 'better-sqlite3';
import {
  finalGateRecoveryAttemptCount,
  missionRecoveryAttemptCount,
  recoveryEpochStartId,
  taskRecoveryAttemptCount,
} from '../src/recovery-budget.js';
import { decideStartupRecovery } from '../src/startup-recovery-policy.js';

function fixture(){
  const database=new Database(':memory:');
  database.exec(`
    CREATE TABLE mission_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      mission_id TEXT NOT NULL,
      task_id TEXT,
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    );
  `);
  const insert=database.prepare(`
    INSERT INTO mission_events
      (mission_id,task_id,event_type,payload_json,created_at)
    VALUES ('mission_fixture',?,?,?,?)
  `);
  return {database,insert};
}

test('recovery epoch and attempt budgets use monotonic event IDs',()=>{
  const {database,insert}=fixture();
  insert.run(null,'lead.recovery_budget_epoch_reset','{}','2026-08-16T10:00:00.000Z');
  const epochId=recoveryEpochStartId(database,'mission_fixture');

  // Equal and backwards wall-clock values still occur after the epoch by ID.
  insert.run('task_1','lead.task_recovered','{"taskKey":"T1"}','2026-08-16T10:00:00.000Z');
  insert.run('task_1','lead.recovery_failed','{"taskKey":"T1"}','2026-08-16T09:59:59.000Z');

  assert.equal(epochId,1);
  assert.equal(taskRecoveryAttemptCount(database,'mission_fixture','task_1'),2);
  assert.equal(missionRecoveryAttemptCount(database,'mission_fixture'),2);
  database.close();
});

test('mixed failed and applied task recoveries share one task budget',()=>{
  const {database,insert}=fixture();
  insert.run(null,'lead.recovery_budget_epoch_reset','{}','2026-08-16T10:00:00.000Z');
  insert.run('task_1','lead.recovery_failed','{"taskKey":"T1"}','2026-08-16T10:00:01.000Z');
  insert.run('task_1','lead.task_recovered','{"taskKey":"T1"}','2026-08-16T10:00:02.000Z');
  insert.run('task_1','lead.task_recovered','{"taskKey":"T1"}','2026-08-16T10:00:03.000Z');

  assert.equal(taskRecoveryAttemptCount(database,'mission_fixture','task_1'),3);
  assert.equal(missionRecoveryAttemptCount(database,'mission_fixture'),3);
  database.close();
});

test('durable attempt-start markers count crashes without double-counting outcomes',()=>{
  const {database,insert}=fixture();
  insert.run(null,'lead.recovery_budget_epoch_reset','{}','2026-08-16T10:00:00.000Z');
  insert.run('task_1','lead.task_recovered','{"taskKey":"T1"}','2026-08-16T10:00:01.000Z');
  insert.run('task_1','lead.recovery_attempt_started','{"kind":"task"}','2026-08-16T09:00:00.000Z');
  insert.run('task_1','lead.task_recovered','{"taskKey":"T1"}','2026-08-16T09:00:01.000Z');
  insert.run('task_1','lead.recovery_attempt_started','{"kind":"task"}','2026-08-16T08:00:00.000Z');
  insert.run(null,'lead.recovery_attempt_started','{"kind":"final_gate"}','2026-08-16T07:00:00.000Z');
  insert.run(null,'lead.final_gate_recovery_started','{"gateType":"qa"}','2026-08-16T07:00:01.000Z');
  insert.run(null,'lead.recovery_failed','{"gateType":"qa"}','2026-08-16T07:00:02.000Z');

  assert.equal(taskRecoveryAttemptCount(database,'mission_fixture','task_1'),3);
  assert.equal(finalGateRecoveryAttemptCount(database,'mission_fixture'),1);
  assert.equal(missionRecoveryAttemptCount(database,'mission_fixture'),4);
  database.close();
});

test('legacy final-gate starts consume budget even when decisions fail',()=>{
  const {database,insert}=fixture();
  insert.run(null,'lead.recovery_budget_epoch_reset','{}','2026-08-16T10:00:00.000Z');
  for(let i=0;i<3;i++){
    insert.run(null,'lead.final_gate_recovery_started','{"gateType":"qa"}',`2026-08-16T10:00:0${i+1}.000Z`);
    insert.run(null,'lead.recovery_failed','{"gateType":"qa"}',`2026-08-16T10:00:0${i+1}.500Z`);
  }

  assert.equal(finalGateRecoveryAttemptCount(database,'mission_fixture'),3);
  assert.equal(missionRecoveryAttemptCount(database,'mission_fixture'),3);
  assert.equal(decideStartupRecovery({
    status:'blocked',
    taskCount:2,
    errorText:'QA_GATE_FAILED',
    blockedTaskRecoveryAllowed:
      finalGateRecoveryAttemptCount(database,'mission_fixture')<3,
  }).action,'skip');
  database.close();
});

test('detached task attempt markers never consume the final-gate budget',()=>{
  const {database,insert}=fixture();
  insert.run(null,'lead.recovery_budget_epoch_reset','{}','2026-08-16T10:00:00.000Z');
  for(let i=0;i<3;i++){
    insert.run(
      `task_${i+1}`,
      'lead.recovery_attempt_started',
      '{"kind":"task"}',
      `2026-08-16T10:00:0${i+1}.000Z`,
    );
  }

  database.prepare(`UPDATE mission_events SET task_id=NULL WHERE task_id IS NOT NULL`).run();

  assert.equal(missionRecoveryAttemptCount(database,'mission_fixture'),3);
  assert.equal(finalGateRecoveryAttemptCount(database,'mission_fixture'),0);

  insert.run(
    null,
    'lead.recovery_attempt_started',
    '{"kind":"final_gate"}',
    '2026-08-16T10:00:04.000Z',
  );
  assert.equal(finalGateRecoveryAttemptCount(database,'mission_fixture'),1);
  database.close();
});
