import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test, { after } from 'node:test';

const scratchDir=mkdtempSync(
  join(tmpdir(),'doom-phase1d-retry-')
);

process.env.DOOM_DB=join(
  scratchDir,
  'doom-phase1d.sqlite'
);

// Important: only dynamically import Doom modules after DOOM_DB
// points at the isolated scratch database.
const {
  db,
  now,
}=await import('../src/db/index.js');

const {
  retryMissionTask,
}=await import('../src/missions.js');

after(()=>{
  try{
    db.close();
  }finally{
    rmSync(
      scratchDir,
      {
        recursive:true,
        force:true,
      }
    );
  }
});

type BaseRefs={
  agentId:string;
  agentVersionId:string;
  runtimeId:string;
  modelId:string;
};

function baseRefs():BaseRefs{
  const agent:any=db.prepare(`
    SELECT
      a.id AS agent_id,
      av.id AS agent_version_id
    FROM agents a
    JOIN agent_versions av
      ON av.agent_id=a.id
    WHERE a.enabled=1
    ORDER BY a.id,av.version_no DESC
    LIMIT 1
  `).get();

  const runtime:any=db.prepare(`
    SELECT id
    FROM runtimes
    WHERE enabled=1
    ORDER BY id
    LIMIT 1
  `).get();

  const model:any=db.prepare(`
    SELECT id
    FROM models
    WHERE enabled=1
    ORDER BY id
    LIMIT 1
  `).get();

  assert.ok(
    agent?.agent_id,
    'scratch seed must provide an enabled agent'
  );

  assert.ok(
    agent?.agent_version_id,
    'scratch seed must provide an agent version'
  );

  assert.ok(
    runtime?.id,
    'scratch seed must provide an enabled runtime'
  );

  assert.ok(
    model?.id,
    'scratch seed must provide an enabled model'
  );

  return {
    agentId:String(agent.agent_id),
    agentVersionId:String(agent.agent_version_id),
    runtimeId:String(runtime.id),
    modelId:String(model.id),
  };
}

let sequence=0;

function createFixture(
  executionStatus:string|null
){
  const refs=baseRefs();
  const n=++sequence;
  const t=now();

  const teamId=`phase1d_team_${n}`;
  const missionId=`phase1d_mission_${n}`;
  const taskId=`phase1d_task_${n}`;
  const taskKey=`T${n}`;

  const executionId=
    executionStatus===null
      ? null
      : `phase1d_execution_${n}`;

  db.prepare(`
    INSERT INTO teams
    (
      id,
      name,
      description,
      lead_agent_id,
      enabled,
      created_at,
      updated_at
    )
    VALUES (?,?,?,?,?,?,?)
  `).run(
    teamId,
    `Phase1D Team ${n}`,
    'Phase1D retry regression fixture',
    refs.agentId,
    1,
    t,
    t
  );

  db.prepare(`
    INSERT INTO missions
    (
      id,
      title,
      objective,
      team_id,
      lead_agent_id,
      status,
      planning_execution_id,
      plan_json,
      error_text,
      created_at,
      updated_at,
      started_at,
      ended_at
    )
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    missionId,
    `Phase1D Mission ${n}`,
    'Retry idempotency regression',
    teamId,
    refs.agentId,
    'running',
    null,
    '{}',
    null,
    t,
    t,
    t,
    null
  );

  if(executionId){
    db.prepare(`
      INSERT INTO executions
      (
        id,
        agent_id,
        agent_version_id,
        runtime_id,
        model_id,
        external_session_id,
        task_text,
        status,
        result_text,
        error_text,
        started_at,
        ended_at,
        snapshot_json
      )
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      executionId,
      refs.agentId,
      refs.agentVersionId,
      refs.runtimeId,
      refs.modelId,
      null,
      'Phase1D retry regression execution',
      executionStatus,
      null,
      null,
      t,
      executionStatus==='done'
        ? t
        : null,
      '{}'
    );
  }

  db.prepare(`
    INSERT INTO mission_tasks
    (
      id,
      mission_id,
      task_key,
      sequence_no,
      title,
      instruction,
      expected_outcome,
      work_mode,
      description,
      acceptance_criteria_json,
      assigned_agent_id,
      status,
      priority,
      execution_id,
      attempt_no,
      result_text,
      error_text,
      created_at,
      updated_at,
      started_at,
      ended_at
    )
    VALUES (
      ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
    )
  `).run(
    taskId,
    missionId,
    taskKey,
    0,
    `Phase1D Task ${n}`,
    'Do work',
    '',
    'implement',
    'Retry regression task',
    '[]',
    refs.agentId,
    'failed',
    100,
    executionId,
    1,
    null,
    'prior failure',
    t,
    t,
    t,
    t
  );

  return {
    missionId,
    taskId,
    taskKey,
    executionId,
  };
}

for(
  const activeStatus
  of ['running','validating']
){
  test(
    `retryMissionTask fails closed while bound execution is ${activeStatus}`,
    ()=>{
      const fixture=createFixture(
        activeStatus
      );

      assert.throws(
        ()=>{
          retryMissionTask(
            fixture.missionId,
            fixture.taskId
          );
        },
        (error:unknown)=>{
          assert.ok(
            error instanceof Error
          );

          assert.equal(
            error.message,
            `DUPLICATE_EXECUTION_PREVENTED:${fixture.taskKey}:active_execution_must_be_terminal`
          );

          return true;
        }
      );

      const task:any=db.prepare(`
        SELECT
          status,
          execution_id,
          error_text
        FROM mission_tasks
        WHERE id=?
      `).get(
        fixture.taskId
      );

      assert.equal(
        task.status,
        'failed'
      );

      assert.equal(
        task.execution_id,
        fixture.executionId
      );

      assert.equal(
        task.error_text,
        'prior failure'
      );

      const execution:any=db.prepare(`
        SELECT status
        FROM executions
        WHERE id=?
      `).get(
        fixture.executionId
      );

      assert.equal(
        execution.status,
        activeStatus,
        'retry guard must not mutate or fake-terminalize the old execution'
      );
    }
  );
}

test(
  'retryMissionTask allows legitimate retry after bound execution is terminal',
  ()=>{
    const fixture=createFixture(
      'done'
    );

    const result:any=retryMissionTask(
      fixture.missionId,
      fixture.taskId
    );

    assert.equal(
      result instanceof Promise,
      false,
      'public retryMissionTask API must remain synchronous'
    );

    const task:any=db.prepare(`
      SELECT
        status,
        execution_id,
        result_text,
        error_text,
        started_at,
        ended_at
      FROM mission_tasks
      WHERE id=?
    `).get(
      fixture.taskId
    );

    assert.equal(
      task.status,
      'pending'
    );

    assert.equal(
      task.execution_id,
      null
    );

    assert.equal(
      task.result_text,
      null
    );

    assert.equal(
      task.error_text,
      null
    );

    assert.equal(
      task.started_at,
      null
    );

    assert.equal(
      task.ended_at,
      null
    );

    const mission:any=db.prepare(`
      SELECT status
      FROM missions
      WHERE id=?
    `).get(
      fixture.missionId
    );

    assert.equal(
      mission.status,
      'ready'
    );
  }
);

test(
  'retryMissionTask allows legitimate retry when no execution is bound',
  ()=>{
    const fixture=createFixture(
      null
    );

    const result:any=retryMissionTask(
      fixture.missionId,
      fixture.taskId
    );

    assert.equal(
      result instanceof Promise,
      false,
      'public retryMissionTask API must remain synchronous'
    );

    const task:any=db.prepare(`
      SELECT
        status,
        execution_id
      FROM mission_tasks
      WHERE id=?
    `).get(
      fixture.taskId
    );

    assert.equal(
      task.status,
      'pending'
    );

    assert.equal(
      task.execution_id,
      null
    );
  }
);
