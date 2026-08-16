import { db, id, now, parseJson } from './db/index.js';

export type ModelPolicyDefinition = {
  location?: 'local'|'cloud'|'any';
  require?: {
    chat?: boolean;
    tools?: boolean;
    vision?: boolean;
    reasoning?: boolean;
    structuredOutput?: boolean;
    coding?: boolean;
  };
  preferredLabels?: string[];
  preferredCapabilities?: string[];
  preferLowerLatency?: boolean;
  preferLargerContext?: boolean;
  fallbackModelIds?: string[];
};

function policyRow(policyId:string) {
  return db.prepare(`SELECT * FROM model_policies WHERE id=?`).get(policyId) as any;
}

export function listModelPolicies() {
  return (db.prepare(`
    SELECT p.*,v.version_no,v.definition_json,v.created_at AS version_created_at
    FROM model_policies p
    LEFT JOIN model_policy_versions v ON v.id=p.current_version_id
    ORDER BY p.name
  `).all() as any[]).map(x=>({
    ...x,
    definition:parseJson<ModelPolicyDefinition>(x.definition_json,{})
  }));
}

export function getModelPolicy(policyId:string) {
  const p:any=db.prepare(`
    SELECT p.*,v.version_no,v.definition_json,v.created_at AS version_created_at
    FROM model_policies p
    LEFT JOIN model_policy_versions v ON v.id=p.current_version_id
    WHERE p.id=?
  `).get(policyId);
  if(!p)return null;
  return {...p,definition:parseJson<ModelPolicyDefinition>(p.definition_json,{})};
}

export function listModelPolicyVersions(policyId:string) {
  if(!policyRow(policyId)) throw new Error('MODEL_POLICY_NOT_FOUND');
  const current=(policyRow(policyId) as any).current_version_id;
  return (db.prepare(`
    SELECT * FROM model_policy_versions WHERE policy_id=? ORDER BY version_no DESC
  `).all(policyId) as any[]).map(x=>({
    ...x,
    isCurrent:x.id===current,
    definition:parseJson<ModelPolicyDefinition>(x.definition_json,{})
  }));
}

export function createModelPolicy(input:any) {
  const t=now();
  const policyId=id('policy');
  const versionId=id('policyver');
  db.transaction(()=>{
    db.prepare(`
      INSERT INTO model_policies
      (id,name,description,current_version_id,enabled,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?)
    `).run(
      policyId,
      String(input.name ?? '').trim(),
      String(input.description ?? ''),
      versionId,
      input.enabled===false?0:1,
      t,t
    );
    db.prepare(`
      INSERT INTO model_policy_versions
      (id,policy_id,version_no,definition_json,created_at,created_by)
      VALUES (?,?,1,?,?,'boss')
    `).run(versionId,policyId,JSON.stringify(input.definition ?? {}),t);
  })();
  return getModelPolicy(policyId);
}

export function newModelPolicyVersion(policyId:string,input:any) {
  const p=policyRow(policyId);
  if(!p) throw new Error('MODEL_POLICY_NOT_FOUND');
  const last:any=db.prepare(`SELECT MAX(version_no) n FROM model_policy_versions WHERE policy_id=?`).get(policyId);
  const versionNo=Number(last?.n ?? 0)+1;
  const versionId=id('policyver');
  const t=now();
  db.transaction(()=>{
    db.prepare(`
      INSERT INTO model_policy_versions
      (id,policy_id,version_no,definition_json,created_at,created_by)
      VALUES (?,?,?,?,?,'boss')
    `).run(versionId,policyId,versionNo,JSON.stringify(input.definition ?? {}),t);
    db.prepare(`
      UPDATE model_policies SET
        name=COALESCE(?,name),
        description=COALESCE(?,description),
        enabled=COALESCE(?,enabled),
        current_version_id=?,
        updated_at=?
      WHERE id=?
    `).run(
      input.name!==undefined?String(input.name):null,
      input.description!==undefined?String(input.description):null,
      input.enabled!==undefined?(input.enabled?1:0):null,
      versionId,t,policyId
    );
  })();
  return getModelPolicy(policyId);
}

export function activateModelPolicyVersion(policyId:string,versionId:string) {
  const v:any=db.prepare(`
    SELECT id FROM model_policy_versions WHERE id=? AND policy_id=?
  `).get(versionId,policyId);
  if(!v) throw new Error('MODEL_POLICY_VERSION_NOT_FOUND');
  db.prepare(`UPDATE model_policies SET current_version_id=?,updated_at=? WHERE id=?`)
    .run(versionId,now(),policyId);
  return getModelPolicy(policyId);
}

export function deleteModelPolicy(policyId:string) {
  const p=policyRow(policyId);
  if(!p) throw new Error('MODEL_POLICY_NOT_FOUND');

  const versions=db.prepare(`SELECT id FROM model_policy_versions WHERE policy_id=?`).all(policyId) as any[];
  const ids=new Set(versions.map(x=>x.id));
  const agentVersions=db.prepare(`SELECT model_policy_json FROM agent_versions`).all() as any[];
  for(const row of agentVersions){
    const policy=parseJson<any>(row.model_policy_json,{});
    if(policy?.policyId===policyId || (policy?.policyVersionId && ids.has(policy.policyVersionId))){
      throw new Error('MODEL_POLICY_USED_BY_AGENT_VERSION');
    }
  }

  db.prepare(`DELETE FROM model_policies WHERE id=?`).run(policyId);
  return {ok:true};
}
