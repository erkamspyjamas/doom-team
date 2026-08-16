type SqliteLikeDatabase = {
  prepare(sql:string):{
    run(...params:any[]):{changes?:number};
  };
};

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
