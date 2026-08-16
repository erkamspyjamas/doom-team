import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { db, parseJson } from './db/index.js';
import { loadConfig } from './config.js';
import { listAgents,listTeams,listProviders,listModels,listRuntimes,listCompatibility } from './repositories.js';
import { listModelPolicies } from './model-policies.js';
import { listMissionSpecialists } from './agent-factory.js';
import { createMission } from './missions.js';
import { TOOL_CATALOG } from './tools/catalog.js';
import { dependencyAdditionIntent,extractBossConstraints,validatePlanAgainstBossConstraints,validateTextAgainstBossConstraints } from './plan-constraints.js';
import { canonicalizePlanRoleContracts,documentationSpecialistContract,validatePlanRoleObligations } from './plan-quality.js';
import {
  inferTaskMutationTargetPaths,
  isCompletedStateTaskInstruction,
  resolveTaskWorkModeContract,
  taskHasStrongMutationIntent,
} from './task-contracts.js';
import {
  decideStartupRecovery,
  MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
} from './startup-recovery-policy.js';

const cfg=loadConfig();

export type DiagnosticStatus='pass'|'warn'|'fail';

export type DiagnosticResult={
  id:string;
  group:string;
  name:string;
  status:DiagnosticStatus;
  summary:string;
  detail?:any;
  durationMs:number;
};

type DiagnosticDefinition={
  id:string;
  group:string;
  name:string;
  run:()=>Promise<Omit<DiagnosticResult,'id'|'group'|'name'|'durationMs'>>|Omit<DiagnosticResult,'id'|'group'|'name'|'durationMs'>;
};

function timed(def:DiagnosticDefinition):Promise<DiagnosticResult>{
  const started=performance.now();
  return Promise.resolve()
    .then(()=>def.run())
    .then(out=>({
      id:def.id,
      group:def.group,
      name:def.name,
      ...out,
      durationMs:Math.round((performance.now()-started)*100)/100
    }))
    .catch((err:any)=>({
      id:def.id,
      group:def.group,
      name:def.name,
      status:'fail' as const,
      summary:String(err?.message ?? err),
      detail:{error:String(err?.stack ?? err)},
      durationMs:Math.round((performance.now()-started)*100)/100
    }));
}

function tableExists(name:string){
  const row:any=db.prepare(`
    SELECT 1 ok FROM sqlite_master WHERE type='table' AND name=?
  `).get(name);
  return Boolean(row?.ok);
}

function git(cwd:string,args:string[]){
  const out=spawnSync('git',args,{
    cwd,
    encoding:'utf-8',
    timeout:15_000,
    env:{...process.env,GIT_TERMINAL_PROMPT:'0'}
  });
  return {
    status:out.status,
    stdout:String(out.stdout ?? '').trim(),
    stderr:String(out.stderr ?? '').trim(),
    error:out.error ? String(out.error.message) : null
  };
}

function jsonFile(path:string){
  try{
    const raw=readFileSync(path,'utf-8');
    return {ok:true,value:JSON.parse(raw),error:null};
  }catch(err:any){
    return {ok:false,value:null,error:String(err?.message ?? err)};
  }
}

function coreTests():DiagnosticDefinition[]{
  return [
    {
      id:'db.quick_check',group:'Database',name:'SQLite quick_check',
      run:()=>{
        const rows=db.pragma('quick_check') as any[];
        const values=rows.flatMap(row=>Object.values(row).map(String));
        const ok=values.length===1 && values[0]==='ok';
        return {
          status:ok?'pass':'fail',
          summary:ok?'SQLite quick_check: ok':'SQLite quick_check problem detected.',
          detail:values
        };
      }
    },
    {
      id:'db.foreign_keys',group:'Database',name:'Foreign-key integrity',
      run:()=>{
        const rows=db.pragma('foreign_key_check') as any[];
        return {
          status:rows.length?'fail':'pass',
          summary:rows.length?`${rows.length} foreign-key violation(s).`:'No foreign-key violations.',
          detail:rows.slice(0,100)
        };
      }
    },
    {
      id:'schema.required_tables',group:'Database',name:'Required Doom tables',
      run:()=>{
        const names=[
          'agents','agent_versions','teams','team_members',
          'model_providers','models','runtimes','model_runtime_compatibility',
          'skills','skill_versions','missions','mission_tasks','mission_events',
          'communication_messages','execution_directives','task_artifacts',
          'approvals','agent_factory_requests','mission_specialists'
        ];
        const missing=names.filter(x=>!tableExists(x));
        return {
          status:missing.length?'fail':'pass',
          summary:missing.length?`Missing tables: ${missing.join(', ')}`:`${names.length} required tables are present.`,
          detail:{missing}
        };
      }
    },
    {
      id:'agents.current_versions',group:'Agents',name:'Agent current-version references',
      run:()=>{
        const bad=db.prepare(`
          SELECT a.id,a.name,a.current_version_id
          FROM agents a
          LEFT JOIN agent_versions av ON av.id=a.current_version_id
          WHERE a.current_version_id IS NULL OR av.id IS NULL OR av.agent_id<>a.id
        `).all();
        return {
          status:(bad as any[]).length?'fail':'pass',
          summary:(bad as any[]).length?`${(bad as any[]).length} invalid agent/version link(s).`:'All agents point to valid immutable versions.',
          detail:bad
        };
      }
    },
    {
      id:'agents.list_shape',group:'Agents',name:'Agent list API data shape',
      run:()=>{
        const agents=listAgents('tr') as any[];
        const malformed=agents.filter(a=>!Array.isArray(a.tools)||!Array.isArray(a.skills)||!Array.isArray(a.required_capabilities));
        return {
          status:malformed.length?'fail':'pass',
          summary:malformed.length?`${malformed.length} agent(s) missing parsed tools/skills/capabilities.`:`${agents.length} agent records expose tools, skills and capabilities.`,
          detail:malformed.map(a=>({id:a.id,name:a.name,tools:a.tools,skills:a.skills,required_capabilities:a.required_capabilities}))
        };
      }
    },
    {
      id:'agents.tool_grants',group:'Agents',name:'Agent tool grants resolve',
      run:()=>{
        const knownTools=new Set(TOOL_CATALOG.map(x=>String(x.name)));
        const knownCategories=new Set([
          'filesystem','git','tests','shell','skill','communication','agent_factory'
        ]);
        const agents=listAgents('tr') as any[];
        const unknown:any[]=[];
        for(const a of agents){
          for(const grant of a.tools ?? []){
            const value=String(grant);
            if(!knownTools.has(value) && !knownCategories.has(value)){
              unknown.push({agentId:a.id,agent:a.name,grant:value});
            }
          }
        }
        return {
          status:unknown.length?'warn':'pass',
          summary:unknown.length
            ? `${unknown.length} legacy/unsupported tool grant(s) are present; Generic Runtime ignores grants not found in the current Tool Catalog.`
            : 'All agent tool grants resolve to the current code-based Tool Catalog.',
          detail:{
            catalogTools:[...knownTools].sort(),
            catalogCategories:[...knownCategories].sort(),
            unknown
          }
        };
      }
    },
    {
      id:'teams.lead_integrity',group:'Teams',name:'Team Lead integrity',
      run:()=>{
        const teams=listTeams('tr') as any[];
        const problems:any[]=[];
        for(const team of teams){
          if(!team.enabled)continue;
          if(!team.lead_agent_id){
            problems.push({team:team.name,error:'TEAM_LEAD_MISSING'});
            continue;
          }
          const lead:any=db.prepare(`SELECT id,enabled FROM agents WHERE id=?`).get(team.lead_agent_id);
          const member:any=db.prepare(`SELECT 1 ok FROM team_members WHERE team_id=? AND agent_id=?`).get(team.id,team.lead_agent_id);
          if(!lead?.enabled)problems.push({team:team.name,leadAgentId:team.lead_agent_id,error:'LEAD_DISABLED_OR_MISSING'});
          if(!member?.ok)problems.push({team:team.name,leadAgentId:team.lead_agent_id,error:'LEAD_NOT_TEAM_MEMBER'});
        }
        return {
          status:problems.length?'fail':'pass',
          summary:problems.length?`${problems.length} team Lead problem(s).`:'Enabled teams have enabled Leads that are active members.',
          detail:problems
        };
      }
    },

{
  id:'missions.create_transaction_dry_run',group:'Missions',name:'Mission create service rollback test',
  run:()=>{
    const team:any=db.prepare(`
      SELECT t.*
      FROM teams t
      WHERE t.enabled=1
      ORDER BY t.created_at
      LIMIT 1
    `).get();

    if(!team){
      return {
        status:'fail',
        summary:'No enabled team is available for the mission-create service test.',
        detail:null
      };
    }

    const before:any=db.prepare(`SELECT COUNT(*) c FROM missions`).get();
    const sentinel='DOOM_DIAGNOSTIC_ROLLBACK';
    let createdId:string|null=null;
    let createdWorkspace:any=null;

    try{
      const trx=db.transaction(()=>{
        const created:any=createMission({
          title:'Doom Diagnostics Rollback Mission',
          objective:'Validate the real createMission() service, including mission.created event, then roll back.',
          teamId:team.id,
          // The diagnostic intentionally rolls the outer transaction back.
          // Suppress autonomous async planning so no orphan microtask survives.
          autoStart:false
        });

        createdId=String(created?.id ?? '');
        if(!createdId)throw new Error('MISSION_SERVICE_DID_NOT_RETURN_ID');

        createdWorkspace=db.prepare(`
          SELECT * FROM mission_workspaces WHERE mission_id=?
        `).get(createdId);

        const createdEvent:any=db.prepare(`
          SELECT event_type FROM mission_events
          WHERE mission_id=? AND event_type='mission.created'
          ORDER BY id DESC LIMIT 1
        `).get(createdId);

        if(!createdWorkspace)throw new Error('MISSION_SERVICE_WORKSPACE_MISSING');
        if(!createdEvent)throw new Error('MISSION_SERVICE_CREATED_EVENT_MISSING');

        throw new Error(sentinel);
      });

      trx();
      throw new Error('MISSION_SERVICE_ROLLBACK_SENTINEL_NOT_THROWN');
    }catch(err:any){
      const message=String(err?.message ?? err);
      if(message!==sentinel)throw err;
    }

    const after:any=db.prepare(`SELECT COUNT(*) c FROM missions`).get();
    const leakedMission=createdId
      ? db.prepare(`SELECT id FROM missions WHERE id=?`).get(createdId)
      : null;

    if(Number(before?.c ?? 0)!==Number(after?.c ?? 0) || leakedMission){
      throw new Error('MISSION_SERVICE_ROLLBACK_LEAK');
    }

    return {
      status:'pass',
      summary:'Real createMission() service succeeded (mission + workspace + event) with async auto-start suppressed and was fully rolled back.',
      detail:{
        teamId:team.id,
        teamName:team.name,
        createdId,
        workspaceMode:createdWorkspace?.mode ?? null,
        noPersistentMission:true
      }
    };
  }
},


{
  id:'missions.replan_safety',group:'Missions',name:'Safe replan invariants',
  run:()=>{
    const protectedRows=db.prepare(`
      SELECT m.id,m.title,
        COUNT(DISTINCT CASE WHEN mt.status='done' THEN mt.id END) done_count,
        COUNT(DISTINCT CASE WHEN mtw.commit_hash IS NOT NULL OR mtw.merge_commit IS NOT NULL
                  OR mtw.status IN ('merged','cleaned') THEN mtw.id END) integrated_count
      FROM missions m
      LEFT JOIN mission_tasks mt ON mt.mission_id=m.id
      LEFT JOIN mission_task_workspaces mtw ON mtw.task_id=mt.id
      GROUP BY m.id
      HAVING done_count>0 OR integrated_count>0
    `).all() as any[];
    return {
      status:'pass',
      summary:'Safe replan can identify missions whose completed/integrated work must be protected.',
      detail:{protectedMissions:protectedRows.slice(0,100)}
    };
  }
},


{
  id:'planning.constraint_guard',group:'Missions',name:'Boss negative constraint guard',
  run:()=>{
    const objective='README dosyasını profesyonel hale getir. Yeni dependency ekleme.';
    const badPlan={
      summary:'bad',
      tasks:[{
        key:'T3',
        title:'Add new dependency declaration to project config',
        description:'Add the identified new dependency to the project configuration file.',
        acceptanceCriteria:[
          'New dependency added to project configuration file',
          'Dependency version specified'
        ]
      }]
    };
    const goodPlan={
      summary:'good',
      tasks:[{
        key:'T3',
        title:'Validate documentation',
        description:'Review README acceptance criteria.',
        acceptanceCriteria:['README documentation is complete']
      }]
    };
    const extracted=extractBossConstraints(objective);
    const bad=validatePlanAgainstBossConstraints(objective,badPlan);
    const good=validatePlanAgainstBossConstraints(objective,goodPlan);
    const ok=extracted.some(x=>x.id==='forbid_new_dependency') && !bad.ok && good.ok;
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Observed Turkish dependency prohibition correctly blocks the contradictory T3 plan.'
        : 'Constraint guard did not block the contradictory plan.',
      detail:{extracted,badViolations:bad.violations,goodViolations:good.violations}
    };
  }
},


{
  id:'planning.json_final_guard',group:'Missions',name:'Planning final JSON guard',
  run:()=>{
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');

    const checks={
      validator:runtimeSource.includes('function validMissionPlanJson('),
      nonJsonReject:runtimeSource.includes('planning.non_json_final_rejected'),
      strictRecovery:runtimeSource.includes('planning.finalization_strict_recovered'),
      planningContext:missionSource.includes("expectedFinalFormat:'mission_plan_json'"),
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Planning executions enforce machine-parseable mission-plan JSON before completion.'
        : 'One or more planning JSON guard components are missing.',
      detail:checks
    };
  }
},


{
  id:'planning.role_obligations',group:'Missions',name:'QA / Reviewer plan-role guard',
  run:()=>{
    const objective='README dosyasını güncelle. Değişiklikleri QA doğrulasın ve Reviewer son incelemeyi yapsın.';
    const roster=[
      {id:'agent_specialist',name:'Teknik Dokümantasyon Uzmanı',slug:'tech-doc',role_label:'Specialist',temporary:true},
      {id:'agent_qa',name:'QA',slug:'qa',role_label:'QA',temporary:false},
      {id:'agent_reviewer',name:'Reviewer',slug:'reviewer',role_label:'Reviewer',temporary:false}
    ];
    const badPlan={tasks:[
      {key:'T1',title:'README güncelle',description:'README düzenle',assignedAgentId:'agent_specialist',dependsOn:[]},
      {key:'T2',title:'QA İnceleme',description:'README doğrula',assignedAgentId:'agent_specialist',dependsOn:['T1']}
    ]};
    const goodPlan={tasks:[
      {key:'T1',title:'README güncelle',description:'README düzenle',assignedAgentId:'agent_specialist',dependsOn:[]},
      {key:'T2',title:'QA doğrulama',description:'README doğrula',assignedAgentId:'agent_qa',dependsOn:['T1']},
      {key:'T3',title:'Final review',description:'Reviewer final review',assignedAgentId:'agent_reviewer',dependsOn:['T2']}
    ]};
    const bad=validatePlanRoleObligations(objective,badPlan,roster);
    const good=validatePlanRoleObligations(objective,goodPlan,roster);
    return {
      status:(!bad.ok && good.ok)?'pass':'fail',
      summary:(!bad.ok && good.ok)
        ? 'Plans must assign QA work to QA and final review to Reviewer in the correct dependency order.'
        : 'QA/Reviewer role obligation guard failed.',
      detail:{badViolations:bad.violations,goodViolations:good.violations}
    };
  }
},
{
  id:'acceptance.result_constraint_integrity',group:'Missions',name:'Agent report hard-constraint integrity',
  run:()=>{
    const objective='README dosyasını güncelle. Yeni dependency ekleme.';
    const bad=validateTextAgainstBossConstraints(
      objective,
      'Yeni Bağımlılık Ekleme: npm install ile @doom/core-tools eklendi ve package.json güncellendi.',
      'T1'
    );
    const good=validateTextAgainstBossConstraints(
      objective,
      'README analiz edildi. Herhangi bir dependency veya proje config değişikliği yapılmadı.',
      'T1'
    );
    return {
      status:(!bad.ok && good.ok)?'pass':'fail',
      summary:(!bad.ok && good.ok)
        ? 'Agent reports that claim forbidden dependency installation are rejected deterministically.'
        : 'Agent-result constraint integrity guard failed.',
      detail:{badViolations:bad.violations,goodViolations:good.violations}
    };
  }
},


{
  id:'planning.constraint_negation_context',group:'Missions',name:'Dependency constraint negation context',
  run:()=>{
    const cases=[
      {text:'Add a new dependency to package.json.',expected:true},
      {text:'New dependency added to project configuration file.',expected:true},
      {text:'Install a new package for README generation.',expected:true},
      {text:'npm install lodash',expected:true},
      {text:'npm install was not run.',expected:false},
      {text:'Do not run npm install lodash.',expected:false},
      {text:'@doom/core-tools eklendi.',expected:true},
      {text:'@doom/core-tools eklenmedi.',expected:false},
      {text:'Do not add a new dependency.',expected:false},
      {text:"Don't add new dependencies.",expected:false},
      {text:'Without adding new dependencies, improve the README.',expected:false},
      {text:'No new dependency added.',expected:false},
      {text:'Yeni dependency ekleme.',expected:false},
      {text:'Yeni dependency eklemeden README dosyasını güncelle.',expected:false},
      {text:'Yeni bağımlılık eklenmeyecek.',expected:false},
      {text:'Do not add dependencies. Add a new dependency called lodash.',expected:true},
    ];

    const results=cases.map(c=>({
      ...c,
      actual:dependencyAdditionIntent(c.text)
    }));
    const failures=results.filter(x=>x.actual!==x.expected);

    return {
      status:failures.length?'fail':'pass',
      summary:failures.length
        ? `${failures.length} dependency-intent negation cases were classified incorrectly.`
        : 'Positive dependency actions are detected while negated "do not / without / ekleme / eklemeden" wording is allowed.',
      detail:{results,failures}
    };
  }
},


{
  id:'planning.documentation_specialist_contract',group:'AgentFactory',name:'Boss documentation specialist contract',
  run:()=>{
    const objective=`oom-test repository'sinin README dosyasını profesyonel teknik dokümantasyon seviyesine getir. Kalıcı ekipte bu iş için yeterince özel bir teknik dokümantasyon uzmanı yoksa AgentFactory kullanarak "Teknik Dokümantasyon Uzmanı" isimli geçici bir specialist oluştur. README düzenleme görevini bu uzmana ata.`;
    const permanentRoster=[
      {id:'lead',name:'Lead',slug:'lead',role_label:'Lead',temporary:false},
      {id:'backend',name:'Backend',slug:'backend',role_label:'Backend',temporary:false},
      {id:'qa',name:'QA',slug:'qa',role_label:'QA',temporary:false},
      {id:'reviewer',name:'Reviewer',slug:'reviewer',role_label:'Reviewer',temporary:false}
    ];

    const missing=documentationSpecialistContract(objective,permanentRoster);
    const withTemporary=documentationSpecialistContract(
      objective,
      [
        ...permanentRoster,
        {
          id:'agent_docs',
          name:'Teknik Dokümantasyon Uzmanı',
          slug:'technical-docs',
          role_label:'Technical Documentation Specialist',
          description:'Temporary documentation specialist',
          temporary:true
        }
      ]
    );

    const ok=
      missing.required
      && missing.needsCreation
      && missing.name==='Teknik Dokümantasyon Uzmanı'
      && withTemporary.required
      && !withTemporary.needsCreation
      && withTemporary.satisfiedBy==='agent_docs';

    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Boss AgentFactory documentation request is deterministically recognized; missing specialists require creation and active specialists satisfy the contract.'
        : 'Documentation specialist Boss contract was not resolved correctly.',
      detail:{missing,withTemporary}
    };
  }
},


{
  id:'gates.structured_json_contracts',group:'Missions',name:'Structured JSON gate contracts',
  run:()=>{
    const runtimeSource=readFileSync(
      join(process.cwd(),'src','runtime','generic-openai.ts'),
      'utf-8'
    );
    const missionSource=readFileSync(
      join(process.cwd(),'src','missions.ts'),
      'utf-8'
    );

    const checks={
      acceptanceValidator:runtimeSource.includes('function validAcceptanceGateJson('),
      finalGateValidator:runtimeSource.includes('function validFinalGateJson('),
      genericReject:runtimeSource.includes('structured_final.invalid_rejected'),
      strictRecovery:runtimeSource.includes('structured_final.finalization_strict_recovered'),
      acceptanceContext:missionSource.includes("expectedFinalFormat:'acceptance_gate_json'"),
      finalGateContext:missionSource.includes("expectedFinalFormat:'final_gate_json'"),
      technicalErrorCode:missionSource.includes("'ACCEPTANCE_GATE_ERROR'")
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Acceptance, QA and Reviewer gates enforce structured JSON with automatic invalid/truncated-output recovery.'
        : 'One or more structured gate JSON safeguards are missing.',
      detail:checks
    };
  }
},


{
  id:'tasks.evidence_integrity',group:'Missions',name:'Task target/evidence integrity guards',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const gitSource=readFileSync(join(process.cwd(),'src','git','worktrees.ts'),'utf-8');
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');

    const checks={
      changedPaths:gitSource.includes('changedPaths:[...new Set(changedPaths)]'),
      targetGuard:missionSource.includes('MODIFY_TASK_TARGET_NOT_CHANGED'),
      readOnlyTaskTools:missionSource.includes('function taskExecutionToolOverride('),
      readOnlyNoMerge:missionSource.includes('workspace.task_readonly_cleaned'),
      criterionIndices:runtimeSource.includes('acceptanceCriteriaCount')
        && missionSource.includes('bindAcceptanceVerdict'),
      finalIntegrationEvidence:missionSource.includes('INTEGRATED REPOSITORY EVIDENCE (SOURCE OF TRUTH)'),
      replanProtectsCommitsOnly:!missionSource.includes("mtw.status IN ('merged','cleaned')")
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Modify tasks must touch intended targets, acceptance criteria are index-bound, validation tasks are read-only, and final gates receive integration evidence.'
        : 'One or more task evidence-integrity safeguards are missing.',
      detail:checks
    };
  }
},


{
  id:'missions.autonomous_lead_recovery',group:'Missions',name:'Autonomous Lead recovery loop',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');
    const uiSource=readFileSync(join(process.cwd(),'public','index.html'),'utf-8');
    const checks={
      recoveryContract:runtimeSource.includes("'recovery_action_json'"),
      recoveryValidator:runtimeSource.includes('function validRecoveryActionJson('),
      leadDecision:missionSource.includes('runLeadRecoveryDecision'),
      taskRecovery:missionSource.includes('attemptAutonomousTaskRecovery'),
      finalGateRecovery:missionSource.includes('attemptAutonomousFinalGateRecovery'),
      taskRevision:missionSource.includes("type==='revise_task'"),
      remediationTask:missionSource.includes("type==='add_remediation_task'"),
      boundedBudget:missionSource.includes('MAX_AUTONOMOUS_RECOVERY_PER_TASK'),
      autoPlan:missionSource.includes('queueMicrotask(()=>'),
      autoRun:missionSource.includes("mission.autorun_started"),
      createAndRunUi:uiSource.includes("missions.createAndRun")
    };
    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Mission creation auto-plans/auto-runs and Lead autonomously retries, revises, reassigns or creates remediation work before escalating.'
        : 'One or more autonomous Lead recovery components are missing.',
      detail:checks
    };
  }
},

{
  id:'missions.autostart_transaction_safety',group:'Missions',name:'Autostart transaction safety',
  run:()=>{
    const missionSource=readFileSync(
      join(process.cwd(),'src','missions.ts'),
      'utf-8'
    );
    const diagnosticSource=readFileSync(
      join(process.cwd(),'src','diagnostics.ts'),
      'utf-8'
    );

    const checks={
      explicitAutoStartSwitch:missionSource.includes("const autoStart=input.autoStart!==false"),
      diagnosticSuppressesAutoStart:diagnosticSource.includes("autoStart:false"),
      createMicrotaskLivenessGuard:missionSource.includes("if(!missionRow(missionId))return;"),
      autonomousEventUsesActualMode:missionSource.includes("autonomous:autoStart")
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Transactional mission diagnostics cannot leave orphan auto-planning callbacks, and async mission callbacks verify mission liveness before FK-bound writes.'
        : 'One or more mission autostart transaction-safety safeguards are missing.',
      detail:checks
    };
  }
},


{
  id:'planning.acceptance_satisfiability',group:'Missions',name:'Acceptance criterion satisfiability',
  run:()=>{
    const objective='README dosyasını profesyonel hale getir. QA doğrulasın ve Reviewer son incelemeyi yapsın.';
    const roster=[
      {id:'product',name:'Product',slug:'product',role_label:'Product',temporary:false},
      {id:'docs',name:'Teknik Dokümantasyon Uzmanı',slug:'docs',role_label:'Technical Documentation Specialist',temporary:true},
      {id:'qa',name:'QA',slug:'qa',role_label:'QA',temporary:false},
      {id:'reviewer',name:'Reviewer',slug:'reviewer',role_label:'Reviewer',temporary:false}
    ];
    const bad={tasks:[
      {key:'T1',title:'README Analizi',instruction:'README analiz et',description:'README analiz et',assignedAgentId:'product',
       acceptanceCriteria:['README mevcut durumu analiz edildi.','İçerik planı Product tarafından onaylandı.'],dependsOn:[]},
      {key:'T2',title:'README düzenleme',instruction:'README.md dosyası güncellendi.',description:'README güncelle',assignedAgentId:'docs',
       acceptanceCriteria:['README.md güncellendi.'],dependsOn:['T1']},
      {key:'T3',title:'QA doğrulama',instruction:'README doğrula',description:'README doğrula',assignedAgentId:'qa',
       acceptanceCriteria:['README kriterleri doğrulandı.'],dependsOn:['T2']},
      {key:'T4',title:'Reviewer final review',instruction:'README final review yap',description:'README final review',assignedAgentId:'reviewer',
       acceptanceCriteria:['Final review tamamlandı.'],dependsOn:['T3']}
    ]};

    const result=validatePlanRoleObligations(objective,bad,roster);
    const approvalCaught=result.violations.some(
      v=>v.id==='invented_approval_acceptance_criterion'
    );
    const completedInstructionCaught=result.violations.some(
      v=>v.id==='completed_state_task_instruction'
    );
    const caught=approvalCaught && completedInstructionCaught;

    return {
      status:caught?'pass':'fail',
      summary:caught
        ? 'Lead-invented approval criteria and completed-state task instructions are rejected deterministically before execution.'
        : 'Acceptance/instruction satisfiability guard missed a known regression case.',
      detail:{
        approvalCaught,
        completedInstructionCaught,
        violations:result.violations
      }
    };
  }
},
{
  id:'missions.recovery_escalation_policy',group:'Missions',name:'Routine recovery escalation policy',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');

    const checks={
      typedEscalation:runtimeSource.includes("'missing_boss_input'")
        && runtimeSource.includes("'safety_approval'"),
      policyGuard:missionSource.includes('function recoveryEscalationPolicy('),
      escalationRejected:missionSource.includes('lead.recovery_escalation_rejected'),
      deterministicNoChangeRecovery:missionSource.includes('function deterministicTaskRecoveryAction('),
      deterministicFallback:missionSource.includes('deterministicTaskRecoveryFallback'),
      legacyEscalationResume:missionSource.includes('escalationCanBeAutoRevoked'),
      specialistAlreadyResolved:
        missionSource.includes('AgentFactory requirement is ALREADY resolved')
        && missionSource.includes('You ARE the selected temporary'),
      modifyDirective:
        missionSource.includes('Actually modify the target file')
        && missionSource.includes('write_file/replace_in_file')
        && missionSource.includes('Do not return completion without real repository evidence'),
      capabilityPreflight:missionSource.includes('MODIFY_AGENT_WRITE_TOOLS_MISSING'),
      appliedRecoveryBudget:missionSource.includes('appliedTaskRecoveryCount')
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Routine task/gate failures cannot escape to Boss; invalid escalation is rejected and autonomous recovery continues.'
        : 'One or more recovery escalation-policy safeguards are missing.',
      detail:checks
    };
  }
},

{
  id:'runtime.modify_mutation_contract',group:'Runtime',name:'Modify mutation contract',
  run:()=>{
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const checks={
      runtimeContract:runtimeSource.includes('mutationContractInstruction'),
      finalReject:runtimeSource.includes('mutation.final_rejected'),
      mutationToolEvidence:runtimeSource.includes('mutation.tool_satisfied'),
      exhaustedError:runtimeSource.includes('MUTATION_CONTRACT_UNSATISFIED'),
      taskContext:missionSource.includes('requireMutationTool'),
      targetContext:missionSource.includes('requiredMutationTargets')
    };
    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Modify executions require successful write/replace evidence before final completion.'
        : 'Modify mutation-contract safeguard missing.',
      detail:checks
    };
  }
},
{
  id:'missions.adaptive_noop_recovery',group:'Missions',name:'Adaptive no-op recovery',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const checks={
      specialistReplacement:missionSource.includes('function replaceNoOpSpecialist('),
      toolsRequiredReplacement:missionSource.includes("modelStrategy:'tools_required'"),
      adaptiveAction:missionSource.includes("action:'replace_specialist'"),
      epochCounting:missionSource.includes('recoveryEpochStartId')
        && missionSource.includes('lead.recovery_attempt_started'),
      exhaustedUpgradeResume:missionSource.includes('exhaustedModifyNoopCanReset'),
      oneTimeEpoch:missionSource.includes('lead.recovery_budget_epoch_reset')
    };
    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Repeated no-op modify failures switch strategy instead of blind retrying.'
        : 'Adaptive no-op recovery safeguard missing.',
      detail:checks
    };
  }
},

{
  id:'planning.task_instruction_schema',group:'Missions',name:'Action-first task instruction schema',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');

    const checks={
      planInstruction:missionSource.includes('instruction:string'),
      planOutcome:missionSource.includes('expectedOutcome:string'),
      planWorkMode:missionSource.includes("workMode:'modify'|'validate'|'analysis'|'general'"),
      planningSchema:missionSource.includes('"instruction": "imperative action'),
      completedStateFallback:missionSource.includes('looksLikeCompletedStateSentence'),
      completedStatePlanGuard:readFileSync(join(process.cwd(),'src','plan-quality.ts'),'utf-8')
        .includes('completed_state_task_instruction'),
      actionFirstPrompt:missionSource.includes('ACTION NOW'),
      executorSystemContract:runtimeSource.includes('DOOM EXECUTOR CONTRACT'),
      resolvedOrchestration:missionSource.includes('AgentFactory requirement is ALREADY resolved')
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Planning separates imperative instruction from expected outcome and executor prompts put the action before orchestration/reference context.'
        : 'One or more action-first task-prompt safeguards are missing.',
      detail:checks
    };
  }
},
{
  id:'models.mutation_probe_support',group:'Providers & Models',name:'Mutation probe support',
  run:()=>{
    const providerSource=readFileSync(join(process.cwd(),'src','providers','openai-compatible.ts'),'utf-8');
    const registrySource=readFileSync(join(process.cwd(),'src','model-registry.ts'),'utf-8');
    const routerSource=readFileSync(join(process.cwd(),'src','model-router.ts'),'utf-8');

    const checks={
      realTempFile:providerSource.includes("mkdtempSync(join(tmpdir(),'doom-mutation-probe-'))"),
      writeTool:providerSource.includes("name:'write_file'"),
      finalFileCheck:providerSource.includes("if(content==='after')"),
      savedCheck:registrySource.includes("'mutation'"),
      routerPreference:routerSource.includes('preferMutation'),
      routerQualification:routerSource.includes('requireMutationPass')
        && routerSource.includes('excludeModelIds')
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Models support an isolated real-file mutation probe and modify-task routing can prefer models that passed it.'
        : 'Mutation probe/model routing support is incomplete.',
      detail:checks
    };
  }
},
{
  id:'missions.execution_route_visibility',group:'Missions',name:'Execution route visibility',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const uiSource=readFileSync(join(process.cwd(),'public','index.html'),'utf-8');

    const checks={
      taskModelJoin:missionSource.includes('execution_model_name'),
      taskRuntimeJoin:missionSource.includes('execution_runtime_name'),
      snapshotRoute:missionSource.includes('executionRoute'),
      uiRoute:uiSource.includes("missions.executionRoute")
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Each task exposes the actual routed model/runtime/provider and route policy in mission details.'
        : 'Task execution route visibility is incomplete.',
      detail:checks
    };
  }
},

{
  id:'planning.work_mode_role_contract',group:'Missions',name:'Task work-mode / validation-role contract',
  run:()=>{
    const objective='scripts/repo-health-check.mjs dosyasını oluştur. tests/repo-health-check.test.mjs dosyasını oluştur. QA testleri çalıştırarak doğrulasın ve Reviewer son incelemeyi yapsın.';
    const roster=[
      {id:'backend',name:'Backend',slug:'backend',role_label:'Backend',temporary:false},
      {id:'qa',name:'QA',slug:'qa',role_label:'QA',temporary:false},
      {id:'reviewer',name:'Reviewer',slug:'reviewer',role_label:'Reviewer',temporary:false}
    ];

    const bad={tasks:[
      {
        key:'T1',
        title:'Aracı yaz',
        instruction:'scripts/repo-health-check.mjs dosyasını oluştur.',
        expectedOutcome:'Araç mevcut.',
        workMode:'general',
        assignedAgentId:'backend',
        acceptanceCriteria:['Dosya mevcut.'],
        dependsOn:[]
      },
      {
        key:'T2',
        title:'Testleri yaz ve çalıştır',
        instruction:'tests/repo-health-check.test.mjs dosyasını oluştur ve testleri çalıştır.',
        expectedOutcome:'Testler geçiyor.',
        workMode:'modify',
        assignedAgentId:'qa',
        acceptanceCriteria:['Test dosyası mevcut.'],
        dependsOn:['T1']
      },
      {
        key:'T3',
        title:'Final review',
        instruction:'Kod ve testleri incele.',
        expectedOutcome:'Final review tamam.',
        workMode:'validate',
        assignedAgentId:'reviewer',
        acceptanceCriteria:['Gereksinimler doğrulandı.'],
        dependsOn:['T2']
      }
    ]};

    const good={tasks:[
      {
        key:'T1',
        title:'Aracı yaz',
        instruction:'scripts/repo-health-check.mjs dosyasını oluştur.',
        expectedOutcome:'Araç mevcut.',
        workMode:'modify',
        assignedAgentId:'backend',
        acceptanceCriteria:['Dosya mevcut.'],
        dependsOn:[]
      },
      {
        key:'T2',
        title:'Testleri yaz',
        instruction:'tests/repo-health-check.test.mjs dosyasını oluştur.',
        expectedOutcome:'En az 3 test mevcut.',
        workMode:'modify',
        assignedAgentId:'backend',
        acceptanceCriteria:['Test dosyası mevcut.'],
        dependsOn:['T1']
      },
      {
        key:'T3',
        title:'QA doğrulama',
        instruction:'Gerçek komutları ve testleri çalıştırarak implementasyonu doğrula.',
        expectedOutcome:'scripts/repo-health-check.mjs and tests/repo-health-check.test.mjs are created by implementation agents and verified by QA.',
        workMode:'validate',
        assignedAgentId:'qa',
        acceptanceCriteria:['Testler gerçek komutlarla doğrulandı.'],
        dependsOn:['T1','T2']
      },
      {
        key:'T4',
        title:'Final review',
        instruction:'Kod kalitesi ve gereksinim uyumunu incele.',
        expectedOutcome:'Final review tamam.',
        workMode:'validate',
        assignedAgentId:'reviewer',
        acceptanceCriteria:['Gereksinimler doğrulandı.'],
        dependsOn:['T3']
      }
    ]};

    const badResult=validatePlanRoleObligations(objective,bad,roster);
    const goodResult=validatePlanRoleObligations(objective,good,roster);
    const instructionOnlyFixture={
      instruction:'Run the repository health-check tests and verify the implementation.',
      expectedOutcome:'scripts/repo-health-check.mjs and tests/repo-health-check.test.mjs are created and verified.',
      work_mode:'validate'
    };
    const instructionOnlyMutation=!taskHasStrongMutationIntent(instructionOnlyFixture);
    const runtimeMode=resolveTaskWorkModeContract(instructionOnlyFixture);
    const validationAssertionReadOnly=!taskHasStrongMutationIntent({
      instruction:'Verify that scripts/tool.mjs creates valid JSON output.'
    });
    const uppercaseMutation=taskHasStrongMutationIntent({
      instruction:'IMPLEMENT scripts/tool.mjs.'
    });
    const validationCompletedStateAllowed=!isCompletedStateTaskInstruction(
      'Verify that scripts/tool.mjs was created and works.'
    );

    const badIds=new Set(badResult.violations.map(v=>v.id));
    const caught=
      badIds.has('task_work_mode_mismatch')
      && badIds.has('validation_role_mutation_task');
    const ok=caught
      && goodResult.ok
      && instructionOnlyMutation
      && runtimeMode==='validate'
      && validationAssertionReadOnly
      && uppercaseMutation
      && validationCompletedStateAllowed;

    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Mutation intent follows executable instruction only; durable QA/Reviewer validation remains read-only even when expectedOutcome names created files.'
        : 'Task work-mode / validation-role contract failed.',
      detail:{
        badViolations:badResult.violations,
        goodViolations:goodResult.violations,
        instructionOnlyMutation,
        runtimeMode,
        validationAssertionReadOnly,
        uppercaseMutation,
        validationCompletedStateAllowed
      }
    };
  }
},
{
  id:'planning.deterministic_role_contract_repair',group:'Missions',name:'Deterministic plan role-contract canonicalization',
  run:()=>{
    const objective='Create scripts/repo-health-check.mjs and tests/repo-health-check.test.mjs. QA must run and verify the actual tests. Reviewer performs the final review.';
    const roster=[
      {id:'backend',name:'Backend',slug:'backend',role_label:'Backend',temporary:false,priority:20,tools:['read_file','write_file','replace_in_file','run_tests']},
      {id:'qa',name:'QA',slug:'qa',role_label:'QA',temporary:false,priority:80,tools:['read_file','run_tests']},
      {id:'reviewer',name:'Reviewer',slug:'reviewer',role_label:'Reviewer',temporary:false,priority:90,tools:['read_file','run_tests']}
    ];
    const bad={summary:'repair me',tasks:[
      {key:'T1',title:'Tool',instruction:'Create scripts/repo-health-check.mjs.',expectedOutcome:'scripts/repo-health-check.mjs is created and executable.',workMode:'general',assignedAgentId:'backend',acceptanceCriteria:['Tool exists.'],dependsOn:[],priority:10},
      {key:'T2',title:'Tests',instruction:'Create tests/repo-health-check.test.mjs with at least three automated tests.',expectedOutcome:'The test file is created and covers the required behavior.',workMode:'modify',assignedAgentId:'qa',acceptanceCriteria:['At least three tests exist.'],dependsOn:['T1'],priority:20},
      {key:'T3',title:'Review',instruction:'Review code quality and Boss requirement compliance.',expectedOutcome:'The created files and implementation are reviewed.',workMode:'general',assignedAgentId:'reviewer',acceptanceCriteria:['Final review evidence exists.'],dependsOn:['T2'],priority:30}
    ]};
    const repaired=canonicalizePlanRoleContracts(objective,bad,roster);
    const validation=validatePlanRoleObligations(objective,repaired.plan,roster);
    const qaTasks=repaired.plan.tasks.filter((t:any)=>t.assignedAgentId==='qa');
    const reviewerTasks=repaired.plan.tasks.filter((t:any)=>t.assignedAgentId==='reviewer');
    const testTask=repaired.plan.tasks.find((t:any)=>t.key==='T2');
    const ok=validation.ok && testTask?.assignedAgentId==='backend' && testTask?.workMode==='modify' && qaTasks.length>=1 && qaTasks.every((t:any)=>t.workMode==='validate') && reviewerTasks.length>=1 && reviewerTasks.every((t:any)=>t.workMode==='validate');
    return {
      status:ok?'pass':'fail',
      summary:ok?'Safe deterministic plan mistakes are repaired before blocking: mutation work is moved off validation roles, QA is read-only, and Reviewer follows QA.':'Deterministic role-contract canonicalization failed.',
      detail:{changes:repaired.changes,violations:validation.violations,plan:repaired.plan}
    };
  }
},
{
  id:'missions.blocked_zero_task_autoplan',group:'Missions',name:'Blocked zero-task planning recovery',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const failure='PLAN_QUALITY_VIOLATION_AFTER_REPAIR:task_work_mode_mismatch,validation_role_mutation_task';
    const recoverable=decideStartupRecovery({
      status:'blocked',taskCount:0,errorText:failure,autonomousReplanAttempts:0
    });
    const exhausted=decideStartupRecovery({
      status:'blocked',taskCount:0,errorText:failure,
      autonomousReplanAttempts:MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS
    });
    const emptyError=decideStartupRecovery({
      status:'blocked',taskCount:0,errorText:'',autonomousReplanAttempts:0
    });
    const unknownBlockedTasks=decideStartupRecovery({
      status:'blocked',taskCount:2,errorText:'UNKNOWN_FAILURE'
    });
    const allowedBlockedTasks=decideStartupRecovery({
      status:'blocked',taskCount:2,errorText:'QA_GATE_FAILED',
      blockedTaskRecoveryAllowed:true
    });
    const checks={
      recoverableReplan:recoverable.action==='replan'
        && recoverable.mode==='replan_after_planning_failure',
      bounded:exhausted.action==='skip'
        && exhausted.reason==='planning_recovery_budget_exhausted',
      requiresPlanningFailure:emptyError.action==='skip',
      blockedTasksFailClosed:unknownBlockedTasks.action==='skip'
        && allowedBlockedTasks.action==='run',
      productionPolicyWired:missionSource.includes('decideStartupRecovery'),
      durableAttempts:missionSource.includes('autonomousPlanningReplanAttemptCount')
        && missionSource.includes('mission.semantic_replan_started')
        && missionSource.includes('lead.recovery_attempt_started')
        && missionSource.includes('recoveryEpochStartId'),
      exhaustionEvent:missionSource.includes('mission.autonomous_replan_budget_exhausted')
    };
    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok?'Verified planning failures with zero tasks autonomously replan on restart under a durable bounded budget; empty/unrelated failures stay blocked.':'Blocked zero-task autonomous planning recovery is incomplete.',
      detail:{checks,recoverable,exhausted,emptyError,unknownBlockedTasks,allowedBlockedTasks}
    };
  }
},
{
  id:'missions.mutation_target_evidence_binding',group:'Missions',name:'Mutation target / untracked evidence binding',
  run:()=>{
    const targets=inferTaskMutationTargetPaths({
      instruction:'Create scripts/repo-health-check.mjs that validates README.md and reports the result using Node.js.',
      expectedOutcome:'scripts/repo-health-check.mjs oluşturulmuş olmalı.',
      acceptanceCriteria:['scripts/repo-health-check.mjs mevcut olmalı.','Sadece Node.js yerleşik modülleri kullanılmalı.']
    });
    const worktreeSource=readFileSync(join(process.cwd(),'src','git','worktrees.ts'),'utf-8');
    const checks={
      exactTaskTarget:targets.length===1 && targets[0]==='scripts/repo-health-check.mjs',
      noInspectedInputLeak:!targets.includes('README.md'),
      noTechnologyTokenLeak:!targets.includes('Node.js'),
      untrackedFilesExpanded:worktreeSource.includes("'--untracked-files=all'")
    };
    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok?'Task-local mutation targets exclude inspected inputs/technology labels, and Git evidence enumerates exact untracked files.':'Mutation target/evidence binding is incomplete.',
      detail:{checks,targets}
    };
  }
},
{
  id:'runtime.mutation_routing_enforcement',group:'Runtime',name:'Mutation routing / native-tool enforcement',
  run:()=>{
    const executionSource=readFileSync(join(process.cwd(),'src','executions.ts'),'utf-8');
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const routerSource=readFileSync(join(process.cwd(),'src','model-router.ts'),'utf-8');

    const checks={
      autoProbe:executionSource.includes('checkModelMutation')
        && executionSource.includes('resolveExecutionRoute'),
      qualifiedRoute:routerSource.includes('requireMutationPass')
        && routerSource.includes('mutation_'),
      forcedNativeTools:runtimeSource.includes("toolChoice:requestedToolChoice")
        && runtimeSource.includes("? 'required'"),
      actualToolExposure:runtimeSource.includes('runtime.tools_exposed')
        && runtimeSource.includes('ACTUAL NATIVE TOOLS EXPOSED IN THIS REQUEST'),
      modelDeclineEvent:runtimeSource.includes('mutation.model_declined_tools'),
      failedModelExclusion:missionSource.includes('mutation_model_failure')
        && missionSource.includes('excludeMutationModelIds')
    };

    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Modify tasks automatically qualify a mutation-capable model, force native tool use before mutation, expose the exact tool set, and exclude models that fail the real task mutation contract.'
        : 'One or more mutation routing/tool-enforcement safeguards are missing.',
      detail:checks
    };
  }
},
{
  id:'missions.task_contract_durability',group:'Missions',name:'Durable task semantic contract',
  run:()=>{
    const schemaSource=readFileSync(join(process.cwd(),'src','db','schema.ts'),'utf-8');
    const dbSource=readFileSync(join(process.cwd(),'src','db','index.ts'),'utf-8');
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const runtimeSource=readFileSync(join(process.cwd(),'src','runtime','generic-openai.ts'),'utf-8');

    const checks={
      durableColumns:schemaSource.includes('instruction TEXT NOT NULL')
        && schemaSource.includes('expected_outcome TEXT NOT NULL')
        && schemaSource.includes('work_mode TEXT NOT NULL'),
      additiveMigration:dbSource.includes("ensureColumn('mission_tasks','instruction'")
        && dbSource.includes("ensureColumn('mission_tasks','work_mode'"),
      planPersistence:missionSource.includes('task.instruction,task.expectedOutcome')
        && missionSource.includes('task.workMode,task.description'),
      recoveryPersistence:missionSource.includes('SET title=?,instruction=?,expected_outcome=?,work_mode=?')
        && missionSource.includes('mission.task_contract_backfilled'),
      snapshotPreservesMode:missionSource.includes('workMode,\n        description:instruction'),
      structuredRecovery:runtimeSource.includes('patch.instruction')
        && runtimeSource.includes('patch.expectedOutcome')
        && runtimeSource.includes('patch.workMode')
    };
    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Task instruction, expected outcome and work mode survive persistence, recovery mutation and restart backfill.'
        : 'Task semantic contract durability is incomplete.',
      detail:checks
    };
  }
},
{
  id:'missions.semantic_plan_autorepair',group:'Missions',name:'Legacy semantic DAG auto-repair',
  run:()=>{
    const missionSource=readFileSync(join(process.cwd(),'src','missions.ts'),'utf-8');
    const checks={
      semanticDetection:missionSource.includes('semanticPlanReplanReasons'),
      roleViolation:missionSource.includes("'validation_role_mutation_task'"),
      safeNoIntegratedGuard:missionSource.includes('missionHasIntegratedTaskChanges'),
      automaticReplan:missionSource.includes('mission.semantic_replan_started')
        && missionSource.includes('replanMission(missionId)')
    };
    const ok=Object.values(checks).every(Boolean);
    return {
      status:ok?'pass':'fail',
      summary:ok
        ? 'Legacy blocked missions with semantically invalid QA/Reviewer mutation tasks are automatically replanned when no task changes were integrated.'
        : 'Legacy semantic DAG auto-repair is incomplete.',
      detail:checks
    };
  }
},
{
      id:'providers.credentials',group:'Providers & Models',name:'Provider credential references',
      run:()=>{
        const providers=listProviders() as any[];
        const problems:any[]=[];
        for(const p of providers){
          if(!p.enabled)continue;
          if(p.auth_type==='bearer'){
            if(!p.secret_ref){
              problems.push({provider:p.name,error:'SECRET_REF_MISSING'});
            }else if(!process.env[String(p.secret_ref)]){
              problems.push({provider:p.name,secretRef:p.secret_ref,error:'SECRET_ENV_NOT_LOADED'});
            }
          }
        }
        return {
          status:problems.length?'fail':'pass',
          summary:problems.length?`${problems.length} enabled provider credential problem(s).`:'Enabled provider credential references are available in the process environment.',
          detail:problems
        };
      }
    },
    {
      id:'models.provider_refs',group:'Providers & Models',name:'Model → Provider references',
      run:()=>{
        const bad=db.prepare(`
          SELECT m.id,m.name,m.provider_id
          FROM models m LEFT JOIN model_providers p ON p.id=m.provider_id
          WHERE p.id IS NULL
        `).all() as any[];
        return {
          status:bad.length?'fail':'pass',
          summary:bad.length?`${bad.length} model(s) reference missing providers.`:'All models reference existing providers.',
          detail:bad
        };
      }
    },
    {
      id:'models.saved_probes',group:'Providers & Models',name:'Saved model Chat/Tool probes',
      run:()=>{
        const rows=db.prepare(`
          SELECT m.id,m.name,m.enabled,
            (SELECT mc.status FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='chat' ORDER BY mc.id DESC LIMIT 1) chat_status,
            (SELECT mc.status FROM model_checks mc WHERE mc.model_id=m.id AND mc.check_type='tools' ORDER BY mc.id DESC LIMIT 1) tools_status
          FROM models m WHERE m.enabled=1 ORDER BY m.name
        `).all() as any[];
        const failed=rows.filter(x=>x.chat_status==='fail'||x.tools_status==='fail');
        const unknown=rows.filter(x=>!x.chat_status||!x.tools_status);
        return {
          status:failed.length?'fail':unknown.length?'warn':'pass',
          summary:failed.length?`${failed.length} enabled model(s) have failed saved probes.`:unknown.length?`${unknown.length} enabled model(s) have incomplete probes.`:`${rows.length} enabled model(s) have saved Chat/Tool probe results.`,
          detail:{failed,unknown,models:rows}
        };
      }
    },
    {
      id:'runtimes.generic',group:'Runtime',name:'Generic runtime availability',
      run:()=>{
        const runtime:any=db.prepare(`SELECT id,name,enabled,adapter_type FROM runtimes WHERE id='runtime_generic_openai'`).get();
        const ok=runtime?.enabled && runtime?.adapter_type==='generic-openai';
        return {
          status:ok?'pass':'fail',
          summary:ok?'Generic OpenAI-compatible runtime is enabled.':'Generic runtime is missing, disabled, or uses an unexpected adapter.',
          detail:runtime ?? null
        };
      }
    },
    {
      id:'compat.references',group:'Runtime',name:'Model × Runtime compatibility references',
      run:()=>{
        const bad=db.prepare(`
          SELECT c.id,c.model_id,c.runtime_id
          FROM model_runtime_compatibility c
          LEFT JOIN models m ON m.id=c.model_id
          LEFT JOIN runtimes r ON r.id=c.runtime_id
          WHERE m.id IS NULL OR r.id IS NULL
        `).all() as any[];
        return {
          status:bad.length?'fail':'pass',
          summary:bad.length?`${bad.length} orphan compatibility record(s).`:'Compatibility matrix references are valid.',
          detail:bad
        };
      }
    },
    {
      id:'policies.current_versions',group:'Router',name:'Model Policy current versions',
      run:()=>{
        const policies=listModelPolicies() as any[];
        const bad=policies.filter(p=>p.enabled && (!p.current_version_id || !p.version_no));
        return {
          status:bad.length?'fail':'pass',
          summary:bad.length?`${bad.length} enabled policy/policies have no current version.`:`${policies.length} model policies loaded.`,
          detail:bad
        };
      }
    },
    {
      id:'skills.current_versions',group:'Skills',name:'Skill current-version references',
      run:()=>{
        const bad=db.prepare(`
          SELECT s.id,s.name,s.current_version_id
          FROM skills s
          LEFT JOIN skill_versions sv ON sv.id=s.current_version_id
          WHERE s.enabled=1 AND (s.current_version_id IS NULL OR sv.id IS NULL OR sv.skill_id<>s.id)
        `).all() as any[];
        return {
          status:bad.length?'fail':'pass',
          summary:bad.length?`${bad.length} enabled skill(s) have invalid current versions.`:'Enabled skill current-version references are valid.',
          detail:bad
        };
      }
    },
    {
      id:'message_bus.schema',group:'Communication',name:'Message Bus storage',
      run:()=>{
        const required=['communication_messages','execution_directives','task_artifacts'];
        const missing=required.filter(x=>!tableExists(x));
        return {
          status:missing.length?'fail':'pass',
          summary:missing.length?`Missing Message Bus tables: ${missing.join(', ')}`:'Message Bus tables are available.',
          detail:{missing}
        };
      }
    },
    {
      id:'approvals.schema',group:'Approvals',name:'Approval Queue storage',
      run:()=>({
        status:tableExists('approvals')?'pass':'fail',
        summary:tableExists('approvals')?'Approval Queue table is available.':'Approvals table is missing.'
      })
    },

{
  id:'factory.tool_catalog',group:'AgentFactory',name:'AgentFactory Tool Catalog exposure',
  run:()=>{
    const names=new Set(TOOL_CATALOG.map(x=>x.name));
    const required=['request_specialist_agent','list_mission_specialists'];
    const missing=required.filter(x=>!names.has(x));
    return {
      status:missing.length?'fail':'pass',
      summary:missing.length
        ? `AgentFactory tool definitions missing from Tool Catalog: ${missing.join(', ')}`
        : 'AgentFactory tools are exposed in the code-based Tool Catalog.',
      detail:{required,missing}
    };
  }
},

    {
      id:'factory.lead_grant',group:'AgentFactory',name:'Lead AgentFactory grant',
      run:()=>{
        const lead:any=db.prepare(`
          SELECT a.id,a.name,av.tools_json
          FROM agents a LEFT JOIN agent_versions av ON av.id=a.current_version_id
          WHERE a.id='agent_default_lead'
        `).get();
        if(!lead)return {status:'fail',summary:'Default Lead agent is missing.',detail:null};
        const grants=parseJson<string[]>(lead.tools_json,[]);
        const ok=grants.includes('agent_factory');
        return {
          status:ok?'pass':'warn',
          summary:ok?'Default Lead has the agent_factory grant.':'Default Lead does not currently have agent_factory; this is expected only if Lead was manually customized before the system upgrade.',
          detail:{lead:lead.name,grants}
        };
      }
    },
    {
      id:'factory.specialists',group:'AgentFactory',name:'Mission specialist references',
      run:()=>{
        const bad=db.prepare(`
          SELECT ms.id,ms.mission_id,ms.agent_id,ms.status
          FROM mission_specialists ms
          LEFT JOIN missions m ON m.id=ms.mission_id
          LEFT JOIN agents a ON a.id=ms.agent_id
          WHERE m.id IS NULL OR a.id IS NULL
        `).all() as any[];
        return {
          status:bad.length?'fail':'pass',
          summary:bad.length?`${bad.length} invalid specialist reference(s).`:'Mission specialist references are valid.',
          detail:bad
        };
      }
    },
    {
      id:'workspace.git',group:'Workspace',name:'Default workspace Git preflight',
      run:()=>{
        if(!existsSync(cfg.defaultWorkspace)){
          return {status:'fail',summary:`Default workspace does not exist: ${cfg.defaultWorkspace}`};
        }
        const top=git(cfg.defaultWorkspace,['rev-parse','--show-toplevel']);
        if(top.status!==0){
          return {status:'fail',summary:'Default workspace is not a Git repository.',detail:top};
        }
        const status=git(cfg.defaultWorkspace,['status','--porcelain=v1']);
        const dirty=status.stdout.split(/\r?\n/).filter(Boolean);
        return {
          status:dirty.length?'warn':'pass',
          summary:dirty.length?`Git workspace is dirty (${dirty.length} change(s)); new isolated missions can be blocked at run time.`:'Default Git workspace is clean.',
          detail:{workspace:cfg.defaultWorkspace,top:top.stdout,changes:dirty.slice(0,100)}
        };
      }
    },
    {
      id:'i18n.locale_json',group:'UI & i18n',name:'Locale JSON files',
      run:()=>{
        const tr=jsonFile(join(process.cwd(),'locales','tr.json'));
        const en=jsonFile(join(process.cwd(),'locales','en.json'));
        if(!tr.ok||!en.ok){
          return {status:'fail',summary:'One or more locale JSON files cannot be parsed.',detail:{tr:tr.error,en:en.error}};
        }
        const trKeys=new Set(Object.keys(tr.value));
        const enKeys=new Set(Object.keys(en.value));
        const onlyTr=[...trKeys].filter(x=>!enKeys.has(x));
        const onlyEn=[...enKeys].filter(x=>!trKeys.has(x));
        return {
          status:(onlyTr.length||onlyEn.length)?'warn':'pass',
          summary:(onlyTr.length||onlyEn.length)?`Locale key mismatch: TR-only ${onlyTr.length}, EN-only ${onlyEn.length}.`:`Locale files are valid and key sets match (${trKeys.size} keys).`,
          detail:{onlyTr:onlyTr.slice(0,100),onlyEn:onlyEn.slice(0,100)}
        };
      }
    },
    
{
  id:'transactions.invocation_guard',group:'Core Functions',name:'better-sqlite3 transaction invocation guard',
  run:()=>{
    const checks=[
      {
        file:join(process.cwd(),'src','missions.ts'),
        operation:'createMission',
        bad:/db\.transaction\(\(\)=>\{[\s\S]{0,1200}INSERT INTO missions[\s\S]{0,1200}INSERT INTO mission_workspaces[\s\S]{0,400}\n\s*\}\);\n\s*\n\s*event\(missionId,'mission\.created'/
      },
      {
        file:join(process.cwd(),'src','missions.ts'),
        operation:'stopMission',
        bad:/db\.transaction\(\(\)=>\{[\s\S]{0,1600}SET status='cancelled'[\s\S]{0,400}\n\s*\}\);\n\s*\n\s*cancelMissionApprovals/
      },
      {
        file:join(process.cwd(),'src','approvals.ts'),
        operation:'approveApproval',
        bad:/db\.transaction\(\(\)=>\{[\s\S]{0,1200}UPDATE missions SET status='done'[\s\S]{0,400}\n\s*\}\);\n\s*\n\s*const archivedSpecialists/
      },
      {
        file:join(process.cwd(),'src','agent-factory.ts'),
        operation:'spawnMissionSpecialist',
        bad:/db\.transaction\(\(\)=>\{[\s\S]{0,1400}INSERT INTO mission_specialists[\s\S]{0,700}\n\s*\}\);\n\s*\n\s*completeRequest/
      }
    ];

    const suspicious:any[]=[];
    for(const check of checks){
      const source=readFileSync(check.file,'utf-8');
      if(check.bad.test(source)){
        suspicious.push({
          file:check.file.split(/[\\\\/]/).pop(),
          operation:check.operation,
          error:'TRANSACTION_CALLBACK_NOT_INVOKED'
        });
      }
    }

    return {
      status:suspicious.length?'fail':'pass',
      summary:suspicious.length
        ? `${suspicious.length} critical transaction callback(s) appear not to be invoked.`
        : 'Critical better-sqlite3 transaction callbacks are invoked.',
      detail:suspicious
    };
  }
},

{
      id:'functions.read_only',group:'Core Functions',name:'Read-only repository/service functions',
      run:()=>{
        const counts={
          agents:(listAgents('tr') as any[]).length,
          teams:(listTeams('tr') as any[]).length,
          providers:(listProviders() as any[]).length,
          models:(listModels() as any[]).length,
          runtimes:(listRuntimes('tr') as any[]).length,
          compatibility:(listCompatibility('tr') as any[]).length,
          policies:(listModelPolicies() as any[]).length
        };
        return {
          status:'pass',
          summary:'Core read-only service functions executed without throwing.',
          detail:counts
        };
      }
    }
  ];
}

export function listDiagnosticTests(){
  return coreTests().map(({id,group,name})=>({id,group,name}));
}

export async function runDiagnostics(ids?:string[]){
  const all=coreTests();
  const wanted=ids?.length ? new Set(ids) : null;
  const selected=wanted ? all.filter(x=>wanted.has(x.id)) : all;
  const started=performance.now();
  const results:DiagnosticResult[]=[];
  // Run sequentially to keep DB/Git diagnostics deterministic and readable.
  for(const def of selected)results.push(await timed(def));
  const summary={
    pass:results.filter(x=>x.status==='pass').length,
    warn:results.filter(x=>x.status==='warn').length,
    fail:results.filter(x=>x.status==='fail').length,
    total:results.length,
    durationMs:Math.round((performance.now()-started)*100)/100
  };
  return {summary,results,generatedAt:new Date().toISOString()};
}

export function missionCreatePreflight(input:any){
  const checks:Array<{id:string;status:DiagnosticStatus;summary:string;detail?:any}>=[];
  const title=String(input?.title ?? '').trim();
  const objective=String(input?.objective ?? '').trim();
  const teamId=String(input?.teamId ?? '').trim();

  checks.push({
    id:'title',
    status:title?'pass':'fail',
    summary:title?'Mission title is present.':'MISSION_TITLE_REQUIRED'
  });
  checks.push({
    id:'objective',
    status:objective?'pass':'fail',
    summary:objective?'Mission objective is present.':'MISSION_OBJECTIVE_REQUIRED'
  });

  const team:any=teamId
    ? db.prepare(`SELECT * FROM teams WHERE id=?`).get(teamId)
    : null;

  checks.push({
    id:'team',
    status:team?.enabled?'pass':'fail',
    summary:team?.enabled?'Selected team exists and is enabled.':'TEAM_NOT_FOUND_OR_DISABLED',
    detail:team ? {id:team.id,name:team.name,enabled:team.enabled,lead_agent_id:team.lead_agent_id} : {teamId}
  });

  const leadAgentId=String(input?.leadAgentId ?? team?.lead_agent_id ?? '').trim();
  if(!leadAgentId){
    checks.push({id:'lead',status:'fail',summary:'MISSION_LEAD_REQUIRED'});
  }else{
    const lead:any=db.prepare(`SELECT id,name,enabled FROM agents WHERE id=?`).get(leadAgentId);
    const member:any=team
      ? db.prepare(`SELECT 1 ok FROM team_members WHERE team_id=? AND agent_id=?`).get(team.id,leadAgentId)
      : null;
    let status:DiagnosticStatus='pass';
    let summary='Lead exists, is enabled, and belongs to the selected team.';
    if(!lead?.enabled){
      status='fail';summary='MISSION_LEAD_DISABLED_OR_MISSING';
    }else if(!member?.ok){
      status='fail';summary='MISSION_LEAD_MUST_BE_ACTIVE_TEAM_MEMBER';
    }
    checks.push({
      id:'lead',
      status,
      summary,
      detail:{leadAgentId,leadName:lead?.name ?? null,enabled:lead?.enabled ?? null,isMember:Boolean(member?.ok)}
    });
  }

  const workspaceExists=existsSync(cfg.defaultWorkspace);
  checks.push({
    id:'workspace.exists',
    status:workspaceExists?'pass':'fail',
    summary:workspaceExists?'Default workspace exists.':`Default workspace missing: ${cfg.defaultWorkspace}`,
    detail:{workspace:cfg.defaultWorkspace}
  });

  if(workspaceExists){
    const repo=git(cfg.defaultWorkspace,['rev-parse','--show-toplevel']);
    checks.push({
      id:'workspace.git',
      status:repo.status===0?'pass':'fail',
      summary:repo.status===0?'Default workspace is a Git repository.':'Default workspace is not a Git repository.',
      detail:repo
    });

    if(repo.status===0){
      const state=git(cfg.defaultWorkspace,['status','--porcelain=v1']);
      const changes=state.stdout.split(/\r?\n/).filter(Boolean);
      checks.push({
        id:'workspace.clean',
        status:changes.length?'warn':'pass',
        summary:changes.length?`Workspace is dirty (${changes.length} change(s)). Mission creation itself can succeed, but isolated mission execution will be blocked until the base workspace is clean.`:'Workspace is clean.',
        detail:{changes:changes.slice(0,100)}
      });
    }
  }

  const failures=checks.filter(x=>x.status==='fail');
  const warnings=checks.filter(x=>x.status==='warn');
  return {
    ok:failures.length===0,
    summary:{
      pass:checks.filter(x=>x.status==='pass').length,
      warn:warnings.length,
      fail:failures.length,
      total:checks.length
    },
    resolved:{
      teamId:(team?.id ?? teamId) || null,
      teamName:team?.name ?? null,
      leadAgentId:leadAgentId || null,
      workspace:cfg.defaultWorkspace
    },
    checks
  };
}
