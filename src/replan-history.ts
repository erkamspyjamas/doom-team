type SqliteLikeDatabase = {
  prepare(sql:string):{
    run(...params:any[]):{changes?:number};
  };
};

import { existsSync, mkdirSync, readFileSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Relative directory under the base workspace where replan crash-window
 * snapshots are persisted.
 */
export const REPLAN_SNAPSHOT_DIR_REL = '.doom/replan-snapshots';

/**
 * Detach task-scoped mission events before a semantic replan deletes the old
 * DAG. The events remain mission history and continue to count toward durable
 * recovery budgets instead of being removed by the task foreign-key cascade.
 */
export function preserveMissionTaskEventsForReplan(
  database:SqliteLikeDatabase,
  missionId:string,
){
  const result=database.prepare(`
    UPDATE mission_events
    SET task_id=NULL
    WHERE mission_id=? AND task_id IS NOT NULL
  `).run(missionId);

  return Number(result?.changes ?? 0);
}

/**
 * Write a JSON snapshot of the mission state *before* cleanup begins for a
 * semantic replan.  This is the crash-window preservation: if the process
 * dies mid-replan, the snapshot lets us determine which worktrees had already
 * been removed and reconstruct the original DAG so it can be restored to a
 * safe blocked state rather than left in an inconsistent 'new' state with
 * no tasks.
 */
export function writeReplanSnapshot(input:{
  snapshotDir: string;
  missionId: string;
  missionStatus: string;
  missionErrorText: string | null;
  missionPlanJson: string;
  tasks: Array<{
    id: string;
    taskKey: string;
    status: string;
    sequenceNo: number;
    executionId: string | null;
    attemptNo: number;
  }>;
  gates: Array<{ id: string; status: string }>;
  worktreeInfos: Array<{
    workspacePath: string;
    branchName: string;
    baseCommit: string | null;
    status: string;
    existsOnDisk: boolean;
  }>;
  integrationHead: string | null;
  integrationBranch: string | null;
}): string {
  mkdirSync(input.snapshotDir, { recursive: true });

  const path = join(input.snapshotDir, `${input.missionId}.json`);
  const snapshot = {
    schemaVersion: 'doom-replan-snapshot/v1',
    capturedAt: new Date().toISOString(),
    mission: {
      id: input.missionId,
      status: input.missionStatus,
      errorText: input.missionErrorText,
      planJson: input.missionPlanJson,
    },
    integration: {
      head: input.integrationHead,
      branch: input.integrationBranch,
    },
    tasks: input.tasks,
    gates: input.gates,
    cleanupOrder: input.tasks.map(t => t.taskKey),
    worktreeInfos: input.worktreeInfos,
    cleanedWorktreePaths: [] as string[],
  };

  writeFileSync(path, JSON.stringify(snapshot, null, 2), 'utf-8');
  return path;
}

/**
 * Mark a worktree path as already cleaned in a snapshot in-place.  Called
 * after each successful `removeTaskWorktree` so that crash recovery can see
 * which worktrees still need cleanup.
 */
export function markWorktreePathCleaned(snapshotPath: string, workspacePath: string): void {
  if (!existsSync(snapshotPath)) return;
  try {
    const raw = readFileSync(snapshotPath, 'utf-8');
    const snapshot = JSON.parse(raw) as Record<string, unknown>;
    const cleaned = new Set<string>(
      Array.isArray((snapshot.cleanedWorktreePaths as string[]) ?? [])
        ? (snapshot.cleanedWorktreePaths as string[])
        : []
    );
    if (!cleaned.has(workspacePath)) {
      cleaned.add(workspacePath);
      const updated = { ...snapshot, cleanedWorktreePaths: [...cleaned] };
      writeFileSync(snapshotPath, JSON.stringify(updated, null, 2), 'utf-8');
    }
  } catch {
    // Best-effort: read/write failures are non-fatal.
  }
}

/**
 * Remove a replan snapshot file safely.  Accepts either an absolute or
 * relative path; removes at most one file and silently ignores absence.
 */
export function removeReplanSnapshot(snapshotPath: string): void {
  try {
    const candidates = [snapshotPath];
    // If a relative path was passed without a leading './' or '/', also try
    // resolving from process.cwd() to cover the common case of the caller
    // joining process.cwd() into the snapshot path.
    if (!snapshotPath.startsWith('./') && !snapshotPath.startsWith('/')) {
      candidates.push(join(process.cwd(), snapshotPath));
    }
    for (const p of candidates) {
      try {
        const st = statSync(p);
        if (st.isFile()) {
          unlinkSync(p);
          return;
        }
      } catch { /* not found – try next candidate */ }
    }
  } catch {
    // Swallow all removal errors: absence of a snapshot is never a failure.
  }
}

/**
 * Human-readable summary of a snapshot for log/diagnostic output.
 */
export function replayPlanSnapshotSummary(snapshotPath: string): string | null {
  try {
    const raw = readFileSync(snapshotPath, 'utf-8');
    const s = JSON.parse(raw) as Record<string, unknown>;
    const mission = (s.mission ?? {}) as Record<string, unknown>;
    const tasks = (s.tasks ?? []) as Array<Record<string, unknown>>;
    const cleaned = Array.isArray((s.cleanedWorktreePaths as string[]) ?? [])
      ? (s.cleanedWorktreePaths as string[]).length
      : 0;
    return (
      `snapshot=${snapshotPath} ` +
      `status=${mission.status ?? '?'} ` +
      `tasks=${tasks.length} cleaned=${cleaned} ` +
      `capturedAt=${(s as Record<string, string>).capturedAt ?? '?'}`
    );
  } catch {
    return null;
  }
}

