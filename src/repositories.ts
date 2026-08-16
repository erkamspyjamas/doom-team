import { db, id, now, parseJson } from './db/index.js';
import { bindCurrentSkills, copySkillBindings, skillBindingsForAgentVersion } from './skills/registry.js';

function translated(entityType:string, alias:string, field:string) {
  return `COALESCE(
    (SELECT et.value FROM entity_translations et
     WHERE et.entity_type='${entityType}'
       AND et.entity_id=${alias}.id
       AND et.locale=?
       AND et.field='${field}'
     LIMIT 1),
    ${alias}.${field}
  )`;
}

export function listProviders() {
  return db.prepare(`SELECT * FROM model_providers ORDER BY name`).all();
}

export function listModels() {
  return db.prepare(`
    SELECT m.*, p.name AS provider_name, p.base_url, p.protocol
    FROM models m JOIN model_providers p ON p.id=m.provider_id
    ORDER BY m.name
  `).all();
}

export function listRuntimes(locale='tr') {
  return db.prepare(`
    SELECT r.*,
      ${translated('runtime','r','name')} AS display_name
    FROM runtimes r
    ORDER BY display_name
  `).all(locale);
}

export function listCompatibility(locale='tr') {
  return db.prepare(`
    SELECT c.*,
      m.name AS model_name,
      COALESCE(
        (SELECT et.value FROM entity_translations et
         WHERE et.entity_type='runtime' AND et.entity_id=r.id
           AND et.locale=? AND et.field='name' LIMIT 1),
        r.name
      ) AS runtime_name,
      COALESCE(
        (SELECT et.value FROM entity_translations et
         WHERE et.entity_type='compatibility' AND et.entity_id=c.id
           AND et.locale=? AND et.field='notes' LIMIT 1),
        c.notes
      ) AS display_notes
    FROM model_runtime_compatibility c
    JOIN models m ON m.id=c.model_id
    JOIN runtimes r ON r.id=c.runtime_id
    ORDER BY m.name, runtime_name
  `).all(locale,locale);
}

export function listTemplates(locale='tr') {
  return db.prepare(`
    SELECT at.*,
      ${translated('agent_template','at','name')} AS display_name,
      ${translated('agent_template','at','description')} AS display_description
    FROM agent_templates at
    WHERE enabled=1
    ORDER BY category, display_name
  `).all(locale,locale)
    .map((x:any)=>({...x, definition: parseJson(x.definition_json,{})}));
}

export function listAgents(locale='tr') {
  return db.prepare(`
    SELECT a.*, av.version_no, av.system_prompt, av.instructions,
           av.runtime_policy_json, av.model_policy_json, av.required_capabilities_json,
           av.tools_json, av.skills_json, av.autonomy_level,
           ${translated('agent','a','name')} AS display_name,
           ${translated('agent','a','description')} AS display_description
    FROM agents a
    LEFT JOIN agent_versions av ON av.id=a.current_version_id
    ORDER BY display_name
  `).all(locale,locale).map((x:any)=>({
    ...x,
    runtime_policy: parseJson(x.runtime_policy_json,{}),
    model_policy: parseJson(x.model_policy_json,{}),
    required_capabilities: parseJson(x.required_capabilities_json,[]),
    tools: parseJson(x.tools_json,[]),
    skills: parseJson(x.skills_json,[]),
  }));
}

export function getAgent(agentId:string, locale='tr') {
  const agent:any = db.prepare(`
    SELECT a.*,
      ${translated('agent','a','name')} AS display_name,
      ${translated('agent','a','description')} AS display_description
    FROM agents a WHERE a.id=?
  `).get(locale,locale,agentId);
  if (!agent) return null;
  const version:any = agent.current_version_id
    ? db.prepare(`SELECT * FROM agent_versions WHERE id=?`).get(agent.current_version_id)
    : null;
  return {
    ...agent,
    tags: parseJson(agent.tags_json,[]),
    version: version ? {
      ...version,
      runtimePolicy: parseJson(version.runtime_policy_json,{}),
      modelPolicy: parseJson(version.model_policy_json,{}),
      requiredCapabilities: parseJson(version.required_capabilities_json,[]),
      tools: parseJson(version.tools_json,[]),
      legacySkillTags: parseJson(version.skills_json,[]),
      skillBindings: skillBindingsForAgentVersion(version.id),
      memoryPolicy: parseJson(version.memory_policy_json,{}),
      permissionPolicy: parseJson(version.permission_policy_json,{}),
    } : null,
  };
}

export function createAgent(input:any) {
  const t=now();
  const agentId=id('agent');
  const versionId=id('agentver');
  const slug=(input.slug || input.name.toLowerCase().replace(/[^a-z0-9çğıöşü]+/gi,'-').replace(/^-|-$/g,''))
    + '-' + agentId.slice(-6);
  const trx=db.transaction(()=>{
    db.prepare(`
      INSERT INTO agents
      (id,name,slug,description,current_version_id,enabled,lifetime,tags_json,created_at,updated_at)
      VALUES (?,?,?,?,?,1,?,?,?,?)
    `).run(
      agentId,input.name,slug,input.description ?? '',versionId,
      input.lifetime ?? 'permanent',JSON.stringify(input.tags ?? []),t,t
    );
    db.prepare(`
      INSERT INTO agent_versions
      (id,agent_id,version_no,system_prompt,instructions,runtime_policy_json,model_policy_json,
       required_capabilities_json,tools_json,skills_json,memory_policy_json,permission_policy_json,
       autonomy_level,created_at,created_by)
      VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      versionId,agentId,input.systemPrompt,input.instructions ?? '',
      JSON.stringify(input.runtimePolicy ?? {}),
      JSON.stringify(input.modelPolicy ?? {}),
      JSON.stringify(input.requiredCapabilities ?? []),
      JSON.stringify(input.tools ?? []),
      JSON.stringify(input.skills ?? []),
      JSON.stringify(input.memoryPolicy ?? {}),
      JSON.stringify(input.permissionPolicy ?? {}),
      Number(input.autonomyLevel ?? 2),
      t,String(input.createdBy ?? 'boss')
    );
    if(Array.isArray(input.skillIds) && input.skillIds.length) bindCurrentSkills(versionId,input.skillIds);
  });
  trx();
  return getAgent(agentId);
}

export function newAgentVersion(agentId:string,input:any) {
  const agent:any=db.prepare(`SELECT * FROM agents WHERE id=?`).get(agentId);
  if(!agent) throw new Error('Agent not found');
  const last:any=db.prepare(`SELECT MAX(version_no) n FROM agent_versions WHERE agent_id=?`).get(agentId);
  const versionNo=Number(last?.n ?? 0)+1;
  const versionId=id('agentver');
  const t=now();
  const current:any=agent.current_version_id
    ? db.prepare(`SELECT * FROM agent_versions WHERE id=?`).get(agent.current_version_id)
    : null;

  const pick=(key:string,fallback:any)=> input[key] !== undefined ? input[key] : fallback;

  db.transaction(()=>{
    db.prepare(`
      INSERT INTO agent_versions
      (id,agent_id,version_no,system_prompt,instructions,runtime_policy_json,model_policy_json,
       required_capabilities_json,tools_json,skills_json,memory_policy_json,permission_policy_json,
       autonomy_level,created_at,created_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      versionId,agentId,versionNo,
      pick('systemPrompt',current?.system_prompt ?? ''),
      pick('instructions',current?.instructions ?? ''),
      JSON.stringify(pick('runtimePolicy',parseJson(current?.runtime_policy_json,{}))),
      JSON.stringify(pick('modelPolicy',parseJson(current?.model_policy_json,{}))),
      JSON.stringify(pick('requiredCapabilities',parseJson(current?.required_capabilities_json,[]))),
      JSON.stringify(pick('tools',parseJson(current?.tools_json,[]))),
      JSON.stringify(pick('skills',parseJson(current?.skills_json,[]))),
      JSON.stringify(pick('memoryPolicy',parseJson(current?.memory_policy_json,{}))),
      JSON.stringify(pick('permissionPolicy',parseJson(current?.permission_policy_json,{}))),
      Number(pick('autonomyLevel',current?.autonomy_level ?? 2)),
      t,'boss'
    );
    if(Array.isArray(input.skillIds)) bindCurrentSkills(versionId,input.skillIds);
    else if(current?.id) copySkillBindings(current.id,versionId);
    db.prepare(`UPDATE agents SET current_version_id=?,updated_at=? WHERE id=?`)
      .run(versionId,t,agentId);
  })();
  return getAgent(agentId);
}

export function deleteAgent(agentId:string) {
  db.prepare(`DELETE FROM agents WHERE id=?`).run(agentId);
}

export function listTeams(locale='tr') {
  const teams = db.prepare(`
    SELECT t.*,
      COALESCE(
        (SELECT et.value FROM entity_translations et
         WHERE et.entity_type='agent' AND et.entity_id=a.id
           AND et.locale=? AND et.field='name' LIMIT 1),
        a.name
      ) AS lead_agent_name,
      ${translated('team','t','name')} AS display_name,
      ${translated('team','t','description')} AS display_description
    FROM teams t
    LEFT JOIN agents a ON a.id=t.lead_agent_id
    ORDER BY display_name
  `).all(locale,locale,locale) as any[];

  const membersStmt = db.prepare(`
    SELECT tm.*, a.name AS agent_name, a.slug AS agent_slug,
      ${translated('agent','a','name')} AS display_agent_name
    FROM team_members tm
    JOIN agents a ON a.id=tm.agent_id
    WHERE tm.team_id=?
    ORDER BY tm.priority, display_agent_name
  `);

  return teams.map(team => ({
    ...team,
    members: membersStmt.all(locale,team.id),
  }));
}


export function listAgentVersions(agentId:string) {
  const agent:any=db.prepare(`SELECT id,current_version_id FROM agents WHERE id=?`).get(agentId);
  if(!agent) throw new Error('AGENT_NOT_FOUND');

  return (db.prepare(`
    SELECT * FROM agent_versions
    WHERE agent_id=?
    ORDER BY version_no DESC
  `).all(agentId) as any[]).map(v=>({
    ...v,
    isCurrent:v.id===agent.current_version_id,
    runtimePolicy:parseJson(v.runtime_policy_json,{}),
    modelPolicy:parseJson(v.model_policy_json,{}),
    requiredCapabilities:parseJson(v.required_capabilities_json,[]),
    tools:parseJson(v.tools_json,[]),
    legacySkillTags:parseJson(v.skills_json,[]),
    skillBindings:skillBindingsForAgentVersion(v.id),
    memoryPolicy:parseJson(v.memory_policy_json,{}),
    permissionPolicy:parseJson(v.permission_policy_json,{}),
  }));
}

export function activateAgentVersion(agentId:string, versionId:string) {
  const version:any=db.prepare(`
    SELECT id,agent_id,version_no FROM agent_versions
    WHERE id=? AND agent_id=?
  `).get(versionId,agentId);
  if(!version) throw new Error('AGENT_VERSION_NOT_FOUND');

  db.prepare(`UPDATE agents SET current_version_id=?,updated_at=? WHERE id=?`)
    .run(versionId,now(),agentId);

  return getAgent(agentId);
}

export function updateAgentLocalizedProfile(
  agentId:string,
  locale:string,
  input:{name?:string;description?:string;lifetime?:string;tags?:string[];enabled?:boolean}
) {
  const agent:any=db.prepare(`SELECT * FROM agents WHERE id=?`).get(agentId);
  if(!agent) throw new Error('AGENT_NOT_FOUND');
  const t=now();

  db.transaction(()=>{
    if(input.lifetime!==undefined || input.tags!==undefined || input.enabled!==undefined){
      db.prepare(`
        UPDATE agents
        SET lifetime=?,
            tags_json=?,
            enabled=?,
            updated_at=?
        WHERE id=?
      `).run(
        input.lifetime ?? agent.lifetime,
        JSON.stringify(input.tags ?? parseJson(agent.tags_json,[])),
        input.enabled===undefined ? agent.enabled : (input.enabled ? 1 : 0),
        t,
        agentId
      );
    }

    const upsert=db.prepare(`
      INSERT INTO entity_translations(entity_type,entity_id,locale,field,value,updated_at)
      VALUES ('agent',?,?,?,?,?)
      ON CONFLICT(entity_type,entity_id,locale,field)
      DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    `);

    if(typeof input.name==='string' && input.name.trim()){
      upsert.run(agentId,locale,'name',input.name.trim(),t);
    }
    if(typeof input.description==='string'){
      upsert.run(agentId,locale,'description',input.description.trim(),t);
    }
  })();

  return getAgent(agentId,locale);
}
