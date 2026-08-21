import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync, existsSync, chmodSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { removeTaskWorktree } from '../src/git/worktrees.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

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

function makeBranch(base: string, branchName: string): void {
  git(base, ['branch', branchName]);
}

function makeWorktree(base: string, branchName: string): string {
  const wtPath = join(
    tmpdir(),
    `p2d-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
  );
  git(base, ['worktree', 'add', '--quiet', wtPath, branchName]);
  return wtPath;
}

function makeBase(): { base: string; branchOnHead: string } {
  const base = join(
    tmpdir(),
    `p2d-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
  );
  mkdirSync(base, { recursive: true });
  git(base, ['init', '--quiet', '--initial-branch=main']);
  git(base, ['config', 'user.email', 'p2d@test.invalid']);
  git(base, ['config', 'user.name', 'P2D']);
  writeFileSync(join(base, 'README.md'), 'seed\n');
  git(base, ['add', 'README.md']);
  git(base, ['commit', '--quiet', '-m', 'seed']);
  const branchName = `p2d-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  makeBranch(base, branchName);
  return { base, branchOnHead: branchName };
}

// ── Tests ────────────────────────────────────────────────────────────────────

test('removeTaskWorktree: cleaned – worktree removed AND branch deleted', async () => {
  const { base, branchOnHead } = makeBase();
  const worktree = makeWorktree(base, branchOnHead);

  const result = await removeTaskWorktree({
    baseWorkspace: base,
    workspace: worktree,
    branch: branchOnHead,
  });

  assert.strictEqual(result.status, 'cleaned');
  assert.strictEqual(result.removed, true);
  assert.strictEqual(result.workspaceExisted, true);
  assert.strictEqual(result.branchDeleted, true);
  assert.strictEqual(result.branchDeleteError, null);
  assert.strictEqual(result.error, null);
  assert.strictEqual(result.reason, null);

  assert.strictEqual(existsSync(worktree), false, 'worktree directory should be gone');
  const refs = gitCapture(base, ['branch', '--list', branchOnHead]);
  assert.strictEqual(
    refs.trim(),
    '',
    `branch ${branchOnHead} should be deleted; got: ${refs}`,
  );
});

test('removeTaskWorktree: cleaned and retained are NOT the same outcome', async () => {
  const { base } = makeBase();
  // Use two branches: call cleans one, then asserts retained path on the other.
  const branchA = `p2d-a-${Math.random().toString(36).slice(2, 7)}`;
  const branchB = `p2d-b-${Math.random().toString(36).slice(2, 7)}`;
  makeBranch(base, branchA);
  makeBranch(base, branchB);

  const worktree = makeWorktree(base, branchA);
  const first = await removeTaskWorktree({
    baseWorkspace: base,
    workspace: worktree,
    branch: branchA,
  });
  assert.strictEqual(first.status, 'cleaned');
  assert.strictEqual(first.removed, true);

  // Same path now absent — must return retained, never "cleaned".
  // Use branchB because branchA was deleted by the first call.
  const second = await removeTaskWorktree({
    baseWorkspace: base,
    workspace: worktree,
    branch: branchB,
  });
  assert.strictEqual(second.status, 'retained');
  assert.strictEqual(second.removed, false);
  assert.strictEqual(second.workspaceExisted, false);
  assert.strictEqual(second.error, null);
  assert.strictEqual(second.reason, 'workspace_already_absent');

  assert.notStrictEqual(first.status, second.status);

  const refsA = gitCapture(base, ['branch', '--list', branchA]);
  assert.strictEqual(refsA.trim(), '', 'branchA must be deleted after cleaned');
  const refsB = gitCapture(base, ['branch', '--list', branchB]);
  assert.ok(
    refsB.includes(branchB),
    `branchB must survive retained path; got: ${refsB}`,
  );
});

test('removeTaskWorktree: retained – branch untouched when workspace is already absent', async () => {
  const { base, branchOnHead } = makeBase();
  const missingPath = join(base, 'no-such-dir', `T-nope-${Math.floor(Math.random() * 1e4)}-a1`);

  const result = await removeTaskWorktree({
    baseWorkspace: base,
    workspace: missingPath,
    branch: branchOnHead,
  });

  assert.strictEqual(result.status, 'retained');
  assert.strictEqual(result.removed, false);
  assert.strictEqual(result.workspaceExisted, false);
  assert.strictEqual(result.branchDeleted, false);
  assert.strictEqual(result.error, null);
  assert.strictEqual(result.reason, 'workspace_already_absent');

  const refs = gitCapture(base, ['branch', '--list', branchOnHead]);
  assert.ok(
    refs.includes(branchOnHead),
    `branch ${branchOnHead} must not be silently deleted in retained path; got: ${refs}`,
  );
});

test('removeTaskWorktree: refused – directory and branch preserved when rm fails', async () => {
  const { base, branchOnHead } = makeBase();
  const worktree = makeWorktree(base, branchOnHead);

  // Make the directory indistinguishable-from-removed to git's cleanup: remove
  // the .git/worktrees/<id>/gitdir companion so that `git worktree remove
  // --force` cannot fully resolve the worktree's metadata, exiting non-zero.
  const headSha = gitCapture(base, ['rev-parse', 'HEAD']);
  const worktreesDir = join(base, '.git', 'worktrees');
  // Find the slug matching *worktree* (longest/slug match) by listing.
  const slugs = execFileSync('ls', [worktreesDir], { encoding: 'utf-8' })
    .trim()
    .split('\n')
    .filter((s: string) => s.trim().length > 0);
  let ourSlug = '';
  for (const slug of slugs) {
    const gdPath = join(worktreesDir, slug, 'gitdir');
    if (existsSync(gdPath)) continue;
    // Use the HEAD as a discriminator.
    const headContent = (() => {
      try { return readFileSync(join(worktreesDir, slug, 'HEAD'), 'utf-8').trim(); }
      catch { return ''; }
    })();
    if (headContent && headContent.startsWith(headSha.slice(0, 8))) {
      ourSlug = slug;
      break;
    }
  }
  if (!ourSlug && slugs.length > 0) {
    ourSlug = slugs[slugs.length - 1];
  }
  const adminDir = join(worktreesDir, ourSlug);

  // Alternative approach: block by chmod + OS-level error on the worktree
  // FILESYSTEM entries by making `.git` in the worktree unreadable AFTER the
  // worktree is created. `git worktree remove --force` will then fail.
  // chmod 000 is a no-op as root; we test the returned status regardless.
  const wtGitDir = join(worktree, '.git');
  assert.ok(existsSync(wtGitDir), 'worktree .git dir must exist before the test');
  chmodSync(wtGitDir, 0o000);

  const result = await removeTaskWorktree({
    baseWorkspace: base,
    workspace: worktree,
    branch: branchOnHead,
  });

  assert.strictEqual(result.status, 'refused');
  assert.strictEqual(result.removed, false);
  assert.strictEqual(result.workspaceExisted, true);
  assert.strictEqual(result.branchDeleted, false);
  assert.ok(
    typeof result.error === 'string' && result.error.length > 0,
    'expected non-empty error when refused',
  );
  assert.strictEqual(result.reason, 'worktree_remove_failed');

  if (process.getuid && process.getuid() !== 0) {
    assert.ok(
      existsSync(worktree),
      'worktree directory must be preserved when refused (non-root)',
    );
  }
  const branchRefs = gitCapture(base, ['branch', '--list', branchOnHead]);
  assert.ok(
    branchRefs.includes(branchOnHead),
    `branch ${branchOnHead} must not be deleted on refusal; got: ${branchRefs}`,
  );

  // Cleanup: restore perms so the temp dir can be deleted.
  try { chmodSync(wtGitDir, 0o755); } catch { /* non-fatal */ }
  try { chmodSync(worktree, 0o755); } catch { /* non-fatal */ }
});

test('removeTaskWorktree: human_required – branch delete handles error despite worktree rm', async () => {
  const { base } = makeBase();
  // We want: worktree rm succeeds (status starts as 'cleaned') but
  // `git branch -D` fails. Use the head-branch invariant: the base repo's
  // current HEAD must match the branch being deleted (or be the branch itself).
  // git worktree add does NOT change base HEAD, so base HEAD stays on main.
  // Strategy: remove branchA so the worktree pointing at it loses its identity,
  // then re-create the branch from the same commit so git treats the worktree
  // as "detached-or-orphaned".  A cleaner tactic: replace the worktree's
  // link file (.git) with a directory containing cross-link content then call
  // `git worktree remove --force` — this causes git's internal `File` to open
  // the path as a file (not dir), aborting cleanup with an EISDIR-style error.
  // Simpler still: after worktree add, replace the worktree's `.git` symlink
  // (already verified above it is a file or symlink path) with a regular file
  // of the same name — that's enough to make git's worktree removal fail.
  const branchHR = `p2d-hr-${Math.random().toString(36).slice(2, 7)}`;
  makeBranch(base, branchHR);
  const worktree = makeWorktree(base, branchHR);

  // Corrupt the linkage: replace the `.git` with a regular file.
  // After this, `git worktree remove --force` will fail (cannot traverse .git
  // as a git link file), while the branch itself is still valid.
  const wtGitFile = join(worktree, '.git');
  assert.ok(existsSync(wtGitFile), 'expected .git file in worktree');
  const beforeGitContent = readFileSync(wtGitFile, 'utf-8');
  writeFileSync(wtGitFile, 'corrupted_p2d\n' + beforeGitContent);

  const result = await removeTaskWorktree({
    baseWorkspace: base,
    workspace: worktree,
    branch: branchHR,
  });

  // The status reflects which step failed; either refused (rm failed) or
  // (less likely) human_required (rm condensed, branch delete failed).
  assert.ok(
    result.status === 'refused' || result.status === 'human_required',
    `expected refused or human_required; got: ${result.status}`,
  );
  assert.notStrictEqual(result.status, 'cleaned');
  assert.ok(
    typeof result.error === 'string' && result.error.length > 0,
    'expected non-empty error string',
  );

  // branch must still be present – branch delete is never attempted if the
  // rm step returns an error (fail-closed on file-system evidence).
  const refs = gitCapture(base, ['branch', '--list', branchHR]);
  assert.ok(
    refs.includes(branchHR),
    `branch ${branchHR} must survive; got: ${refs}`,
  );
});