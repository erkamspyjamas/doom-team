import assert from 'node:assert/strict';
import {
  mkdtempSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import test, { after } from 'node:test';

const scratchDir=mkdtempSync(
  join(tmpdir(),'doom-phase1e-final-gate-')
);

process.env.DOOM_DB=join(
  scratchDir,
  'doom-phase1e.sqlite'
);

const {
  db,
  id,
  now,
}=await import('../src/db/index.js');

const {
  runFinalMissionGate,
}=await import('../src/missions.js');

const workspace=join(
  scratchDir,
  'workspace'
);

execFileSync(
  'git',
  [
    'init',
    '--quiet',
    '--initial-branch=main',
    workspace,
  ]
);

execFileSync(
  'git',
  [
    '-C',
    workspace,
    'config',
    'user.email',
    'doom-test@example.invalid',
  ]
);

execFileSync(
  'git',
  [
    '-C',
    workspace,
    'config',
    'user.name',
    'Doom Test',
  ]
);

writeFileSync(
  join(workspace,'README.md'),
  '# Phase 1E fixture\n'
);

execFileSync(
  'git',
  [
    '-C',
    workspace,
    'add',
    'README.md',
  ]
);

execFileSync(
  'git',
  [
    '-C',
    workspace,
    'commit',
    '--quiet',
    '-m',
    'fixture',
  ]
);

const baseCommit=execFileSync(
  'git',
  [
    '-C',
    workspace,
    'rev-parse',
    'HEAD',
  ]
).toString().trim();

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

type GateType='qa'|'reviewer';

function seededRoleAgent(
  role:GateType|'lead'
){
  const row:any=db.prepare(`
    SELECT
      tm.agent_id,
      a.current_version_id
    FROM team_members tm
    JOIN agents a
      ON a.id=tm.agent_id
    WHERE tm.team_id='team_default_software'
      AND tm.role_label=?
    LIMIT 1
  `).get(role);

  assert.ok(
    row?.agent_id,
    `seed must provide ${role} agent`
  );

  assert.ok(
    row?.current_version_id,
    `seed must provide ${role} agent version`
  );

  return {
    agentId:String(row.agent_id),
    agentVersionId:
      String(row.current_version_id),
  };
}

function enabledRuntimeAndModel(){
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
    runtime?.id,
    'seed must provide enabled runtime'
  );

  assert.ok(
    model?.id,
    'seed must provide enabled model'
  );

  return {
    runtimeId:String(runtime.id),
    modelId:String(model.id),
  };
}

let sequence=0;

function fixture(
  gateType:GateType,
  executionStatus:
    'done'|'error'|'cancelled',
  resultText:string|null,
){
  sequence++;

  const t=now();
  const missionId=
    `phase1e_mission_${sequence}`;

  const executionId=
    `phase1e_execution_${sequence}`;

  const lead=seededRoleAgent('lead');
  const gateAgent=
    seededRoleAgent(gateType);

  const {
    runtimeId,
    modelId,
  }=enabledRuntimeAndModel();

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
    `Phase1E Mission ${sequence}`,
    'Verify completion truth',
    'team_default_software',
    lead.agentId,
    'ready',
    null,
    '{}',
    null,
    t,
    t,
    t,
    null
  );

  db.prepare(`
    INSERT INTO mission_workspaces
    (
      mission_id,
      mode,
      base_workspace,
      base_commit,
      integration_workspace,
      integration_branch,
      status,
      error_text,
      created_at,
      updated_at
    )
    VALUES (?,?,?,?,?,?,?,?,?,?)
  `).run(
    missionId,
    'isolated',
    workspace,
    baseCommit,
    workspace,
    `phase1e-${sequence}`,
    'ready',
    null,
    t,
    t
  );

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
    gateAgent.agentId,
    gateAgent.agentVersionId,
    runtimeId,
    modelId,
    null,
    'Phase1E final-gate fixture',
    executionStatus,
    resultText,
    executionStatus==='error'
      ? 'provider failure'
      : null,
    t,
    t,
    '{}'
  );

  const mission:any=db.prepare(`
    SELECT *
    FROM missions
    WHERE id=?
  `).get(missionId);

  assert.ok(mission);

  const execution:any=db.prepare(`
    SELECT *
    FROM executions
    WHERE id=?
  `).get(executionId);

  assert.ok(execution);

  return {
    mission,
    missionId,
    execution,
    executionId,
    gateAgentId:gateAgent.agentId,
  };
}

function fakeOps(
  fx:ReturnType<typeof fixture>
){
  return {
    startExecution:
      (async(
        agentId:string
      )=>{
        assert.equal(
          agentId,
          fx.gateAgentId,
          'final gate must select expected role agent'
        );

        return fx.execution;
      }) as any,

    waitForExecution:
      (async(
        executionId:string
      )=>{
        assert.equal(
          executionId,
          fx.executionId
        );

        return fx.execution;
      }) as any,
  };
}

function missionState(
  missionId:string
){
  return db.prepare(`
    SELECT
      status,
      error_text
    FROM missions
    WHERE id=?
  `).get(missionId) as any;
}

function gateState(
  missionId:string
){
  return db.prepare(`
    SELECT *
    FROM mission_gates
    WHERE mission_id=?
    LIMIT 1
  `).get(missionId) as any;
}

test(
  'QA final-gate execution error blocks mission immediately',
  async ()=>{
    const fx=fixture(
      'qa',
      'error',
      null
    );

    const result:any=
      await runFinalMissionGate(
        fx.mission,
        'qa',
        workspace,
        fakeOps(fx)
      );

    assert.equal(
      result.pass,
      false
    );

    const mission=
      missionState(fx.missionId);

    assert.equal(
      mission.status,
      'blocked'
    );

    assert.equal(
      mission.error_text,
      'FINAL_GATE_EXECUTION_ERROR:qa'
    );

    const gate=
      gateState(fx.missionId);

    assert.equal(
      gate.status,
      'error'
    );
  }
);

test(
  'reviewer final-gate execution error blocks mission immediately',
  async ()=>{
    const fx=fixture(
      'reviewer',
      'error',
      null
    );

    const result:any=
      await runFinalMissionGate(
        fx.mission,
        'reviewer',
        workspace,
        fakeOps(fx)
      );

    assert.equal(
      result.pass,
      false
    );

    const mission=
      missionState(fx.missionId);

    assert.equal(
      mission.status,
      'blocked'
    );

    assert.equal(
      mission.error_text,
      'FINAL_GATE_EXECUTION_ERROR:reviewer'
    );

    const gate=
      gateState(fx.missionId);

    assert.equal(
      gate.status,
      'error'
    );
  }
);

test(
  'successful final-gate execution preserves non-blocked behavior',
  async ()=>{
    const fx=fixture(
      'qa',
      'done',
      JSON.stringify({
        pass:true,
        summary:'QA OK',
        findings:[],
      })
    );

    const result:any=
      await runFinalMissionGate(
        fx.mission,
        'qa',
        workspace,
        fakeOps(fx)
      );

    assert.equal(
      result.pass,
      true
    );

    const mission=
      missionState(fx.missionId);

    assert.equal(
      mission.status,
      'ready'
    );

    assert.equal(
      mission.error_text,
      null
    );

    const gate=
      gateState(fx.missionId);

    assert.equal(
      gate.status,
      'pass'
    );
  }
);
