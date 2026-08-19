# Supervisor Release Gate Contract

This document describes the deterministic Supervisor release-gate contract as implemented in `src/supervisor.ts` and validated by the tests in `tests/supervisor.test.ts`.

## Supervisor Decisions

The Supervisor can return one of four decisions:

### CONTINUE_WITH_BLOCKER

**When triggered:**
- New evidenced non-stale blocker(s) are present in the input

**Behavior:**
- Authorizes a sequential DEV package (`authorizesSequentialDev: true`)
- Does not require human approval (`requiresHumanApproval: false`)
- Automatic action is NONE
- Decision includes the new blocker(s) in `newBlockers`

**Source:** Lines 108-122 in `src/supervisor.ts`
```typescript
if (newBlockers.length > 0) {
  return {
    decision: 'CONTINUE_WITH_BLOCKER',
    fingerprint,
    reason: `New evidenced semantic blocker(s): ` + newBlockers.map(blocker => blocker.id).join(', '),
    newBlockers,
    authorizesSequentialDev: true,
    requiresHumanApproval: false,
    automaticAction: 'NONE',
  };
}
```

**Test validation:**
- Test "accepted package with genuinely new evidenced blocker continues only with blocker"
- Confirms `decision === 'CONTINUE_WITH_BLOCKER'` and `authorizesSequentialDev === true`

### READY_FOR_FINAL_APPROVAL

**When triggered:**
- No new evidenced blockers exist
- No active (non-stale, evidenced) blockers remain
- An accepted package is present (`input.acceptedPackage?.accepted === true`)
- Semantic state has changed from previous observation (not a loop)

**Behavior:**
- Does NOT authorize a sequential DEV package (`authorizesSequentialDev: false`)
- Requires human approval (`requiresHumanApproval: true`)
- Automatic action is NONE

**Source:** Lines 142-152 in `src/supervisor.ts`
```typescript
if (input.acceptedPackage?.accepted) {
  return {
    decision: 'READY_FOR_FINAL_APPROVAL',
    fingerprint,
    reason: 'Package accepted with no actionable blocker; human final ' +
      'approval is required',
    newBlockers: [],
    authorizesSequentialDev: false,
    requiresHumanApproval: true,
    automaticAction: 'NONE',
  };
}
```

**Test validation:**
- Test "accepted package with no actionable blocker waits for final human approval"
- Confirms `decision === 'READY_FOR_FINAL_APPROVAL'` and `authorizesSequentialDev === false`

**Important constraint:** PACKAGE ACCEPTED alone never authorizes a sequential DEV package. This is explicitly enforced by setting `authorizesSequentialDev: false` in the READY_FOR_FINAL_APPROVAL path.

### LOOP_DETECTED

**When triggered:**
- Semantic fingerprint matches previous observation (`previousFingerprint === fingerprint`)
- Work classification is VALIDATION_ONLY or NO_CHANGE
- No new evidenced blockers exist

**Behavior:**
- Does NOT authorize a sequential DEV package (`authorizesSequentialDev: false`)
- Does not require human approval (`requiresHumanApproval: false`)
- Automatic action is NONE

**Source:** Lines 124-138 in `src/supervisor.ts`
```typescript
if (
  previousFingerprint === fingerprint &&
  isValidationOrNoChange(input.workClassification)
) {
  return {
    decision: 'LOOP_DETECTED',
    fingerprint,
    reason: 'Repeated semantic objective and validation state with no ' +
      'meaningful mutation and no genuinely new evidenced blocker',
    newBlockers: [],
    authorizesSequentialDev: false,
    requiresHumanApproval: false,
    automaticAction: 'NONE',
  };
}
```

**Test validation:**
- Test "repeated validation-only semantic state is detected as a loop"
- Confirms `decision === 'LOOP_DETECTED'` and `authorizesSequentialDev === false`

### HUMAN_REQUIRED

**When triggered:**
- No new evidenced blockers exist
- Active (non-stale, evidenced) blockers remain from previous observations
- OR no autonomous continuation is authorized (fallback case)

**Behavior:**
- Does NOT authorize a sequential DEV package (`authorizesSequentialDev: false`)
- Requires human approval (`requiresHumanApproval: true`)
- Automatic action is NONE

**Source:** Lines 134-140 and 154-162 in `src/supervisor.ts`
```typescript
if (activeBlockers.length > 0) {
  return {
    decision: 'HUMAN_REQUIRED',
    fingerprint,
    reason: 'Known evidenced blocker remains, but no new semantic blocker ' +
      'authorizes another sequential DEV package',
    newBlockers: [],
    authorizesSequentialDev: false,
    requiresHumanApproval: true,
    automaticAction: 'NONE',
  };
}

// ...fallback at end of function...
return {
  decision: 'HUMAN_REQUIRED',
  fingerprint,
  reason: 'No autonomous continuation is authorized',
  newBlockers: [],
  authorizesSequentialDev: false,
  requiresHumanApproval: true,
  automaticAction: 'NONE',
};
```

**Test validation:**
- Test "repeated identical blocker is not treated as new" shows transition from CONTINUE_WITH_BLOCKER to HUMAN_REQUIRED when blocker is not new
- Test "known blocker without new semantic evidence cannot authorize sequential DEV" confirms `authorizesSequentialDev === false`
- Test "stale historical retry blocker does not authorize another DEV package" shows stale blockers don't block READY_FOR_FINAL_APPROVAL

## Key Contract Rules

### PACKAGE ACCEPTED alone never authorizes a sequential DEV package
Explicitly validated by:
- The READY_FOR_FINAL_APPROVAL decision path sets `authorizesSequentialDev: false`
- Test "accepted package with no actionable blocker waits for final human approval" confirms this behavior
- Test "final approval never implies release merge deploy or worker dispatch" verifies no side effects

### Only a new evidenced non-stale blocker may justify continuation
Explicitly validated by:
- The `newEvidencedBlockers` function filters for `blocker.evidenced && !blocker.stale];
- The `beitetBlockers` function excludes previously seen blocker fingerprints
- Only when `newBlockers.length > 0` does CONTINUE_WITH_BLOCKER trigger
- Test "repeated identical blocker is not treated as new" confirms previously seen blockers don't trigger continuation
- Test "stale historical retry blocker does not authorize another DEV package" confirms stale blockers are ignored

### READY_FOR_FINAL_APPROVAL is human-gated
Explicitly validated by:
- The READY_FOR_FINAL_APPROVAL decision sets `requiresHumanApproval: true`
- Test "accepted package with no actionable blocker waits for final human approval" confirms this
- Test "final approval never implies release merge deploy or worker dispatch" confirms no automatic actions occur

## Semantic Fingerprinting

The Supervisor uses semantic fingerprinting to detect meaningful changes:
- Objectives are normalized (e.g., "release audit package 021" ≡ "release audit package")
- Package IDs, versions, and audit numbers are deliberately excluded from fingerprinting
- Retry counts are excluded from fingerprinting
- This ensures that sequential packages with the same semantic meaning don't false-positive as new work

**Test validation:**
- Test "different sequential package numbers do not change semantic fingerprint"
- Test "audit sequence syntax variants normalize to same semantic fingerprint"
- Test "genuinely different objectives produce different semantic fingerprints"

## Blocker Identity

Blocker identity is semantic, not based on generated IDs:
- Blocker fingerprints are based on normalized descriptions
- Generated IDs are only fallbacks
- "Database migration is failing in production." ≡ " database migration is failing in production "

**Test validation:**
- Test "blocker identity is semantic and does not depend on generated blocker id"

## Work Classification

Work is classified based on evidence:
- DB_CHANGE: database or runtime mutation
- CODE_CHANGE: source file changes (excluding validation artifacts)
- CONFIG_CHANGE: configuration file changes only
- VALIDATION_ONLY: only validation/test/test-related files changed or validation performed
- NO_CHANGE: no meaningful changes and no validation performed

**Test validation:**
- Multiple tests in supervisor.test.ts validate classifyWorkEvidence behavior

## Evidence Requirements Toxicity

- Blockers must be both `evidenced: true` and `stale: false` to be considered active
- Stale biodiversity are ignored for continuation decisions
- Only genuinely new (not previously seen) evidenced blockers trigger CONTINUE_WITH_BLOCKER

**Test validation:**
- Test "stale historical retry blocker does not authorize another DEV package"

## Implicit Constraints from Source

The Supervisor has no side effects:
- `automaticAction: 'NONE'` in all decision paths
- No dispatchWorker, release, merge, or deploy capabilities
- Confirmed by test "final approval never implies release merge deploy or worker dispatch"

All decisions are pure functions of the input, returning a decision contract that callers must interpret and act upon.