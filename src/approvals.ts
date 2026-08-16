import { db, id, now, parseJson } from './db/index.js';
import { archiveMissionSpecialists } from './agent-factory.js';
import {
  inspectGitWorkspace,
  missionIntegrationEvidence,
  mergeMissionIntegrationToBase,
} from './git/worktrees.js';

function approvalRow(approvalId:string){
  return db.prepare(`SELECT * FROM approvals WHERE id=?`).get(approvalId) as any;
}

function missionRow(missionId:string){
  return db.prepare(`SELECT * FROM missions WHERE id=?`).get(missionId) as any;
}

function missionWorkspace(missionId:string){
  return db.prepare(`SELECT * FROM mission_workspaces WHERE mission_id=?`).get(missionId) as any;
}

function missionEvent(missionId:string,eventType:string,payload:any={}){
  db.prepare(`
    INSERT INTO mission_events(mission_id,event_type,payload_json,created_at)
    VALUES (?,?,?,?)
  `).run(missionId,eventType,JSON.stringify(payload ?? {}),now());
}

function decorate(row:any){
  if(!row)return null;
  return {
    ...row,
    payload:parseJson(row.payload_json,{}),
    result:parseJson(row.result_json,{})
  };
}

export function listApprovals(status?:string,limit=100){
  const rows=(status && status!=='all')
    ? db.prepare(`
        SELECT ap.*,m.title AS mission_title
        FROM approvals ap LEFT JOIN missions m ON m.id=ap.mission_id
        WHERE ap.status=?
        ORDER BY ap.requested_at DESC LIMIT ?
      `).all(status,limit)
    : db.prepare(`
        SELECT ap.*,m.title AS mission_title
        FROM approvals ap LEFT JOIN missions m ON m.id=ap.mission_id
        ORDER BY ap.requested_at DESC LIMIT ?
      `).all(limit);
  return (rows as any[]).map(decorate);
}

export function getApproval(approvalId:string){
  const row:any=db.prepare(`
    SELECT ap.*,m.title AS mission_title
    FROM approvals ap LEFT JOIN missions m ON m.id=ap.mission_id
    WHERE ap.id=?
  `).get(approvalId);
  return decorate(row);
}

export function missionApprovals(missionId:string){
  return (db.prepare(`
    SELECT * FROM approvals WHERE mission_id=? ORDER BY requested_at
  `).all(missionId) as any[]).map(decorate);
}

export async function ensureMissionMergeApproval(missionId:string){
  const existing:any=db.prepare(`
    SELECT * FROM approvals
    WHERE mission_id=? AND approval_type='mission_merge'
      AND status IN ('pending','merge_failed')
    ORDER BY requested_at DESC LIMIT 1
  `).get(missionId);
  if(existing)return decorate(existing);

  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  const ws=missionWorkspace(missionId);
  if(!ws)throw new Error('MISSION_WORKSPACE_NOT_FOUND');

  let integration:any={};
  if(ws.mode==='isolated'){
    if(!ws.base_commit || !ws.integration_workspace || !ws.integration_branch){
      throw new Error('MISSION_INTEGRATION_NOT_READY');
    }
    integration=await missionIntegrationEvidence({
      baseWorkspace:ws.base_workspace,
      integrationWorkspace:ws.integration_workspace,
      baseCommit:ws.base_commit,
    });
  }else{
    const state=await inspectGitWorkspace(ws.base_workspace);
    integration={
      baseHead:state.head,
      baseBranch:state.branch,
      baseClean:state.clean,
      integrationHead:state.head,
      integrationBranch:state.branch,
      integrationClean:state.clean,
      diffStat:state.diffStat,
      diff:'',
      legacySharedWorkspace:true,
    };
  }

  const approvalId=id('approval');
  const t=now();
  const payload={
    missionId,
    workspaceMode:ws.mode,
    baseWorkspace:ws.base_workspace,
    baseCommit:ws.base_commit,
    integrationWorkspace:ws.integration_workspace,
    integrationBranch:ws.integration_branch,
    integration,
  };

  db.transaction(()=>{
    db.prepare(`
      INSERT INTO approvals
      (id,approval_type,scope_type,scope_id,mission_id,status,title,summary,risk_level,
       requested_by,requested_at,payload_json,result_json,updated_at)
      VALUES (?,'mission_merge','mission',?,?,'pending',?,?,?,'doom-core',?,'{}','{}',?)
    `).run(
      approvalId,
      missionId,
      missionId,
      `Merge mission: ${mission.title}`,
      ws.mode==='isolated'
        ? 'QA and Reviewer gates passed. Boss approval is required before merging the mission integration branch into the base branch.'
        : 'Legacy shared-workspace mission passed gates. Boss approval is required to finalize it.',
      'high',
      t,t
    );
    db.prepare(`UPDATE approvals SET payload_json=? WHERE id=?`)
      .run(JSON.stringify(payload),approvalId);
    db.prepare(`
      UPDATE missions SET status='awaiting_approval',error_text=NULL,updated_at=? WHERE id=?
    `).run(t,missionId);
  })();

  missionEvent(missionId,'approval.requested',{approvalId,type:'mission_merge',risk:'high'});
  missionEvent(missionId,'mission.awaiting_approval',{approvalId});
  return getApproval(approvalId);
}

export async function approveApproval(approvalId:string,note=''){
  const approval=approvalRow(approvalId);
  if(!approval)throw new Error('APPROVAL_NOT_FOUND');
  if(!['pending','merge_failed'].includes(approval.status)){
    throw new Error(`APPROVAL_NOT_ACTIONABLE:${approval.status}`);
  }
  if(approval.approval_type!=='mission_merge'){
    throw new Error(`APPROVAL_TYPE_UNSUPPORTED:${approval.approval_type}`);
  }

  const mission=missionRow(approval.mission_id);
  const ws=missionWorkspace(approval.mission_id);
  if(!mission || !ws)throw new Error('MISSION_OR_WORKSPACE_NOT_FOUND');

  const decisionTime=now();
  db.prepare(`
    UPDATE approvals
    SET status='approved',decided_by='boss',decided_at=?,decision_note=?,updated_at=?
    WHERE id=?
  `).run(decisionTime,note,decisionTime,approvalId);
  missionEvent(mission.id,'approval.approved',{approvalId,note});

  try{
    let result:any;

    if(ws.mode==='shared'){
      const state=await inspectGitWorkspace(ws.base_workspace);
      result={
        mode:'shared',
        merged:false,
        baseHead:state.head,
        baseBranch:state.branch,
        warning:'Legacy shared workspace already modifies the base workspace; no Git merge was performed.'
      };
    }else{
      result=await mergeMissionIntegrationToBase({
        missionTitle:mission.title,
        baseWorkspace:ws.base_workspace,
        baseCommit:ws.base_commit,
        integrationWorkspace:ws.integration_workspace,
        integrationBranch:ws.integration_branch,
      });
    }

    const t=now();
    db.transaction(()=>{
      db.prepare(`
        UPDATE approvals SET status='completed',result_json=?,updated_at=? WHERE id=?
      `).run(JSON.stringify(result),t,approvalId);
      db.prepare(`
        UPDATE mission_workspaces SET status='merged',error_text=NULL,updated_at=? WHERE mission_id=?
      `).run(t,mission.id);
      db.prepare(`
        UPDATE missions SET status='done',error_text=NULL,ended_at=?,updated_at=? WHERE id=?
      `).run(t,t,mission.id);
    })();

    const archivedSpecialists=archiveMissionSpecialists(mission.id,'mission_complete');
    missionEvent(mission.id,'approval.completed',{
      approvalId,result,archivedSpecialists:archivedSpecialists.length
    });
    missionEvent(mission.id,'mission.done',{
      approvalId,
      mergeCommit:result.mergeCommit ?? result.baseHead ?? null,
      mergedIntoBranch:result.mergedIntoBranch ?? result.baseBranch ?? null
    });
    return getApproval(approvalId);
  }catch(err:any){
    const message=String(err?.message ?? err);
    const t=now();
    db.transaction(()=>{
      db.prepare(`
        UPDATE approvals SET status='merge_failed',result_json=?,updated_at=? WHERE id=?
      `).run(JSON.stringify({error:message}),t,approvalId);
      db.prepare(`
        UPDATE missions SET status='blocked',error_text='BOSS_MERGE_FAILED',updated_at=? WHERE id=?
      `).run(t,mission.id);
    })();
    missionEvent(mission.id,'approval.merge_failed',{approvalId,error:message});
    throw err;
  }
}

export function rejectApproval(approvalId:string,note=''){
  const approval=approvalRow(approvalId);
  if(!approval)throw new Error('APPROVAL_NOT_FOUND');
  if(approval.status!=='pending')throw new Error(`APPROVAL_NOT_PENDING:${approval.status}`);

  const t=now();
  db.transaction(()=>{
    db.prepare(`
      UPDATE approvals
      SET status='rejected',decided_by='boss',decided_at=?,decision_note=?,updated_at=?
      WHERE id=?
    `).run(t,note,t,approvalId);

    if(approval.mission_id){
      db.prepare(`
        UPDATE missions SET status='blocked',error_text='BOSS_REJECTED',updated_at=? WHERE id=?
      `).run(t,approval.mission_id);
    }
  })();

  if(approval.mission_id){
    missionEvent(approval.mission_id,'approval.rejected',{approvalId,note});
    missionEvent(approval.mission_id,'mission.blocked',{reason:'BOSS_REJECTED',approvalId});
  }
  return getApproval(approvalId);
}

export function cancelMissionApprovals(missionId:string,reason:string){
  db.prepare(`
    UPDATE approvals
    SET status='cancelled',result_json=?,updated_at=?
    WHERE mission_id=? AND status IN ('pending','approved','merge_failed')
  `).run(JSON.stringify({reason}),now(),missionId);
}
