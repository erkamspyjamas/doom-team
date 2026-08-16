import { db, id, now, parseJson, getSetting } from './db/index.js';
import { runtimeAdapter } from './runtime/registry.js';
import { loadConfig } from './config.js';
import { runtimeSkillsForAgentVersion } from './skills/registry.js';
import { resolveToolDefinitions } from './tools/catalog.js';
import { routeModel,recordRouteDecision,getRouteDecision } from './model-router.js';
import { checkModelMutation } from './model-registry.js';
import { pullExecutionDirectives } from './communications.js';

const cfg = loadConfig();

const activeExecutionControllers=new Map<string,AbortController>();

function executionTerminal(status:string){
  return ['done','error','cancelled'].includes(String(status));
}

function row(table:string, rowId:string) {
  return db.prepare(`SELECT * FROM ${table} WHERE id=?`).get(rowId) as any;
}

function resolveFixed(policy:any, field:string) {
  if (policy?.mode !== 'fixed' || !policy?.[field]) {
    throw new Error(`V0.2 execution currently requires fixed ${field} policy`);
  }
  return policy[field];
}

export function getExecution(executionId:string) {
  return db.prepare(`SELECT * FROM executions WHERE id=?`).get(executionId);
}

export function listExecutions(locale='tr', limit=50) {
  return db.prepare(`
    SELECT e.*,
      COALESCE(
        (SELECT et.value FROM entity_translations et
         WHERE et.entity_type='agent' AND et.entity_id=a.id
           AND et.locale=? AND et.field='name' LIMIT 1),
        a.name
      ) AS agent_name,
      m.name AS model_name,
      COALESCE(
        (SELECT et.value FROM entity_translations et
         WHERE et.entity_type='runtime' AND et.entity_id=r.id
           AND et.locale=? AND et.field='name' LIMIT 1),
        r.name
      ) AS runtime_name
    FROM executions e
    JOIN agents a ON a.id=e.agent_id
    JOIN models m ON m.id=e.model_id
    JOIN runtimes r ON r.id=e.runtime_id
    ORDER BY e.started_at DESC LIMIT ?
  `).all(locale,locale,limit);
}


async function resolveExecutionRoute(
  runtimeId:string,
  modelPolicy:any,
  requiredCapabilities:string[],
  preferMutation:boolean,
  initialExcludeModelIds:string[]=[]
){
  if(!preferMutation){
    return {
      routeDecision:routeModel({
        runtimeId,
        modelPolicy,
        requiredCapabilities
      }),
      mutationQualification:{
        required:false,
        tested:[],
        excludedModelIds:[],
        selectedStatus:null,
        policyFallback:false
      }
    };
  }

  if(modelPolicy?.mode === 'fixed'){
    const routeDecision = routeModel({
      runtimeId,
      modelPolicy,
      requiredCapabilities,
      preferMutation: true,
      requireMutationPass: true
    });
    return {
      routeDecision,
      mutationQualification: {
        required: true,
        tested: [],
        excludedModelIds: [],
        selectedStatus: 'fixed_policy',
        policyFallback: false
      }
    };
  }

  const tested:Array<{
    modelId:string;
    modelName:string;
    beforeStatus:string;
    result:string;
    reason?:string;
    policyLabel:string;
  }>=[];

  const maxAttempts=Math.max(
    1,
    Math.min(
      8,
      Number(
        (db.prepare(`
          SELECT COUNT(*) c
          FROM models m
          JOIN model_providers p ON p.id=m.provider_id
          WHERE m.enabled=1 AND p.enabled=1
        `).get() as any)?.c ?? 1
      )
    )
  );

  const qualifyPolicy=async(
    policy:any,
    policyLabel:string,
    baseExcluded:string[]
  )=>{
    const excluded=[...new Set(baseExcluded.map(String))];

    for(let attempt=0;attempt<maxAttempts;attempt++){
      let decision:any;
      try{
        decision=routeModel({
          runtimeId,
          modelPolicy:policy,
          requiredCapabilities,
          preferMutation:true,
          excludeModelIds:excluded
        });
      }catch{
        break;
      }

      const candidate:any=decision.candidates.find(
        (x:any)=>String(x.modelId)===String(decision.selectedModelId)
      );
      const mutationStatus=String(candidate?.mutationStatus ?? 'unknown');

      if(mutationStatus==='pass'){
        return {
          routeDecision:decision,
          excluded,
          policyLabel
        };
      }

      if(mutationStatus==='fail'){
        excluded.push(String(decision.selectedModelId));
        tested.push({
          modelId:String(decision.selectedModelId),
          modelName:String(decision.selectedModelName),
          beforeStatus:'fail',
          result:'excluded',
          reason:'saved_mutation_probe_fail',
          policyLabel
        });
        continue;
      }

      try{
        const probe:any=await checkModelMutation(String(decision.selectedModelId));
        tested.push({
          modelId:String(decision.selectedModelId),
          modelName:String(decision.selectedModelName),
          beforeStatus:mutationStatus,
          result:probe?.pass===true?'pass':'fail',
          reason:probe?.pass===true
            ? undefined
            : String(probe?.reason ?? 'mutation_probe_failed'),
          policyLabel
        });

        if(probe?.pass===true){
          const qualified=routeModel({
            runtimeId,
            modelPolicy:policy,
            requiredCapabilities,
            preferMutation:true,
            requireMutationPass:true,
            excludeModelIds:excluded
          });
          return {
            routeDecision:qualified,
            excluded,
            policyLabel
          };
        }
      }catch(err:any){
        tested.push({
          modelId:String(decision.selectedModelId),
          modelName:String(decision.selectedModelName),
          beforeStatus:mutationStatus,
          result:'fail',
          reason:String(err?.message ?? err),
          policyLabel
        });
      }

      excluded.push(String(decision.selectedModelId));
    }

    try{
      const qualified=routeModel({
        runtimeId,
        modelPolicy:policy,
        requiredCapabilities,
        preferMutation:true,
        requireMutationPass:true,
        excludeModelIds:excluded
      });
      return {
        routeDecision:qualified,
        excluded,
        policyLabel
      };
    }catch{
      return null;
    }
  };

  const primary=await qualifyPolicy(
    modelPolicy,
    'agent_policy',
    initialExcludeModelIds
  );

  if(primary){
    return {
      routeDecision:primary.routeDecision,
      mutationQualification:{
        required:true,
        tested,
        excludedModelIds:primary.excluded,
        selectedStatus:'pass',
        policyFallback:false,
        policyLabel:primary.policyLabel
      }
    };
  }

  // A local-only coder policy must not dead-end an autonomous modify task
  // when its local model cannot perform a real native-tool mutation.
  // Fall back execution-only to the global Tools Required policy.
  const toolsRequired:any=db.prepare(`
    SELECT id,current_version_id
    FROM model_policies
    WHERE id='policy_tools_required' AND enabled=1
  `).get();

  const currentPolicyId=String(modelPolicy?.policyId ?? '');
  if(
    toolsRequired?.current_version_id
    && currentPolicyId!=='policy_tools_required'
  ){
    const fallbackPolicy={
      mode:'policy',
      policyId:String(toolsRequired.id),
      policyVersionId:String(toolsRequired.current_version_id)
    };

    const fallback=await qualifyPolicy(
      fallbackPolicy,
      'tools_required_fallback',
      initialExcludeModelIds
    );

    if(fallback){
      return {
        routeDecision:fallback.routeDecision,
        mutationQualification:{
          required:true,
          tested,
          excludedModelIds:fallback.excluded,
          selectedStatus:'pass',
          policyFallback:true,
          policyLabel:fallback.policyLabel,
          originalPolicyId:currentPolicyId || null,
          fallbackPolicyId:'policy_tools_required'
        }
      };
    }
  }

  throw new Error(
    `NO_MUTATION_CAPABLE_MODEL:${tested.map(
      x=>`${x.policyLabel}/${x.modelName}:${x.result}${x.reason?`(${x.reason})`:''}`
    ).join(';') || 'no qualified candidate'}`
  );
}

export async function startExecution(
  agentId:string,
  task:string,
  options:{
    workspace?:string;
    context?:Record<string,unknown>;
    toolGrantsOverride?:string[];
  }={}
) {
  const agent:any=row('agents',agentId);
  if(!agent) throw new Error('Agent not found');
  const version:any=row('agent_versions',agent.current_version_id);
  if(!version) throw new Error('Agent has no current version');

  const runtimePolicy=parseJson(version.runtime_policy_json,{});
  const modelPolicy=parseJson(version.model_policy_json,{});
  const runtimeId=resolveFixed(runtimePolicy,'runtimeId');

  const runtime:any=row('runtimes',runtimeId);
  if(!runtime?.enabled) throw new Error('Runtime disabled');

  const requiredCapabilities=parseJson<string[]>(version.required_capabilities_json,[]);
  const routed=await resolveExecutionRoute(
    runtimeId,
    modelPolicy,
    requiredCapabilities,
    Boolean(options.context?.preferMutationModel),
    Array.isArray(options.context?.excludeMutationModelIds)
      ? (options.context!.excludeMutationModelIds as unknown[]).map(String)
      : []
  );
  const routeDecision=routed.routeDecision;
  const modelId=routeDecision.selectedModelId;
  const model:any=row('models',modelId);
  if(!model?.enabled) throw new Error('Routed model disabled');
  const provider:any=row('model_providers',model.provider_id);
  if(!provider?.enabled) throw new Error('Routed provider disabled');

  const responseLocale=getSetting('agent_response_locale','tr');
  const executionWorkspace=options.workspace
    ?? parseJson<{ workspace?: string }>(runtime.config_json, {}).workspace
    ?? cfg.defaultWorkspace;
  const skills=runtimeSkillsForAgentVersion(version.id);
  const versionToolGrants=parseJson<string[]>(version.tools_json,[]);
  const toolGrants=Array.isArray(options.toolGrantsOverride)
    ? [...new Set(options.toolGrantsOverride.map(String))]
    : versionToolGrants;
  const grantedToolNames=new Set(resolveToolDefinitions(toolGrants).map(t=>t.name));
  for(const skill of skills){
    const required=Array.isArray(skill.manifest.requiredTools)?skill.manifest.requiredTools:[];
    const missing=required.filter(name=>!grantedToolNames.has(String(name)));
    if(missing.length) throw new Error(`SKILL_REQUIRED_TOOLS_MISSING:${skill.slug}:${missing.join(',')}`);
  }
  const executionId=id('exec');
  const controller=new AbortController();
  activeExecutionControllers.set(executionId,controller);
  const started=now();
  const snapshot={
    agent:{id:agent.id,name:agent.name,slug:agent.slug},
    responseLocale,
    version:{
      id:version.id,
      versionNo:version.version_no,
      systemPrompt:version.system_prompt,
      instructions:version.instructions,
      runtimePolicy,
      modelPolicy,
      tools:versionToolGrants,
      executionTools:toolGrants,
      legacySkillTags:parseJson(version.skills_json,[]),
      skills:skills.map(s=>({skillId:s.skillId,skillVersionId:s.skillVersionId,slug:s.slug,name:s.name,versionNo:s.versionNo,packageVersion:s.packageVersion,contentHash:s.contentHash,resourceFiles:Object.keys(s.files)})),
      autonomyLevel:version.autonomy_level,
      permissionPolicy:parseJson(version.permission_policy_json,{}),
    },
    routeDecision,
    mutationQualification:routed.mutationQualification,
    workspace:executionWorkspace,
    context:options.context ?? {},
    runtime,
    model,
    provider,
  };

  db.prepare(`
    INSERT INTO executions
    (id,agent_id,agent_version_id,runtime_id,model_id,task_text,status,started_at,snapshot_json)
    VALUES (?,?,?,?,?,?,?, ?,?)
  `).run(
    executionId,agent.id,version.id,runtime.id,model.id,task,'running',started,JSON.stringify(snapshot)
  );

  recordRouteDecision(executionId,routeDecision);
  db.prepare(`INSERT INTO events(execution_id,event_type,payload_json,created_at) VALUES (?,?,?,?)`)
    .run(executionId,'model.route',JSON.stringify(routeDecision),now());
  db.prepare(`INSERT INTO events(execution_id,event_type,payload_json,created_at) VALUES (?,?,?,?)`)
    .run(
      executionId,
      'model.mutation_qualification',
      JSON.stringify(routed.mutationQualification),
      now()
    );

  const onEvent=(type:string,payload:any)=>{
    db.prepare(`INSERT INTO events(execution_id,event_type,payload_json,created_at) VALUES (?,?,?,?)`)
      .run(executionId,type,JSON.stringify(payload ?? {}),now());
    if(type==='session.started' && payload?.sessionId){
      db.prepare(`UPDATE executions SET external_session_id=? WHERE id=?`)
        .run(String(payload.sessionId),executionId);
    }
  };

  void (async()=>{
    try{
      const adapter=runtimeAdapter(runtime.adapter_type);
      const result=await adapter.execute({
        executionId,
        agentName:agent.name,
        systemPrompt:version.system_prompt,
        instructions:version.instructions,
        task,
        responseLocale,
        toolGrants,
        autonomyLevel:Number(version.autonomy_level ?? 2),
        permissionPolicy:parseJson<Record<string, unknown>>(version.permission_policy_json,{}),
        skills,
        provider,
        model,
        runtime,
        workspace: executionWorkspace,
        context: options.context ?? {},
        pullDirectives:()=>pullExecutionDirectives(executionId),
        signal: controller.signal,
        onEvent,
      });
      const current:any=getExecution(executionId);
      if(current?.status!=='cancelled'){
        db.prepare(`
          UPDATE executions SET status='done',external_session_id=COALESCE(?,external_session_id),result_text=?,ended_at=? WHERE id=?
        `).run(result.externalSessionId ?? null,result.text ?? '',now(),executionId);
      }
    }catch(err:any){
      const current:any=getExecution(executionId);
      const message=String(err?.message ?? err);
      if(current?.status==='cancelled' || controller.signal.aborted){
        onEvent('runtime.cancelled',{reason:message});
        db.prepare(`UPDATE executions SET status='cancelled',ended_at=COALESCE(ended_at,?) WHERE id=?`)
          .run(now(),executionId);
      }else{
        onEvent('runtime.error',{error:message});
        db.prepare(`UPDATE executions SET status='error',error_text=?,ended_at=? WHERE id=?`)
          .run(message,now(),executionId);
      }
    }finally{
      activeExecutionControllers.delete(executionId);
    }
  })();

  return getExecution(executionId);
}


export async function cancelExecution(executionId:string,mode:'cancel'|'kill'='cancel') {
  const execution:any=getExecution(executionId);
  if(!execution)throw new Error('EXECUTION_NOT_FOUND');
  if(executionTerminal(execution.status))return execution;

  db.prepare(`INSERT INTO events(execution_id,event_type,payload_json,created_at) VALUES (?,?,?,?)`)
    .run(executionId,mode==='kill'?'execution.kill_requested':'execution.cancel_requested',JSON.stringify({mode}),now());

  const controller=activeExecutionControllers.get(executionId);
  controller?.abort();

  if(execution.external_session_id){
    try{
      const runtime:any=row('runtimes',execution.runtime_id);
      const adapter=runtimeAdapter(runtime.adapter_type);
      if(adapter.cancel)await adapter.cancel(execution.external_session_id);
    }catch(err:any){
      db.prepare(`INSERT INTO events(execution_id,event_type,payload_json,created_at) VALUES (?,?,?,?)`)
        .run(executionId,'execution.cancel_runtime_error',JSON.stringify({error:String(err?.message ?? err)}),now());
    }
  }

  db.prepare(`
    UPDATE executions
    SET status='cancelled',error_text=?,ended_at=COALESCE(ended_at,?)
    WHERE id=? AND status NOT IN ('done','error','cancelled')
  `).run(mode==='kill'?'KILLED_BY_BOSS':'CANCELLED_BY_BOSS',now(),executionId);

  return getExecution(executionId);
}

export function executionEvents(executionId:string,limit=200) {
  return db.prepare(`
    SELECT * FROM events WHERE execution_id=? ORDER BY id DESC LIMIT ?
  `).all(executionId,limit).reverse();
}

export { getRouteDecision };
