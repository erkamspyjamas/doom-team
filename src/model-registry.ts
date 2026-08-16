import { db, id, now, parseJson } from './db/index.js';
import { chatProbe, listRemoteModels, mutationProbe, toolProbe } from './providers/openai-compatible.js';

function providerRow(providerId:string) {
  return db.prepare(`SELECT * FROM model_providers WHERE id=?`).get(providerId) as any;
}

function modelRow(modelId:string) {
  return db.prepare(`
    SELECT m.*,p.name AS provider_name,p.protocol,p.base_url,p.auth_type,p.secret_ref,p.location AS provider_location
    FROM models m JOIN model_providers p ON p.id=m.provider_id
    WHERE m.id=?
  `).get(modelId) as any;
}

function normalizeBaseUrl(value:string) {
  const base=String(value ?? '').trim().replace(/\/+$/,'');
  if(!/^https?:\/\//i.test(base)) throw new Error('PROVIDER_BASE_URL_INVALID');
  return base;
}

function ensureCompatibility(modelId:string) {
  const runtimes=db.prepare(`SELECT id FROM runtimes`).all() as any[];
  const insert=db.prepare(`
    INSERT OR IGNORE INTO model_runtime_compatibility
    (id,model_id,runtime_id,chat_status,tools_status,coding_status,structured_output_status,metadata_json)
    VALUES (?,?,?,'unknown','unknown','unknown','unknown','{}')
  `);
  for(const runtime of runtimes) insert.run(id('compat'),modelId,runtime.id);
}

function recordProviderCheck(providerId:string,checkType:string,status:string,latencyMs:number|null,detail:any) {
  db.prepare(`
    INSERT INTO provider_checks(provider_id,check_type,status,latency_ms,detail_json,checked_at)
    VALUES (?,?,?,?,?,?)
  `).run(providerId,checkType,status,latencyMs,JSON.stringify(detail ?? {}),now());
}

function recordModelCheck(modelId:string,checkType:string,status:string,latencyMs:number|null,detail:any) {
  db.prepare(`
    INSERT INTO model_checks(model_id,check_type,status,latency_ms,detail_json,checked_at)
    VALUES (?,?,?,?,?,?)
  `).run(modelId,checkType,status,latencyMs,JSON.stringify(detail ?? {}),now());
}

export function providersDetailed() {
  return (db.prepare(`
    SELECT p.*,
      (SELECT pc.status FROM provider_checks pc WHERE pc.provider_id=p.id ORDER BY pc.id DESC LIMIT 1) AS last_check_status,
      (SELECT pc.latency_ms FROM provider_checks pc WHERE pc.provider_id=p.id ORDER BY pc.id DESC LIMIT 1) AS last_check_latency_ms,
      (SELECT pc.checked_at FROM provider_checks pc WHERE pc.provider_id=p.id ORDER BY pc.id DESC LIMIT 1) AS last_checked_at,
      (SELECT COUNT(*) FROM models m WHERE m.provider_id=p.id) AS model_count
    FROM model_providers p
    ORDER BY p.name
  `).all() as any[]).map(x=>({
    ...x,
    metadata:parseJson(x.metadata_json,{}),
  }));
}

export function modelsDetailed() {
  return (db.prepare(`
    SELECT m.*,p.name AS provider_name,p.base_url,p.protocol,p.location AS provider_location,
      (SELECT mc.status FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='chat' ORDER BY mc.id DESC LIMIT 1) AS last_chat_status,
      (SELECT mc.latency_ms FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='chat' ORDER BY mc.id DESC LIMIT 1) AS last_chat_latency_ms,
      (SELECT mc.status FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='tools' ORDER BY mc.id DESC LIMIT 1) AS last_tools_status,
      (SELECT mc.latency_ms FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='tools' ORDER BY mc.id DESC LIMIT 1) AS last_tools_latency_ms,
      (SELECT mc.status FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='mutation' ORDER BY mc.id DESC LIMIT 1) AS last_mutation_status,
      (SELECT mc.latency_ms FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='mutation' ORDER BY mc.id DESC LIMIT 1) AS last_mutation_latency_ms
    FROM models m JOIN model_providers p ON p.id=m.provider_id
    ORDER BY m.name
  `).all() as any[]).map(x=>({
    ...x,
    capabilities:parseJson(x.capabilities_json,{}),
    labels:parseJson(x.labels_json,[]),
    metadata:parseJson(x.metadata_json,{}),
  }));
}

export function createProvider(input:any) {
  const t=now();
  const providerId=id('provider');
  db.prepare(`
    INSERT INTO model_providers
    (id,name,protocol,base_url,auth_type,secret_ref,location,enabled,metadata_json,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    providerId,
    String(input.name).trim(),
    String(input.protocol ?? 'openai-compatible'),
    normalizeBaseUrl(input.baseUrl),
    String(input.authType ?? 'none'),
    input.secretRef ? String(input.secretRef).trim() : null,
    String(input.location ?? 'local'),
    input.enabled===false ? 0 : 1,
    JSON.stringify(input.metadata ?? {}),
    t,t
  );
  return providerRow(providerId);
}

export function updateProvider(providerId:string,input:any) {
  const current=providerRow(providerId);
  if(!current) throw new Error('PROVIDER_NOT_FOUND');
  db.prepare(`
    UPDATE model_providers
    SET name=?,protocol=?,base_url=?,auth_type=?,secret_ref=?,location=?,enabled=?,metadata_json=?,updated_at=?
    WHERE id=?
  `).run(
    input.name!==undefined ? String(input.name).trim() : current.name,
    input.protocol!==undefined ? String(input.protocol) : current.protocol,
    input.baseUrl!==undefined ? normalizeBaseUrl(input.baseUrl) : current.base_url,
    input.authType!==undefined ? String(input.authType) : current.auth_type,
    input.secretRef!==undefined ? (input.secretRef ? String(input.secretRef).trim() : null) : current.secret_ref,
    input.location!==undefined ? String(input.location) : current.location,
    input.enabled!==undefined ? (input.enabled ? 1 : 0) : current.enabled,
    JSON.stringify(input.metadata ?? parseJson(current.metadata_json,{})),
    now(),
    providerId
  );
  return providerRow(providerId);
}

export function deleteProvider(providerId:string) {
  const provider=providerRow(providerId);
  if(!provider) throw new Error('PROVIDER_NOT_FOUND');
  const count=(db.prepare(`SELECT COUNT(*) c FROM models WHERE provider_id=?`).get(providerId) as any)?.c ?? 0;
  if(Number(count)>0) throw new Error('PROVIDER_HAS_MODELS');
  db.prepare(`DELETE FROM model_providers WHERE id=?`).run(providerId);
  return {ok:true};
}

export async function checkProvider(providerId:string) {
  const provider=providerRow(providerId);
  if(!provider) throw new Error('PROVIDER_NOT_FOUND');
  try{
    const result=await listRemoteModels(provider);
    recordProviderCheck(providerId,'models','pass',result.latencyMs,{
      modelCount:result.models.length,
      models:result.models.slice(0,100).map((x:{id:string})=>x.id),
    });
    return {pass:true,latencyMs:result.latencyMs,modelCount:result.models.length};
  }catch(err:any){
    recordProviderCheck(providerId,'models','fail',null,{error:String(err?.message ?? err)});
    throw err;
  }
}

export async function discoverProviderModels(providerId:string) {
  const provider=providerRow(providerId);
  if(!provider) throw new Error('PROVIDER_NOT_FOUND');
  const result=await listRemoteModels(provider);
  recordProviderCheck(providerId,'discover','pass',result.latencyMs,{modelCount:result.models.length});
  const registered=db.prepare(`SELECT provider_model_id,id FROM models WHERE provider_id=?`).all(providerId) as any[];
  const byId=new Map(registered.map(x=>[x.provider_model_id,x.id]));
  return {
    latencyMs:result.latencyMs,
    items:result.models.map((x:{id:string;ownedBy?:string;raw:any})=>({...x,registeredModelId:byId.get(x.id) ?? null})),
  };
}

export function createModel(input:any) {
  const provider=providerRow(String(input.providerId));
  if(!provider) throw new Error('PROVIDER_NOT_FOUND');
  const t=now();
  const modelId=id('model');
  db.prepare(`
    INSERT INTO models
    (id,provider_id,name,provider_model_id,enabled,context_window_configured,max_context_reported,
     capabilities_json,labels_json,metadata_json,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    modelId,
    provider.id,
    String(input.name).trim(),
    String(input.providerModelId).trim(),
    input.enabled===false ? 0 : 1,
    input.contextWindowConfigured ?? null,
    input.maxContextReported ?? null,
    JSON.stringify(input.capabilities ?? {}),
    JSON.stringify(input.labels ?? []),
    JSON.stringify(input.metadata ?? {}),
    t,t
  );
  ensureCompatibility(modelId);
  return modelRow(modelId);
}

export function updateModel(modelId:string,input:any) {
  const current=modelRow(modelId);
  if(!current) throw new Error('MODEL_NOT_FOUND');
  const providerId=input.providerId!==undefined ? String(input.providerId) : current.provider_id;
  if(!providerRow(providerId)) throw new Error('PROVIDER_NOT_FOUND');
  db.prepare(`
    UPDATE models
    SET provider_id=?,name=?,provider_model_id=?,enabled=?,context_window_configured=?,
        max_context_reported=?,capabilities_json=?,labels_json=?,metadata_json=?,updated_at=?
    WHERE id=?
  `).run(
    providerId,
    input.name!==undefined ? String(input.name).trim() : current.name,
    input.providerModelId!==undefined ? String(input.providerModelId).trim() : current.provider_model_id,
    input.enabled!==undefined ? (input.enabled ? 1 : 0) : current.enabled,
    input.contextWindowConfigured!==undefined ? input.contextWindowConfigured : current.context_window_configured,
    input.maxContextReported!==undefined ? input.maxContextReported : current.max_context_reported,
    JSON.stringify(input.capabilities ?? parseJson(current.capabilities_json,{})),
    JSON.stringify(input.labels ?? parseJson(current.labels_json,[])),
    JSON.stringify(input.metadata ?? parseJson(current.metadata_json,{})),
    now(),modelId
  );
  ensureCompatibility(modelId);
  return modelRow(modelId);
}

export function deleteModel(modelId:string) {
  const model=modelRow(modelId);
  if(!model) throw new Error('MODEL_NOT_FOUND');

  const executions=(db.prepare(`SELECT COUNT(*) c FROM executions WHERE model_id=?`).get(modelId) as any)?.c ?? 0;
  if(Number(executions)>0) throw new Error('MODEL_HAS_EXECUTIONS');

  const versions=db.prepare(`SELECT id,model_policy_json FROM agent_versions`).all() as any[];
  if(versions.some(v=>parseJson<any>(v.model_policy_json,{})?.modelId===modelId)){
    throw new Error('MODEL_USED_BY_AGENT_VERSION');
  }

  const templates=db.prepare(`SELECT definition_json FROM agent_templates`).all() as any[];
  if(templates.some(v=>parseJson<any>(v.definition_json,{})?.modelPolicy?.modelId===modelId)){
    throw new Error('MODEL_USED_BY_TEMPLATE');
  }

  db.prepare(`DELETE FROM models WHERE id=?`).run(modelId);
  return {ok:true};
}

export async function checkModelChat(modelId:string) {
  const model=modelRow(modelId);
  if(!model) throw new Error('MODEL_NOT_FOUND');
  const provider=providerRow(model.provider_id);
  try{
    const result=await chatProbe(provider,model.provider_model_id);
    recordModelCheck(modelId,'chat',result.pass?'pass':'fail',result.latencyMs,{
      content:result.content,
      expectedTokenMatch:result.expectedTokenMatch,
      finishReason:result.finishReason,
      reasoningPresent:result.reasoningPresent,
      reasoningLength:result.reasoningLength,
      usage:result.usage,
    });
    if(result.pass){
      const caps=parseJson<any>(model.capabilities_json,{});
      caps.chat=true;
      db.prepare(`UPDATE models SET capabilities_json=?,updated_at=? WHERE id=?`)
        .run(JSON.stringify(caps),now(),modelId);
    }
    db.prepare(`
      UPDATE model_runtime_compatibility
      SET chat_status=?,last_tested_at=?,notes=?
      WHERE model_id=? AND runtime_id='runtime_generic_openai'
    `).run(result.pass?'pass':'fail',now(),'V0.6.2 chat capability probe: reasoning disabled when supported; plain-request fallback for compatible providers.',modelId);
    return result;
  }catch(err:any){
    recordModelCheck(modelId,'chat','fail',null,{error:String(err?.message ?? err)});
    throw err;
  }
}

export async function checkModelTools(modelId:string) {
  const model=modelRow(modelId);
  if(!model) throw new Error('MODEL_NOT_FOUND');
  const provider=providerRow(model.provider_id);
  try{
    const result=await toolProbe(provider,model.provider_model_id);
    recordModelCheck(modelId,'tools',result.pass?'pass':'fail',result.latencyMs,{
      toolCall:result.toolCall,
      usage:result.usage,
    });
    if(result.pass){
      const caps=parseJson<any>(model.capabilities_json,{});
      caps.tools=true;
      db.prepare(`UPDATE models SET capabilities_json=?,updated_at=? WHERE id=?`)
        .run(JSON.stringify(caps),now(),modelId);
    }
    db.prepare(`
      UPDATE model_runtime_compatibility
      SET tools_status=?,last_tested_at=?,notes=?
      WHERE model_id=? AND runtime_id='runtime_generic_openai'
    `).run(result.pass?'pass':'fail',now(),'V0.6 automated native tool-call capability probe.',modelId);
    return result;
  }catch(err:any){
    recordModelCheck(modelId,'tools','fail',null,{error:String(err?.message ?? err)});
    throw err;
  }
}

export async function checkModelMutation(modelId:string) {
  const model=modelRow(modelId);
  if(!model) throw new Error('MODEL_NOT_FOUND');
  const provider=providerRow(model.provider_id);

  try{
    const result=await mutationProbe(provider,model.provider_model_id);
    recordModelCheck(
      modelId,
      'mutation',
      result.pass?'pass':'fail',
      result.latencyMs,
      {
        steps:result.steps,
        finalContent:result.finalContent,
        observedCalls:result.observedCalls,
        reason:(result as any).reason ?? null
      }
    );

    const caps=parseJson<any>(model.capabilities_json,{});
    caps.mutation=Boolean(result.pass);
    db.prepare(`UPDATE models SET capabilities_json=?,updated_at=? WHERE id=?`)
      .run(JSON.stringify(caps),now(),modelId);

    db.prepare(`
      UPDATE model_runtime_compatibility
      SET coding_status=?,last_tested_at=?,notes=?
      WHERE model_id=? AND runtime_id='runtime_generic_openai'
    `).run(
      result.pass?'pass':'fail',
      now(),
      result.pass
        ? 'V0.12.15 mutation probe: model completed a real read/write tool loop against an isolated temporary file.'
        : `V0.12.15 mutation probe failed: ${(result as any).reason ?? 'mutation not completed'}.`,
      modelId
    );

    return result;
  }catch(err:any){
    recordModelCheck(modelId,'mutation','fail',null,{
      error:String(err?.message ?? err)
    });
    throw err;
  }
}
