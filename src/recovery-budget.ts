type SqliteCountDatabase={
  prepare(sql:string):{
    get(...params:any[]):any;
  };
};

function count(database:SqliteCountDatabase,sql:string,...params:any[]){
  const row=database.prepare(sql).get(...params);
  return Number(row?.c ?? 0);
}

/** Check whether the recovery_attempts table exists in the given database. */
function hasRecoveryAttemptsTable(database:SqliteCountDatabase):boolean{
  try{
    const row=database.prepare(
      `SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_attempts'`
    ).get();
    return !!row;
  }catch{
    return false;
  }
}

/**
 * Authoritative recovery-attempt counters that read from the unified
 * recovery_attempts ledger.  Falls back to the legacy mission_events-based
 * computation when the table does not yet exist (pre-migration databases).
 */
export function taskRecoveryAttemptCount(
  database:SqliteCountDatabase,
  missionId:string,
  taskId:string,
){
  if(!hasRecoveryAttemptsTable(database)){
    return legacyTaskRecoveryAttemptCount(database,missionId,taskId);
  }
  return count(database,`
    SELECT COUNT(*) c FROM recovery_attempts
    WHERE mission_id=? AND kind='task' AND task_id=?
  `,missionId,taskId);
}

export function finalGateRecoveryAttemptCount(
  database:SqliteCountDatabase,
  missionId:string,
){
  if(!hasRecoveryAttemptsTable(database)){
    return legacyFinalGateRecoveryAttemptCount(database,missionId);
  }
  return count(database,`
    SELECT COUNT(*) c FROM recovery_attempts
    WHERE mission_id=? AND kind='final_gate'
  `,missionId);
}

export function missionRecoveryAttemptCount(
  database:SqliteCountDatabase,
  missionId:string,
){
  if(!hasRecoveryAttemptsTable(database)){
    return legacyMissionRecoveryAttemptCount(database,missionId);
  }
  return count(database,`
    SELECT COUNT(*) c FROM recovery_attempts WHERE mission_id=?
  `,missionId);
}

/** Event IDs are monotonic; wall-clock timestamps are not. */
export function recoveryEpochStartId(
  database:SqliteCountDatabase,
  missionId:string,
){
  const row=database.prepare(`
    SELECT id FROM mission_events
    WHERE mission_id=? AND event_type='lead.recovery_budget_epoch_reset'
    ORDER BY id DESC LIMIT 1
  `).get(missionId);
  return Number(row?.id ?? 0);
}

// ─── Legacy counters (unchanged; used for pre-migration databases) ─────────────

function legacyTaskRecoveryAttemptCount(
  database:SqliteCountDatabase,
  missionId:string,
  taskId:string,
){
  const epochId=recoveryEpochStartId(database,missionId);
  const firstNew=legacyFirstAttemptMarkerId(database,missionId,epochId,taskId);
  const legacyUpperBound=firstNew ?? Number.MAX_SAFE_INTEGER;
  const legacy=count(database,`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND task_id=? AND id>? AND id<?
      AND event_type IN ('lead.task_recovered','lead.recovery_failed')
  `,missionId,taskId,epochId,legacyUpperBound);
  const durableStarts=count(database,`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND task_id=? AND id>?
      AND event_type='lead.recovery_attempt_started'
  `,missionId,taskId,epochId);
  return legacy+durableStarts;
}

function legacyFinalGateRecoveryAttemptCount(
  database:SqliteCountDatabase,
  missionId:string,
){
  const epochId=recoveryEpochStartId(database,missionId);
  const firstNew=legacyFirstAttemptMarkerId(database,missionId,epochId,null);
  const legacyUpperBound=firstNew ?? Number.MAX_SAFE_INTEGER;
  const legacy=count(database,`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND id>? AND id<?
      AND event_type='lead.final_gate_recovery_started'
  `,missionId,epochId,legacyUpperBound);
  const durableStarts=count(database,`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND task_id IS NULL AND id>?
      AND event_type='lead.recovery_attempt_started'
      AND json_extract(payload_json,'$.kind')='final_gate'
  `,missionId,epochId);
  return legacy+durableStarts;
}

function legacyMissionRecoveryAttemptCount(
  database:SqliteCountDatabase,
  missionId:string,
){
  const epochId=recoveryEpochStartId(database,missionId);
  const firstNew=legacyFirstAttemptMarkerId(database,missionId,epochId,undefined);
  const legacyUpperBound=firstNew ?? Number.MAX_SAFE_INTEGER;
  const legacy=count(database,`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND id>? AND id<?
      AND (
        event_type='lead.task_recovered'
        OR event_type='lead.final_gate_recovery_started'
        OR (
          event_type='lead.recovery_failed'
          AND (task_id IS NOT NULL OR payload_json LIKE '%"taskKey"%')
        )
      )
  `,missionId,epochId,legacyUpperBound);
  const durableStarts=count(database,`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND id>?
      AND event_type='lead.recovery_attempt_started'
  `,missionId,epochId);
  return legacy+durableStarts;
}

function legacyFirstAttemptMarkerId(
  database:SqliteCountDatabase,
  missionId:string,
  epochId:number,
  taskId:string|null|undefined,
){
  const taskPredicate=taskId===undefined
    ? ''
    : taskId===null
      ? `AND task_id IS NULL
         AND json_extract(payload_json,'$.kind')='final_gate'`
      : 'AND task_id=?';
  const params=taskId===undefined || taskId===null
    ? [missionId,epochId]
    : [missionId,epochId,taskId];
  const row=database.prepare(`
    SELECT MIN(id) id FROM mission_events
    WHERE mission_id=? AND id>?
      AND event_type='lead.recovery_attempt_started'
      ${taskPredicate}
  `).get(...params);
  return row?.id===null || row?.id===undefined ? null : Number(row.id);
}