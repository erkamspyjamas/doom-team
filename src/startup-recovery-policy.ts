export const MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS = 3;

export const RECOVERABLE_PLANNING_FAILURE_CODES = [
  'EXECUTION_DISAPPEARED',
  'EXECUTION_WAIT_TIMEOUT',
  'JSON_OBJECT_NOT_FOUND',
  'LEAD_PLAN_TASKS_REQUIRED',
  'LEAD_PLAN_TOO_MANY_TASKS',
  'MISSION_HAS_NO_TASKS',
  'PLAN_QUALITY_VALIDATION_FAILED_AFTER_REPAIR',
] as const;

export const RECOVERABLE_PLANNING_FAILURE_PREFIXES = [
  'LEAD_PLANNING_EXECUTION_',
  'LEAD_PLAN_AGENT_OUTSIDE_TEAM:',
  'LEAD_PLAN_DUPLICATE_KEY:',
  'LEAD_PLAN_EXPECTED_OUTCOME_REQUIRED:',
  'LEAD_PLAN_INSTRUCTION_REQUIRED:',
  'LEAD_PLAN_SELF_DEPENDENCY:',
  'LEAD_PLAN_TASK_KEY_REQUIRED:',
  'LEAD_PLAN_TITLE_REQUIRED:',
  'LEAD_PLAN_UNKNOWN_DEPENDENCY:',
  'LEAD_PLAN_WORK_MODE_INVALID:',
  'MISSION_PLAN_CYCLE:',
  'PLAN_CONSTRAINT_REPAIR_EXECUTION_FAILED:',
  'PLAN_QUALITY_VIOLATION_AFTER_REPAIR:',
] as const;

const RECOVERABLE_PLANNING_FAILURE_WRAPPERS = [
  'AUTO_PLAN_FAILED:',
  'AUTO_SEMANTIC_REPLAN_FAILED:',
] as const;

const AUTONOMOUS_RESUMABLE_STATUSES = new Set([
  'new',
  'ready',
  'running',
  'blocked',
  'validating',
  'reviewing',
]);

export type StartupRecoveryAction = 'plan' | 'replan' | 'run' | 'skip';

export type StartupRecoveryMode =
  | 'plan'
  | 'replan_after_planning_failure'
  | 'run'
  | null;

export type StartupRecoveryReason =
  | 'new_zero_task_mission'
  | 'recoverable_planning_failure'
  | 'planning_recovery_budget_exhausted'
  | 'planning_failure_not_recoverable'
  | 'resumable_tasks_present'
  | 'blocked_tasks_not_recoverable'
  | 'inconsistent_new_tasks'
  | 'status_not_autonomously_resumable'
  | 'zero_tasks_not_plannable'
  | 'invalid_recovery_state';

export type StartupRecoveryPolicyInput = {
  status: string;
  taskCount: number;
  errorText?: string | null;
  autonomousReplanAttempts?: number;
  maxAutonomousReplanAttempts?: number;
  blockedTaskRecoveryAllowed?: boolean;
};

export type StartupRecoveryDecision = {
  action: StartupRecoveryAction;
  mode: StartupRecoveryMode;
  reason: StartupRecoveryReason;
  autonomousReplanAttempt: number | null;
  autonomousReplanBudget: number;
};

export function hasUnusedTaskRecoveryBudget(input:{
  missionAttempts:number;
  taskAttempts:number;
  maxMissionAttempts:number;
  maxTaskAttempts:number;
}){
  const values=Object.values(input);
  if(values.some(value=>!Number.isInteger(value) || value<0))return false;
  if(input.maxMissionAttempts<1 || input.maxTaskAttempts<1)return false;
  return (
    input.missionAttempts<input.maxMissionAttempts
    && input.taskAttempts<input.maxTaskAttempts
  );
}

function unwrapPlanningFailure(errorText: string) {
  let error = errorText.trim();

  // Wrappers are accepted only when their nested cause is itself allowlisted.
  // This prevents AUTO_PLAN_FAILED from turning configuration or safety errors
  // into an autonomous restart loop.
  for (let depth = 0; depth < 4; depth++) {
    const wrapper = RECOVERABLE_PLANNING_FAILURE_WRAPPERS.find((value) =>
      error.startsWith(value)
    );
    if (!wrapper) break;
    error = error.slice(wrapper.length).trim();
  }

  return error;
}

export function isRecoverablePlanningFailure(errorText: unknown) {
  if (typeof errorText !== 'string' || !errorText.trim()) return false;

  const error = unwrapPlanningFailure(errorText);
  if (!error) return false;

  if (
    (RECOVERABLE_PLANNING_FAILURE_CODES as readonly string[]).includes(error)
  ) {
    return true;
  }

  return RECOVERABLE_PLANNING_FAILURE_PREFIXES.some((prefix) =>
    error.startsWith(prefix)
  );
}

function skip(
  reason: StartupRecoveryReason,
  budget: number
): StartupRecoveryDecision {
  return {
    action: 'skip',
    mode: null,
    reason,
    autonomousReplanAttempt: null,
    autonomousReplanBudget: budget,
  };
}

export function decideStartupRecovery(
  input: StartupRecoveryPolicyInput
): StartupRecoveryDecision {
  const budget =
    input.maxAutonomousReplanAttempts ??
    MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS;
  const attempts = input.autonomousReplanAttempts ?? 0;

  if (
    !Number.isInteger(input.taskCount) ||
    input.taskCount < 0 ||
    !Number.isInteger(attempts) ||
    attempts < 0 ||
    !Number.isInteger(budget) ||
    budget < 1
  ) {
    return skip('invalid_recovery_state', budget);
  }

  const status = String(input.status ?? '').trim();
  if (!AUTONOMOUS_RESUMABLE_STATUSES.has(status)) {
    return skip('status_not_autonomously_resumable', budget);
  }

  if (input.taskCount > 0) {
    if(status==='blocked' && input.blockedTaskRecoveryAllowed!==true){
      return skip('blocked_tasks_not_recoverable',budget);
    }
    if(status==='new'){
      return skip('inconsistent_new_tasks',budget);
    }
    return {
      action: 'run',
      mode: 'run',
      reason: 'resumable_tasks_present',
      autonomousReplanAttempt: null,
      autonomousReplanBudget: budget,
    };
  }

  if (status === 'new') {
    return {
      action: 'plan',
      mode: 'plan',
      reason: 'new_zero_task_mission',
      autonomousReplanAttempt: null,
      autonomousReplanBudget: budget,
    };
  }

  if (status !== 'blocked') {
    return skip('zero_tasks_not_plannable', budget);
  }

  if (!isRecoverablePlanningFailure(input.errorText)) {
    return skip('planning_failure_not_recoverable', budget);
  }

  if (attempts >= budget) {
    return skip('planning_recovery_budget_exhausted', budget);
  }

  return {
    action: 'replan',
    mode: 'replan_after_planning_failure',
    reason: 'recoverable_planning_failure',
    autonomousReplanAttempt: attempts + 1,
    autonomousReplanBudget: budget,
  };
}
