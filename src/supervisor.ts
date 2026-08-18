export type SupervisorDecision =
  | 'CONTINUE_WITH_BLOCKER'
  | 'READY_FOR_FINAL_APPROVAL'
  | 'LOOP_DETECTED'
  | 'HUMAN_REQUIRED';

export type WorkClassification =
  | 'CODE_CHANGE'
  | 'CONFIG_CHANGE'
  | 'DB_CHANGE'
  | 'VALIDATION_ONLY'
  | 'NO_CHANGE';

export interface Blocker {
  id: string;
  description: string;
  evidenced: boolean;
  stale: boolean;
  createdAt: number;
}

export interface Package {
  id: string;
  accepted: boolean;
  version: string;
  auditNumber: number;
}

export interface WorkEvidence {
  changedFiles?: readonly string[];
  dbMutation?: boolean;
  runtimeMutation?: boolean;
  validationPerformed?: boolean;
}

export interface SupervisorInput {
  objective: string;
  workClassification: WorkClassification;
  validationOutcome: string;
  blockers: readonly Blocker[];

  /**
   * Semantic fingerprint returned by the immediately relevant prior
   * Supervisor observation.
   */
  previousFingerprint?: string | null;

  /**
   * Semantic blocker fingerprints already observed previously.
   * Re-observing the same blocker must not authorize a fresh DEV package.
   */
  priorBlockerFingerprints?: readonly string[];

  acceptedPackage?: Package | null;

  /**
   * Operational metadata retained for callers, but deliberately excluded
   * from semantic fingerprinting.
   */
  retryCount?: number;
}

export interface SupervisorResult {
  decision: SupervisorDecision;
  fingerprint: string;
  reason: string;
  newBlockers: readonly Blocker[];

  /**
   * A decision contract only. Supervisor never dispatches workers itself.
   */
  authorizesSequentialDev: boolean;

  /**
   * READY_FOR_FINAL_APPROVAL and HUMAN_REQUIRED stop at a human boundary.
   */
  requiresHumanApproval: boolean;

  /**
   * Supervisor has no merge/release/deploy side effect.
   */
  automaticAction: 'NONE';
}

export function normalizeSemanticText(input: string): string {
  return input
    .normalize('NFKC')
    .toLowerCase()
    // "release audit package 021" and "... 022" are semantically identical.
    .replace(
      /\b(?:release\s+)?audit\s+package\s*[-#:]?\s*0*\d+\b/gu,
      'release audit package',
    )
    // Also normalize standalone package/audit sequence identifiers.
    .replace(/\bpackage\s*[-#:]?\s*0*\d+\b/gu, 'package')
    .replace(/\baudit\s*[-#:]?\s*0*\d+\b/gu, 'audit')
    // Collapse insignificant punctuation and whitespace conservatively.
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/gu, ' ')
    .trim();
}

function hashString(input: string): string {
  // Deterministic 32-bit FNV-1a. This is an identity fingerprint, not crypto.
  let hash = 0x811c9dc5;

  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }

  return hash.toString(16).padStart(8, '0');
}

export function blockerSemanticFingerprint(blocker: Blocker): string {
  const semanticDescription = normalizeSemanticText(blocker.description);

  // ID is only a fallback. Generated IDs must not define blocker identity.
  const semanticIdentity =
    semanticDescription || normalizeSemanticText(blocker.id);

  return hashString(semanticIdentity);
}

function activeEvidencedBlockers(
  blockers: readonly Blocker[],
): readonly Blocker[] {
  return blockers.filter(blocker => blocker.evidenced && !blocker.stale);
}

function newEvidencedBlockers(
  input: SupervisorInput,
): readonly Blocker[] {
  const previouslySeen =
    new Set(input.priorBlockerFingerprints ?? []);

  return activeEvidencedBlockers(input.blockers).filter(
    blocker =>
      !previouslySeen.has(blockerSemanticFingerprint(blocker)),
  );
}

export function computeSemanticFingerprint(
  input: SupervisorInput,
): string {
  const blockerFingerprints =
    activeEvidencedBlockers(input.blockers)
      .map(blockerSemanticFingerprint)
      .sort();

  const semanticState = {
    objective: normalizeSemanticText(input.objective),
    workClassification: input.workClassification,
    validationOutcome:
      normalizeSemanticText(input.validationOutcome),
    blockerFingerprints,
  };

  /*
   * Deliberately excluded:
   * - package id
   * - version
   * - auditNumber
   * - retryCount
   * - acceptance sequence metadata
   */
  return hashString(JSON.stringify(semanticState));
}

function normalizePath(path: string): string {
  return path.replaceAll('\\', '/').toLowerCase();
}

function isValidationArtifact(path: string): boolean {
  const p = normalizePath(path);

  return (
    p.startsWith('tests/') ||
    p.startsWith('test/') ||
    p.startsWith('coverage/') ||
    p.startsWith('reports/') ||
    /(^|\/)audit[-_/]?\d*[-_/]?evidence(\/|$)/u.test(p) ||
    /(^|\/)(?:diagnostic|diagnostics)(?:[-_.\/]|$)/u.test(p) ||
    /(^|\/)evidence(\/|$)/u.test(p) ||
    /\.(?:test|spec)\.[cm]?[jt]sx?$/u.test(p)
  );
}

function isConfigArtifact(path: string): boolean {
  const p = normalizePath(path);

  return (
    p === 'package.json' ||
    p === 'package-lock.json' ||
    p === 'tsconfig.json' ||
    p === '.gitignore' ||
    p.startsWith('.github/') ||
    p.startsWith('config/') ||
    p.includes('/config/') ||
    /(^|\/)(?:[^/]+\.)?config\.(?:ts|js|mjs|cjs|json|ya?ml|toml)$/u.test(p) ||
    /(^|\/)\.env(?:\.|$)/u.test(p)
  );
}

function isSourceArtifact(path: string): boolean {
  const p = normalizePath(path);

  return (
    p.startsWith('src/') &&
    /\.[cm]?[jt]sx?$/u.test(p) &&
    !isConfigArtifact(p)
  );
}

/**
 * Pure evidence classifier.
 *
 * Evidence from Git/runtime state is authoritative. Agent prose is
 * intentionally not an input.
 */
export function classifyWorkEvidence(
  evidence: WorkEvidence,
): WorkClassification {
  if (evidence.dbMutation || evidence.runtimeMutation) {
    return 'DB_CHANGE';
  }

  const changedFiles =
    (evidence.changedFiles ?? [])
      .map(normalizePath)
      .filter(Boolean);

  const meaningfulFiles =
    changedFiles.filter(path => !isValidationArtifact(path));

  if (meaningfulFiles.some(isSourceArtifact)) {
    return 'CODE_CHANGE';
  }

  if (
    meaningfulFiles.length > 0 &&
    meaningfulFiles.every(isConfigArtifact)
  ) {
    return 'CONFIG_CHANGE';
  }

  /*
   * Unknown meaningful product deltas are treated conservatively as code
   * change so they cannot be hidden by a validation-only classification.
   */
  if (meaningfulFiles.length > 0) {
    return 'CODE_CHANGE';
  }

  if (
    changedFiles.length > 0 ||
    evidence.validationPerformed
  ) {
    return 'VALIDATION_ONLY';
  }

  return 'NO_CHANGE';
}

function isValidationOrNoChange(
  classification: WorkClassification,
): boolean {
  return (
    classification === 'VALIDATION_ONLY' ||
    classification === 'NO_CHANGE'
  );
}

export function decideSupervisor(
  input: SupervisorInput,
): SupervisorResult {
  const fingerprint = computeSemanticFingerprint(input);
  const newBlockers = newEvidencedBlockers(input);
  const activeBlockers = activeEvidencedBlockers(input.blockers);

  if (newBlockers.length > 0) {
    return {
      decision: 'CONTINUE_WITH_BLOCKER',
      fingerprint,
      reason:
        `New evidenced semantic blocker(s): ` +
        newBlockers.map(blocker => blocker.id).join(', '),
      newBlockers,
      authorizesSequentialDev: true,
      requiresHumanApproval: false,
      automaticAction: 'NONE',
    };
  }

  const previousFingerprint =
    input.previousFingerprint ?? null;

  if (
    previousFingerprint === fingerprint &&
    isValidationOrNoChange(input.workClassification)
  ) {
    return {
      decision: 'LOOP_DETECTED',
      fingerprint,
      reason:
        'Repeated semantic objective and validation state with no ' +
        'meaningful mutation and no genuinely new evidenced blocker',
      newBlockers: [],
      authorizesSequentialDev: false,
      requiresHumanApproval: false,
      automaticAction: 'NONE',
    };
  }

  /*
   * A previously-known active blocker must not manufacture a new sequential
   * DEV package. It remains visible and requires explicit human/controller
   * handling instead.
   */
  if (activeBlockers.length > 0) {
    return {
      decision: 'HUMAN_REQUIRED',
      fingerprint,
      reason:
        'Known evidenced blocker remains, but no new semantic blocker ' +
        'authorizes another sequential DEV package',
      newBlockers: [],
      authorizesSequentialDev: false,
      requiresHumanApproval: true,
      automaticAction: 'NONE',
    };
  }

  if (input.acceptedPackage?.accepted) {
    return {
      decision: 'READY_FOR_FINAL_APPROVAL',
      fingerprint,
      reason:
        'Package accepted with no actionable blocker; human final ' +
        'approval is required',
      newBlockers: [],
      authorizesSequentialDev: false,
      requiresHumanApproval: true,
      automaticAction: 'NONE',
    };
  }

  return {
    decision: 'HUMAN_REQUIRED',
    fingerprint,
    reason: 'No autonomous continuation is authorized',
    newBlockers: [],
    authorizesSequentialDev: false,
    requiresHumanApproval: true,
    automaticAction: 'NONE',
  };
}
