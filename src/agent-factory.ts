import { db, id, now, parseJson } from './db/index.js';
import { createAgent, getAgent } from './repositories.js';
import { loadConfig } from './config.js';
import { resolveToolDefinitions } from './tools/catalog.js';

const cfg=loadConfig();

const STRATEGY_POLICY:Record<string,string>={
  best_available:'policy_best_available',
  best_local_general:'policy_best_local_general',
  best_local_coder:'policy_best_local_coder',
  best_local_creative:'policy_best_local_creative',
  tools_required:'policy_tools_required',
  vision_required:'policy_vision_required',
};

const SAFE_PROFILES=new Set([
  'read','write','git','tests','skills','communication'
]);

function missionRow(missionId:string){
  return db.prepare(`SELECT * FROM missions WHERE id=?`).get(missionId) as any;
}

function requestRow(requestId:string){
  return db.prepare(`SELECT * FROM agent_factory_requests WHERE id=?`).get(requestId) as any;
}

function missionEvent(missionId:string,eventType:string,payload:any={},taskId:string|null=null){
  db.prepare(`
    INSERT INTO mission_events(mission_id,task_id,event_type,payload_json,created_at)
    VALUES (?,?,?,?,?)
  `).run(missionId,taskId,eventType,JSON.stringify(payload ?? {}),now());
}

function policyReference(strategy:string){
  const policyId=STRATEGY_POLICY[strategy] ?? strategy;
  const policy:any=db.prepare(`
    SELECT id,current_version_id,enabled FROM model_policies WHERE id=?
  `).get(policyId);
  if(!policy || !policy.enabled || !policy.current_version_id){
    throw new Error(`AGENT_FACTORY_MODEL_POLICY_UNAVAILABLE:${policyId}`);
  }
  return {
    mode:'policy',
    policyId:policy.id,
    policyVersionId:policy.current_version_id,
  };
}

function runtimeReference(){
  const runtime:any=db.prepare(`
    SELECT id,enabled FROM runtimes WHERE id='runtime_generic_openai'
  `).get();
  if(!runtime?.enabled)throw new Error('AGENT_FACTORY_GENERIC_RUNTIME_UNAVAILABLE');
  return {mode:'fixed',runtimeId:runtime.id};
}

function normalizeProfiles(input:any){
  const profiles=Array.isArray(input)
    ? [...new Set(input.map((x:any)=>String(x).trim()).filter(Boolean))]
    : ['read','communication'];

  for(const profile of profiles){
    if(!SAFE_PROFILES.has(profile)){
      if(profile==='shell' || profile==='command' || profile==='run_command'){
        throw new Error('AGENT_FACTORY_ELEVATED_TOOL_DENIED');
      }
      throw new Error(`AGENT_FACTORY_TOOL_PROFILE_UNSUPPORTED:${profile}`);
    }
  }

  if(!profiles.includes('communication'))profiles.push('communication');
  return profiles;
}

function grantsFromProfiles(profiles:string[]){
  const grants:string[]=[];
  for(const profile of profiles){
    if(profile==='read'){
      grants.push('list_files','read_file');
    }else if(profile==='write'){
      grants.push('filesystem');
    }else if(profile==='git'){
      grants.push('git');
    }else if(profile==='tests'){
      grants.push('tests');
    }else if(profile==='skills'){
      grants.push('skill');
    }else if(profile==='communication'){
      grants.push('communication');
    }
  }

  const unique=[...new Set(grants)];
  // Defense in depth: factory-created agents never receive shell execution in V0.12.
  const tools=resolveToolDefinitions(unique);
  if(tools.some(t=>t.name==='run_command')){
    throw new Error('AGENT_FACTORY_ELEVATED_TOOL_DENIED');
  }
  return unique;
}

function validateSkillIds(skillIds:string[]){
  for(const skillId of skillIds){
    const row:any=db.prepare(`
      SELECT id,enabled,current_version_id FROM skills WHERE id=?
    `).get(skillId);
    if(!row || !row.enabled || !row.current_version_id){
      throw new Error(`AGENT_FACTORY_SKILL_UNAVAILABLE:${skillId}`);
    }
  }
}


function findReusableSpecialist(missionId:string,name:string,roleName:string){
  return db.prepare(`
    SELECT ms.agent_id
    FROM mission_specialists ms
    JOIN agents a ON a.id=ms.agent_id
    WHERE ms.mission_id=?
      AND ms.status='active'
      AND a.enabled=1
      AND (
        lower(a.name)=lower(?)
        OR lower(ms.role_name)=lower(?)
      )
    ORDER BY ms.created_at
    LIMIT 1
  `).get(missionId,name,roleName) as any;
}

function activeSpecialistCount(missionId:string){
  const row:any=db.prepare(`
    SELECT COUNT(*) c FROM mission_specialists WHERE mission_id=? AND status='active'
  `).get(missionId);
  return Number(row?.c ?? 0);
}

function specialistPrompt(input:{
  roleName:string;
  objective:string;
  missionTitle:string;
  missionObjective:string;
  scopeType:string;
  scopeTaskKey?:string|null;
}){
  return `You are a temporary Doom Team specialist.

SPECIALIST ROLE:
${input.roleName}

SPECIALIST OBJECTIVE:
${input.objective}

MISSION:
${input.missionTitle}

BOSS OBJECTIVE:
${input.missionObjective}

SCOPE:
${input.scopeType}${input.scopeTaskKey ? ` (${input.scopeTaskKey})` : ''}

Operating rules:
- You exist only for this Doom mission/task scope.
- Follow Boss and Lead directives delivered through Doom Message Bus.
- Communicate material handoffs/questions instead of silently assuming missing facts.
- Use only tools granted by Doom Core.
- Do not attempt production deployment, host administration, credential access, or destructive operations.
- Produce evidence for claims and report blockers explicitly.
- When your assigned scope is complete, provide a concise handoff/report for the permanent team.`;
}

function createRequest(input:{
  missionId:string;
  taskId?:string|null;
  sourceExecutionId?:string|null;
  requestedByType:string;
  requestedByAgentId?:string|null;
  spec:any;
}){
  const requestId=id('factoryreq');
  db.prepare(`
    INSERT INTO agent_factory_requests
    (id,mission_id,task_id,source_execution_id,requested_by_type,requested_by_agent_id,
     status,spec_json,created_at)
    VALUES (?,?,?,?,?,?,'pending',?,?)
  `).run(
    requestId,
    input.missionId,
    input.taskId ?? null,
    input.sourceExecutionId ?? null,
    input.requestedByType,
    input.requestedByAgentId ?? null,
    JSON.stringify(input.spec ?? {}),
    now()
  );
  return requestId;
}

function failRequest(requestId:string,error:string){
  db.prepare(`
    UPDATE agent_factory_requests
    SET status='failed',error_text=?,resolved_at=? WHERE id=?
  `).run(error,now(),requestId);
}

function completeRequest(requestId:string,agentId:string){
  db.prepare(`
    UPDATE agent_factory_requests
    SET status='completed',result_agent_id=?,error_text=NULL,resolved_at=? WHERE id=?
  `).run(agentId,now(),requestId);
}

function validateRequester(
  mission:any,
  requestedByType:string,
  requestedByAgentId:string|null|undefined
){
  if(requestedByType==='boss')return;
  if(requestedByType!=='lead')throw new Error('AGENT_FACTORY_REQUESTER_DENIED');
  if(!requestedByAgentId || requestedByAgentId!==mission.lead_agent_id){
    throw new Error('AGENT_FACTORY_ONLY_MISSION_LEAD');
  }
}

function optionalTask(missionId:string,taskKey:any){
  const value=String(taskKey ?? '').trim();
  if(!value)return null;

  const task:any=db.prepare(`
    SELECT * FROM mission_tasks WHERE mission_id=? AND task_key=?
  `).get(missionId,value);
  if(!task)throw new Error(`AGENT_FACTORY_TASK_NOT_FOUND:${value}`);
  if(['done','running','validating','cancelled'].includes(task.status)){
    throw new Error(`AGENT_FACTORY_TASK_NOT_ASSIGNABLE:${task.status}`);
  }
  return task;
}

export function spawnMissionSpecialist(input:{
  missionId:string;
  requestedByType:'boss'|'lead'|string;
  requestedByAgentId?:string|null;
  sourceExecutionId?:string|null;
  name:string;
  roleName:string;
  objective:string;
  scopeType?:'mission'|'task'|string;
  assignTaskKey?:string|null;
  toolProfiles?:string[];
  skillIds?:string[];
  requiredCapabilities?:string[];
  modelStrategy?:string;
  autonomyLevel?:number;
}){
  const mission=missionRow(input.missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');
  if(['done','cancelled'].includes(mission.status)){
    throw new Error(`AGENT_FACTORY_MISSION_CLOSED:${mission.status}`);
  }

  validateRequester(mission,input.requestedByType,input.requestedByAgentId);

  const task=optionalTask(mission.id,input.assignTaskKey);
  const scopeType=task ? 'task' : (input.scopeType==='task'?'task':'mission');
  if(scopeType==='task' && !task){
    throw new Error('AGENT_FACTORY_TASK_SCOPE_REQUIRES_ASSIGN_TASK_KEY');
  }

  const spec={
    name:String(input.name ?? '').trim(),
    roleName:String(input.roleName ?? '').trim(),
    objective:String(input.objective ?? '').trim(),
    scopeType,
    assignTaskKey:task?.task_key ?? null,
    toolProfiles:normalizeProfiles(input.toolProfiles),
    skillIds:[...new Set((input.skillIds ?? []).map(String))],
    requiredCapabilities:[...new Set((input.requiredCapabilities ?? []).map(String))],
    modelStrategy:String(input.modelStrategy ?? 'best_available'),
    autonomyLevel:Math.max(0,Math.min(3,Number(input.autonomyLevel ?? 2))),
  };

  if(!spec.name)throw new Error('AGENT_FACTORY_NAME_REQUIRED');
  if(!spec.roleName)throw new Error('AGENT_FACTORY_ROLE_REQUIRED');
  if(!spec.objective)throw new Error('AGENT_FACTORY_OBJECTIVE_REQUIRED');

  const reusable=findReusableSpecialist(mission.id,spec.name,spec.roleName);
  if(reusable?.agent_id){
    const existing=getMissionSpecialist(mission.id,String(reusable.agent_id));
    missionEvent(mission.id,'agent_factory.specialist_reused',{
      agentId:String(reusable.agent_id),
      name:existing?.name ?? spec.name,
      roleName:existing?.role_name ?? spec.roleName,
      requestedName:spec.name,
      requestedRoleName:spec.roleName
    },task?.id ?? null);
    return {
      reused:true,
      request:null,
      specialist:existing,
      agent:getAgent(String(reusable.agent_id)),
    };
  }

  if(activeSpecialistCount(mission.id)>=cfg.maxMissionSpecialists){
    throw new Error(`AGENT_FACTORY_LIMIT_REACHED:${cfg.maxMissionSpecialists}`);
  }

  const requestId=createRequest({
    missionId:mission.id,
    taskId:task?.id ?? null,
    sourceExecutionId:input.sourceExecutionId ?? null,
    requestedByType:input.requestedByType,
    requestedByAgentId:input.requestedByAgentId ?? null,
    spec,
  });

  try{
    validateSkillIds(spec.skillIds);
    const tools=grantsFromProfiles(spec.toolProfiles);
    const required=new Set(spec.requiredCapabilities);
    required.add('chat');
    if(resolveToolDefinitions(tools).length)required.add('tools');

    const agent=createAgent({
      name:spec.name,
      description:`Temporary ${spec.roleName} specialist for mission: ${mission.title}`,
      lifetime:scopeType,
      tags:['temporary','specialist','agent-factory',`mission:${mission.id}`,`role:${spec.roleName}`],
      systemPrompt:specialistPrompt({
        roleName:spec.roleName,
        objective:spec.objective,
        missionTitle:mission.title,
        missionObjective:mission.objective,
        scopeType,
        scopeTaskKey:task?.task_key ?? null,
      }),
      instructions:`Temporary specialist created by Doom AgentFactory request ${requestId}.`,
      runtimePolicy:runtimeReference(),
      modelPolicy:policyReference(spec.modelStrategy),
      requiredCapabilities:[...required],
      tools,
      skills:[],
      skillIds:spec.skillIds,
      memoryPolicy:{
        scopes:['mission','task','agent'],
        promoteOnArchive:false,
        temporary:true,
      },
      permissionPolicy:{
        factoryCreated:true,
        missionId:mission.id,
        scopeType,
        scopeTaskId:task?.id ?? null,
        elevatedShell:false,
        production:false,
      },
      autonomyLevel:spec.autonomyLevel,
      createdBy:`agent-factory:${input.requestedByType}`,
    });

    if(!agent)throw new Error('AGENT_FACTORY_AGENT_CREATE_FAILED');

    const membershipId=id('specialist');
    const t=now();

    db.transaction(()=>{
      db.prepare(`
        INSERT INTO mission_specialists
        (id,mission_id,agent_id,request_id,scope_type,scope_task_id,role_name,objective,
         status,source,created_at)
        VALUES (?,?,?,?,?,?,?,?,'active','agent_factory',?)
      `).run(
        membershipId,mission.id,agent.id,requestId,scopeType,task?.id ?? null,
        spec.roleName,spec.objective,t
      );

      if(task){
        db.prepare(`
          UPDATE mission_tasks
          SET assigned_agent_id=?,status='pending',execution_id=NULL,result_text=NULL,error_text=NULL,
              started_at=NULL,ended_at=NULL,updated_at=?
          WHERE id=?
        `).run(agent.id,t,task.id);
      }
    })();

    completeRequest(requestId,agent.id);
    missionEvent(mission.id,'agent_factory.specialist_created',{
      requestId,
      specialistId:membershipId,
      agentId:agent.id,
      name:agent.name,
      roleName:spec.roleName,
      scopeType,
      taskKey:task?.task_key ?? null,
      toolProfiles:spec.toolProfiles,
      modelStrategy:spec.modelStrategy
    },task?.id ?? null);

    return {
      request:getFactoryRequest(requestId),
      specialist:getMissionSpecialist(mission.id,agent.id),
      agent:getAgent(agent.id),
    };
  }catch(err:any){
    const message=String(err?.message ?? err);
    failRequest(requestId,message);
    missionEvent(mission.id,'agent_factory.request_failed',{
      requestId,error:message
    },task?.id ?? null);
    throw err;
  }
}

export function getFactoryRequest(requestId:string){
  const row=requestRow(requestId);
  return row ? {...row,spec:parseJson(row.spec_json,{})} : null;
}

export function listFactoryRequests(missionId?:string,limit=200){
  const rows=missionId
    ? db.prepare(`
        SELECT afr.*,a.name AS result_agent_name
        FROM agent_factory_requests afr
        LEFT JOIN agents a ON a.id=afr.result_agent_id
        WHERE afr.mission_id=?
        ORDER BY afr.created_at DESC LIMIT ?
      `).all(missionId,limit)
    : db.prepare(`
        SELECT afr.*,a.name AS result_agent_name
        FROM agent_factory_requests afr
        LEFT JOIN agents a ON a.id=afr.result_agent_id
        ORDER BY afr.created_at DESC LIMIT ?
      `).all(limit);

  return (rows as any[]).map(x=>({...x,spec:parseJson(x.spec_json,{})}));
}

function specialistRows(missionId:string,status?:string){
  const args:any[]=[missionId];
  let where=`ms.mission_id=?`;
  if(status){
    where+=` AND ms.status=?`;
    args.push(status);
  }

  return db.prepare(`
    SELECT ms.*,a.name,a.slug,a.description,a.enabled,a.current_version_id,
      av.version_no,av.tools_json,av.skills_json,av.required_capabilities_json,
      av.runtime_policy_json,av.model_policy_json,av.autonomy_level
    FROM mission_specialists ms
    JOIN agents a ON a.id=ms.agent_id
    LEFT JOIN agent_versions av ON av.id=a.current_version_id
    WHERE ${where}
    ORDER BY ms.created_at
  `).all(...args) as any[];
}

export function listMissionSpecialists(missionId:string,status?:string){
  return specialistRows(missionId,status).map(x=>({
    ...x,
    tools:parseJson(x.tools_json,[]),
    skills:parseJson(x.skills_json,[]),
    requiredCapabilities:parseJson(x.required_capabilities_json,[]),
    runtimePolicy:parseJson(x.runtime_policy_json,{}),
    modelPolicy:parseJson(x.model_policy_json,{}),
  }));
}

export function getMissionSpecialist(missionId:string,agentId:string){
  return listMissionSpecialists(missionId).find(x=>x.agent_id===agentId) ?? null;
}

export function missionAgentIsActive(missionId:string,agentId:string){
  const row:any=db.prepare(`
    SELECT 1 ok FROM mission_specialists
    WHERE mission_id=? AND agent_id=? AND status='active'
  `).get(missionId,agentId);
  return Boolean(row?.ok);
}

export function missionRoster(missionId:string){
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  const permanent=db.prepare(`
    SELECT a.id,a.name,a.slug,a.description,tm.role_label,tm.priority,tm.can_delegate,
           av.tools_json,av.skills_json,av.required_capabilities_json,av.autonomy_level
    FROM team_members tm
    JOIN agents a ON a.id=tm.agent_id
    LEFT JOIN agent_versions av ON av.id=a.current_version_id
    WHERE tm.team_id=? AND a.enabled=1
    ORDER BY tm.priority,a.name
  `).all(mission.team_id) as any[];

  const specialists=specialistRows(missionId,'active').map((row,index)=>({
    id:row.agent_id,
    name:row.name,
    slug:row.slug,
    description:row.description,
    role_label:row.role_name,
    priority:500+index,
    can_delegate:0,
    tools_json:row.tools_json,
    skills_json:row.skills_json,
    required_capabilities_json:row.required_capabilities_json,
    autonomy_level:row.autonomy_level,
    temporary:true,
    specialist_id:row.id,
    specialist_scope_type:row.scope_type,
    specialist_scope_task_id:row.scope_task_id,
  }));

  return [...permanent,...specialists].map((row:any)=>({
    ...row,
    temporary:Boolean(row.temporary),
    tools:resolveToolDefinitions(parseJson<string[]>(row.tools_json,[])).map(x=>x.name),
    skills:parseJson<string[]>(row.skills_json,[]),
    requiredCapabilities:parseJson<string[]>(row.required_capabilities_json,[]),
    autonomyLevel:Number(row.autonomy_level ?? 0),
  }));
}

export function archiveMissionSpecialist(
  missionId:string,
  agentId:string,
  reason='boss_archive'
){
  const specialist:any=db.prepare(`
    SELECT * FROM mission_specialists WHERE mission_id=? AND agent_id=?
  `).get(missionId,agentId);
  if(!specialist)throw new Error('MISSION_SPECIALIST_NOT_FOUND');
  if(specialist.status==='archived')return getMissionSpecialist(missionId,agentId);

  const activeTask:any=db.prepare(`
    SELECT task_key,status FROM mission_tasks
    WHERE mission_id=? AND assigned_agent_id=? AND status IN ('running','validating')
    LIMIT 1
  `).get(missionId,agentId);
  if(activeTask)throw new Error(`MISSION_SPECIALIST_ACTIVE_TASK:${activeTask.task_key}`);

  const t=now();
  db.transaction(()=>{
    db.prepare(`
      UPDATE mission_specialists
      SET status='archived',archived_at=?,archive_reason=?
      WHERE mission_id=? AND agent_id=?
    `).run(t,reason,missionId,agentId);
    db.prepare(`UPDATE agents SET enabled=0,updated_at=? WHERE id=?`).run(t,agentId);
  })();

  missionEvent(missionId,'agent_factory.specialist_archived',{
    agentId,reason
  });
  return getMissionSpecialist(missionId,agentId);
}

export function archiveMissionSpecialists(
  missionId:string,
  reason='mission_complete'
){
  const rows=listMissionSpecialists(missionId,'active');
  const archived:any[]=[];
  for(const row of rows){
    try{
      archived.push(archiveMissionSpecialist(missionId,row.agent_id,reason));
    }catch{
      // A still-running specialist must remain active rather than being forcibly disabled.
    }
  }
  return archived;
}

export function spawnSpecialistFromExecution(
  executionId:string,
  context:Record<string,unknown>|undefined,
  args:any
){
  const missionId=String(context?.missionId ?? '');
  if(!missionId)throw new Error('AGENT_FACTORY_REQUIRES_MISSION_CONTEXT');

  const execution:any=db.prepare(`SELECT * FROM executions WHERE id=?`).get(executionId);
  if(!execution)throw new Error('EXECUTION_NOT_FOUND');

  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');
  if(execution.agent_id!==mission.lead_agent_id){
    throw new Error('AGENT_FACTORY_ONLY_MISSION_LEAD');
  }

  return spawnMissionSpecialist({
    missionId,
    requestedByType:'lead',
    requestedByAgentId:execution.agent_id,
    sourceExecutionId:executionId,
    name:String(args?.name ?? ''),
    roleName:String(args?.role_name ?? ''),
    objective:String(args?.objective ?? ''),
    scopeType:String(args?.scope_type ?? 'mission'),
    assignTaskKey:args?.assign_task_key ? String(args.assign_task_key) : null,
    toolProfiles:Array.isArray(args?.tool_profiles)?args.tool_profiles.map(String):undefined,
    skillIds:Array.isArray(args?.skill_ids)?args.skill_ids.map(String):undefined,
    requiredCapabilities:Array.isArray(args?.required_capabilities)?args.required_capabilities.map(String):undefined,
    modelStrategy:String(args?.model_strategy ?? 'best_available'),
    autonomyLevel:Number(args?.autonomy_level ?? 2),
  });
}
