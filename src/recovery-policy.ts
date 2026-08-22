/**
 * Versioned Recovery Policy
 *
 * Canonical single-source for all recovery budgets, allowlists, retry/backoff
 * semantics, and terminal cutover rules.
 *
 * Former duplicated paths (reproducible policy ambiguity):
 *
 *   src/startup-recovery-policy.ts  MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS = 3
 *   src/missions.ts                 MAX_AUTONOMOUS_RECOVERY_PER_TASK       = 3
 *                                    MAX_AUTONOMOUS_RECOVERY_PER_MISSION   = 8
 *                                    MAX_AUTONOMOUS_FINAL_GATE_RECOVERY    = 3
 *
 * Both paths ultimately governed the same budget ceiling but read from
 * unrelated constant definitions, so changing one silently left the other
 * stale.  All four values now flow through resolveRecoveryPolicy().
 *
 * Policy versioning invariant — changing the policy version must NOT silently
 * reset consumed recovery history:
 *
 *   Recovery history lives in the recovery_attempts ledger (row-per-attempt)
 * and in the mission_events audit log (already monotonic by event id).  Those
 * stores are append-only; this module only supplies the *budget ceiling* for
 * *new* attempts.  Existing ledger rows remain fully readable after a version
 * bump — no migration rewrites historic counts.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

export interface RecoveryPolicyBudgets {
  /** Max autonomous task-level recovery attempts per failed task.            */
  task: number;
  /** Max total recovery attempts across an entire mission.                    */
  mission: number;
  /** Max final-gate (QA / reviewer) recovery attempts per gate.               */
  finalGate: number;
  /** Max zero-task autonomous planning replan attempts.                       */
  zeroTaskReplan: number;
}

export interface RecoveryPolicy {
  version: string;
  budgets: RecoveryPolicyBudgets;

  /** Planning failure codes that are safe to retry autonomously.              */
  recoverablePlanningFailureCodes: readonly string[];
  /** Prefix-match variants (applied only after code-set match fails).         */
  recoverablePlanningFailurePrefixes: readonly string[];

  /** Initial delay before first retry (informational; ms).                   */
  backoffBaseMs: number;
  /** Upper bound on any single delay (informational; ms).                    */
  maxBackoffMs: number;

  /**
   * When true the policy explicitly allows writing a 'refused' terminal
   * outcome on top of a prior 'exhausted' outcome if the caller had consumed
   * the budget under an older version before the version change.
   * This NEVER grants new attempts; it only prevents stale terminal states.
   */
  overwriteIfExhausted: boolean;

  /**
   * When true an unrecognised version string is treated as fatal: resolve
   * returns FAIL_CLOSED_SENTINEL (all-zero budgets).  When false the
   * default registered policy is used as the safe fallback.
   */
  failClosedOnUnknownPolicyVersion: boolean;
}

// ─── Safe-fail sentinel ───────────────────────────────────────────────────────

/**
 * Sentinel returned when policy validation fails or when an unknown version
 * is encountered and the default policy requests fail-closed behaviour.
 *
 * All-zero budgets guarantee the exhaustion guard fires on the very next
 * attempt — no unlimited retries are possible under this policy.
 */
export const FAIL_CLOSED_SENTINEL: RecoveryPolicy = {
  version: 'fail-closed-sentinel',
  budgets: { task: 0, mission: 0, finalGate: 0, zeroTaskReplan: 0 },
  recoverablePlanningFailureCodes: [],
  recoverablePlanningFailurePrefixes: [],
  backoffBaseMs: 0,
  maxBackoffMs: 0,
  overwriteIfExhausted: false,
  failClosedOnUnknownPolicyVersion: true,
};

// ─── Authoritative allowlists ────────────────────────────────────────────────

/**
 * Planning failure codes that are safe to retry autonomously.
 * This is the single authoritative source; callers should import these
 * constants rather than copy-paste the values.
 */
export const RECOVERABLE_PLANNING_FAILURE_CODES = /** @type *const */ [
  'EXECUTION_DISAPPEARED',
  'EXECUTION_WAIT_TIMEOUT',
  'JSON_OBJECT_NOT_FOUND',
  'LEAD_PLAN_TASKS_REQUIRED',
  'LEAD_PLAN_TOO_MANY_TASKS',
  'MISSION_HAS_NO_TASKS',
  'PLAN_QUALITY_VALIDATION_FAILED_AFTER_REPAIR',
] as const;

/**
 * Prefix-match variants.  Only consulted after code-set match fails, to
 * prevent wrapping safety / config errors inside AUTO_PLAN_FAILED.
 */
export const RECOVERABLE_PLANNING_FAILURE_PREFIXES = /** @type *const */ [
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

// ─── Policy registry ─────────────────────────────────────────────────────────

/**
 * Known policy versions.  Add new versions by appending — never remove or
 * reuse an existing label.
 *
 * Version "2025-08-22-v1" is the initial production default.  Its budgets
 * exactly match the legacy hard-coded constants so the transition is
 * behaviour-preserving:
 *
 *   task         = 3  (was MAX_AUTONOMOUS_RECOVERY_PER_TASK)
 *   mission      = 8  (was MAX_AUTONOMOUS_RECOVERY_PER_MISSION)
 *   finalGate    = 3  (was MAX_AUTONOMOUS_FINAL_GATE_RECOVERY)
 *   zeroTaskReplan = 3 (was MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS)
 */
const REGISTERED_POLICIES: ReadonlyMap<string, RecoveryPolicy> = new Map([
  [
    '2025-08-22-v1',
    {
      version: '2025-08-22-v1',
      budgets: {
        task: 3,
        mission: 8,
        finalGate: 3,
        zeroTaskReplan: 3,
      },
      recoverablePlanningFailureCodes: [...RECOVERABLE_PLANNING_FAILURE_CODES],
      recoverablePlanningFailurePrefixes: [...RECOVERABLE_PLANNING_FAILURE_PREFIXES],
      backoffBaseMs: 500,
      maxBackoffMs: 30_000,
      overwriteIfExhausted: true,
      failClosedOnUnknownPolicyVersion: false,
    },
  ],
] as const);

// ─── Validation ──────────────────────────────────────────────────────────────

interface BudgetFieldSpec {
  key: keyof RecoveryPolicyBudgets;
  min: number;
  max: number;
}

const BUDGET_FIELD_SPECS: readonly BudgetFieldSpec[] = [
  { key: 'task',          min: 1, max: 2_147_483_647 },
  { key: 'mission',       min: 1, max: 2_147_483_647 },
  { key: 'finalGate',     min: 1, max: 2_147_483_647 },
  { key: 'zeroTaskReplan', min: 1, max: 2_147_483_647 },
];

/**
 * validatePolicy runs a structural safety check on a policy definition.
 *
 * Rules enforced:
 *   - version is a non-empty string.
 *   - every budget ceiling is an integer in [min, max] (no ≤0 = unbounded).
 *   - recoverablePlanningFailureCodes: unique, non-empty strings.
 *   - recoverablePlanningFailurePrefixes: unique, non-empty strings.
 *   - backoffBaseMs ≥ 0, maxBackoffMs ≥ 0, backoffBaseMs ≤ maxBackoffMs.
 *   - overwriteIfExhausted and failClosedOnUnknownPolicyVersion are booleans.
 *
 * @returns null when the policy passes every check, or a human-readable
 *          violation string otherwise.
 */
export function validatePolicy(policy: RecoveryPolicy): string | null {
  if (typeof policy.version !== 'string' || policy.version.trim() === '') {
    return 'policy.version must be a non-empty string';
  }

  const b = policy.budgets;
  for (const spec of BUDGET_FIELD_SPECS) {
    const v = b[spec.key];
    if (
      !Number.isInteger(v) ||
      v < spec.min ||
      v > spec.max
    ) {
      return `budgets.${String(spec.key)} must be an integer in [${spec.min}, ${spec.max}]; got ${v}`;
    }
  }

  const codeSet = new Set<string>();
  for (const code of policy.recoverablePlanningFailureCodes) {
    if (typeof code !== 'string' || code.trim() === '') {
      return 'recoverablePlanningFailureCodes entries must be non-empty strings';
    }
    if (codeSet.has(code)) {
      return `recoverablePlanningFailureCodes contains duplicate: ${code}`;
    }
    codeSet.add(code);
  }

  const prefixSet = new Set<string>();
  for (const prefix of policy.recoverablePlanningFailurePrefixes) {
    if (typeof prefix !== 'string' || prefix === '') {
      return 'recoverablePlanningFailurePrefixes entries must be non-empty strings';
    }
    if (prefixSet.has(prefix)) {
      return `recoverablePlanningFailurePrefixes contains duplicate: ${prefix}`;
    }
    prefixSet.add(prefix);
  }

  if (!Number.isInteger(policy.backoffBaseMs) || policy.backoffBaseMs < 0) {
    return `backoffBaseMs must be a non-negative integer; got ${policy.backoffBaseMs}`;
  }
  if (!Number.isInteger(policy.maxBackoffMs) || policy.maxBackoffMs < 0) {
    return `maxBackoffMs must be a non-negative integer; got ${policy.maxBackoffMs}`;
  }
  if (policy.backoffBaseMs > policy.maxBackoffMs) {
    return `backoffBaseMs (${policy.backoffBaseMs}) must be ≤ maxBackoffMs (${policy.maxBackoffMs})`;
  }

  if (typeof policy.overwriteIfExhausted !== 'boolean') {
    return 'overwriteIfExhausted must be a boolean';
  }
  if (typeof policy.failClosedOnUnknownPolicyVersion !== 'boolean') {
    return 'failClosedOnUnknownPolicyVersion must be a boolean';
  }

  return null;
}

// ─── Registry helpers ────────────────────────────────────────────────────────

export function listRegisteredPolicyVersions(): readonly string[] {
  return [...REGISTERED_POLICIES.keys()];
}

export function getRegisteredPolicy(version: string): RecoveryPolicy | undefined {
  return REGISTERED_POLICIES.get(version);
}

// ─── Resolver ────────────────────────────────────────────────────────────────

/**
 * resolveRecoveryPolicy returns the policy for a given version label.
 *
 * Resolution rules:
 *   1. Registered + valid              → that policy
 *   2. Registered + invalid            → FAIL_CLOSED_SENTINEL (developer error)
 *   3. Not registered, default fails-close → FAIL_CLOSED_SENTINEL
 *   4. Not registered, default permissive → default policy (back-compat path)
 *
 * Rule 4 preserves backward compatibility for any mission recorded before
 * versioned policies were introduced.
 */
export function resolveRecoveryPolicy(version: string): RecoveryPolicy {
  const registered = REGISTERED_POLICIES.get(version);

  if (registered) {
    // Even registered policies can fail validation if a developer pushes a
    // malformed entry; fail closed in that case.
    return validatePolicy(registered) === null
      ? registered
      : FAIL_CLOSED_SENTINEL;
  }

  // Unknown version: consult the default policy's failClosed flag.
  const defaultPolicy = REGISTERED_POLICIES.values().next().value as
    | RecoveryPolicy
    | undefined;

  if (defaultPolicy?.failClosedOnUnknownPolicyVersion) {
    return FAIL_CLOSED_SENTINEL;
  }

  // Backward-compat: unknown version silently falls back to default.
  return defaultPolicy ?? FAIL_CLOSED_SENTINEL;
}

// ─── Convenience accessors ────────────────────────────────────────────────────

/**
 * resolveBudgets returns only the budget ceiling object for a version.
 * Unknown or unsafe versions yield all-zero budgets so the exhaustion guard
 * fires immediately.
 */
export function resolveBudgets(version: string): RecoveryPolicyBudgets {
  return resolveRecoveryPolicy(version).budgets;
}

// Active-version cache (set at mission-start or system start).
let _activeVersion: string | undefined;

export function setActiveRecoveryPolicyVersion(version: string | undefined): void {
  _activeVersion = version;
}

export function getActiveRecoveryPolicyVersion(): string | undefined {
  return _activeVersion;
}

/** Resolve the policy for the currently cached active version. */
export function resolveActivePolicy(): RecoveryPolicy {
  if (_activeVersion !== undefined) {
    return resolveRecoveryPolicy(_activeVersion);
  }
  const defaultPolicy = REGISTERED_POLICIES.values().next().value as
    | RecoveryPolicy
    | undefined;
  return defaultPolicy ?? FAIL_CLOSED_SENTINEL;
}

/**
 * CURRENT_POLICY_VERSION is the label of the current default policy.
 *
 * Call-sites that need to stamp a mission or decision with the policy
 * version should use this constant.  Missions that already carry a version
 * attribute resolved at creation time must preserve that value across
 * restarts — changing CURRENT_POLICY_VERSION does NOT reset their consumed
 * recovery history.
 */
export const CURRENT_POLICY_VERSION: string =
  (REGISTERED_POLICIES.keys().next().value as string | undefined) ?? '2025-08-22-v1';

// ─── Semantic helpers (importable by callers) ─────────────────────────────────

/**
 * isRecoverablePlanningFailureFromPolicy checks a failure token against the
 * given policy's allowlist.  Returns false for FAIL_CLOSED_SENTINEL (unknown
 * or unsafe policies deny all planning recovery).
 */
export function isRecoverablePlanningFailureFromPolicy(
  errorText: unknown,
  policy: RecoveryPolicy,
): boolean {
  if (
    typeof errorText !== 'string' ||
    !errorText.trim() ||
    policy === FAIL_CLOSED_SENTINEL
  ) {
    return false;
  }

  const error = unwrapPlanningFailure(errorText);
  if (!error) return false;

  if (policy.recoverablePlanningFailureCodes.includes(error)) {
    return true;
  }

  return policy.recoverablePlanningFailurePrefixes.some((prefix) =>
    error.startsWith(prefix)
  );
}

// ─── Internal helpers ────────────────────────────────────────────────────────

/** Unwrap AUTO_PLAN_FAILED / AUTO_SEMANTIC_REPLAN_FAILED wrappers iteratively. */
function unwrapPlanningFailure(errorText: string): string {
  let error = errorText.trim();
  const wrappers = [
    'AUTO_PLAN_FAILED:',
    'AUTO_SEMANTIC_REPLAN_FAILED:',
  ] as const;

  // At most 4 wrapper depths (guard against pathological nesting).
  for (let depth = 0; depth < 4; depth += 1) {
    const wrapper = wrappers.find((w) => error.startsWith(w));
    if (!wrapper) break;
    error = error.slice(wrapper.length).trim();
  }

  return error;
}