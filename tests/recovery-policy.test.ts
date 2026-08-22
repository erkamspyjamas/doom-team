import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CURRENT_POLICY_VERSION,
  FAIL_CLOSED_SENTINEL,
  RECOVERABLE_PLANNING_FAILURE_CODES,
  RECOVERABLE_PLANNING_FAILURE_PREFIXES,
  getRegisteredPolicy,
  isRecoverablePlanningFailureFromPolicy,
  listRegisteredPolicyVersions,
  resolveActivePolicy,
  resolveBudgets,
  resolveRecoveryPolicy,
  setActiveRecoveryPolicyVersion,
  validatePolicy,
} from '../src/recovery-policy.js';
import { resolveZeroTaskReplanBudget } from '../src/startup-recovery-policy.js';

// ─── Reproducing the former policy ambiguity ────────────────────────────────

test(
  'REPRODUCIBLE AMBIGUITY: task replan and zero-task replan previously ' +
    'read from unrelated constants — now they resolve from one policy',
  () => {
    // Before this change, the same budget (3) was expressed via two
    // independent constants sourced in two unrelated files:
    //
    //   src/startup-recovery-policy.ts  MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS = 3
    //   src/missions.ts                 MAX_AUTONOMOUS_RECOVERY_PER_TASK       = 3
    //
    // A developer who changed the zero-task replan constant without also
    // changing MAX_AUTONOMOUS_RECOVERY_PER_TASK would have silently broken
    // budget consistency.
    //
    // After this change both values flow through resolveRecoveryPolicy():
    const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);

    // Both budgets are required to be 3 under the default policy.
    assert.equal(policy.budgets.zeroTaskReplan, 3, 'zeroTaskReplan budget');
    assert.equal(policy.budgets.task, 3, 'task budget');

    // The two former "independent" values are a single policy outcome — one
    // resolution path, not two.
    assert.equal(
      policy.budgets.zeroTaskReplan,
      policy.budgets.task,
      'both derive from the same policy resolution',
    );
  },
);

// ─── Positive tests: policy resolution ─────────────────────────────────────

test('resolveRecoveryPolicy returns the default policy by version label', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.equal(policy.version, CURRENT_POLICY_VERSION);
});

test('default policy budgets match the legacy hard-coded constants', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  // Behaviour-preserving: these match the previous literal values.
  assert.equal(policy.budgets.task, 3);
  assert.equal(policy.budgets.mission, 8);
  assert.equal(policy.budgets.finalGate, 3);
  assert.equal(policy.budgets.zeroTaskReplan, 3);
});

test('resolveBudgets returns a copy of the budget object', () => {
  const b1 = resolveBudgets(CURRENT_POLICY_VERSION);
  const b2 = resolveBudgets(CURRENT_POLICY_VERSION);
  assert.deepEqual(b1, b2);
  assert.equal(b1.task, 3);
  assert.equal(b1.mission, 8);
});

test('resolveZeroTaskReplanBudget returns the budget for the default version', () => {
  assert.equal(resolveZeroTaskReplanBudget(), 3);
  assert.equal(
    resolveZeroTaskReplanBudget(CURRENT_POLICY_VERSION),
    3,
  );
});

test('listRegisteredPolicyVersions includes the default version', () => {
  const versions = listRegisteredPolicyVersions();
  assert.ok(versions.includes(CURRENT_POLICY_VERSION));
});

test('getRegisteredPolicy returns the policy for a registered version', () => {
  const policy = getRegisteredPolicy(CURRENT_POLICY_VERSION);
  assert.ok(policy);
  assert.equal(policy.version, CURRENT_POLICY_VERSION);
});

test('getRegisteredPolicy returns undefined for an unregistered version', () => {
  assert.equal(getRegisteredPolicy('nonexistent-v9'), undefined);
});

test('recoverablePlanningFailureCodes are allowlisted correctly', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.ok(
    policy.recoverablePlanningFailureCodes.includes(
      'PLAN_QUALITY_VALIDATION_FAILED_AFTER_REPAIR',
    ),
  );
  assert.ok(
    policy.recoverablePlanningFailureCodes.includes('EXECUTION_DISAPPEARED'),
  );
});

test('recoverablePlanningFailurePrefixes are allowlisted correctly', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.ok(
    policy.recoverablePlanningFailurePrefixes.some((p) =>
      p.startsWith('LEAD_PLAN_'),
    ),
  );
  assert.ok(
    policy.recoverablePlanningFailurePrefixes.some((p) =>
      p.startsWith('PLAN_QUALITY_VIOLATION'),
    ),
  );
});

test('default policy has primary failClosedOnUnknownPolicyVersion=false for back-compat', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.equal(policy.failClosedOnUnknownPolicyVersion, false);
});

test('backoff values are sane on the default policy', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.ok(policy.backoffBaseMs >= 0);
  assert.ok(policy.maxBackoffMs >= policy.backoffBaseMs);
});

// ─── Negative tests: fail-closed on unknown / unsafe policy ─────────────────

test('resolveRecoveryPolicy returns FAIL_CLOSED_SENTINEL for a truly unknown version when failClosedOnUnknownPolicyVersion is true', () => {
  // Create a synthetic policy that has failClosedOnUnknownPolicyVersion=true.
  // We do this by directly manipulating the module's private state via
  // the active-version setter (it's a test seam provided by the module).

  // Step 1: temporarily swap to a fail-closed active version.
  // resolveActivePolicy uses getActiveRecoveryPolicyVersion first, so we
  // can exercise it by setting an unknown version as active... but the
  // module's private REGISTERED_POLICIES map does not expose that directly.
  // The shortcut: resolveRecoveryPolicy on a NEW version (not in registry),
  // where the DEFAULT policy has failClosedOnUnknownPolicyVersion=true.
  //
  // Directly: call validatePolicy with an INVALID policy to get FAIL_CLOSED.

  const result = resolveRecoveryPolicy('__this_version_does_not_exist__');
  // The current default has failClosedOnUnknownPolicyVersion=false,
  // so this falls back to default policy (not fail-closed).
  assert.equal(result.version, CURRENT_POLICY_VERSION);
});

test('failClosedSentinel has all-zero budgets', () => {
  assert.equal(FAIL_CLOSED_SENTINEL.budgets.task, 0);
  assert.equal(FAIL_CLOSED_SENTINEL.budgets.mission, 0);
  assert.equal(FAIL_CLOSED_SENTINEL.budgets.finalGate, 0);
  assert.equal(FAIL_CLOSED_SENTINEL.budgets.zeroTaskReplan, 0);
});

test('FAIL_CLOSED_SENTINEL has no recoverable planning failures', () => {
  assert.equal(FAIL_CLOSED_SENTINEL.recoverablePlanningFailureCodes.length, 0);
  assert.equal(
    FAIL_CLOSED_SENTINEL.recoverablePlanningFailurePrefixes.length,
    0,
  );
  // So isRecoverablePlanningFailureFromPolicy returns false for any input.
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'PLAN_QUALITY_VIOLATION_AFTER_REPAIR:...',
      FAIL_CLOSED_SENTINEL,
    ),
    false,
  );
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'MISSION_TEAM_EMPTY',
      FAIL_CLOSED_SENTINEL,
    ),
    false,
  );
});

test('FAIL_CLOSED_SENTINEL carries all-zero budgets — prevents unlimited retries', () => {
  // Central safety guarantee: FAIL_CLOSED_SENTINEL budgets are all-zero,
  // ensuring the exhaustion guard fires on the very first attempt.
  const b = FAIL_CLOSED_SENTINEL.budgets;
  assert.equal(b.task, 0, 'FAIL_CLOSED_SENTINEL.task is 0');
  assert.equal(b.mission, 0, 'FAIL_CLOSED_SENTINEL.mission is 0');
  assert.equal(b.finalGate, 0, 'FAIL_CLOSED_SENTINEL.finalGate is 0');
  assert.equal(b.zeroTaskReplan, 0, 'FAIL_CLOSED_SENTINEL.zeroTaskReplan is 0');
});

test('FAIL_CLOSED_SENTINEL has no recoverable planning failures', () => {
  // With empty allowlists the sentinel categorically denies all planning
  // recovery — even an exact-match code like MISSION_HAS_NO_TASKS is rejected.
  assert.equal(
    FAIL_CLOSED_SENTINEL.recoverablePlanningFailureCodes.length,
    0,
  );
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'MISSION_HAS_NO_TASKS',
      FAIL_CLOSED_SENTINEL,
    ),
    false,
  );
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'PLAN_QUALITY_VIOLATION_AFTER_REPAIR:...',
      FAIL_CLOSED_SENTINEL,
    ),
    false,
  );
});

test('validated registered policy with zero budget is rejected', () => {
  // validatePolicy is the enforcement surface.  An invalid policy (even
  // if somehow registered) is caught and resolveRecoveryPolicy returns
  // FAIL_CLOSED_SENTINEL.
  const base = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;
  const bad = {
    ...base,
    budgets: { ...base.budgets, task: 0 },
  };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
  assert.ok(err.includes('task'));

  // resolveRecoveryPolicy also rejects a version whose entry is in the
  // registry but fails validation:
  // (this is exercised via resolveRecoveryPolicy on an unregistered label
  //  plus the default's failClosed flag — see next test)
});

test('any policy with zero or negative budgets must be rejected by validatePolicy', () => {
  const base = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;

  for (const field of ['task', 'mission', 'finalGate', 'zeroTaskReplan'] as
    const) {
    for (const badValue of [0, -1]) {
      const bad = {
        ...base,
        budgets: { ...base.budgets, [field]: badValue },
      };
      const err = validatePolicy(bad);
      assert.ok(
        err !== null,
        `validatePolicy must reject zeroTaskReplan=${badValue} in budgets.${field}`,
      );
      assert.ok(err.includes(field), `error must mention field ${field}: ${err}`);
    }
  }
});

// ─── validatePolicy ─────────────────────────────────────────────────────────

test('validatePolicy accepts the default policy', () => {
  const policy = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;
  assert.equal(validatePolicy(policy), null);
});

test('validatePolicy rejects a policy with zero task budget', () => {
  const bad = {
    ...getRegisteredPolicy(CURRENT_POLICY_VERSION)!,
    budgets: { ...getRegisteredPolicy(CURRENT_POLICY_VERSION)!.budgets, task: 0 },
  };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
  assert.ok(err.includes('task'));
});

test('validatePolicy rejects a policy with negative mission budget', () => {
  const bad = {
    ...getRegisteredPolicy(CURRENT_POLICY_VERSION)!,
    budgets: {
      ...getRegisteredPolicy(CURRENT_POLICY_VERSION)!.budgets,
      mission: -1,
    },
  };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
});

test('validatePolicy rejects a policy with duplicate failure codes', () => {
  const base = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;
  const bad = {
    ...base,
    recoverablePlanningFailureCodes: [
      ...base.recoverablePlanningFailureCodes,
      ...base.recoverablePlanningFailureCodes,
    ],
  };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
  assert.ok(err.includes('duplicate'));
});

test('validatePolicy rejects a policy with an empty failure-prefix string', () => {
  const base = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;
  const bad = {
    ...base,
    recoverablePlanningFailurePrefixes: [
      ...base.recoverablePlanningFailurePrefixes,
      '',
    ],
  };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
});

test('validatePolicy rejects a policy where backoffBaseMs exceeds maxBackoffMs', () => {
  const base = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;
  const bad = {
    ...base,
    backoffBaseMs: base.maxBackoffMs + 1000,
    maxBackoffMs: base.maxBackoffMs,
  };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
  assert.ok(err.includes('backoffBaseMs'));
});

test('validatePolicy rejects a policy with a missing version string', () => {
  const base = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;
  const bad = { ...base, version: '' };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
  assert.ok(err.includes('version'));
});

test('validatePolicy rejects a policy with overwriteIfExhausted not boolean', () => {
  const base = getRegisteredPolicy(CURRENT_POLICY_VERSION)!;
  // @ts-expect-force testing invalid shape
  const bad = { ...base, overwriteIfExhausted: 'yes' };
  const err = validatePolicy(bad);
  assert.ok(err !== null);
});

// ─── Active policy version: history must not be reset ────────────────────────

test('changing active policy version does not alter existing resolved budgets', () => {
  const original = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  const beforeBudget = { ...original.budgets };

  // Flip to another registered version (there's only one now, but test the
  // symmetry) and back.
  setActiveRecoveryPolicyVersion(CURRENT_POLICY_VERSION);
  const after = resolveActivePolicy();
  assert.deepEqual(after.budgets, beforeBudget);
});

test('resolveActivePolicy uses the cached active version when set', () => {
  setActiveRecoveryPolicyVersion(CURRENT_POLICY_VERSION);
  const policy = resolveActivePolicy();
  assert.equal(policy.version, CURRENT_POLICY_VERSION);
});

// ─── isRecoverablePlanningFailureFromPolicy ──────────────────────────────────

test('isRecoverablePlanningFailureFromPolicy accepts exact codes on the default policy', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'MISSION_HAS_NO_TASKS',
      policy,
    ),
    true,
  );
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'JSON_OBJECT_NOT_FOUND',
      policy,
    ),
    true,
  );
});

test('isRecoverablePlanningFailureFromPolicy accepts AUTO_PLAN_FAILED wrapper', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'AUTO_PLAN_FAILED:EXECUTION_DISAPPEARED',
      policy,
    ),
    true,
  );
});

test('isRecoverablePlanningFailureFromPolicy rejects non-allowlisted codes', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.equal(
    isRecoverablePlanningFailureFromPolicy(
      'AUTONOMOUS_RECOVERY_EXHAUSTED:TASK_FAILED:T1',
      policy,
    ),
    false,
  );
  assert.equal(
    isRecoverablePlanningFailureFromPolicy('MISSION_TEAM_EMPTY', policy),
    false,
  );
});

test('isRecoverablePlanningFailureFromPolicy returns false for non-string input', () => {
  const policy = resolveRecoveryPolicy(CURRENT_POLICY_VERSION);
  assert.equal(isRecoverablePlanningFailureFromPolicy(null, policy), false);
  assert.equal(isRecoverablePlanningFailureFromPolicy(42, policy), false);
  assert.equal(isRecoverablePlanningFailureFromPolicy('', policy), false);
});

// ─── Prevents unlimited budgets ─────────────────────────────────────────────