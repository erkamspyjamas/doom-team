/**
 * Startup Recovery Policy
 *
 * Decision logic for determining what action a mission should take when
 * it is first started and its recovery state is inspected.
 *
 * All budget values are now resolved through the versioned recovery policy
 * (src/recovery-policy.ts) rather than copied as local constants.
 */

import {
  CURRENT_POLICY_VERSION,
  RECOVERABLE_PLANNING_FAILURE_CODES,
  RECOVERABLE_PLANNING_FAILURE_PREFIXES,
  FAIL_CLOSED_SENTINEL,
  resolveRecoveryPolicy,
} from './recovery-policy.js';

// ─── Public budget accessor ────────────────────────────────────────────────

/**
 * Resolve the zero-task replan budget for a given policy version.
 *
 * Defaults to CURRENT_POLICY_VERSION when no version is provided, matching
 * the legacy MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS = 3 exactly.
 *
 * Changing CURRENT_POLICY_VERSION bumps the policy label; individual missions
 * stamped with an older version continue to use that version's budget without
 * resetting their consumed history.
 */
export function resolveZeroTaskReplanBudget(
  policyVersion = CURRENT_POLICY_VERSION,
): number {
  const policy = resolveRecoveryPolicy(policyVersion);
  return policy.budgets.zeroTaskReplan;
}

/**
 * @deprecated Use resolveZeroTaskReplanBudget(policyVersion) for new code.
 *
 * Backward-compatible re-export so diagnostics.ts and any call-site that
 * imports MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS directly still resolves
 * to the policy-layer value (3 for the default '2025-08-22-v1').
 */
export const MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS: number =
  resolveZeroTaskReplanBudget();

// ─── Internal allowlists (imported from policy module) ──────────────────────

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

// ─── Types ───────────────────────────────────────────────────────────────────

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
  /** Optional explicit policy version; defaults to CURRENT_POLICY_VERSION. */
  policyVersion?: string;
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

export type InterruptedTaskExecutionDecision =
  | {
      action: 'mark_failed';
      errorText: string;
    }
  | {
      action: 'recovery_required';
      detail: string;
    };

// ─── Task-interrupted decision ───────────────────────────────────────────────

export function decideInterruptedTaskExecution(input: {
  status?: unknown;
  errorText?: unknown;
}): InterruptedTaskExecutionDecision {
  const status =
    typeof input.status === 'string'
      ? input.status.trim()
      : '';

  if (status === 'error') {
    const errorText =
      typeof input.errorText === 'string' && input.errorText.trim()
        ? input.errorText
        : 'EXECUTION_FAILED';

    return {
      action: 'mark_failed',
      errorText,
    };
  }

  /*
   * Any non-error execution attached to a task that is still
   * running/validating is ambiguous after interruption.
   *
   * In particular, "done" does not prove that all task-level
   * side effects, evidence, validation and state transitions
   * completed. Never silently replay or advance other work.
   */
  return {
    action: 'recovery_required',
    detail: status || 'missing',
  };
}

// ─── Budget utility ──────────────────────────────────────────────────────────

export function hasUnusedTaskRecoveryBudget(input: {
  missionAttempts: number;
  taskAttempts: number;
  maxMissionAttempts: number;
  maxTaskAttempts: number;
}): boolean {
  const values = Object.values(input);
  if (values.some((v) => !Number.isInteger(v) || v < 0)) return false;
  if (input.maxMissionAttempts < 1 || input.maxTaskAttempts < 1) return false;
  return (
    input.missionAttempts < input.maxMissionAttempts &&
    input.taskAttempts < input.maxTaskAttempts
  );
}

// ─── Planning failure classifier ─────────────────────────────────────────────

function unwrapPlanningFailure(errorText: string): string {
  let error = errorText.trim();

  // Wrappers are accepted only when their nested cause is itself allowlisted.
  // This prevents AUTO_PLAN_FAILED from turning configuration or safety errors
  // into an autonomous restart loop.
  for (let depth = 0; depth < 4; depth++) {
    const wrapper = RECOVERABLE_PLANNING_FAILURE_WRAPPERS.find((w) =>
      error.startsWith(w)
    );
    if (!wrapper) break;
    error = error.slice(wrapper.length).trim();
  }

  return error;
}

export function isRecoverablePlanningFailure(errorText: unknown): boolean {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  // FAIL_CLOSED_SENTINEL (and any invalid policy) returns false -- no
  // planning failure is recoverable under a failed-closed policy.
  if (policy === FAIL_CLOSED_SENTINEL) return false;

  if (typeof errorText !== 'string' || !errorText.trim()) return false;

  const error = unwrapPlanningFailure(errorText);
  if (!error) return false;

  if (policy.recoverablePlanningFailureCodes.includes(error)) {
    return true;
  }

  return policy.recoverablePlanningFailurePrefixes.some((prefix) =>
    error.startsWith(prefix)
  );
}

// ─── Startup recovery decision ───────────────────────────────────────────────

function skip(
  reason: StartupRecoveryReason,
  budget: number,
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
  input: StartupRecoveryPolicyInput,
): StartupRecoveryDecision {
  const policyVersion = input.policyVersion ?? CURRENT_POLICY_VERSION;
  const policy = resolveRecoveryPolicy(policyVersion);
  const budget = policy.budgets.zeroTaskReplan;

  // fail-closed: unknown or unsafe policy → no replans allowed
  if (budget < 1) {
    return skip('planning_recovery_budget_exhausted', budget);
  }

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
    if (status === 'blocked' && input.blockedTaskRecoveryAllowed !== true) {
      return skip('blocked_tasks_not_recoverable', budget);
    }
    if (status === 'new') {
      return skip('inconsistent_new_tasks', budget);
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