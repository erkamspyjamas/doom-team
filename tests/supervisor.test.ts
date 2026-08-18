import assert from 'node:assert/strict';
import test from 'node:test';

import {
  blockerSemanticFingerprint,
  classifyWorkEvidence,
  computeSemanticFingerprint,
  decideSupervisor,
  type Blocker,
  type SupervisorInput,
} from '../src/supervisor.js';

const acceptedPackage = {
  id: 'pkg-any',
  accepted: true,
  version: '0.12.19',
  auditNumber: 21,
};

function input(
  overrides: Partial<SupervisorInput> = {},
): SupervisorInput {
  return {
    objective: 'Release audit package 021',
    workClassification: 'NO_CHANGE',
    validationOutcome: '68 tests pass, 49 diagnostics pass',
    blockers: [],
    previousFingerprint: null,
    priorBlockerFingerprints: [],
    acceptedPackage,
    retryCount: 0,
    ...overrides,
  };
}

function blocker(
  overrides: Partial<Blocker> = {},
): Blocker {
  return {
    id: 'blocker-1',
    description: 'Production release is blocked by failing validation',
    evidenced: true,
    stale: false,
    createdAt: 1,
    ...overrides,
  };
}

test('accepted package with genuinely new evidenced blocker continues only with blocker', () => {
  const result = decideSupervisor(
    input({
      blockers: [blocker()],
    }),
  );

  assert.equal(result.decision, 'CONTINUE_WITH_BLOCKER');
  assert.equal(result.authorizesSequentialDev, true);
  assert.equal(result.newBlockers.length, 1);
  assert.equal(result.automaticAction, 'NONE');
});

test('repeated identical blocker is not treated as new', () => {
  const existing = blocker();

  const result = decideSupervisor(
    input({
      blockers: [existing],
      priorBlockerFingerprints: [
        blockerSemanticFingerprint(existing),
      ],
    }),
  );

  assert.notEqual(result.decision, 'CONTINUE_WITH_BLOCKER');
  assert.equal(result.decision, 'HUMAN_REQUIRED');
  assert.equal(result.newBlockers.length, 0);
  assert.equal(result.authorizesSequentialDev, false);
});

test('accepted package with no actionable blocker waits for final human approval', () => {
  const result = decideSupervisor(input());

  assert.equal(result.decision, 'READY_FOR_FINAL_APPROVAL');
  assert.equal(result.authorizesSequentialDev, false);
  assert.equal(result.requiresHumanApproval, true);
  assert.equal(result.automaticAction, 'NONE');
});

test('repeated validation-only semantic state is detected as a loop', () => {
  const firstInput = input({
    workClassification: 'VALIDATION_ONLY',
  });

  const first = decideSupervisor(firstInput);

  const second = decideSupervisor({
    ...firstInput,
    objective: 'Release audit package 022',
    previousFingerprint: first.fingerprint,
  });

  assert.equal(second.decision, 'LOOP_DETECTED');
  assert.equal(second.authorizesSequentialDev, false);
  assert.equal(second.automaticAction, 'NONE');
});

test('different sequential package numbers do not change semantic fingerprint', () => {
  const package21 = computeSemanticFingerprint(
    input({
      objective: 'Release audit package 021',
      acceptedPackage: {
        ...acceptedPackage,
        id: 'pkg-021',
        auditNumber: 21,
      },
      retryCount: 1,
    }),
  );

  const package22 = computeSemanticFingerprint(
    input({
      objective: 'Release audit package 022',
      acceptedPackage: {
        ...acceptedPackage,
        id: 'pkg-022',
        version: '99.99.99',
        auditNumber: 22,
      },
      retryCount: 999,
    }),
  );

  assert.equal(package21, package22);
});

test('audit sequence syntax variants normalize to same semantic fingerprint', () => {
  const a = computeSemanticFingerprint(
    input({ objective: 'Audit-021 release validation' }),
  );

  const b = computeSemanticFingerprint(
    input({ objective: 'audit 022 release validation' }),
  );

  assert.equal(a, b);
});

test('genuinely different objectives produce different semantic fingerprints', () => {
  const releaseAudit = computeSemanticFingerprint(
    input({ objective: 'Release audit package 021' }),
  );

  const securityFix = computeSemanticFingerprint(
    input({ objective: 'Repair authentication security vulnerability' }),
  );

  assert.notEqual(releaseAudit, securityFix);
});

test('blocker identity is semantic and does not depend on generated blocker id', () => {
  const first = blocker({
    id: 'generated-111',
    description: 'Database migration is failing in production.',
  });

  const second = blocker({
    id: 'generated-999',
    description: ' database migration is failing in production ',
  });

  assert.equal(
    blockerSemanticFingerprint(first),
    blockerSemanticFingerprint(second),
  );
});

test('source TypeScript delta is CODE_CHANGE', () => {
  assert.equal(
    classifyWorkEvidence({
      changedFiles: ['src/missions.ts'],
    }),
    'CODE_CHANGE',
  );
});

test('config-only delta is CONFIG_CHANGE', () => {
  assert.equal(
    classifyWorkEvidence({
      changedFiles: [
        'src/config.ts',
        'package.json',
      ],
    }),
    'CONFIG_CHANGE',
  );
});

test('explicit database mutation is DB_CHANGE and outranks file evidence', () => {
  assert.equal(
    classifyWorkEvidence({
      changedFiles: ['tests/supervisor.test.ts'],
      dbMutation: true,
    }),
    'DB_CHANGE',
  );
});

test('runtime mutation is classified separately as DB_CHANGE', () => {
  assert.equal(
    classifyWorkEvidence({
      runtimeMutation: true,
    }),
    'DB_CHANGE',
  );
});

test('tests diagnostics and audit evidence only are VALIDATION_ONLY', () => {
  assert.equal(
    classifyWorkEvidence({
      changedFiles: [
        'tests/supervisor.test.ts',
        'audit-022-evidence/diagnostics.json',
        'diagnostics-output.json',
      ],
      validationPerformed: true,
    }),
    'VALIDATION_ONLY',
  );
});

test('validation performed without meaningful mutation is VALIDATION_ONLY', () => {
  assert.equal(
    classifyWorkEvidence({
      validationPerformed: true,
    }),
    'VALIDATION_ONLY',
  );
});

test('no mutation and no validation is NO_CHANGE', () => {
  assert.equal(
    classifyWorkEvidence({}),
    'NO_CHANGE',
  );
});

test('stale historical retry blocker does not authorize another DEV package', () => {
  const staleRetry = blocker({
    id: 'package-001-retry',
    description: 'Historical Package 001 retry artifact',
    stale: true,
  });

  const result = decideSupervisor(
    input({
      blockers: [staleRetry],
    }),
  );

  assert.equal(result.decision, 'READY_FOR_FINAL_APPROVAL');
  assert.equal(result.authorizesSequentialDev, false);
  assert.equal(result.newBlockers.length, 0);
});

test('known blocker without new semantic evidence cannot authorize sequential DEV', () => {
  const existing = blocker();

  const result = decideSupervisor(
    input({
      workClassification: 'CODE_CHANGE',
      blockers: [existing],
      priorBlockerFingerprints: [
        blockerSemanticFingerprint(existing),
      ],
    }),
  );

  assert.equal(result.decision, 'HUMAN_REQUIRED');
  assert.equal(result.authorizesSequentialDev, false);
});

test('final approval never implies release merge deploy or worker dispatch', () => {
  const result = decideSupervisor(input());
  const exposed = result as unknown as Record<string, unknown>;

  assert.equal(result.decision, 'READY_FOR_FINAL_APPROVAL');
  assert.equal(result.automaticAction, 'NONE');
  assert.equal(result.authorizesSequentialDev, false);
  assert.equal(result.requiresHumanApproval, true);

  assert.equal('dispatchWorker' in exposed, false);
  assert.equal('release' in exposed, false);
  assert.equal('merge' in exposed, false);
  assert.equal('deploy' in exposed, false);
});
