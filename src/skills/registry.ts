import { createHash } from 'node:crypto';
import { db, id, now, parseJson } from '../db/index.js';
import type { DoomSkillManifest, DoomSkillPackageV1, RuntimeSkill } from './types.js';

const MAX_SKILL_FILES = 100;
const MAX_SKILL_FILE_BYTES = 256 * 1024;
const MAX_SKILL_TOTAL_BYTES = 1024 * 1024;

function slugify(value:string) {
  return value.toLowerCase().trim()
    .normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[^a-z0-9._-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,80);
}

function safeFilePath(raw:string) {
  const path=raw.replace(/\\/g,'/').replace(/^\.\//,'');
  if(!path || path.startsWith('/') || path.split('/').some(p=>p==='..' || p==='')) {
    throw new Error(`SKILL_FILE_PATH_INVALID:${raw}`);
  }
  if(path.includes('\0')) throw new Error('SKILL_FILE_PATH_INVALID');
  return path;
}

function normalizeFiles(input:Record<string,string>|undefined) {
  const entries=Object.entries(input ?? {});
  if(entries.length>MAX_SKILL_FILES) throw new Error('SKILL_TOO_MANY_FILES');
  const files:Record<string,string>={};
  let total=0;
  for(const [raw,value] of entries){
    if(typeof value!=='string') throw new Error(`SKILL_FILE_NOT_TEXT:${raw}`);
    const path=safeFilePath(raw);
    const bytes=Buffer.byteLength(value,'utf-8');
    if(bytes>MAX_SKILL_FILE_BYTES) throw new Error(`SKILL_FILE_TOO_LARGE:${path}`);
    total+=bytes;
    if(total>MAX_SKILL_TOTAL_BYTES) throw new Error('SKILL_PACKAGE_TOO_LARGE');
    files[path]=value;
  }
  return files;
}

function stableHash(value:unknown) {
  const canonical=JSON.stringify(value,(_k,v)=>{
    if(v && typeof v==='object' && !Array.isArray(v)){
      return Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b)));
    }
    return v;
  });
  return createHash('sha256').update(canonical,'utf-8').digest('hex');
}

export function normalizeSkillPackage(raw:any):DoomSkillPackageV1 {
  const input=raw?.format==='doom-skill/v1' ? raw : {format:'doom-skill/v1',skill:raw?.skill ?? raw,files:raw?.files};
  const files=normalizeFiles(input.files);
  let instructions=typeof input.skill?.instructions==='string' ? input.skill.instructions.trim() : '';
  if(!instructions && typeof files['SKILL.md']==='string') instructions=files['SKILL.md'].trim();
  if(!instructions) throw new Error('SKILL_INSTRUCTIONS_REQUIRED');
  const name=String(input.skill?.name ?? '').trim();
  if(!name) throw new Error('SKILL_NAME_REQUIRED');
  const slug=slugify(String(input.skill?.slug || name));
  if(!slug) throw new Error('SKILL_SLUG_INVALID');
  const manifest:DoomSkillManifest={
    name, slug,
    version:String(input.skill?.version || '1.0.0'),
    description:String(input.skill?.description || '').trim(),
    requiredTools:Array.isArray(input.skill?.requiredTools)?input.skill.requiredTools.map(String):[],
    recommendedCapabilities:Array.isArray(input.skill?.recommendedCapabilities)?input.skill.recommendedCapabilities.map(String):[],
    tags:Array.isArray(input.skill?.tags)?input.skill.tags.map(String):[],
    source:input.skill?.source ? String(input.skill.source) : undefined,
    metadata:input.skill?.metadata && typeof input.skill.metadata==='object' ? input.skill.metadata : {},
  };
  return {format:'doom-skill/v1',skill:{...manifest,instructions},files};
}

export function importSkillPackage(raw:any, createdBy='boss', sourceType='external') {
  const pkg=normalizeSkillPackage(raw);
  const manifest={...pkg.skill}; delete (manifest as any).instructions;
  const instructions=String(pkg.skill.instructions || '');
  const hash=stableHash({manifest,instructions,files:pkg.files ?? {}});
  const t=now();
  let skill:any=db.prepare(`SELECT * FROM skills WHERE slug=?`).get(pkg.skill.slug);
  if(skill){
    const duplicate:any=db.prepare(`SELECT * FROM skill_versions WHERE skill_id=? AND content_hash=?`).get(skill.id,hash);
    if(duplicate){
      return {created:false,duplicate:true,skill:getSkill(skill.id),version:duplicate};
    }
  }
  const skillId=skill?.id ?? id('skill');
  const last:any=skill ? db.prepare(`SELECT MAX(version_no) n FROM skill_versions WHERE skill_id=?`).get(skillId) : null;
  const versionNo=Number(last?.n ?? 0)+1;
  const versionId=id('skillver');
  db.transaction(()=>{
    if(!skill){
      db.prepare(`INSERT INTO skills(id,slug,name,description,current_version_id,enabled,source_type,source_uri,tags_json,created_at,updated_at)
        VALUES (?,?,?,?,?,1,?,?,?, ?,?)`).run(
        skillId,pkg.skill.slug,pkg.skill.name,pkg.skill.description ?? '',versionId,sourceType,pkg.skill.source ?? null,
        JSON.stringify(pkg.skill.tags ?? []),t,t
      );
    }else{
      db.prepare(`UPDATE skills SET name=?,description=?,current_version_id=?,enabled=1,source_type=?,source_uri=?,tags_json=?,updated_at=? WHERE id=?`)
        .run(pkg.skill.name,pkg.skill.description ?? '',versionId,sourceType,pkg.skill.source ?? skill.source_uri,
          JSON.stringify(pkg.skill.tags ?? []),t,skillId);
    }
    db.prepare(`INSERT INTO skill_versions(id,skill_id,version_no,package_version,instructions_md,manifest_json,files_json,content_hash,created_at,created_by)
      VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
        versionId,skillId,versionNo,pkg.skill.version ?? '1.0.0',instructions,JSON.stringify(manifest),JSON.stringify(pkg.files ?? {}),hash,t,createdBy
      );
  })();
  return {created:true,duplicate:false,skill:getSkill(skillId),versionId};
}

export function listSkills(){
  return (db.prepare(`SELECT s.*,sv.version_no,sv.package_version,sv.content_hash,sv.manifest_json,sv.created_at AS version_created_at
    FROM skills s LEFT JOIN skill_versions sv ON sv.id=s.current_version_id ORDER BY s.name`).all() as any[])
    .map(x=>({...x,tags:parseJson(x.tags_json,[]),manifest:parseJson(x.manifest_json,{})}));
}

export function getSkill(skillId:string){
  const skill:any=db.prepare(`SELECT * FROM skills WHERE id=?`).get(skillId);
  if(!skill) return null;
  const version:any=skill.current_version_id ? db.prepare(`SELECT * FROM skill_versions WHERE id=?`).get(skill.current_version_id):null;
  return {...skill,tags:parseJson(skill.tags_json,[]),version:version?{
    ...version,manifest:parseJson(version.manifest_json,{}),files:parseJson(version.files_json,{})
  }:null};
}

export function listSkillVersions(skillId:string){
  const skill:any=db.prepare(`SELECT current_version_id FROM skills WHERE id=?`).get(skillId);
  if(!skill) throw new Error('SKILL_NOT_FOUND');
  return (db.prepare(`SELECT * FROM skill_versions WHERE skill_id=? ORDER BY version_no DESC`).all(skillId) as any[])
    .map(v=>({...v,isCurrent:v.id===skill.current_version_id,manifest:parseJson(v.manifest_json,{}),files:parseJson(v.files_json,{})}));
}

export function activateSkillVersion(skillId:string,versionId:string){
  const v:any=db.prepare(`SELECT * FROM skill_versions WHERE id=? AND skill_id=?`).get(versionId,skillId);
  if(!v) throw new Error('SKILL_VERSION_NOT_FOUND');
  db.prepare(`UPDATE skills SET current_version_id=?,updated_at=? WHERE id=?`).run(versionId,now(),skillId);
  return getSkill(skillId);
}

export function exportSkillPackage(skillId:string,versionId?:string):DoomSkillPackageV1 {
  const skill:any=db.prepare(`SELECT * FROM skills WHERE id=?`).get(skillId);
  if(!skill) throw new Error('SKILL_NOT_FOUND');
  const target=versionId || skill.current_version_id;
  const v:any=db.prepare(`SELECT * FROM skill_versions WHERE id=? AND skill_id=?`).get(target,skillId);
  if(!v) throw new Error('SKILL_VERSION_NOT_FOUND');
  const manifest=parseJson<any>(v.manifest_json,{});
  return {format:'doom-skill/v1',skill:{...manifest,name:skill.name,slug:skill.slug,version:v.package_version,description:skill.description,instructions:v.instructions_md},files:parseJson(v.files_json,{})};
}

export function setSkillEnabled(skillId:string,enabled:boolean){
  const info=db.prepare(`UPDATE skills SET enabled=?,updated_at=? WHERE id=?`).run(enabled?1:0,now(),skillId);
  if(!info.changes) throw new Error('SKILL_NOT_FOUND');
  return getSkill(skillId);
}

export function bindCurrentSkills(agentVersionId:string,skillIds:string[]){
  const unique=[...new Set((skillIds ?? []).map(String))];
  db.prepare(`DELETE FROM agent_version_skills WHERE agent_version_id=?`).run(agentVersionId);
  const insert=db.prepare(`INSERT INTO agent_version_skills(agent_version_id,skill_version_id,priority,enabled) VALUES (?,?,?,1)`);
  let priority=100;
  for(const skillId of unique){
    const skill:any=db.prepare(`SELECT id,current_version_id,enabled FROM skills WHERE id=?`).get(skillId);
    if(!skill || !skill.enabled || !skill.current_version_id) throw new Error(`SKILL_NOT_AVAILABLE:${skillId}`);
    insert.run(agentVersionId,skill.current_version_id,priority);
    priority+=10;
  }
}

export function copySkillBindings(fromAgentVersionId:string,toAgentVersionId:string){
  db.prepare(`INSERT INTO agent_version_skills(agent_version_id,skill_version_id,priority,enabled)
    SELECT ?,skill_version_id,priority,enabled FROM agent_version_skills WHERE agent_version_id=?`).run(toAgentVersionId,fromAgentVersionId);
}

export function skillBindingsForAgentVersion(agentVersionId:string){
  return (db.prepare(`SELECT avs.priority,avs.enabled,s.id AS skill_id,s.slug,s.name,s.description,
      sv.id AS skill_version_id,sv.version_no,sv.package_version,sv.content_hash,sv.manifest_json
    FROM agent_version_skills avs
    JOIN skill_versions sv ON sv.id=avs.skill_version_id
    JOIN skills s ON s.id=sv.skill_id
    WHERE avs.agent_version_id=? ORDER BY avs.priority,s.name`).all(agentVersionId) as any[])
    .map(x=>({...x,manifest:parseJson(x.manifest_json,{})}));
}

export function runtimeSkillsForAgentVersion(agentVersionId:string):RuntimeSkill[]{
  const rows=(db.prepare(`SELECT s.id AS skill_id,s.slug,s.name,s.description,s.enabled AS skill_enabled,sv.*
    FROM agent_version_skills avs
    JOIN skill_versions sv ON sv.id=avs.skill_version_id
    JOIN skills s ON s.id=sv.skill_id
    WHERE avs.agent_version_id=? AND avs.enabled=1
    ORDER BY avs.priority,s.name`).all(agentVersionId) as any[]);
  const disabled=rows.find(x=>!x.skill_enabled);
  if(disabled) throw new Error(`SKILL_DISABLED:${disabled.slug}`);
  return rows.map(x=>({
      skillId:x.skill_id,skillVersionId:x.id,slug:x.slug,name:x.name,description:x.description ?? '',
      versionNo:Number(x.version_no),packageVersion:x.package_version,instructions:x.instructions_md,
      manifest:parseJson<DoomSkillManifest>(x.manifest_json,{name:x.name,slug:x.slug}),
      files:parseJson<Record<string,string>>(x.files_json,{}),contentHash:x.content_hash,
    }));
}
