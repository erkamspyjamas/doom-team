import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import test from 'node:test';
import {
  retainForensicArtifact,
  buildForensicDiffBundle,
} from '../src/git/worktrees.js';

function git(workspace: string, args: string[]) {
  execFileSync('git', args, {
    cwd: workspace,
    encoding: 'utf-8',
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
  });
}

function gitCapture(workspace: string, args: string[]): string {
  return execFileSync('git', args, {
    cwd: workspace,
    encoding: 'utf-8',
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
  }).trim();
}

/** Mirror of safeSegment in src/git/worktrees.ts: strips up to and including first underscore. */
function safeSegment(value: string): string {
  return String(value).replace(/^.*?_/, '').replace(/[^a-zA-Z0-9._-]+/g, '-').slice(0, 48) || 'item';
}

function initBase(base: string, branch = 'main') {
  git(base, ['init', '--quiet', `--initial-branch=${branch}`]);
  git(base, ['config', 'user.name', 'Doom Test']);
  git(base, ['config', 'user.email', 'doom-test@example.invalid']);
  writeFileSync(join(base, 'README.md'), '# Fixture\n');
  git(base, ['add', 'README.md']);
  git(base, ['commit', '--quiet', '-m', 'initial']);
}

/**
 * Worktree is created INSIDE `baseDir` so that `git` run inside the worktree
 * can traverse up to find `.git` in `baseDir`.
 *
 * Layout inside baseDir:
 *   .git/
 *   worktrees/<missionshort>/       ← linked worktree (returned)
 *   retained/<missionshort>/        ← where retainForensicArtifact writes
 */
function createDirtyWorktree(baseDir: string, missionId: string): string {
  const missionshort = safeSegment(missionId);
  const wtDir = join(baseDir, 'worktrees', missionshort);
  try { rmSync(wtDir, { recursive: true, force: true }); } catch { /* ignore */ }
  mkdirSync(dirname(wtDir), { recursive: true });

  git(baseDir, ['worktree', 'add', '-b', `doom/task/${missionshort}-a1`, wtDir, 'HEAD']);

  // Tracked dirty change: modify the README.md brought in from base.
  writeFileSync(join(wtDir, 'README.md'), '# Dirty task worktree (agent-modified)\n');

  // Untracked at root level.
  writeFileSync(join(wtDir, '.env.local'), 'SECRET=test-only-not-real\n');

  // Untracked in sub-directory.
  mkdirSync(join(wtDir, 'scratch'), { recursive: true });
  writeFileSync(join(wtDir, 'scratch', 'session.log'), 'agent stdout before worker crash\n');

  // Ensure retention target directory exists (mirrors production call pattern).
  mkdirSync(join(baseDir, 'retained', missionshort), { recursive: true });

  return wtDir;
}

// ─── retainForensicArtifact ─────────────────────────────────────────────────

test(
  'retainForensicArtifact dirty: patch+metadata with correct schema and provenance bound to mission/task',
  async (t) => {
    const base = mkdtempSync(join(tmpdir(), 'doom-RA-dirty-'));
    // missionId contains an underscore so safeSegment returns a valid path segment.
    const missionId = 'phase2b_forensic_dirty';
    t.after(() => rmSync(base, { recursive: true, force: true }));
    initBase(base);

    const wtDir = createDirtyWorktree(base, missionId);

    const { artifactPath, metadataPath } = await retainForensicArtifact({
      workspace: wtDir,
      missionId,
      taskKey: 'T1',
      attemptNo: 1,
      worktreeRoot: base,
      context: 'cleanupFailedTaskAttempt',
      tags: {
        guardBlockReason: 'PRE_CLEANUP_WORKTREE_BLOCKED:2 paths dirty',
        recoveryAttempt: 1,
      },
    });

    assert.ok(existsSync(artifactPath));
    assert.ok(existsSync(metadataPath));
    const meta = JSON.parse(readFileSync(metadataPath, 'utf8'));

    assert.equal(meta.schemaVersion, 'doom-forensic-artifact/v1');
    assert.ok(typeof meta.retainedAt === 'string' && meta.retainedAt.length > 10);
    assert.ok(meta.retainedAt.startsWith('20'), 'retainedAt must be a valid ISO timestamp');

    assert.equal(meta.provenance.missionId, missionId);
    assert.equal(meta.provenance.taskKey, 'T1');
    assert.equal(meta.provenance.attemptNo, 1);
    assert.equal(meta.provenance.context, 'cleanupFailedTaskAttempt');
    assert.equal(meta.provenance.worktreeRoot, base);
    assert.ok(meta.provenance.headCommit !== null);
    assert.ok(meta.provenance.branch !== null);

    assert.deepEqual(meta.tags, {
      guardBlockReason: 'PRE_CLEANUP_WORKTREE_BLOCKED:2 paths dirty',
      recoveryAttempt: 1,
    });

    assert.ok(
      meta.provenance.changedPaths.some((p: string) => p.includes('README.md')),
      `expected README.md in changedPaths, got: ${JSON.stringify(meta.provenance.changedPaths)}`
    );
    assert.ok(
      (meta.provenance.changedPaths.length >= 2 || meta.provenance.untrackedPaths.length >= 1),
      `expected at least 2 dirty paths, got changed=${meta.provenance.changedPaths.length} untracked=${meta.provenance.untrackedPaths.length}`
    );

    const patch = readFileSync(artifactPath, 'utf8');
    assert.ok(patch.length > 0, 'patch must be non-empty for a dirty worktree');
    assert.ok(
      patch.includes('README.md') || patch.includes('# Dirty task worktree') || patch.includes('diff --git'),
      'expected README.md change in patch'
    );
  }
);

test(
  'retainForensicArtifact clean: empty patch, full provenance, empty changedPaths',
  async (t) => {
    const base = mkdtempSync(join(tmpdir(), 'doom-RA-clean-'));
    const missionId = 'phase2b_forensic_clean';
    mkdirSync(join(base, 'retained', safeSegment(missionId)), { recursive: true });
    t.after(() => rmSync(base, { recursive: true, force: true }));
    initBase(base);

    const wtDir = join(base, 'worktrees', safeSegment(missionId));
    mkdirSync(dirname(wtDir), { recursive: true });
    git(base, ['worktree', 'add', '-b', 'doom/task/cleanT-a1', wtDir, 'HEAD']);

    const { artifactPath, metadataPath } = await retainForensicArtifact({
      workspace: wtDir,
      missionId,
      taskKey: 'T-clean',
      attemptNo: 1,
      worktreeRoot: base,
      context: 'unit-test-clean',
      tags: { scenario: 'clean' },
    });

    assert.ok(existsSync(artifactPath));
    assert.ok(existsSync(metadataPath));
    const meta = JSON.parse(readFileSync(metadataPath, 'utf8'));
    assert.equal(meta.schemaVersion, 'doom-forensic-artifact/v1');
    assert.equal(meta.provenance.missionId, missionId);
    assert.equal(meta.provenance.taskKey, 'T-clean');
    assert.equal(meta.provenance.attemptNo, 1);
    assert.equal(meta.provenance.context, 'unit-test-clean');
    assert.equal(meta.provenance.worktreeRoot, base);
    assert.ok(meta.provenance.headCommit !== null);
    assert.ok(meta.provenance.branch !== null);
    assert.equal(meta.provenance.changedPaths.length, 0);
    assert.equal(readFileSync(artifactPath, 'utf8').length, 0);
    assert.equal(meta.provenance.patchByteLength, 0);
  }
);

test(
  'retainForensicArtifact: de-duplicates slugs by appending a counter',
  async (t) => {
    const base = mkdtempSync(join(tmpdir(), 'doom-RA-dup-'));
    const missionId = 'phase2b_forensic_dup';
    t.after(() => rmSync(base, { recursive: true, force: true }));
    initBase(base);

    const wtDir = createDirtyWorktree(base, missionId);
    const inputs = {
      workspace: wtDir,
      missionId,
      taskKey: 'T1',
      attemptNo: 1,
      worktreeRoot: base,
      context: 'same-ctx',
      tags: { run: 1 },
    };

    const r1 = await retainForensicArtifact(inputs);
    const r2 = await retainForensicArtifact(inputs);

    assert.notEqual(r1.artifactPath, r2.artifactPath, 'paths must differ after 2nd call');
    assert.notEqual(r1.metadataPath, r2.metadataPath, 'metadata paths must differ after 2nd call');
    // The second slug must include a counter suffix showing a new file was created.
    assert.ok(
      r2.artifactPath !== r1.artifactPath && r2.artifactPath.endsWith('.patch'),
      `second artifact path should be distinct: ${r2.artifactPath}`
    );
  }
);

test(
  'retainForensicArtifact: untracked files listed in changedPaths despite absent from patch',
  async (t) => {
    const base = mkdtempSync(join(tmpdir(), 'doom-RA-ut-'));
    const missionId = 'phase2b_forensic_ut';
    t.after(() => rmSync(base, { recursive: true, force: true }));
    initBase(base);

    const wtDir = createDirtyWorktree(base, missionId);

    const { artifactPath, metadataPath } = await retainForensicArtifact({
      workspace: wtDir,
      missionId,
      taskKey: 'T1',
      attemptNo: 1,
      worktreeRoot: base,
      context: 'untracked-test',
    });

    const meta = JSON.parse(readFileSync(metadataPath, 'utf8'));
    assert.ok(
      meta.provenance.changedPaths.some((p: string) => p.includes('README.md') || p.endsWith('README.md')),
      `expected README.md in changedPaths, got: ${JSON.stringify(meta.provenance.changedPaths)}`
    );
    assert.ok(
      meta.provenance.changedPaths.some((p: string) => p.includes('session.log') || p.includes('scratch/')),
      `expected scratch/session.log in changedPaths, got: ${JSON.stringify(meta.provenance.changedPaths)}`
    );
    assert.ok(
      meta.provenance.untrackedPaths.some((p: string) => p.includes('session.log') || p.includes('.env.local')),
      `got: ${JSON.stringify(meta.provenance.untrackedPaths)}`
    );
    const patch = readFileSync(artifactPath, 'utf8');
    assert.ok(patch.length > 0, 'patch must be non-empty for a dirty worktree');
    assert.ok(
      patch.includes('README.md') || patch.includes('Dirty worktree') || patch.includes('diff --git'),
      'expected README.md change in patch'
    );
  }
);

// ─── buildForensicDiffBundle ───────────────────────────────────────────────

test(
  'buildForensicDiffBundle: self-contained bundle with header, provenance, and patch',
  async (t) => {
    const base = mkdtempSync(join(tmpdir(), 'doom-bundle-'));
    const missionId = 'phase2b_bundle';
    t.after(() => rmSync(base, { recursive: true, force: true }));
    initBase(base);

    const wtDir = createDirtyWorktree(base, missionId);

    const { artifactPath, metadataPath } = await retainForensicArtifact({
      workspace: wtDir,
      missionId,
      taskKey: 'T-bar',
      attemptNo: 3,
      worktreeRoot: base,
      context: 'cleanupFailedTaskAttempt',
      tags: { actor: 'cleanupFailedTaskAttempt', guardBlock: 'true' },
    });

    const bundle = await buildForensicDiffBundle(metadataPath, artifactPath);

    assert.match(bundle, /Forensic Retention Bundle/);
    assert.match(bundle, /phase2b_bundle/);
    assert.match(bundle, /T-bar/);
    assert.match(bundle, /attempt=3/);
    assert.match(bundle, /cleanupFailedTaskAttempt/);
    assert.match(bundle, /doom-forensic-artifact/);

    assert.match(bundle, /actor/);
    assert.match(bundle, /cleanupFailedTaskAttempt/);
    assert.match(bundle, /guardBlock/);
    // With tracked README change, the bundle has real diff content.
    assert.ok(bundle.includes('unified diff follows'));
    // Header uses '# Changed   : 3 path(s)' style (colons spaced before value).
    assert.ok(bundle.includes('Changed'), 'bundle header must show changed path count');
    assert.ok(bundle.includes('Untracked'), 'bundle header must show untracked file count');
    assert.ok(
      bundle.includes('README.md') || bundle.includes('Dirty worktree'),
      `expected README.md or diff content in bundle, got: ${bundle.slice(0, 300)}`
    );
  }
);

test(
  'retainForensicArtifact + buildForensicDiffBundle: independent provenance per retention call',
  async (t) => {
    const base = mkdtempSync(join(tmpdir(), 'doom-RA-multi-'));
    const missionA = 'phase2b_multi_A';
    const missionB = 'phase2b_multi_B';
    mkdirSync(join(base, 'retained', safeSegment(missionA)), { recursive: true });
    mkdirSync(join(base, 'retained', safeSegment(missionB)), { recursive: true });
    t.after(() => rmSync(base, { recursive: true, force: true }));
    initBase(base);

    const wtDirA = createDirtyWorktree(base, missionA);

    const wtDirB = join(base, 'worktrees', safeSegment(missionB));
    mkdirSync(dirname(wtDirB), { recursive: true });
    git(base, ['worktree', 'add', '-b', `doom/task/${safeSegment(missionB)}-a1`, wtDirB, 'HEAD']);
    mkdirSync(join(wtDirB, 'src'), { recursive: true });
    writeFileSync(join(wtDirB, 'src', 'helper.mjs'), 'export const y = 99;\n');

    const rA = await retainForensicArtifact({
      workspace: wtDirA, missionId: missionA, taskKey: 'T1', attemptNo: 1,
      worktreeRoot: base, context: 'cleanup-failed', tags: { mission: 'A' },
    });
    const rB = await retainForensicArtifact({
      workspace: wtDirB, missionId: missionB, taskKey: 'T2', attemptNo: 1,
      worktreeRoot: base, context: 'replanMission', tags: { mission: 'B' },
    });

    const metaA = JSON.parse(readFileSync(rA.metadataPath, 'utf8'));
    const metaB = JSON.parse(readFileSync(rB.metadataPath, 'utf8'));

    assert.equal(metaA.provenance.missionId, missionA);
    assert.equal(metaA.provenance.taskKey, 'T1');
    assert.equal(metaA.provenance.context, 'cleanup-failed');
    assert.equal(metaB.provenance.missionId, missionB);
    assert.equal(metaB.provenance.taskKey, 'T2');
    assert.equal(metaB.provenance.context, 'replanMission');

    const bundleA = await buildForensicDiffBundle(rA.metadataPath, rA.artifactPath);
    const bundleB = await buildForensicDiffBundle(rB.metadataPath, rB.artifactPath);
    assert.match(bundleA, new RegExp(missionA));
    assert.match(bundleB, new RegExp(missionB));
    assert.ok(!bundleA.match(new RegExp(missionB)), `bundleA must not reference ${missionB}`);
    assert.ok(!bundleB.match(new RegExp(missionA)), `bundleB must not reference ${missionA}`);
  }
);