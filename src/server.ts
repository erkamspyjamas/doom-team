import Fastify from 'fastify';
import staticPlugin from '@fastify/static';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { z } from 'zod';
import { loadConfig } from './config.js';
import { readFileSync } from 'node:fs';
import {
  listDiagnosticTests,runDiagnostics,missionCreatePreflight
} from './diagnostics.js';
import { db, now, getSetting, setSetting } from './db/index.js';
import {
  listProviders,listModels,listRuntimes,listCompatibility,listTemplates,
  listAgents,getAgent,createAgent,newAgentVersion,deleteAgent,listTeams,listAgentVersions,activateAgentVersion,updateAgentLocalizedProfile
} from './repositories.js';
import { installedRuntimeAdapters } from './runtime/registry.js';
import { startExecution,listExecutions,getExecution,executionEvents,getRouteDecision,cancelExecution } from './executions.js';
import { availableLocales, loadLocale, localeExists } from './i18n/index.js';
import {
  providersDetailed,modelsDetailed,createProvider,updateProvider,deleteProvider,checkProvider,discoverProviderModels,
  createModel,updateModel,deleteModel,checkModelChat,checkModelTools,checkModelMutation
} from './model-registry.js';
import { publicToolCatalog } from './tools/catalog.js';
import {
  listModelPolicies,getModelPolicy,listModelPolicyVersions,createModelPolicy,newModelPolicyVersion,
  activateModelPolicyVersion,deleteModelPolicy
} from './model-policies.js';
import { routeModel } from './model-router.js';
import {
  listApprovals,getApproval,approveApproval,rejectApproval
} from './approvals.js';
import {
  listMessages,sendBossMessage,steerExecution,listExecutionDirectives,missionArtifacts,taskArtifacts
} from './communications.js';
import {
  spawnMissionSpecialist,listMissionSpecialists,listFactoryRequests,
  archiveMissionSpecialist
} from './agent-factory.js';
import { listSkills,getSkill,listSkillVersions,importSkillPackage,activateSkillVersion,exportSkillPackage,setSkillEnabled } from './skills/registry.js';
import {
  createMission,listMissions,getMission,planMission,replanMission,runMission,pauseMission,retryMissionTask,reassignMissionTask,cancelMissionTask,stopMission,missionEvents,resumeAutonomousMissions
} from './missions.js';


// Global error handlers to prevent process crashes
process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

process.on('uncaughtException', (error) => {
  console.error('Uncaught Exception:', error);
});

const cfg=loadConfig();
const app=Fastify({logger:true});
const __dirname=fileURLToPath(new URL('.',import.meta.url));
let version = '0.12.19';
try {
  const packageJson = JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf-8'));
  version = packageJson.version;
} catch (err) {
  console.warn('Could not read package.json for version:', err);
}

await app.register(staticPlugin,{
  root:join(__dirname,'..','public'),
  prefix:'/',
  wildcard:false,
});

function uiLocale(requested?:string) {
  if (requested && localeExists(requested)) return requested;
  return getSetting('ui_locale',cfg.defaultLocale);
}

app.get('/health',async()=>({
  status:'ok',
  doom:'online',
  version:'0.12.19',
  db:cfg.dbPath,
  defaultLocale:cfg.defaultLocale,
  installedRuntimeAdapters:installedRuntimeAdapters(),
}));

app.get('/api/i18n/locales',async()=>({items:availableLocales()}));

app.get<{Params:{locale:string}}>('/api/i18n/:locale',async(req,reply)=>{
  if(!localeExists(req.params.locale)) {
    return reply.code(404).send({code:'LOCALE_NOT_FOUND'});
  }
  return loadLocale(req.params.locale);
});

app.get('/api/settings',async()=>({
  uiLocale:getSetting('ui_locale',cfg.defaultLocale),
  agentResponseLocale:getSetting('agent_response_locale',cfg.defaultLocale),
}));

app.put<{Body:{uiLocale?:string;agentResponseLocale?:string}}>('/api/settings',async(req,reply)=>{
  const ui=req.body?.uiLocale;
  const agent=req.body?.agentResponseLocale;

  if(ui && !localeExists(ui)) {
    return reply.code(400).send({code:'UNSUPPORTED_UI_LOCALE'});
  }
  if(agent && !localeExists(agent)) {
    return reply.code(400).send({code:'UNSUPPORTED_AGENT_LOCALE'});
  }

  if(ui) setSetting('ui_locale',ui);
  if(agent) setSetting('agent_response_locale',agent);

  return {
    uiLocale:getSetting('ui_locale',cfg.defaultLocale),
    agentResponseLocale:getSetting('agent_response_locale',cfg.defaultLocale),
  };
});




app.get<{Querystring:{missionId?:string;limit?:string}}>('/api/agent-factory/requests',async(req)=>({
  items:listFactoryRequests(req.query.missionId,Number(req.query.limit ?? 200))
}));

app.get<{Params:{id:string}}>('/api/missions/:id/specialists',async(req)=>({
  items:listMissionSpecialists(req.params.id)
}));

app.post<{
  Params:{id:string};
  Body:{
    name?:string;roleName?:string;objective?:string;scopeType?:string;assignTaskKey?:string|null;
    toolProfiles?:string[];skillIds?:string[];requiredCapabilities?:string[];
    modelStrategy?:string;autonomyLevel?:number
  }
}>('/api/missions/:id/specialists',async(req,reply)=>{
  try{
    return spawnMissionSpecialist({
      missionId:req.params.id,
      requestedByType:'boss',
      name:String(req.body?.name ?? ''),
      roleName:String(req.body?.roleName ?? ''),
      objective:String(req.body?.objective ?? ''),
      scopeType:String(req.body?.scopeType ?? 'mission'),
      assignTaskKey:req.body?.assignTaskKey ?? null,
      toolProfiles:req.body?.toolProfiles,
      skillIds:req.body?.skillIds,
      requiredCapabilities:req.body?.requiredCapabilities,
      modelStrategy:req.body?.modelStrategy,
      autonomyLevel:req.body?.autonomyLevel
    });
  }catch(err:any){
    return reply.code(409).send({code:'AGENT_FACTORY_SPAWN_FAILED',details:String(err?.message??err)});
  }
});

app.post<{Params:{id:string;agentId:string};Body:{reason?:string}}>('/api/missions/:id/specialists/:agentId/archive',async(req,reply)=>{
  try{return archiveMissionSpecialist(req.params.id,req.params.agentId,String(req.body?.reason ?? 'boss_archive'))}
  catch(err:any){return reply.code(409).send({code:'AGENT_FACTORY_ARCHIVE_FAILED',details:String(err?.message??err)})}
});

app.get<{Querystring:{status?:string;limit?:string}}>('/api/approvals',async(req)=>({
  items:listApprovals(req.query.status,Number(req.query.limit ?? 100))
}));

app.get<{Params:{id:string}}>('/api/approvals/:id',async(req,reply)=>{
  const approval=getApproval(req.params.id);
  if(!approval)return reply.code(404).send({code:'APPROVAL_NOT_FOUND'});
  return approval;
});

app.post<{Params:{id:string};Body:{note?:string}}>('/api/approvals/:id/approve',async(req,reply)=>{
  try{return await approveApproval(req.params.id,String(req.body?.note ?? ''))}
  catch(err:any){return reply.code(409).send({code:'APPROVAL_APPROVE_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string};Body:{note?:string}}>('/api/approvals/:id/reject',async(req,reply)=>{
  try{return rejectApproval(req.params.id,String(req.body?.note ?? ''))}
  catch(err:any){return reply.code(409).send({code:'APPROVAL_REJECT_FAILED',details:String(err?.message??err)})}
});


app.get<{Querystring:{missionId?:string;taskId?:string;executionId?:string;limit?:string}}>('/api/messages',async(req)=>({
  items:listMessages({
    missionId:req.query.missionId,
    taskId:req.query.taskId,
    executionId:req.query.executionId,
    limit:Number(req.query.limit ?? 150)
  })
}));

app.get<{Params:{id:string};Querystring:{limit?:string}}>('/api/missions/:id/messages',async(req)=>({
  items:listMessages({missionId:req.params.id,limit:Number(req.query.limit ?? 200)})
}));

app.post<{
  Params:{id:string};
  Body:{recipientType?:string;recipientId?:string;content?:string;priority?:string;messageType?:string}
}>('/api/missions/:id/messages',async(req,reply)=>{
  try{
    const recipientType=String(req.body?.recipientType ?? 'mission');
    const recipientId=String(req.body?.recipientId ?? req.params.id);
    return sendBossMessage({
      missionId:req.params.id,
      recipientType,
      recipientId,
      content:String(req.body?.content ?? ''),
      priority:req.body?.priority,
      messageType:req.body?.messageType
    });
  }catch(err:any){
    return reply.code(409).send({code:'MISSION_MESSAGE_FAILED',details:String(err?.message??err)});
  }
});

app.post<{Params:{id:string};Body:{content?:string}}>('/api/executions/:id/steer',async(req,reply)=>{
  try{return steerExecution(req.params.id,String(req.body?.content ?? ''),'boss')}
  catch(err:any){return reply.code(409).send({code:'EXECUTION_STEER_FAILED',details:String(err?.message??err)})}
});

app.get<{Params:{id:string};Querystring:{limit?:string}}>('/api/executions/:id/directives',async(req)=>({
  items:listExecutionDirectives(req.params.id,Number(req.query.limit ?? 100))
}));

app.get<{Params:{id:string}}>('/api/missions/:id/artifacts',async(req)=>({
  items:missionArtifacts(req.params.id,500)
}));

app.get<{Params:{id:string;taskId:string}}>('/api/missions/:id/tasks/:taskId/artifacts',async(req)=>({
  items:taskArtifacts(req.params.taskId)
}));


app.get('/api/diagnostics/tests',async()=>({items:listDiagnosticTests()}));

app.post<{Body:{ids?:string[]}}>('/api/diagnostics/run',async(req,reply)=>{
  try{return await runDiagnostics(Array.isArray(req.body?.ids)?req.body.ids:undefined)}
  catch(err:any){return reply.code(500).send({code:'DIAGNOSTICS_RUN_FAILED',details:String(err?.message??err)})}
});

app.post<{Body:{title?:string;objective?:string;teamId?:string;leadAgentId?:string}}>('/api/diagnostics/mission-preflight',async(req)=>{
  return missionCreatePreflight(req.body ?? {});
});

app.get('/api/missions',async()=>({items:listMissions()}));

app.get<{Params:{id:string}}>('/api/missions/:id',async(req,reply)=>{
  const mission=getMission(req.params.id);
  if(!mission)return reply.code(404).send({code:'MISSION_NOT_FOUND'});
  return mission;
});

app.post('/api/missions',async(req:any,reply)=>{
  try{return createMission(req.body ?? {})}
  catch(err:any){
    const details=String(err?.message??err);
    req.log.warn({
      code:'MISSION_CREATE_FAILED',
      details,
      teamId:String(req.body?.teamId ?? ''),
      hasTitle:Boolean(String(req.body?.title ?? '').trim()),
      hasObjective:Boolean(String(req.body?.objective ?? '').trim())
    },'mission create failed');
    return reply.code(400).send({code:'MISSION_CREATE_FAILED',details});
  }
});

app.post<{Params:{id:string}}>('/api/missions/:id/plan',async(req,reply)=>{
  try{return await planMission(req.params.id)}
  catch(err:any){return reply.code(409).send({code:'MISSION_PLAN_FAILED',details:String(err?.message??err)})}
});


app.post<{Params:{id:string}}>('/api/missions/:id/replan',async(req,reply)=>{
  try{return await replanMission(req.params.id)}
  catch(err:any){
    const details=String(err?.message??err);
    req.log.warn({code:'MISSION_REPLAN_FAILED',details,missionId:req.params.id},'mission replan failed');
    return reply.code(409).send({code:'MISSION_REPLAN_FAILED',details});
  }
});

app.post<{Params:{id:string}}>('/api/missions/:id/run',async(req,reply)=>{
  try{return runMission(req.params.id)}
  catch(err:any){return reply.code(409).send({code:'MISSION_RUN_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/missions/:id/pause',async(req,reply)=>{
  try{return pauseMission(req.params.id)}
  catch(err:any){return reply.code(409).send({code:'MISSION_PAUSE_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string;taskId:string}}>('/api/missions/:id/tasks/:taskId/retry',async(req,reply)=>{
  try{return retryMissionTask(req.params.id,req.params.taskId)}
  catch(err:any){return reply.code(409).send({code:'MISSION_TASK_RETRY_FAILED',details:String(err?.message??err)})}
});


app.post<{Params:{id:string;taskId:string};Body:{agentId?:string}}>('/api/missions/:id/tasks/:taskId/reassign',async(req,reply)=>{
  try{
    if(!req.body?.agentId)return reply.code(400).send({code:'AGENT_ID_REQUIRED'});
    return reassignMissionTask(req.params.id,req.params.taskId,String(req.body.agentId));
  }catch(err:any){
    return reply.code(409).send({code:'MISSION_TASK_REASSIGN_FAILED',details:String(err?.message??err)});
  }
});


app.post<{Params:{id:string}}>('/api/missions/:id/cancel',async(req,reply)=>{
  try{return await stopMission(req.params.id,'cancel')}
  catch(err:any){return reply.code(409).send({code:'MISSION_CANCEL_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/missions/:id/kill',async(req,reply)=>{
  try{return await stopMission(req.params.id,'kill')}
  catch(err:any){return reply.code(409).send({code:'MISSION_KILL_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string;taskId:string}}>('/api/missions/:id/tasks/:taskId/cancel',async(req,reply)=>{
  try{return await cancelMissionTask(req.params.id,req.params.taskId,'cancel')}
  catch(err:any){return reply.code(409).send({code:'MISSION_TASK_CANCEL_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string;taskId:string}}>('/api/missions/:id/tasks/:taskId/kill',async(req,reply)=>{
  try{return await cancelMissionTask(req.params.id,req.params.taskId,'kill')}
  catch(err:any){return reply.code(409).send({code:'MISSION_TASK_KILL_FAILED',details:String(err?.message??err)})}
});

app.get<{Params:{id:string};Querystring:{limit?:string}}>('/api/missions/:id/events',async(req)=>({
  items:missionEvents(req.params.id,Number(req.query.limit ?? 200))
}));

app.get('/api/tools',async()=>({items:publicToolCatalog()}));


app.get('/api/skills',async()=>({items:listSkills()}));
app.get<{Params:{id:string}}>('/api/skills/:id',async(req,reply)=>{
  const skill=getSkill(req.params.id); if(!skill)return reply.code(404).send({code:'SKILL_NOT_FOUND'}); return skill;
});
app.get<{Params:{id:string}}>('/api/skills/:id/versions',async(req,reply)=>{
  try{return {items:listSkillVersions(req.params.id)}}catch(err:any){return reply.code(404).send({code:'SKILL_VERSIONS_NOT_FOUND',details:String(err?.message??err)})}
});
app.post('/api/skills/import',async(req,reply)=>{
  try{return importSkillPackage(req.body,'boss','external')}
  catch(err:any){return reply.code(400).send({code:'SKILL_IMPORT_FAILED',details:String(err?.message??err)})}
});
app.post('/api/skills',async(req:any,reply)=>{
  try{return importSkillPackage({format:'doom-skill/v1',skill:req.body,files:req.body?.files ?? {}},'boss','manual')}
  catch(err:any){return reply.code(400).send({code:'SKILL_CREATE_FAILED',details:String(err?.message??err)})}
});
app.post<{Params:{id:string;versionId:string}}>('/api/skills/:id/versions/:versionId/activate',async(req,reply)=>{
  try{return activateSkillVersion(req.params.id,req.params.versionId)}catch(err:any){return reply.code(404).send({code:'SKILL_VERSION_ACTIVATE_FAILED',details:String(err?.message??err)})}
});
app.get<{Params:{id:string};Querystring:{versionId?:string}}>('/api/skills/:id/export',async(req,reply)=>{
  try{return exportSkillPackage(req.params.id,req.query.versionId)}catch(err:any){return reply.code(404).send({code:'SKILL_EXPORT_FAILED',details:String(err?.message??err)})}
});
app.patch<{Params:{id:string};Body:{enabled?:boolean}}>('/api/skills/:id',async(req,reply)=>{
  try{return setSkillEnabled(req.params.id,req.body?.enabled!==false)}catch(err:any){return reply.code(404).send({code:'SKILL_UPDATE_FAILED',details:String(err?.message??err)})}
});


app.get('/api/model-policies',async()=>({items:listModelPolicies()}));

app.get<{Params:{id:string}}>('/api/model-policies/:id',async(req,reply)=>{
  const p=getModelPolicy(req.params.id);
  if(!p)return reply.code(404).send({code:'MODEL_POLICY_NOT_FOUND'});
  return p;
});

app.get<{Params:{id:string}}>('/api/model-policies/:id/versions',async(req,reply)=>{
  try{return {items:listModelPolicyVersions(req.params.id)}}
  catch(err:any){return reply.code(404).send({code:'MODEL_POLICY_VERSIONS_NOT_FOUND',details:String(err?.message??err)})}
});

app.post('/api/model-policies',async(req:any,reply)=>{
  try{return createModelPolicy(req.body ?? {})}
  catch(err:any){return reply.code(400).send({code:'MODEL_POLICY_CREATE_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/model-policies/:id/versions',async(req:any,reply)=>{
  try{return newModelPolicyVersion(req.params.id,req.body ?? {})}
  catch(err:any){return reply.code(400).send({code:'MODEL_POLICY_VERSION_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string;versionId:string}}>('/api/model-policies/:id/versions/:versionId/activate',async(req,reply)=>{
  try{return activateModelPolicyVersion(req.params.id,req.params.versionId)}
  catch(err:any){return reply.code(404).send({code:'MODEL_POLICY_ACTIVATE_FAILED',details:String(err?.message??err)})}
});

app.delete<{Params:{id:string}}>('/api/model-policies/:id',async(req,reply)=>{
  try{return deleteModelPolicy(req.params.id)}
  catch(err:any){return reply.code(409).send({code:'MODEL_POLICY_DELETE_FAILED',details:String(err?.message??err)})}
});

app.post('/api/router/simulate',async(req:any,reply)=>{
  try{
    return routeModel({
      runtimeId:String(req.body?.runtimeId ?? 'runtime_generic_openai'),
      modelPolicy:req.body?.modelPolicy ?? {},
      requiredCapabilities:Array.isArray(req.body?.requiredCapabilities)?req.body.requiredCapabilities:[]
    });
  }catch(err:any){
    return reply.code(409).send({code:'MODEL_ROUTE_FAILED',details:String(err?.message??err)});
  }
});

app.get('/api/registry/providers',async()=>({items:providersDetailed()}));
app.get('/api/registry/models',async()=>({items:modelsDetailed()}));

app.post('/api/providers',async(req:any,reply)=>{
  try{return createProvider(req.body ?? {})}
  catch(err:any){return reply.code(400).send({code:'PROVIDER_CREATE_FAILED',details:String(err?.message??err)})}
});

app.patch<{Params:{id:string}}>('/api/providers/:id',async(req:any,reply)=>{
  try{return updateProvider(req.params.id,req.body ?? {})}
  catch(err:any){return reply.code(400).send({code:'PROVIDER_UPDATE_FAILED',details:String(err?.message??err)})}
});

app.delete<{Params:{id:string}}>('/api/providers/:id',async(req,reply)=>{
  try{return deleteProvider(req.params.id)}
  catch(err:any){return reply.code(409).send({code:'PROVIDER_DELETE_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/providers/:id/test',async(req,reply)=>{
  try{return await checkProvider(req.params.id)}
  catch(err:any){return reply.code(502).send({code:'PROVIDER_TEST_FAILED',details:String(err?.message??err)})}
});

app.get<{Params:{id:string}}>('/api/providers/:id/discover-models',async(req,reply)=>{
  try{return await discoverProviderModels(req.params.id)}
  catch(err:any){return reply.code(502).send({code:'PROVIDER_DISCOVERY_FAILED',details:String(err?.message??err)})}
});

app.post('/api/models',async(req:any,reply)=>{
  try{return createModel(req.body ?? {})}
  catch(err:any){return reply.code(400).send({code:'MODEL_CREATE_FAILED',details:String(err?.message??err)})}
});

app.patch<{Params:{id:string}}>('/api/models/:id',async(req:any,reply)=>{
  try{return updateModel(req.params.id,req.body ?? {})}
  catch(err:any){return reply.code(400).send({code:'MODEL_UPDATE_FAILED',details:String(err?.message??err)})}
});

app.delete<{Params:{id:string}}>('/api/models/:id',async(req,reply)=>{
  try{return deleteModel(req.params.id)}
  catch(err:any){return reply.code(409).send({code:'MODEL_DELETE_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/models/:id/test-chat',async(req,reply)=>{
  try{return await checkModelChat(req.params.id)}
  catch(err:any){return reply.code(502).send({code:'MODEL_CHAT_TEST_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/models/:id/test-tools',async(req,reply)=>{
  try{return await checkModelTools(req.params.id)}
  catch(err:any){return reply.code(502).send({code:'MODEL_TOOL_TEST_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/models/:id/test-mutation',async(req,reply)=>{
  try{return await checkModelMutation(req.params.id)}
  catch(err:any){return reply.code(502).send({code:'MODEL_MUTATION_TEST_FAILED',details:String(err?.message??err)})}
});

app.get<{Querystring:{lang?:string}}>('/api/registry/runtimes',async(req)=>({
  items:listRuntimes(uiLocale(req.query.lang))
}));

app.get<{Querystring:{lang?:string}}>('/api/registry/compatibility',async(req)=>({
  items:listCompatibility(uiLocale(req.query.lang))
}));

app.get<{Querystring:{lang?:string}}>('/api/registry/templates',async(req)=>({
  items:listTemplates(uiLocale(req.query.lang))
}));

app.get<{Querystring:{lang?:string}}>('/api/agents',async(req)=>({
  items:listAgents(uiLocale(req.query.lang))
}));

app.get<{Querystring:{lang?:string}}>('/api/teams',async(req)=>({
  items:listTeams(uiLocale(req.query.lang))
}));

app.get<{Params:{id:string};Querystring:{lang?:string}}>('/api/agents/:id',async(req,reply)=>{
  const x=getAgent(req.params.id,uiLocale(req.query.lang));
  if(!x)return reply.code(404).send({code:'AGENT_NOT_FOUND'});
  return x;
});

const AgentInput=z.object({
  name:z.string().min(1),
  slug:z.string().optional(),
  description:z.string().optional(),
  systemPrompt:z.string().min(1),
  instructions:z.string().optional(),
  runtimePolicy:z.record(z.string(),z.any()),
  modelPolicy:z.record(z.string(),z.any()),
  requiredCapabilities:z.array(z.string()).optional(),
  tools:z.array(z.string()).optional(),
  skills:z.array(z.string()).optional(),
  skillIds:z.array(z.string()).optional(),
  memoryPolicy:z.record(z.string(),z.any()).optional(),
  permissionPolicy:z.record(z.string(),z.any()).optional(),
  autonomyLevel:z.number().int().min(0).max(5).optional(),
  lifetime:z.string().optional(),
  tags:z.array(z.string()).optional(),
});

app.post('/api/agents',async(req,reply)=>{
  const parsed=AgentInput.safeParse(req.body);
  if(!parsed.success)return reply.code(400).send({code:'INVALID_AGENT_INPUT',details:parsed.error.flatten()});
  try{return createAgent(parsed.data);}
  catch(err:any){return reply.code(409).send({code:'AGENT_CREATE_FAILED',details:String(err?.message??err)});}
});

app.post<{Params:{id:string}}>('/api/agents/:id/versions',async(req,reply)=>{
  try{return newAgentVersion(req.params.id,req.body ?? {});}
  catch(err:any){return reply.code(400).send({code:'AGENT_VERSION_FAILED',details:String(err?.message??err)});}
});

app.get<{Params:{id:string}}>('/api/agents/:id/versions',async(req,reply)=>{
  try{return {items:listAgentVersions(req.params.id)};}
  catch(err:any){return reply.code(404).send({code:'AGENT_VERSIONS_NOT_FOUND',details:String(err?.message??err)});}
});

app.post<{Params:{id:string;versionId:string}}>('/api/agents/:id/versions/:versionId/activate',async(req,reply)=>{
  try{return activateAgentVersion(req.params.id,req.params.versionId);}
  catch(err:any){return reply.code(404).send({code:'AGENT_VERSION_ACTIVATE_FAILED',details:String(err?.message??err)});}
});

app.patch<{
  Params:{id:string};
  Querystring:{lang?:string};
  Body:{name?:string;description?:string;lifetime?:string;tags?:string[];enabled?:boolean}
}>('/api/agents/:id/profile',async(req,reply)=>{
  try{
    return updateAgentLocalizedProfile(
      req.params.id,
      uiLocale(req.query.lang),
      req.body ?? {}
    );
  }catch(err:any){
    return reply.code(400).send({code:'AGENT_PROFILE_UPDATE_FAILED',details:String(err?.message??err)});
  }
});

app.delete<{Params:{id:string}}>('/api/agents/:id',async(req,reply)=>{
  try{deleteAgent(req.params.id);return {ok:true};}
  catch(err:any){return reply.code(409).send({code:'AGENT_DELETE_FAILED',details:String(err?.message??err)});}
});

app.post<{Params:{id:string};Body:{task?:string}}>('/api/agents/:id/run',async(req,reply)=>{
  if(!req.body?.task?.trim())return reply.code(400).send({code:'TASK_REQUIRED'});
  try{return await startExecution(req.params.id,req.body.task.trim());}
  catch(err:any){return reply.code(409).send({code:'EXECUTION_START_FAILED',details:String(err?.message??err)});}
});

app.get<{Querystring:{lang?:string}}>('/api/executions',async(req)=>({
  items:listExecutions(uiLocale(req.query.lang))
}));


app.post<{Params:{id:string}}>('/api/executions/:id/cancel',async(req,reply)=>{
  try{return await cancelExecution(req.params.id,'cancel')}
  catch(err:any){return reply.code(409).send({code:'EXECUTION_CANCEL_FAILED',details:String(err?.message??err)})}
});

app.post<{Params:{id:string}}>('/api/executions/:id/kill',async(req,reply)=>{
  try{return await cancelExecution(req.params.id,'kill')}
  catch(err:any){return reply.code(409).send({code:'EXECUTION_KILL_FAILED',details:String(err?.message??err)})}
});

app.get<{Params:{id:string}}>('/api/executions/:id',async(req,reply)=>{
  const x=getExecution(req.params.id);
  if(!x)return reply.code(404).send({code:'EXECUTION_NOT_FOUND'});
  return x;
});

app.get<{Params:{id:string}}>('/api/executions/:id/route',async(req,reply)=>{
  const x=getRouteDecision(req.params.id);
  if(!x)return reply.code(404).send({code:'ROUTE_DECISION_NOT_FOUND'});
  return x;
});

app.get<{Params:{id:string};Querystring:{limit?:string}}>('/api/executions/:id/events',async(req)=>({
  items:executionEvents(req.params.id,Number(req.query.limit ?? 200))
}));

app.post('/api/compatibility/direct-tool-test',async(req:any,reply)=>{
  const body=req.body ?? {};
  const modelId=body.modelId ?? 'model_qwen35_local';
  const runtimeId=body.runtimeId ?? 'runtime_generic_openai';
  const model:any=db.prepare(`SELECT * FROM models WHERE id=?`).get(modelId);
  const runtime:any=db.prepare(`SELECT * FROM runtimes WHERE id=?`).get(runtimeId);

  if(!model||!runtime)return reply.code(404).send({code:'MODEL_OR_RUNTIME_NOT_FOUND'});
  if(runtime.adapter_type!=='generic-openai') {
    return reply.code(400).send({code:'SMOKE_TEST_RUNTIME_UNSUPPORTED'});
  }

  const provider:any=db.prepare(`SELECT * FROM model_providers WHERE id=?`).get(model.provider_id);
  const url=`${String(provider.base_url).replace(/\/$/,'')}/chat/completions`;

  const payload={
    model:model.provider_model_id,
    messages:[{
      role:'user',
      content:'README.md dosyasını okumak için read_file aracını kullan. Aracı çağırmadan cevap verme.'
    }],
    tools:[{
      type:'function',
      function:{
        name:'read_file',
        description:'Workspace içindeki bir metin dosyasını okur.',
        parameters:{
          type:'object',
          properties:{file_path:{type:'string'}},
          required:['file_path']
        }
      }
    }],
    tool_choice:'auto',
    stream:false
  };

  const res=await fetch(url,{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)
  });
  const raw:any=await res.json();
  const calls=raw?.choices?.[0]?.message?.tool_calls;
  const pass=Array.isArray(calls)&&calls.length>0&&calls[0]?.function?.name==='read_file';

  db.prepare(`
    UPDATE model_runtime_compatibility
    SET tools_status=?,notes=?,last_tested_at=?
    WHERE model_id=? AND runtime_id=?
  `).run(
    pass?'pass':'fail',
    pass?'Automated direct tool-call smoke test passed.':'Automated tool-call smoke test failed.',
    now(),modelId,runtimeId
  );

  return {pass,message:raw?.choices?.[0]?.message,raw};
});

app.listen({host:cfg.host,port:cfg.port}).then(()=>{
  app.log.info(`Doom Core V${version}: http://${cfg.host}:${cfg.port}`);
  app.log.info(`Registry UI: http://${cfg.host}:${cfg.port}/`);
  const resumed=resumeAutonomousMissions();
  app.log.info({resumed},'Autonomous mission recovery scan completed');
});
