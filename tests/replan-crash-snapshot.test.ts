import assert from 'node:assert/strict';
import { mkdirSync, rmSync, writeFileSync, existsSync, readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import test from 'node:test';
import {
  writeReplanSnapshot,
  markWorktreePathCleaned,
  removeReplanSnapshot,
  replayPlanSnapshotSummary,
  REPLAN_SNAPSHOT_DIR_REL,
} from '../src/replan-history.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeInput(snapshotDir: string, opts: {
  missionId?: string;
  taskCount?: number;
  worktreeCount?: number;
} = {}): Parameters<typeof writeReplanSnapshot>[0] {
  const missionId = opts.missionId ?? 'test-mission-001';
  const taskCount = opts.taskCount ?? 2;
  const worktreeCount = opts.worktreeCount ?? 2;

  const tasks = Array.from({ length: taskCount }, (_, i) => ({
    id: `task-${i}`,
    taskKey: `T${i + 1}`,
    status: 'running' as const,
    sequenceNo: i + 1,
    executionId: null,
    attemptNo: 1,
  }));

  const worktreeInfos = Array.from({ length: worktreeCount }, (_, i) => ({
    workspacePath: `/tmp/wt-${missionId}-t${i + 1}`,
    branchName: `doom/task/${missionId}/T${i + 1}-a1`,
    baseCommit: 'abc1234',
    status: 'active' as const,
    existsOnDisk: true,
  }));

  return {
    snapshotDir,
    missionId,
    missionStatus: 'running',
    missionErrorText: 'planning failure',
    missionPlanJson: '{"tasks":[]}',
    tasks,
    gates: [],
    worktreeInfos,
    integrationHead: 'def5678',
    integrationBranch: 'doom/mission/test-mission-001',
  };
}

// ─── writeReplanSnapshot ──────────────────────────────────────────────────────

test('writeReplanSnapshot creates directory and writes a well-formed JSON file', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-snap-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const input = makeInput(snapshotDir, { missionId: 'm-write-ok' });
  const path = writeReplanSnapshot(input);

  assert.ok(existsSync(path), 'snapshot file should exist');
  const raw = readFileSync(path, 'utf-8');
  const doc = JSON.parse(raw);

  assert.equal(doc.schemaVersion, 'doom-replan-snapshot/v1');
  assert.ok(doc.capturedAt, 'should have capturedAt timestamp');
  assert.equal(doc.mission.id, 'm-write-ok');
  assert.equal(doc.mission.status, 'running');
  assert.equal(doc.mission.errorText, 'planning failure');
  assert.equal(doc.integration.head, 'def5678');
  assert.equal(doc.integration.branch, 'doom/mission/test-mission-001');
  assert.equal(doc.tasks.length, 2);
  assert.equal(doc.worktreeInfos.length, 2);
  assert.equal(doc.cleanedWorktreePaths.length, 0);
  assert.deepEqual(doc.cleanupOrder, ['T1', 'T2']);
});

test('writeReplanSnapshot is idempotent-mitigating: second call with deduped slug overwrites', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-snap-dedup-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const input = makeInput(snapshotDir, { missionId: 'm-dedup' });
  const path1 = writeReplanSnapshot(input);

  // Overwrite the same path (simulates re-entry on same mission id in same dir)
  const path2 = writeReplanSnapshot(input);

  assert.equal(path1, path2);
  const doc = JSON.parse(readFileSync(path2, 'utf-8'));
  assert.equal(doc.cleanedWorktreePaths.length, 0);
});

// ─── markWorktreePathCleaned ──────────────────────────────────────────────────

test('markWorktreePathCleaned appends a path to cleanedWorktreePaths', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-mark-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const snapshotPath = writeReplanSnapshot(makeInput(snapshotDir));

  markWorktreePathCleaned(snapshotPath, '/tmp/wt-test-mission-001-t1');
  const doc = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
  assert.ok(doc.cleanedWorktreePaths.includes('/tmp/wt-test-mission-001-t1'));

  // Calling again with the same path must not duplicate (set semantics).
  markWorktreePathCleaned(snapshotPath, '/tmp/wt-test-mission-001-t1');
  const docAgain = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
  assert.equal(
    docAgain.cleanedWorktreePaths.filter((p: string) => p === '/tmp/wt-test-mission-001-t1').length,
    1
  );
});

test('markWorktreePathCleaned is a no-op when the snapshot file does not exist', (t) => {
  // Should not throw.
  markWorktreePathCleaned('/nonexistent/dir/snapshot.json', '/some/path');
  assert.ok(true, 'no error raised for missing snapshot file');
});

test('markWorktreePathCleaned records multiple worktrees in cleanup order', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-multi-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const snapshotPath = writeReplanSnapshot(
    makeInput(snapshotDir, { worktreeCount: 3 })
  );

  markWorktreePathCleaned(snapshotPath, '/tmp/wt-test-mission-001-t1');
  markWorktreePathCleaned(snapshotPath, '/tmp/wt-test-mission-001-t2');
  // Simulate skipping t3 (still needs cleanup).
  markWorktreePathCleaned(snapshotPath, '/tmp/wt-test-mission-001-t3');

  const doc = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
  assert.equal(doc.cleanedWorktreePaths.length, 3);
  assert.deepEqual(doc.cleanedWorktreePaths, [
    '/tmp/wt-test-mission-001-t1',
    '/tmp/wt-test-mission-001-t2',
    '/tmp/wt-test-mission-001-t3',
  ]);
});

// ─── removeReplanSnapshot ─────────────────────────────────────────────────────

test('removeReplanSnapshot removes an existing snapshot file', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-rm-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const snapshotPath = writeReplanSnapshot(makeInput(snapshotDir));
  assert.ok(existsSync(snapshotPath), 'precondition: snapshot exists');

  removeReplanSnapshot(snapshotPath);

  assert.ok(!existsSync(snapshotPath), 'snapshot file should be removed');
});

test('removeReplanSnapshot is a no-op when the snapshot does not exist', (t) => {
  removeReplanSnapshot('/nonexistent/path/snapshot.json');
  assert.ok(true, 'no error raised for missing snapshot');
});

test('removeReplanSnapshot removes via absolute path', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-bare-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const absPath = writeReplanSnapshot(makeInput(snapshotDir));
  assert.ok(existsSync(absPath));

  removeReplanSnapshot(absPath);
  assert.ok(!existsSync(absPath), 'snapshot at absolute path should be removed');
});

test('removeReplanSnapshot with bare relative path from matching cwd', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-barel2-'));
  // Save cwd and temporarily use snapshot dir as cwd so bare relative paths
  // resolve correctly.
  const origCwd = process.cwd();
  t.after(() => {
    try { process.chdir(origCwd); } catch {}
    rmSync(root, { recursive: true, force: true });
  });

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  mkdirSync(snapshotDir, { recursive: true });
  process.chdir(root); // cwd is now root; bare relative path resolves here

  const absPath = writeReplanSnapshot(makeInput(snapshotDir));
  const fileName = join(REPLAN_SNAPSHOT_DIR_REL, 'test-mission-001.json');

  removeReplanSnapshot(fileName);
  assert.ok(!existsSync(absPath), 'snapshot should be removed via cwd-resolved bare path');
});

// ─── replayPlanSnapshotSummary ───────────────────────────────────────────────

test('replayPlanSnapshotSummary returns a descriptive string', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-sum-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const snapshotPath = writeReplanSnapshot(
    makeInput(snapshotDir, { taskCount: 2 })
  );

  const summary = replayPlanSnapshotSummary(snapshotPath);
  assert.ok(summary != null, 'should return a non-null summary');
  assert.ok(summary.includes('snapshot='));
  assert.ok(summary.includes('status=running'));
  assert.ok(summary.includes('tasks=2'));
  assert.ok(summary.includes('cleaned=0'));
});

test('replayPlanSnapshotSummary returns null for a missing or corrupt file', (t) => {
  assert.ok(replayPlanSnapshotSummary('/nonexistent/snap.json') === null);
  // Corrupt JSON
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-corrupt-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  writeFileSync(join(root, 'corrupt.json'), 'not-json');
  assert.ok(replayPlanSnapshotSummary(join(root, 'corrupt.json')) === null);
});

// ─── End-to-end: write → mark cleaned → read back → remove ───────────────────

test('full lifecycle: write, mark some cleaned, read snapshot, then remove', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-lifecycle-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const input = makeInput(snapshotDir, { worktreeCount: 3, taskCount: 3 });
  const snapshotPath = writeReplanSnapshot(input);

  // Simulate cleanup having progressed to task T1 and T2:
  markWorktreePathCleaned(snapshotPath, '/tmp/wt-test-mission-001-t1');
  markWorktreePathCleaned(snapshotPath, '/tmp/wt-test-mission-001-t2');
  // T3 worktree still pending cleanup (still on disk).

  const doc = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
  assert.equal(doc.cleanedWorktreePaths.length, 2);
  assert.ok(doc.cleanedWorktreePaths.includes('/tmp/wt-test-mission-001-t1'));
  assert.ok(doc.cleanedWorktreePaths.includes('/tmp/wt-test-mission-001-t2'));
  // T3 worktree still appears in worktreeInfos (not removed from that list).
  assert.equal(doc.worktreeInfos.length, 3);
  assert.ok(doc.worktreeInfos.some((w: Record<string, unknown>) => w.workspacePath === '/tmp/wt-test-mission-001-t3'));

  // Summary reports 2 cleaned out of 3 worktrees.
  const summary = replayPlanSnapshotSummary(snapshotPath);
  assert.ok(summary?.includes('cleaned=2'), `expected cleaned=2, got: ${summary}`);

  // Successful completion: snapshot is removed.
  removeReplanSnapshot(snapshotPath);
  assert.ok(!existsSync(snapshotPath), 'snapshot should be removed after successful completion');
});

// ─── Schema version contract ─────────────────────────────────────────────────

test('snapshot schemaVersion allows detection of version mismatch in recovery', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'doom-2c-schema-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const snapshotDir = join(root, REPLAN_SNAPSHOT_DIR_REL);
  const snapshotPath = writeReplanSnapshot(makeInput(snapshotDir));
  const doc = JSON.parse(readFileSync(snapshotPath, 'utf-8'));

  assert.equal(doc.schemaVersion, 'doom-replan-snapshot/v1');
  // Caller can use schemaVersion to branch recovery logic for old snapshots.
  assert.ok(typeof doc.schemaVersion === 'string');
});