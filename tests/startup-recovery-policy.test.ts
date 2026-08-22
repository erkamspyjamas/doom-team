import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
  decideInterruptedTaskExecution,
  decideStartupRecovery,
  hasUnusedTaskRecoveryBudget,
  isRecoverablePlanningFailure,
} from '../src/startup-recovery-policy.js';

test('planning failure classifier accepts only explicit recoverable failures', () => {
  const reportedFailure =
    'PLAN_QUALITY_VIOLATION_AFTER_REPAIR:task_work_mode_mismatch,validation_role_mutation_task';

  assert.equal(isRecoverablePlanningFailure(reportedFailure), true);
  assert.equal(
    isRecoverablePlanningFailure(`AUTO_PLAN_FAILED:${reportedFailure}`),
    true
  );
  assert.equal(isRecoverablePlanningFailure(''), false);
  assert.equal(isRecoverablePlanningFailure(null), false);
  assert.equal(isRecoverablePlanningFailure('MISSION_TEAM_EMPTY'), false);
  assert.equal(
    isRecoverablePlanningFailure('AUTO_PLAN_FAILED:MISSION_TEAM_EMPTY'),
    false
  );
  assert.equal(
    isRecoverablePlanningFailure('AUTONOMOUS_RECOVERY_EXHAUSTED:TASK_FAILED:T1'),
    false
  );
});

test('new zero-task mission starts initial planning', () => {
  assert.deepEqual(
    decideStartupRecovery({
      status: 'new',
      taskCount: 0,
      errorText: null,
    }),
    {
      action: 'plan',
      mode: 'plan',
      reason: 'new_zero_task_mission',
      autonomousReplanAttempt: null,
      autonomousReplanBudget:
        MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
    }
  );
});

test('blocked zero-task planning failure starts a bounded autonomous replan', () => {
  assert.deepEqual(
    decideStartupRecovery({
      status: 'blocked',
      taskCount: 0,
      errorText:
        'PLAN_QUALITY_VIOLATION_AFTER_REPAIR:task_work_mode_mismatch,validation_role_mutation_task',
      autonomousReplanAttempts: 1,
    }),
    {
      action: 'replan',
      mode: 'replan_after_planning_failure',
      reason: 'recoverable_planning_failure',
      autonomousReplanAttempt: 2,
      autonomousReplanBudget:
        MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
    }
  );
});

test('blocked zero-task planning recovery stops when its budget is exhausted', () => {
  const decision = decideStartupRecovery({
    status: 'blocked',
    taskCount: 0,
    errorText:
      'PLAN_QUALITY_VIOLATION_AFTER_REPAIR:task_work_mode_mismatch',
    autonomousReplanAttempts:
      MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
  });

  assert.equal(decision.action, 'skip');
  assert.equal(decision.mode, null);
  assert.equal(decision.reason, 'planning_recovery_budget_exhausted');
  assert.equal(decision.autonomousReplanAttempt, null);
});

test('blocked zero-task mission without an allowlisted planning failure stays blocked', () => {
  for (const errorText of [
    '',
    null,
    'AUTONOMOUS_RECOVERY_EXHAUSTED:TASK_FAILED:T1',
    'LEAD_ESCALATION_REQUIRED:credential_required',
  ]) {
    const decision = decideStartupRecovery({
      status: 'blocked',
      taskCount: 0,
      errorText,
    });

    assert.equal(decision.action, 'skip');
    assert.equal(decision.reason, 'planning_failure_not_recoverable');
  }
});

test('active missions with tasks run only from autonomously resumable statuses', () => {
  for (const status of [
    'ready',
    'running',
    'validating',
    'reviewing',
  ]) {
    const decision = decideStartupRecovery({status, taskCount: 2});
    assert.equal(decision.action, 'run');
    assert.equal(decision.mode, 'run');
    assert.equal(decision.reason, 'resumable_tasks_present');
  }

  assert.equal(
    decideStartupRecovery({status:'new',taskCount:2}).reason,
    'inconsistent_new_tasks',
  );

  assert.equal(
    decideStartupRecovery({status:'blocked',taskCount:2}).reason,
    'blocked_tasks_not_recoverable',
  );
  assert.equal(
    decideStartupRecovery({
      status:'blocked',
      taskCount:2,
      blockedTaskRecoveryAllowed:true,
    }).action,
    'run',
  );

  for (const status of ['paused', 'done', 'cancelled', 'awaiting_approval']) {
    const decision = decideStartupRecovery({status, taskCount: 2});
    assert.equal(decision.action, 'skip');
    assert.equal(decision.reason, 'status_not_autonomously_resumable');
  }
});

test('active non-blocked zero-task states do not invent a recovery action', () => {
  for (const status of ['ready', 'running', 'validating', 'reviewing']) {
    const decision = decideStartupRecovery({status, taskCount: 0});
    assert.equal(decision.action, 'skip');
    assert.equal(decision.reason, 'zero_tasks_not_plannable');
  }
});

test('invalid counters fail closed', () => {
  assert.equal(
    decideStartupRecovery({status: 'new', taskCount: -1}).reason,
    'invalid_recovery_state'
  );
  assert.equal(
    decideStartupRecovery({
      status: 'blocked',
      taskCount: 0,
      errorText: 'MISSION_HAS_NO_TASKS',
      autonomousReplanAttempts: -1,
    }).reason,
    'invalid_recovery_state'
  );
  // maxAutonomousReplanAttempts is no longer validated directly; the
  // versioned policy module resolves the budget.  Fail-closed behaviour is
  // tested separately in the versioned-policy test file.
});

test('failed recovery decisions can resume only while bounded budget remains',()=>{
  assert.equal(hasUnusedTaskRecoveryBudget({
    missionAttempts:1,
    taskAttempts:1,
    maxMissionAttempts:8,
    maxTaskAttempts:3,
  }),true);
  assert.equal(hasUnusedTaskRecoveryBudget({
    missionAttempts:3,
    taskAttempts:3,
    maxMissionAttempts:8,
    maxTaskAttempts:3,
  }),false);
  assert.equal(hasUnusedTaskRecoveryBudget({
    missionAttempts:8,
    taskAttempts:0,
    maxMissionAttempts:8,
    maxTaskAttempts:3,
  }),false);
});

test('interrupted running task fails closed for missing and non-error execution states',()=>{
  const cases:Array<[unknown,string]> = [
    ['running','running'],
    [undefined,'missing'],
    [null,'missing'],
    ['done','done'],
    ['cancelled','cancelled'],
    ['unexpected','unexpected'],
  ];

  for(const [status,detail] of cases){
    assert.deepEqual(
      decideInterruptedTaskExecution({status}),
      {
        action:'recovery_required',
        detail,
      },
    );
  }
});

test('explicit execution error remains eligible for existing bounded task recovery',()=>{
  assert.deepEqual(
    decideInterruptedTaskExecution({
      status:'error',
      errorText:'worker failed',
    }),
    {
      action:'mark_failed',
      errorText:'worker failed',
    },
  );

  assert.deepEqual(
    decideInterruptedTaskExecution({
      status:'error',
      errorText:'',
    }),
    {
      action:'mark_failed',
      errorText:'EXECUTION_FAILED',
    },
  );
});
