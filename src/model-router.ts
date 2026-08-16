import { db, now, parseJson } from './db/index.js';
import type { ModelPolicyDefinition } from './model-policies.js';

type Candidate = {
  modelId:string;
  name:string;
  providerId:string;
  providerName:string;
  location:string;
  providerModelId:string;
  score:number;
  eligible:boolean;
  reasons:string[];
  labels:string[];
  capabilities:Record<string,unknown>;
  contextWindow:number;
  chatStatus:string;
  toolsStatus:string;
  codingStatus:string;
  structuredOutputStatus:string;
  chatLatencyMs:number|null;
  toolsLatencyMs:number|null;
  mutationStatus:string;
  mutationLatencyMs:number|null;
};

export type RouteDecision = {
  mode:'fixed'|'policy';
  runtimeId:string;
  selectedModelId:string;
  selectedModelName:string;
  policyId?:string;
  policyVersionId?:string;
  policyName?:string;
  policyDefinition?:ModelPolicyDefinition;
  requiredCapabilities:string[];
  candidates:Candidate[];
  reason:string;
};

function policyVersion(policy:any) {
  const versionId=String(policy?.policyVersionId ?? '');
  if(!versionId) throw new Error('MODEL_POLICY_VERSION_REQUIRED');
  const row:any=db.prepare(`
    SELECT v.*,p.name AS policy_name,p.enabled AS policy_enabled
    FROM model_policy_versions v
    JOIN model_policies p ON p.id=v.policy_id
    WHERE v.id=?
  `).get(versionId);
  if(!row) throw new Error('MODEL_POLICY_VERSION_NOT_FOUND');
  if(!row.policy_enabled) throw new Error('MODEL_POLICY_DISABLED');
  if(policy?.policyId && String(policy.policyId)!==String(row.policy_id)){
    throw new Error('MODEL_POLICY_VERSION_MISMATCH');
  }
  return {
    id:row.id,
    policyId:row.policy_id,
    name:row.policy_name,
    definition:parseJson<ModelPolicyDefinition>(row.definition_json,{})
  };
}

function rows(runtimeId:string) {
  return db.prepare(`
    SELECT
      m.*,
      p.name AS provider_name,
      p.enabled AS provider_enabled,
      p.location AS provider_location,
      c.chat_status,c.tools_status,c.coding_status,c.structured_output_status,
      (SELECT mc.latency_ms FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='chat' ORDER BY mc.id DESC LIMIT 1) AS chat_latency_ms,
      (SELECT mc.latency_ms FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='tools' ORDER BY mc.id DESC LIMIT 1) AS tools_latency_ms,
      (SELECT mc.status FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='mutation' ORDER BY mc.id DESC LIMIT 1) AS mutation_status,
      (SELECT mc.latency_ms FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='mutation' ORDER BY mc.id DESC LIMIT 1) AS mutation_latency_ms
    FROM models m
    JOIN model_providers p ON p.id=m.provider_id
    LEFT JOIN model_runtime_compatibility c ON c.model_id=m.id AND c.runtime_id=?
    ORDER BY m.name
  `).all(runtimeId) as any[];
}

function normalizedRequired(agentCaps:string[], definition:ModelPolicyDefinition) {
  const out=new Set(agentCaps.map(String));
  const r=definition.require ?? {};
  if(r.chat!==false)out.add('chat');
  if(r.tools)out.add('tools');
  if(r.vision)out.add('vision');
  if(r.reasoning)out.add('reasoning');
  if(r.structuredOutput)out.add('structuredOutput');
  if(r.coding)out.add('coding');
  return [...out];
}

function hasCapability(caps:any,name:string) {
  if(name==='chat') return Boolean(caps?.chat);
  if(name==='tools') return Boolean(caps?.tools);
  if(name==='vision') return Boolean(caps?.vision);
  if(name==='reasoning') return Boolean(caps?.reasoning);
  if(name==='structuredOutput') return Boolean(caps?.structuredOutput);
  return Boolean(caps?.[name]);
}

function evaluate(row:any,runtimeId:string,definition:ModelPolicyDefinition,required:string[],runtimeReasons:string[]=[],preferMutation=false,requireMutationPass=false,excludedModelIds:Set<string>=new Set()):Candidate {
  const labels=parseJson<string[]>(row.labels_json,[]).map(x=>String(x).toLowerCase());
  const caps=parseJson<Record<string,unknown>>(row.capabilities_json,{});
  const reasons:string[]=[...runtimeReasons];
  let score=0;

  if(!row.enabled)reasons.push('model_disabled');
  if(!row.provider_enabled)reasons.push('provider_disabled');
  if(row.coding_status==='blocked')reasons.push('runtime_model_blocked');
  if(excludedModelIds.has(String(row.id)))reasons.push('excluded_by_execution');
  if(requireMutationPass && String(row.mutation_status ?? 'unknown')!=='pass'){
    reasons.push(`mutation_${String(row.mutation_status ?? 'unknown')}`);
  }

  const location=String(row.provider_location ?? 'local');
  const wantedLocation=definition.location ?? 'any';
  if(wantedLocation!=='any' && location!==wantedLocation)reasons.push(`location_requires_${wantedLocation}`);

  for(const cap of required){
    if(cap==='chat'){
      if(row.chat_status!=='pass')reasons.push(`chat_${row.chat_status ?? 'unknown'}`);
    }else if(cap==='tools'){
      if(row.tools_status!=='pass')reasons.push(`tools_${row.tools_status ?? 'unknown'}`);
    }else if(cap==='coding'){
      if(row.coding_status==='fail' || row.coding_status==='blocked')reasons.push(`coding_${row.coding_status}`);
      if(row.tools_status!=='pass')reasons.push('coding_requires_tools');
    }else if(cap==='structuredOutput'){
      if(row.structured_output_status!=='pass' && !hasCapability(caps,'structuredOutput')){
        reasons.push(`structured_output_${row.structured_output_status ?? 'unknown'}`);
      }
    }else if(!hasCapability(caps,cap)){
      reasons.push(`capability_missing_${cap}`);
    }
  }

  const eligible=reasons.length===0;
  if(eligible){
    score+=100;
    if(row.chat_status==='pass')score+=20;
    if(row.tools_status==='pass')score+=15;
    if(preferMutation){
      if(row.mutation_status==='pass')score+=45;
      else if(row.mutation_status==='fail')score-=35;
    }
    if(location==='local')score+=5;

    const preferredLabels=(definition.preferredLabels ?? []).map(x=>String(x).toLowerCase());
    for(const label of preferredLabels){
      if(labels.includes(label))score+=18;
    }

    for(const cap of definition.preferredCapabilities ?? []){
      if(hasCapability(caps,String(cap)))score+=8;
    }

    if(definition.preferLowerLatency!==false){
      const latency=Number(row.chat_latency_ms);
      if(Number.isFinite(latency) && latency>=0){
        score+=Math.max(0,20-Math.min(20,latency/250));
      }
      const toolsLatency=Number(row.tools_latency_ms);
      if(required.includes('tools') && Number.isFinite(toolsLatency) && toolsLatency>=0){
        score+=Math.max(0,10-Math.min(10,toolsLatency/300));
      }
    }

    const context=Number(row.context_window_configured ?? row.max_context_reported ?? 0);
    if(definition.preferLargerContext!==false && context>0){
      score+=Math.min(12,Math.max(0,Math.log2(context/4096)*2));
    }
  }

  return {
    modelId:row.id,
    name:row.name,
    providerId:row.provider_id,
    providerName:row.provider_name,
    location,
    providerModelId:row.provider_model_id,
    score:Number(score.toFixed(3)),
    eligible,
    reasons,
    labels,
    capabilities:caps,
    contextWindow:Number(row.context_window_configured ?? row.max_context_reported ?? 0),
    chatStatus:String(row.chat_status ?? 'unknown'),
    toolsStatus:String(row.tools_status ?? 'unknown'),
    codingStatus:String(row.coding_status ?? 'unknown'),
    structuredOutputStatus:String(row.structured_output_status ?? 'unknown'),
    chatLatencyMs:row.chat_latency_ms==null?null:Number(row.chat_latency_ms),
    toolsLatencyMs:row.tools_latency_ms==null?null:Number(row.tools_latency_ms),
    mutationStatus:String(row.mutation_status ?? 'unknown'),
    mutationLatencyMs:row.mutation_latency_ms==null?null:Number(row.mutation_latency_ms),
  };
}

function selectedFrom(candidates:Candidate[],definition:ModelPolicyDefinition) {
  const eligible=candidates.filter(x=>x.eligible).sort((a,b)=>b.score-a.score || a.name.localeCompare(b.name));
  if(!eligible.length)return null;

  const preferredLabels=(definition.preferredLabels ?? []).map(x=>String(x).toLowerCase());
  if(preferredLabels.length){
    const preferred=eligible.filter(c=>preferredLabels.some(label=>c.labels.includes(label)));
    if(preferred.length)return preferred[0];

    for(const modelId of definition.fallbackModelIds ?? []){
      const c=eligible.find(x=>x.modelId===modelId);
      if(c)return c;
    }
  }

  return eligible[0];
}

function runtimeRequirementReasons(runtime:any,required:string[]) {
  const caps=parseJson<Record<string,unknown>>(runtime?.capabilities_json,{});
  const reasons:string[]=[];
  for(const cap of required){
    if(cap==='chat' && !caps.chat)reasons.push('runtime_capability_missing_chat');
    if(cap==='tools' && !caps.tools)reasons.push('runtime_capability_missing_tools');
    if(cap==='vision' && !caps.vision)reasons.push('runtime_capability_missing_vision');
    if(cap==='structuredOutput' && !caps.structuredOutput)reasons.push('runtime_capability_missing_structuredOutput');
  }
  return reasons;
}

export function routeModel(input:{
  runtimeId:string;
  modelPolicy:any;
  requiredCapabilities?:string[];
  preferMutation?:boolean;
  requireMutationPass?:boolean;
  excludeModelIds?:string[];
}):RouteDecision {
  const runtime:any=db.prepare(`SELECT * FROM runtimes WHERE id=?`).get(input.runtimeId);
  if(!runtime?.enabled)throw new Error('RUNTIME_DISABLED_OR_NOT_FOUND');

  const agentCaps=(input.requiredCapabilities ?? []).map(String);
  const policy=input.modelPolicy ?? {};

  if(policy.mode==='fixed'){
    const modelId=String(policy.modelId ?? '');
    if(!modelId)throw new Error('FIXED_MODEL_ID_REQUIRED');
    const definition:ModelPolicyDefinition={location:'any',require:{chat:true}};
    const required=normalizedRequired(agentCaps,definition);
    const runtimeReasons=runtimeRequirementReasons(runtime,required);
    const candidates=rows(input.runtimeId).map(r=>evaluate(r,input.runtimeId,definition,required,runtimeReasons,Boolean(input.preferMutation),Boolean(input.requireMutationPass),new Set((input.excludeModelIds ?? []).map(String))));
    const chosen=candidates.find(x=>x.modelId===modelId);
    if(!chosen)throw new Error('MODEL_NOT_FOUND');
    if(!chosen.eligible)throw new Error(`FIXED_MODEL_INELIGIBLE:${chosen.reasons.join(',')}`);
    return {
      mode:'fixed',
      runtimeId:input.runtimeId,
      selectedModelId:chosen.modelId,
      selectedModelName:chosen.name,
      requiredCapabilities:required,
      candidates,
      reason:`Fixed model selected: ${chosen.name}`
    };
  }

  if(policy.mode!=='policy')throw new Error('MODEL_POLICY_MODE_UNSUPPORTED');

  const pv=policyVersion(policy);
  const definition:ModelPolicyDefinition={
    ...pv.definition,
    ...(policy.overrides ?? {}),
    require:{...(pv.definition.require ?? {}),...(policy.overrides?.require ?? {})},
  };
  const required=normalizedRequired(agentCaps,definition);
  const runtimeReasons=runtimeRequirementReasons(runtime,required);
  const candidates=rows(input.runtimeId).map(r=>evaluate(r,input.runtimeId,definition,required,runtimeReasons,Boolean(input.preferMutation),Boolean(input.requireMutationPass),new Set((input.excludeModelIds ?? []).map(String))));
  const chosen=selectedFrom(candidates,definition);
  if(!chosen){
    const summary=candidates.map(x=>`${x.name}:${x.reasons.join('|') || 'ineligible'}`).join('; ');
    throw new Error(`MODEL_ROUTER_NO_ELIGIBLE_CANDIDATE:${summary}`);
  }

  return {
    mode:'policy',
    runtimeId:input.runtimeId,
    selectedModelId:chosen.modelId,
    selectedModelName:chosen.name,
    policyId:pv.policyId,
    policyVersionId:pv.id,
    policyName:pv.name,
    policyDefinition:definition,
    requiredCapabilities:required,
    candidates,
    reason:`${pv.name} selected ${chosen.name} with score ${chosen.score}`
  };
}

export function recordRouteDecision(executionId:string,decision:RouteDecision) {
  db.prepare(`
    INSERT INTO model_route_decisions
    (execution_id,runtime_id,policy_id,policy_version_id,selected_model_id,decision_json,created_at)
    VALUES (?,?,?,?,?,?,?)
  `).run(
    executionId,
    decision.runtimeId,
    decision.policyId ?? null,
    decision.policyVersionId ?? null,
    decision.selectedModelId,
    JSON.stringify(decision),
    now()
  );
}

export function getRouteDecision(executionId:string) {
  const row:any=db.prepare(`SELECT * FROM model_route_decisions WHERE execution_id=?`).get(executionId);
  return row ? {...row,decision:parseJson(row.decision_json,{})} : null;
}
