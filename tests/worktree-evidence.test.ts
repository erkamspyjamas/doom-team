import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import test from 'node:test';
import {
  taskWorkspaceEvidence,
  classifyWorktreeState,
  preCleanupWorktreeGuard,
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

/** Create a clean git repo at `base` with one commit on `main`. */
function initBase(base: string, branch = 'main') {
  git(base, ['init', '--quiet', `--initial-branch=${branch}`]);
  git(base, ['config', 'user.name', 'Doom Test']);
  git(base, ['config', 'user.email', 'doom-test@example.invalid']);
  writeFileSync(join(base, 'README.md'), '# Fixture\n');
  git(base, ['add', 'README.md']);
  git(base, ['commit', '--quiet', '-m', 'initial']);
}

// ─── existing taskWorkspaceEvidence test ────────────────────────────────────

test('task workspace evidence exposes files inside an untracked directory', async (t) => {
  const workspace = mkdtempSync(join(tmpdir(), 'doom-worktree-evidence-'));
  t.after(() => rmSync(workspace, { recursive: true, force: true }));

  git(workspace, ['init', '--quiet', '--initial-branch=main']);
  git(workspace, ['config', 'user.name', 'Doom Test']);
  git(workspace, ['config', 'user.email', 'doom-test@example.invalid']);
  writeFileSync(join(workspace, 'README.md'), '# Fixture\n');
  git(workspace, ['add', 'README.md']);
  git(workspace, ['commit', '--quiet', '-m', 'fixture']);

  mkdirSync(join(workspace, 'scripts'));
  writeFileSync(
    join(workspace, 'scripts', 'repo-health-check.mjs'),
    'console.log(JSON.stringify({ok:true}));\n'
  );

  const evidence = await taskWorkspaceEvidence(workspace);

  assert.match(evidence.status, /^\?\? scripts\/repo-health-check\.mjs$/m);
  assert.deepEqual(evidence.untracked, ['scripts/repo-health-check.mjs']);
  assert.ok(evidence.changedPaths.includes('scripts/repo-health-check.mjs'));
  assert.ok(!evidence.changedPaths.includes('scripts/'));
});

// ─── classifyWorktreeState ───────────────────────────────────────────────────

test('classifyWorktreeState: clean worktree → clean=true, dirty=false, ahead=0', async (t) => {
  const repo = mkdtempSync(join(tmpdir(), 'doom-cls-clean-'));
  t.after(() => rmSync(repo, { recursive: true, force: true }));
  initBase(repo);
  const s = await classifyWorktreeState(repo);
  assert.ok(s.clean);
  assert.ok(!s.dirty);
  assert.equal(s.ahead, 0);
});

test('classifyWorktreeState: untracked file → dirty=true', async (t) => {
  const repo = mkdtempSync(join(tmpdir(), 'doom-cls-untracked-'));
  t.after(() => rmSync(repo, { recursive: true, force: true }));
  initBase(repo);
  writeFileSync(join(repo, 'stray.log'), ' stray\n');
  const s = await classifyWorktreeState(repo);
  assert.ok(!s.clean);
  assert.ok(s.dirty);
  assert.ok(s.changedPaths.includes('stray.log'));
});

test('classifyWorktreeState: tracked modification → dirty=true', async (t) => {
  const repo = mkdtempSync(join(tmpdir(), 'doom-cls-modified-'));
  t.after(() => rmSync(repo, { recursive: true, force: true }));
  initBase(repo);
  writeFileSync(join(repo, 'README.md'), '# Modified Fixture\n');
  const s = await classifyWorktreeState(repo);
  assert.ok(!s.clean);
  assert.ok(s.dirty);
  assert.ok(s.changedPaths.some((p) => p === 'README.md'));
});

test('classifyWorktreeState: worktree branch 2 commits ahead of main', async (t) => {
  // Use sibling dirs: integrationRoot for main, worktreeRoot for the worktree.
  // Nesting a `git worktree add` inside the base git repo causes the worktree
  // directory to appear as "untracked" to the parent repo, corrupting clean()
  // checks — sibling dirs are the correct pattern.
  const integrationRoot = mkdtempSync(join(tmpdir(), 'doom-cls-integ-'));
  const worktreeRoot = mkdtempSync(join(tmpdir(), 'doom-cls-wt-'));
  t.after(() => {
    try {
      execFileSync('git', ['worktree', 'remove', '--force', worktreeRoot], {
        cwd: integrationRoot,
        encoding: 'utf-8',
        env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
      });
    } catch {}
    rmSync(integrationRoot, { recursive: true, force: true });
    rmSync(worktreeRoot, { recursive: true, force: true });
  });

  initBase(integrationRoot);

  // Create worktree branch from HEAD (sibling dir — not nested inside integrationRoot).
  // The worktree branch starts on the same commit as main.
  git(
    integrationRoot,
    ['worktree', 'add', '-b', 'feature/x', worktreeRoot, 'HEAD']
  );
  // Configure tracking on the worktree branch so `ahead` count is reported.
  git(worktreeRoot, ['branch', '--set-upstream-to=main', 'feature/x']);

  // Commit 1 (ahead=1 from main)
  writeFileSync(join(worktreeRoot, 'root.md'), 'V1\n');
  git(worktreeRoot, ['add', 'root.md']);
  git(worktreeRoot, ['commit', '--quiet', '-m', 'add root V1']);
  // Commit 2 (ahead=2 from main)
  writeFileSync(join(worktreeRoot, 'root.md'), 'V2\n');
  git(worktreeRoot, ['add', 'root.md']);
  git(worktreeRoot, ['commit', '--quiet', '-m', 'update root V2']);

  const s = await classifyWorktreeState(worktreeRoot);
  // Working tree is clean: all changes are committed.
  assert.ok(s.clean, `expected clean=true, got clean=false dirty=${s.dirty} changedPaths=${JSON.stringify(s.changedPaths)}`);
  assert.ok(!s.dirty);
  assert.ok(
    s.ahead >= 2,
    `expected ahead >= 2 (worktree branch is 2 commits ahead of main), got ${s.ahead}`
  );
});

test('classifyWorktreeState: branch on repo with upstream reports ahead count', async (_t) => {
  const base = mkdtempSync(join(tmpdir(), 'doom-cls-upstream-'));
  try {
    initBase(base);

    // Create a feature branch tracking main, then make a new commit.
    git(base, ['checkout', '-b', 'feature/y']);
    // Explicitly set tracking so ahead count is computed.
    git(base, ['branch', '--set-upstream-to=main', 'feature/y']);
    writeFileSync(join(base, 'feat.txt'), 'feat\n');
    git(base, ['add', 'feat.txt']);
    git(base, ['commit', '--quiet', '-m', 'feat first']);

    const s = await classifyWorktreeState(base);
    assert.ok(s.clean);
    assert.ok(
      s.ahead >= 1,
      `expected ahead >= 1 (feature/y is 1 commit ahead of main tracking), got ${s.ahead}`
    );
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});

// ─── preCleanupWorktreeGuard ────────────────────────────────────────────────

test('preCleanupWorktreeGuard: clean worktree passes regardless of evidencePreserved', async () => {
  const repo = mkdtempSync(join(tmpdir(), 'doom-guard-clean-pass-'));
  try {
    initBase(repo);
    const baseCommit = gitCapture(repo, ['rev-parse', 'HEAD']);
    await preCleanupWorktreeGuard({
      workspace: repo,
      baseCommit,
      evidencePreserved: false,
      context: 't-clean',
    });
    await preCleanupWorktreeGuard({
      workspace: repo,
      baseCommit,
      evidencePreserved: true,
      context: 't-clean-2',
    });
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test('preCleanupWorktreeGuard: dirty with unpreserved evidence is blocked', async () => {
  const repo = mkdtempSync(join(tmpdir(), 'doom-guard-dirty-block-'));
  try {
    initBase(repo);
    writeFileSync(join(repo, 'forensic.log'), 'evidence\n');
    const baseCommit = gitCapture(repo, ['rev-parse', 'HEAD']);

    await assert.rejects(
      () =>
        preCleanupWorktreeGuard({
          workspace: repo,
          baseCommit,
          evidencePreserved: false,
          context: 'cleanupFailed:task-1',
        }),
      /PRE_CLEANUP_WORKTREE_BLOCKED:cleanupFailed:task-1/
    );
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test('preCleanupWorktreeGuard: dirty worktree with evidence preserved passes', async () => {
  const repo = mkdtempSync(join(tmpdir(), 'doom-guard-dirty-ok-'));
  try {
    initBase(repo);
    writeFileSync(join(repo, 'forensic.log'), 'evidence\n');
    const baseCommit = gitCapture(repo, ['rev-parse', 'HEAD']);

    await preCleanupWorktreeGuard({
      workspace: repo,
      baseCommit,
      evidencePreserved: true,
      context: 't-dirty-ok',
    });
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test('preCleanupWorktreeGuard: worktree ahead 1 with evidencePreserved passes', async (t) => {
  // 1-commit-ahead worktree: the unintegrated commit IS safely stored in the branch.
  // evidencePreserved=true → guard should allow cleanup.
  const base = mkdtempSync(join(tmpdir(), 'doom-guard-ahead1-'));
  const wtDir = join(base, '.wt-ahead1');
  t.after(() => {
    try {
      execFileSync('git', ['worktree', 'remove', '--force', wtDir], {
        cwd: base,
        encoding: 'utf-8',
        env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
      });
    } catch {}
    rmSync(base, { recursive: true, force: true });
  });
  initBase(base);
  git(base, ['worktree', 'add', '-b', 'doom/task/T1-a1', wtDir, 'HEAD']);
  git(wtDir, ['branch', '--set-upstream-to=main', 'doom/task/T1-a1']);

  writeFileSync(join(wtDir, 'result.txt'), 'done\n');
  git(wtDir, ['add', 'result.txt']);
  git(wtDir, ['commit', '--quiet', '-m', 'task output']);

  const baseCommit = gitCapture(base, ['rev-parse', 'HEAD']);
  await preCleanupWorktreeGuard({
    workspace: wtDir,
    baseCommit,
    evidencePreserved: true,
    context: 'replan_cleanup:T1',
  });
});

test('preCleanupWorktreeGuard: worktree ahead 2 with unpreserved evidence is blocked', async (t) => {
  // 2 extra clean commits ahead; no DB commit_hash → guard must block.
  const base = mkdtempSync(join(tmpdir(), 'doom-guard-ahead2block-'));
  const wtDir = join(base, '.wt-ahead2');
  t.after(() => {
    try {
      execFileSync('git', ['worktree', 'remove', '--force', wtDir], {
        cwd: base,
        encoding: 'utf-8',
        env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
      });
    } catch {}
    rmSync(base, { recursive: true, force: true });
  });
  initBase(base);
  git(base, ['worktree', 'add', '-b', 'doom/task/T2-a1', wtDir, 'HEAD']);
  git(wtDir, ['branch', '--set-upstream-to=main', 'doom/task/T2-a1']);

  writeFileSync(join(wtDir, 'step1.txt'), 'step1\n');
  git(wtDir, ['add', 'step1.txt']);
  git(wtDir, ['commit', '--quiet', '-m', 'step 1']);
  writeFileSync(join(wtDir, 'step2.txt'), 'step2\n');
  git(wtDir, ['add', 'step2.txt']);
  git(wtDir, ['commit', '--quiet', '-m', 'step 2']);

  const baseCommit = gitCapture(base, ['rev-parse', 'HEAD']);
  await assert.rejects(
    () =>
      preCleanupWorktreeGuard({
        workspace: wtDir,
        baseCommit,
        evidencePreserved: false,
        context: 'replan_cleanup:T2',
      }),
    /PRE_CLEANUP_WORKTREE_BLOCKED:replan_cleanup:T2/
  );
});

test('preCleanupWorktreeGuard: ahead 2 with preserved commit_hash passes', async (t) => {
  // 2 extra commits, but DB already recorded commit_hash → guard allows cleanup.
  const base = mkdtempSync(join(tmpdir(), 'doom-guard-ahead2ok-'));
  const wtDir = join(base, '.wt-ahead2ok');
  t.after(() => {
    try {
      execFileSync('git', ['worktree', 'remove', '--force', wtDir], {
        cwd: base,
        encoding: 'utf-8',
        env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
      });
    } catch {}
    rmSync(base, { recursive: true, force: true });
  });
  initBase(base);
  git(base, ['worktree', 'add', '-b', 'doom/task/T3-a1', wtDir, 'HEAD']);
  git(wtDir, ['branch', '--set-upstream-to=main', 'doom/task/T3-a1']);

  writeFileSync(join(wtDir, 'a.txt'), 'a1\n');
  git(wtDir, ['add', 'a.txt']);
  git(wtDir, ['commit', '--quiet', '-m', 'a']);
  writeFileSync(join(wtDir, 'b.txt'), 'b1\n');
  git(wtDir, ['add', 'b.txt']);
  git(wtDir, ['commit', '--quiet', '-m', 'b']);

  const baseCommit = gitCapture(base, ['rev-parse', 'HEAD']);
  await preCleanupWorktreeGuard({
    workspace: wtDir,
    baseCommit,
    evidencePreserved: true,
    context: 'replan_cleanup:T3',
  });
});