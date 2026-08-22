import type { Database } from 'better-sqlite3';

// ─── Types ────────────────────────────────────────────────────────────────────

export type RecoveryKind = 'task' | 'final_gate' | 'planning';
export type RecoveryScopeType = 'task' | 'final_gate' | 'planning' | 'mission';
export type RecoveryTerminalOutcome =
  | 'applied'
  | 'failed'
  | 'exhausted'
  | 'refused'
  | 'escalated'
  | null;

export interface RecoveryAttemptRecord {
  id: number;
  missionId: string;
  taskId: string | null;
  kind: RecoveryKind;
  scopeType: RecoveryScopeType;
  epochEventId: number;
  sourceEventId: number | null;
  attemptNo: number;
  missionBudget: number;
  scopeBudget: number;
  source: string | null;
  reason: string | null;
  terminalOutcome: RecoveryTerminalOutcome;
  terminalReason: string | null;
  terminalEventId: number | null;
  createdAt: string;
  terminalizedAt: string | null;
}

export interface RecoveryLedgerFilter {
  kind?: RecoveryKind;
  scopeType?: RecoveryScopeType;
  taskId?: string | null;   // filter by task; null means only task-less records
  terminalOutcome?: RecoveryTerminalOutcome;
}

export interface RecoveryLedgerEnriched extends RecoveryAttemptRecord {
  terminalEventType: string | null;
  epochEventType: 'lead.recovery_budget_epoch_reset' | string;
}

// ─── Row factory ──────────────────────────────────────────────────────────────

function rowToRecord(row: any): RecoveryAttemptRecord {
  return {
    id: Number(row.id),
    missionId: String(row.mission_id),
    taskId: row.task_id ?? null,
    kind: String(row.kind) as RecoveryKind,
    scopeType: String(row.scope_type) as RecoveryScopeType,
    epochEventId: Number(row.epoch_event_id),
    sourceEventId: row.source_event_id == null ? null : Number(row.source_event_id),
    attemptNo: Number(row.attempt_no),
    missionBudget: Number(row.mission_budget),
    scopeBudget: Number(row.scope_budget),
    source: row.source ?? null,
    reason: row.reason ?? null,
    terminalOutcome: row.terminal_outcome == null ? null : (row.terminal_outcome as RecoveryTerminalOutcome),
    terminalReason: row.terminal_reason ?? null,
    terminalEventId: row.terminal_event_id == null ? null : Number(row.terminal_event_id),
    createdAt: String(row.created_at),
    terminalizedAt: row.terminalized_at ?? null,
  };
}

function rowToEnriched(row: any): RecoveryLedgerEnriched {
  return {
    ...rowToRecord(row),
    terminalEventType: row.terminal_event_type ?? null,
    epochEventType: String(row.epoch_event_type ?? 'lead.recovery_budget_epoch_reset'),
  };
}

// ─── Counters (authoritative; reads from recovery_attempts exclusively) ─────────

export function getTaskRecoveryCount(
  dbLike: Database,
  missionId: string,
  taskId: string | null,
): number {
  // When taskId is null, we match rows whose task_id IS NULL (detached /
  // pre-task events). Using `task_id = ?` with a bound null does not match
  // NULL rows in SQL — we must use the IS NULL operator explicitly.
  const hasTaskId = taskId != null;
  const sql = hasTaskId
    ? `SELECT COUNT(*) c FROM recovery_attempts WHERE mission_id=? AND kind='task' AND task_id=?`
    : `SELECT COUNT(*) c FROM recovery_attempts WHERE mission_id=? AND kind='task' AND task_id IS NULL`;
  const args = hasTaskId ? [missionId, taskId] : [missionId];
  const row = dbLike.prepare(sql).get(...args) as { c: number };
  return Number(row?.c ?? 0);
}

export function getFinalGateRecoveryCount(
  dbLike: Database,
  missionId: string,
): number {
  const row = dbLike
    .prepare(
      `SELECT COUNT(*) c
       FROM recovery_attempts
       WHERE mission_id=? AND kind='final_gate'`
    )
    .get(missionId) as { c: number };
  return Number(row?.c ?? 0);
}

export function getMissionRecoveryCount(
  dbLike: Database,
  missionId: string,
): number {
  const row = dbLike
    .prepare(
      `SELECT COUNT(*) c
       FROM recovery_attempts
       WHERE mission_id=?`
    )
    .get(missionId) as { c: number };
  return Number(row?.c ?? 0);
}

// ─── Enumeration ──────────────────────────────────────────────────────────────

/**
 * List recovery attempts for a mission in event-id order, with optional filters.
 *
 * All required provenance fields are exposed (kind, scope, attempt_no, budget,
 * source, reason, terminal_outcome, epoch, etc.).
 */
export function listRecoveryAttempts(
  dbLike: Database,
  missionId: string,
  filter?: RecoveryLedgerFilter,
  limit = 200,
  offset = 0,
): RecoveryAttemptRecord[] {
  const clauses: string[] = ['ra.mission_id = ?'];
  const params: any[] = [missionId];

  if (filter?.kind) {
    clauses.push('ra.kind = ?');
    params.push(filter.kind);
  }
  if (filter?.scopeType) {
    clauses.push('ra.scope_type = ?');
    params.push(filter.scopeType);
  }
  if (filter?.taskId !== undefined && filter?.taskId !== null) {
    // explicit non-null task_id filter
    clauses.push('ra.task_id = ?');
    params.push(filter.taskId);
  } else if (filter?.taskId === null) {
    // only task-less records (final_gate, planning, pure-mission)
    clauses.push('ra.task_id IS NULL');
  }
  if (filter?.terminalOutcome) {
    clauses.push('ra.terminal_outcome = ?');
    params.push(filter.terminalOutcome);
  }

  const where = clauses.join(' AND ');
  const rows = dbLike
    .prepare(
      `SELECT ra.*
       FROM recovery_attempts ra
       WHERE ${where}
       ORDER BY ra.id ASC
       LIMIT ? OFFSET ?`
    )
    .all(...params, limit, offset) as any[];

  return rows.map(rowToRecord);
}

/**
 * Enriched list that also returns the source/epoch event types and the
 * terminal event type via JOIN against mission_events.
 */
export function listRecoveryAttemptsEnriched(
  dbLike: Database,
  missionId: string,
  filter?: RecoveryLedgerFilter,
  limit = 200,
  offset = 0,
): RecoveryLedgerEnriched[] {
  const clauses: string[] = ['ra.mission_id = ?'];
  const params: any[] = [missionId];

  if (filter?.kind) {
    clauses.push('ra.kind = ?');
    params.push(filter.kind);
  }
  if (filter?.scopeType) {
    clauses.push('ra.scope_type = ?');
    params.push(filter.scopeType);
  }
  if (filter?.taskId !== undefined && filter?.taskId !== null) {
    clauses.push('ra.task_id = ?');
    params.push(filter.taskId);
  } else if (filter?.taskId === null) {
    clauses.push('ra.task_id IS NULL');
  }
  if (filter?.terminalOutcome) {
    clauses.push('ra.terminal_outcome = ?');
    params.push(filter.terminalOutcome);
  }

  const where = clauses.join(' AND ');
  const rows = dbLike
    .prepare(
      `SELECT ra.*,
              ev_epoch.event_type  AS epoch_event_type,
              ev_term.event_type   AS terminal_event_type
       FROM recovery_attempts ra
       LEFT JOIN mission_events ev_epoch ON ev_epoch.id = ra.epoch_event_id
       LEFT JOIN mission_events ev_term  ON ev_term.id = ra.terminal_event_id
       WHERE ${where}
       ORDER BY ra.id ASC
       LIMIT ? OFFSET ?`
    )
    .all(...params, limit, offset) as any[];

  return rows.map(rowToEnriched);
}

/**
 * Retrieve a single attempt by its row-id.
 */
export function getRecoveryAttempt(
  dbLike: Database,
  attemptId: number,
): RecoveryAttemptRecord | null {
  const row = dbLike
    .prepare(`SELECT * FROM recovery_attempts WHERE id = ?`)
    .get(attemptId) as any;
  return row ? rowToRecord(row) : null;
}

/**
 * Count the number of attempts in the ledger for a mission — used for
 * pagination bounds checking, not budget enforcement.
 */
export function getRecoveryAttemptCount(
  dbLike: Database,
  missionId: string,
  filter?: RecoveryLedgerFilter,
): number {
  const clauses: string[] = ['mission_id = ?'];
  const params: any[] = [missionId];

  if (filter?.kind) {
    clauses.push('kind = ?');
    params.push(filter.kind);
  }
  if (filter?.scopeType) {
    clauses.push('scope_type = ?');
    params.push(filter.scopeType);
  }
  if (filter?.taskId !== undefined && filter?.taskId !== null) {
    clauses.push('task_id = ?');
    params.push(filter.taskId);
  } else if (filter?.taskId === null) {
    clauses.push('task_id IS NULL');
  }
  if (filter?.terminalOutcome) {
    clauses.push('terminal_outcome = ?');
    params.push(filter.terminalOutcome);
  }

  const row = dbLike
    .prepare(`SELECT COUNT(*) c FROM recovery_attempts WHERE ${clauses.join(' AND ')}`)
    .get(...params) as { c: number };
  return Number(row?.c ?? 0);
}