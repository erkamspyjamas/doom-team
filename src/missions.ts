import { db, id, now, parseJson } from './db/index.js';
import { startExecution, getExecution, executionEvents, cancelExecution } from './executions.js';
import { resolveToolDefinitions } from './tools/catalog.js';
import { loadConfig } from './config.js';
import { ensureMissionMergeApproval,missionApprovals,cancelMissionApprovals } from './approvals.js';
import { hardConstraintPrompt,validatePlanAgainstBossConstraints,validateTextAgainstBossConstraints } from './plan-constraints.js';
import { canonicalizePlanRoleContracts,documentationSpecialistContract,validatePlanRoleObligations } from './plan-quality.js';
import {
  missionRoster,missionAgentIsActive,listMissionSpecialists,archiveMissionSpecialists,
  archiveMissionSpecialist,spawnMissionSpecialist
} from './agent-factory.js';
import {
  taskCommunicationContext,prerequisiteArtifactContext,publishTaskArtifact,
  createAutomaticHandoffs,taskArtifacts,missionArtifacts,listMessages,publishMessage
} from './communications.js';
import { attemptJsonRepair } from './runtime/generic-openai.js';
import {
  ensureMissionIntegrationWorkspace,
  createTaskWorktree,
  taskWorkspaceEvidence,
  commitAndMergeTask,
  removeTaskWorktree,
  inspectGitWorkspace,
  missionIntegrationEvidence,
} from './git/worktrees.js';
import { preserveMissionTaskEventsForReplan } from './replan-history.js';
import {
  finalGateRecoveryAttemptCount,
  missionRecoveryAttemptCount,
  recoveryEpochStartId,
  taskRecoveryAttemptCount,
} from './recovery-budget.js';
import {
  decideStartupRecovery,
  hasUnusedTaskRecoveryBudget,
  MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
} from './startup-recovery-policy.js';
import {
  inferTaskMutationTargetPaths,
  inferTaskWorkMode as inferTaskWorkModeContract,
  isCompletedStateTaskInstruction,
  resolveTaskWorkModeContract,
} from './task-contracts.js';

const cfg=loadConfig();

type PlanTask = {
  key:string;
  title:string;
  instruction:string;
  expectedOutcome:string;
  workMode:'modify'|'validate'|'analysis'|'general';
  description:string; // backward-compatible DB alias; stores executable instruction
  assignedAgentId:string;
  acceptanceCriteria:string[];
  dependsOn:string[];
  priority:number;
};

type MissionPlan = {
  summary?:string;
  tasks:PlanTask[];
};

const activePlanning = new Set<string>();
const activeMissionLoops = new Set<string>();

const MAX_AUTONOMOUS_RECOVERY_PER_TASK=3;
const MAX_AUTONOMOUS_RECOVERY_PER_MISSION=8;
const MAX_AUTONOMOUS_FINAL_GATE_RECOVERY=3;

type RecoveryResult={
  recovered:boolean;
  escalated?:boolean;
  action?:string;
  reason?:string;
};

function missionRow(missionId:string) {
  return db.prepare(`SELECT * FROM missions WHERE id=?`).get(missionId) as any;
}

function event(missionId:string,eventType:string,payload:any={},taskId:string|null=null) {
  db.prepare(`
    INSERT INTO mission_events(mission_id,task_id,event_type,payload_json,created_at)
    VALUES (?,?,?,?,?)
  `).run(missionId,taskId,eventType,JSON.stringify(payload ?? {}),now());
}

function evidence(
  missionId:string,
  taskId:string,
  executionId:string|null,
  evidenceType:string,
  status:string,
  payload:any,
){
  db.prepare(`
    INSERT INTO mission_task_evidence
    (mission_id,task_id,execution_id,evidence_type,status,payload_json,created_at)
    VALUES (?,?,?,?,?,?,?)
  `).run(missionId,taskId,executionId,evidenceType,status,JSON.stringify(payload ?? {}),now());
}

function teamMembers(teamId:string) {
  const rows=db.prepare(`
    SELECT a.id,a.name,a.slug,a.description,tm.role_label,tm.priority,tm.can_delegate,
           av.tools_json,av.skills_json,av.required_capabilities_json,av.autonomy_level
    FROM team_members tm
    JOIN agents a ON a.id=tm.agent_id
    LEFT JOIN agent_versions av ON av.id=a.current_version_id
    WHERE tm.team_id=? AND a.enabled=1
    ORDER BY tm.priority,a.name
  `).all(teamId) as any[];

  return rows.map(row=>({
    ...row,
    temporary:false,
    tools:resolveToolDefinitions(parseJson<string[]>(row.tools_json,[])).map(x=>x.name),
    skills:parseJson<string[]>(row.skills_json,[]),
    requiredCapabilities:parseJson<string[]>(row.required_capabilities_json,[]),
    autonomyLevel:Number(row.autonomy_level ?? 0),
  }));
}

function findTeamAgentByRole(teamId:string,role:string){
  const exact=db.prepare(`
    SELECT a.id,a.name,a.slug,tm.role_label
    FROM team_members tm JOIN agents a ON a.id=tm.agent_id
    WHERE tm.team_id=? AND a.enabled=1 AND lower(tm.role_label)=lower(?)
    ORDER BY tm.priority LIMIT 1
  `).get(teamId,role) as any;
  if(exact)return exact;

  return db.prepare(`
    SELECT a.id,a.name,a.slug,tm.role_label
    FROM team_members tm JOIN agents a ON a.id=tm.agent_id
    WHERE tm.team_id=? AND a.enabled=1
      AND (lower(a.slug) LIKE ? OR lower(a.name) LIKE ?)
    ORDER BY tm.priority LIMIT 1
  `).get(teamId,`%${role.toLowerCase()}%`,`%${role.toLowerCase()}%`) as any;
}

function sleep(ms:number) {
  return new Promise(resolve=>setTimeout(resolve,ms));
}

async function waitForExecution(executionId:string, timeoutMs=15*60*1000) {
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const execution:any=getExecution(executionId);
    if(!execution) throw new Error('EXECUTION_DISAPPEARED');
    if(['done','error','cancelled'].includes(execution.status)) return execution;
    await sleep(650);
  }
  throw new Error('EXECUTION_WAIT_TIMEOUT');
}

export function extractJsonObject(text:string) {
  const raw=String(text ?? '').trim();
  
  // Try direct parse first
  try { return JSON.parse(raw); } catch {}
  
  // Try fenced JSON
  const fenced=raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced) {
    try { return JSON.parse(fenced[1].trim()); } catch {}
  }

  // Extract balanced JSON objects (like in generic-openai.ts)
  const extractBalancedJsonObjects = (inputText: string): string[] => {
    const results: string[] = [];
    let depth = 0;
    let start = -1;
    let inString = false;
    let escape = false;

    for (let i = 0; i < inputText.length; i++) {
      const ch = inputText[i];

      if (escape) {
        escape = false;
        continue;
      }
      if (ch === '\\') {
        escape = true;
        continue;
      }
      if (ch === '"' && !escape) {
        inString = !inString;
        continue;
      }
      if (inString) continue;

      if (ch === '{') {
        if (depth === 0) start = i;
        depth++;
      } else if (ch === '}') {
        if (depth > 0) {
          depth--;
          if (depth === 0 && start >= 0) {
            results.push(inputText.slice(start, i + 1));
            start = -1;
          }
        }
      }
    }

    return results;
  };

  const balancedObjects = extractBalancedJsonObjects(raw);
  for (const obj of balancedObjects) {
    try { return JSON.parse(obj.trim()); } catch {}
    // Try repairing if direct parse fails
    const repaired = attemptJsonRepair(obj.trim());
    if (repaired) {
      try { return JSON.parse(repaired); } catch {}
    }
  }

  // Fallback to original approach for backward compatibility
  const first=raw.indexOf('{');
  if (first >= 0) {
    let end = raw.lastIndexOf('}');
    if (end <= first) {
      // No closing brace after the opening brace, so we use the rest of the string.
      end = raw.length;
    }
    const substring = raw.slice(first, end);
    try { return JSON.parse(substring); } catch {}
    // Try repairing the fallback
    const repaired = attemptJsonRepair(substring);
    if (repaired) {
      try { return JSON.parse(repaired); } catch {}
    }
  }

  throw new Error('JSON_OBJECT_NOT_FOUND');
}

function assertAcyclic(tasks:PlanTask[]) {
  const deps=new Map(tasks.map(t=>[t.key,new Set(t.dependsOn)]));
  const visiting=new Set<string>();
  const visited=new Set<string>();

  const visit=(key:string)=>{
    if(visited.has(key))return;
    if(visiting.has(key))throw new Error(`MISSION_PLAN_CYCLE:${key}`);
    visiting.add(key);
    for(const dep of deps.get(key) ?? [])visit(dep);
    visiting.delete(key);
    visited.add(key);
  };

  for(const task of tasks)visit(task.key);
}


function inferWorkModeFromPlanText(task:any):PlanTask['workMode']{
  return inferTaskWorkModeContract(task);
}

function normalizePlan(raw:any, validAgentIds:Set<string>):MissionPlan {
  if(!raw || !Array.isArray(raw.tasks) || raw.tasks.length===0){
    throw new Error('LEAD_PLAN_TASKS_REQUIRED');
  }
  if(raw.tasks.length>30) throw new Error('LEAD_PLAN_TOO_MANY_TASKS');

  const seen=new Set<string>();
  const tasks:PlanTask[]=raw.tasks.map((item:any,index:number)=>{
    const key=String(item?.key ?? `T${index+1}`).trim();
    if(!key)throw new Error('LEAD_PLAN_TASK_KEY_REQUIRED');
    if(seen.has(key))throw new Error(`LEAD_PLAN_DUPLICATE_KEY:${key}`);
    seen.add(key);

    const assignedAgentId=String(item?.assignedAgentId ?? '').trim();
    if(!validAgentIds.has(assignedAgentId)){
      throw new Error(`LEAD_PLAN_AGENT_OUTSIDE_TEAM:${assignedAgentId || '(empty)'}`);
    }

    const legacyDescription=String(item?.description ?? '').trim();
    const instruction=String(item?.instruction ?? legacyDescription).trim();
    const expectedOutcome=String(
      item?.expectedOutcome
      ?? item?.expected_outcome
      ?? legacyDescription
      ?? ''
    ).trim();
    const rawWorkMode=String(item?.workMode ?? item?.work_mode ?? '').trim();
    const workMode=(
      ['modify','validate','analysis','general'].includes(rawWorkMode)
        ? rawWorkMode
        : ''
    ) as PlanTask['workMode'] | '';

    return {
      key,
      title:String(item?.title ?? '').trim(),
      instruction,
      expectedOutcome,
      workMode:workMode || inferWorkModeFromPlanText({
        title:item?.title,
        instruction,
        expectedOutcome
      }),
      // Existing DB/API code expects description. Store the imperative instruction,
      // never the completed-state outcome sentence.
      description:instruction,
      assignedAgentId,
      acceptanceCriteria:Array.isArray(item?.acceptanceCriteria)
        ? item.acceptanceCriteria.map((x:any)=>String(x).trim()).filter(Boolean).slice(0,12)
        : [],
      dependsOn:Array.isArray(item?.dependsOn)
        ? item.dependsOn.map((x:any)=>String(x).trim()).filter(Boolean)
        : [],
      priority:Number.isFinite(Number(item?.priority)) ? Number(item.priority) : (index+1)*10,
    };
  });

  for(const task of tasks){
    if(!task.title)throw new Error(`LEAD_PLAN_TITLE_REQUIRED:${task.key}`);
    if(!task.instruction)throw new Error(`LEAD_PLAN_INSTRUCTION_REQUIRED:${task.key}`);
    if(!task.expectedOutcome)throw new Error(`LEAD_PLAN_EXPECTED_OUTCOME_REQUIRED:${task.key}`);
    if(!['modify','validate','analysis','general'].includes(task.workMode)){
      throw new Error(`LEAD_PLAN_WORK_MODE_INVALID:${task.key}:${task.workMode}`);
    }
    for(const dep of task.dependsOn){
      if(!seen.has(dep))throw new Error(`LEAD_PLAN_UNKNOWN_DEPENDENCY:${task.key}:${dep}`);
      if(dep===task.key)throw new Error(`LEAD_PLAN_SELF_DEPENDENCY:${task.key}`);
    }
  }

  assertAcyclic(tasks);
  return {summary:String(raw?.summary ?? ''),tasks};
}

function applyPlan(missionId:string,plan:MissionPlan) {
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  const existing=(db.prepare(`SELECT COUNT(*) c FROM mission_tasks WHERE mission_id=?`).get(missionId) as any)?.c ?? 0;
  if(Number(existing)>0)throw new Error('MISSION_ALREADY_HAS_TASKS');

  const t=now();
  const byKey=new Map<string,string>();

  db.transaction(()=>{
    const taskInsert=db.prepare(`
      INSERT INTO mission_tasks
      (id,mission_id,task_key,sequence_no,title,instruction,expected_outcome,work_mode,
       description,acceptance_criteria_json,assigned_agent_id,status,priority,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)
    `);

    plan.tasks.forEach((task,index)=>{
      const taskId=id('mtask');
      byKey.set(task.key,taskId);
      taskInsert.run(
        taskId,missionId,task.key,index+1,task.title,task.instruction,task.expectedOutcome,
        task.workMode,task.description,JSON.stringify(task.acceptanceCriteria),
        task.assignedAgentId,task.priority,t,t
      );
    });

    const depInsert=db.prepare(`
      INSERT INTO mission_task_dependencies(task_id,depends_on_task_id)
      VALUES (?,?)
    `);
    for(const task of plan.tasks){
      for(const depKey of task.dependsOn){
        depInsert.run(byKey.get(task.key),byKey.get(depKey));
      }
    }

    db.prepare(`
      UPDATE missions
      SET status='ready',plan_json=?,error_text=NULL,updated_at=?
      WHERE id=?
    `).run(JSON.stringify(plan),t,missionId);
  })();

  event(missionId,'mission.planned',{
    taskCount:plan.tasks.length,
    summary:plan.summary ?? ''
  });
}

function planningPrompt(mission:any,members:any[]) {
  const roster=members.map(m=>({
    agentId:m.id,
    name:m.name,
    slug:m.slug,
    role:m.role_label,
    description:m.description,
    tools:m.tools,
    skills:m.skills,
    autonomyLevel:m.autonomyLevel
  }));

  return `You are planning a Doom Team mission as the Lead.

MISSION TITLE:
${mission.title}

BOSS OBJECTIVE:
${mission.objective}

BOSS HARD CONSTRAINTS:
${hardConstraintPrompt(mission.objective)}

AVAILABLE TEAM MEMBERS:
${JSON.stringify(roster,null,2)}

Create an executable dependency DAG. Assign every task to exactly one agentId from the roster.
Prefer parallelizable, evidence-producing tasks. Do not create vague tasks.

Your FINAL ANSWER MUST BE ONLY one valid JSON object. No markdown fences.

Schema:
{
  "summary": "short plan summary",
  "tasks": [
    {
      "key": "T1",
      "title": "short title",
      "instruction": "imperative action the assigned agent must perform now",
      "expectedOutcome": "state that should be true after the task is completed",
      "workMode": "modify|validate|analysis|general",
      "assignedAgentId": "exact agentId from roster",
      "acceptanceCriteria": ["measurable evidence criterion 1"],
      "dependsOn": [],
      "priority": 10
    }
  ]
}

Rules:
- 2 to 12 tasks unless truly necessary.
- dependencies are task keys.
- no circular dependencies.
- every assignedAgentId must exactly match the roster.
- match work to role AND listed tools.
- instruction is an ACTION COMMAND, not a completion claim. Use imperative wording such as "README.md dosyasını güncelle", never "README.md güncellendi/yazıldı".
- expectedOutcome describes the post-task state. Keep it separate from instruction.
- workMode is mandatory: modify for repository/file creation or edits; validate for QA/Reviewer verification; analysis for read-only analysis/planning; general only when none fit.
- If instruction says oluştur/create/yaz/write/ekle/add/güncelle/update/düzenle/edit/değiştir/modify for a repository artifact, workMode MUST be modify.
- QA and Reviewer tasks MUST be validate/read-only; never assign repository creation/editing to QA or Reviewer. If tests/code must be authored, create a separate implementation task first, then QA validates/runs them.
- Acceptance criteria are evidence checks, not instructions and not invented future approvals.
- repository edits require write_file or replace_in_file.
- test execution should use an agent with run_tests.
- Product and UX are analysis/specification roles, not implementation roles.
- Review/Security/QA tasks that inspect implementation must depend on that implementation.
- Backend/Frontend/DevOps perform repository modifications according to artifact ownership.
- If the roster lacks a genuinely necessary specialist and request_specialist_agent is available,
  call request_specialist_agent BEFORE producing the final JSON, then use the returned specialist.agent_id / agent.id in assignedAgentId.
- During initial planning DO NOT pass assign_task_key to request_specialist_agent; DAG task rows do not exist yet.
- Do not create a specialist when a permanent team member already fits the work.
- Reuse a returned/reused specialist; never repeatedly request the same role.
- Maximum planning factory behavior: at most 2 specialist-create attempts total.
- After a successful specialist create/reuse tool result, immediately finish the DAG unless a second genuinely different specialist is essential.
- Do not inspect repository files, run tests, use git, or send Message Bus chatter during planning.
- Specialists are temporary mission resources; keep their scope narrow.
- Boss HARD CONSTRAINTS are non-negotiable. Never create a task or acceptance criterion that contradicts them.
- Example: "Yeni dependency ekleme" means DO NOT add/install/declare a dependency anywhere in the DAG.
- If Boss explicitly says QA must validate, create a QA task assigned to the actual QA agent and make it depend on implementation.
- If Boss explicitly says Reviewer must perform final review, create a Reviewer task assigned to the actual Reviewer agent and make it depend on QA.
- Never assign QA/Reviewer responsibilities to a specialist merely because the specialist performed implementation.
- final answer is JSON only.`;
}




function ensureBossRequiredSpecialists(
  mission:any,
  sourceExecutionId:string
){
  const before=missionRoster(mission.id);
  const contract=documentationSpecialistContract(mission.objective,before);

  if(!contract.required || !contract.needsCreation){
    return {
      contract,
      created:false,
      result:null
    };
  }

  const result:any=spawnMissionSpecialist({
    missionId:mission.id,
    requestedByType:'lead',
    requestedByAgentId:mission.lead_agent_id,
    sourceExecutionId,
    name:String(contract.name ?? 'Teknik Dokümantasyon Uzmanı'),
    roleName:String(contract.roleName ?? 'Technical Documentation Specialist'),
    objective:`Own the README technical documentation work required by the Boss for mission "${mission.title}". Follow the full Boss objective and never claim work without evidence.`,
    scopeType:'mission',
    toolProfiles:['read','write','git','tests','skills','communication'],
    skillIds:[],
    requiredCapabilities:['chat','tools'],
    modelStrategy:'tools_required',
    autonomyLevel:2
  });

  event(mission.id,'mission.required_specialist_resolved',{
    contract,
    created:!Boolean(result?.reused),
    reused:Boolean(result?.reused),
    agentId:String(result?.agent?.id ?? result?.specialist?.agent_id ?? ''),
    sourceExecutionId
  });

  return {
    contract,
    created:!Boolean(result?.reused),
    result
  };
}

function planViolationLabel(v:any){
  if(v?.id)return String(v.id);

  const constraint=String(v?.constraintId ?? 'constraint');
  const task=String(v?.taskKey ?? '?');
  const field=String(v?.field ?? '?');
  return `${constraint}@${task}.${field}`;
}

async function repairPlanForBossConstraints(
  mission:any,
  planningExecutionId:string,
  rawPlan:any,
  violations:any[]
){
  event(mission.id,'mission.plan_constraint_repair_started',{
    planningExecutionId,
    violationCount:violations.length
  });

  const repair:any=await startExecution(
    mission.lead_agent_id,
    `Repair this Doom Team mission plan.

BOSS OBJECTIVE:
${mission.objective}

BOSS HARD CONSTRAINTS:
${hardConstraintPrompt(mission.objective)}

CURRENT MISSION ROSTER WITH EXACT AGENT IDS:
${JSON.stringify(
  missionRoster(mission.id).map((a:any)=>({
    id:a.id,
    name:a.name,
    roleLabel:a.role_label,
    temporary:Boolean(a.temporary)
  })),
  null,
  2
)}

DETERMINISTIC VIOLATIONS:
${JSON.stringify(violations,null,2)}

ORIGINAL PLAN:
${JSON.stringify(rawPlan,null,2)}

Return ONLY corrected valid mission-plan JSON.
Every task MUST contain: key, title, instruction, expectedOutcome, workMode, assignedAgentId, acceptanceCriteria, dependsOn, priority.
instruction MUST be an imperative action to execute now; never phrase it as already completed.
expectedOutcome MUST describe the post-task state and must not replace the action instruction.
Remove or rewrite every violation.
Preserve useful non-conflicting tasks and exact agent IDs.
If the current roster contains a temporary Technical Documentation Specialist and Boss asked that specialist to own README editing, assign the README modification task to that exact specialist agent ID.
If Boss requires QA, use the exact QA agent ID from the roster.
If Boss requires final Reviewer review, use the exact Reviewer agent ID and make it depend on QA.
Do not call tools. Do not use markdown fences.`,
    {
      workspace:cfg.defaultWorkspace,
      context:{
        missionId:mission.id,
        phase:'plan_constraint_repair',
        expectedFinalFormat:'mission_plan_json',
        planningToolStepBudget:4,
        parentPlanningExecutionId:planningExecutionId
      },
      toolGrantsOverride:[]
    }
  );

  const finished:any=await waitForExecution(repair.id);
  if(finished.status!=='done'){
    throw new Error(`PLAN_CONSTRAINT_REPAIR_EXECUTION_FAILED:${finished.error_text ?? finished.status}`);
  }

  let repaired=extractJsonObject(finished.result_text ?? '');
  const repairedCanonical=canonicalizePlanRoleContracts(mission.objective,repaired,missionRoster(mission.id));
  repaired=repairedCanonical.plan;
  if(repairedCanonical.changes.length){
    event(mission.id,'mission.plan_contract_canonicalized',{phase:'constraint_repair',changes:repairedCanonical.changes});
  }
  const hardValidation=validatePlanAgainstBossConstraints(mission.objective,repaired);
  const roleValidation=validatePlanRoleObligations(
    mission.objective,
    repaired,
    missionRoster(mission.id)
  );
  const remainingViolations=[
    ...hardValidation.violations,
    ...roleValidation.violations
  ];

  if(remainingViolations.length){
    event(mission.id,'mission.plan_constraint_repair_failed',{
      repairExecutionId:repair.id,
      violations:remainingViolations
    });
    throw new Error(
      `PLAN_QUALITY_VIOLATION_AFTER_REPAIR:${remainingViolations.map(planViolationLabel).join(',')}`
    );
  }

  event(mission.id,'mission.plan_constraint_repaired',{
    repairExecutionId:repair.id,
    taskCount:Array.isArray(repaired.tasks)?repaired.tasks.length:0
  });
  return repaired;
}

async function finishPlanning(missionId:string,executionId:string) {
  try{
    const execution:any=await waitForExecution(executionId);
    if(execution.status!=='done'){
      throw new Error(`LEAD_PLANNING_EXECUTION_${String(execution.status).toUpperCase()}:${execution.error_text ?? ''}`);
    }

    const mission=missionRow(missionId);
    if(!mission)return;
    const members=missionRoster(mission.id);
    const validAgentIds=new Set(members.map(x=>String(x.id)));
    let raw=extractJsonObject(execution.result_text ?? '');

    ensureBossRequiredSpecialists(mission,executionId);

    const initialRoster=missionRoster(mission.id);
    const initialCanonical=canonicalizePlanRoleContracts(mission.objective,raw,initialRoster);
    raw=initialCanonical.plan;
    if(initialCanonical.changes.length){
      event(mission.id,'mission.plan_contract_canonicalized',{phase:'initial_plan',changes:initialCanonical.changes});
    }
    const constraintValidation=validatePlanAgainstBossConstraints(mission.objective,raw);
    const roleValidation=validatePlanRoleObligations(mission.objective,raw,initialRoster);
    const planningViolations=[
      ...constraintValidation.violations,
      ...roleValidation.violations
    ];

    if(planningViolations.length){
      event(mission.id,'mission.plan_constraint_violation',{
        planningExecutionId:executionId,
        constraints:constraintValidation.constraints,
        roleObligations:roleValidation.obligations,
        violations:planningViolations
      });

      raw=await repairPlanForBossConstraints(
        mission,
        executionId,
        raw,
        planningViolations
      );

      validAgentIds.clear();
      for(const member of missionRoster(mission.id)){
        validAgentIds.add(String(member.id));
      }
    }

    const finalRoster=missionRoster(mission.id);
    const finalCanonical=canonicalizePlanRoleContracts(mission.objective,raw,finalRoster);
    raw=finalCanonical.plan;
    if(finalCanonical.changes.length){
      event(mission.id,'mission.plan_contract_canonicalized',{phase:'final_plan',changes:finalCanonical.changes});
    }
    const finalConstraintValidation=validatePlanAgainstBossConstraints(mission.objective,raw);
    const finalRoleValidation=validatePlanRoleObligations(
      mission.objective,
      raw,
      finalRoster
    );

    if(!finalConstraintValidation.ok || !finalRoleValidation.ok){
      throw new Error('PLAN_QUALITY_VALIDATION_FAILED_AFTER_REPAIR');
    }

    const plan=normalizePlan(raw,validAgentIds);
    applyPlan(missionId,plan);

    db.prepare(`
      UPDATE missions
      SET status='running',started_at=COALESCE(started_at,?),ended_at=NULL,
          error_text=NULL,updated_at=? WHERE id=?
    `).run(now(),now(),missionId);
    event(missionId,'mission.autorun_started',{source:'planning_complete'});
    void missionLoop(missionId);
  }catch(err:any){
    db.prepare(`
      UPDATE missions SET status='blocked',error_text=?,updated_at=? WHERE id=?
    `).run(String(err?.message ?? err),now(),missionId);
    event(missionId,'mission.planning_failed',{error:String(err?.message ?? err)});
  }finally{
    activePlanning.delete(missionId);
  }
}

function dependencies(taskId:string) {
  return db.prepare(`
    SELECT d.depends_on_task_id AS id,t.task_key,t.title,t.status,t.result_text,t.error_text
    FROM mission_task_dependencies d
    JOIN mission_tasks t ON t.id=d.depends_on_task_id
    WHERE d.task_id=?
    ORDER BY t.sequence_no
  `).all(taskId) as any[];
}

function dependencyContext(taskId:string) {
  const deps=dependencies(taskId);
  if(!deps.length)return 'No prerequisite task outputs.';
  return `IMPORTANT: Upstream agent result text is a claim, not repository evidence. Verify important claims with tools and structured artifacts.\n\n`+
  deps.map(dep=>{
    const text=String(dep.result_text ?? '').slice(0,6000);
    return `### ${dep.task_key} - ${dep.title}\nStatus: ${dep.status}\nResult:\n${text || '(no result text)'}`;
  }).join('\n\n');
}


function assignedAgentExecutionContext(mission:any,task:any){
  const agent:any=db.prepare(`
    SELECT a.id,a.name,a.description,av.tools_json
    FROM agents a
    JOIN agent_versions av ON av.id=a.current_version_id
    WHERE a.id=?
  `).get(task.assigned_agent_id);

  const specialist:any=db.prepare(`
    SELECT role_name,scope_type,status
    FROM mission_specialists
    WHERE mission_id=? AND agent_id=? AND status='active'
    ORDER BY created_at DESC LIMIT 1
  `).get(mission.id,task.assigned_agent_id);

  const grants=parseJson<string[]>(agent?.tools_json,[]);
  const tools=resolveToolDefinitions(grants).map(t=>t.name);

  return {
    agentId:String(agent?.id ?? task.assigned_agent_id),
    name:String(agent?.name ?? task.agent_name ?? ''),
    temporarySpecialist:Boolean(specialist),
    specialistRole:specialist?.role_name ?? null,
    toolGrants:grants,
    tools
  };
}

function isModificationToolAvailable(agentContext:any){
  const tools=new Set<string>(
    Array.isArray(agentContext?.tools)
      ? agentContext.tools.map(String)
      : []
  );
  return tools.has('write_file') || tools.has('replace_in_file');
}

function missionPlanTaskSpec(mission:any,task:any){
  const plan=parseJson<any>(mission?.plan_json,{});
  const found=(Array.isArray(plan?.tasks)?plan.tasks:[])
    .find((x:any)=>String(x?.key ?? '')===String(task?.task_key ?? ''));

  return found ?? null;
}

function looksLikeCompletedStateSentence(value:string){
  return isCompletedStateTaskInstruction(value);
}

function synthesizedTaskInstruction(mission:any,task:any,workMode:string){
  const spec=missionPlanTaskSpec(mission,task);
  const durable=String(task?.instruction ?? '').trim();
  if(durable && !looksLikeCompletedStateSentence(durable))return durable;

  const explicit=String(spec?.instruction ?? '').trim();
  if(explicit && !looksLikeCompletedStateSentence(explicit))return explicit;

  const legacy=String(task?.description ?? '').trim();
  if(legacy && !looksLikeCompletedStateSentence(legacy))return legacy;

  const targets=inferExpectedTargetPaths(mission,task);
  if(workMode==='modify'){
    if(targets.length){
      return `${targets.join(', ')} dosyasını bu taskın acceptance kriterlerini karşılayacak şekilde şimdi güncelle. Gerçek repository değişikliği yap.`;
    }
    return `Bu taskın hedef repository artifact'ını şimdi değiştir ve acceptance kriterlerini karşıla.`;
  }

  if(workMode==='validate'){
    return `Task kapsamındaki artifact ve kanıtları şimdi doğrula; acceptance kriterlerini tek tek değerlendir ve yalnız gerçek kanıta dayan.`;
  }

  if(workMode==='analysis'){
    return `Task kapsamını şimdi analiz et; istenen bulguları ve karar girdilerini kanıta dayalı biçimde üret.`;
  }

  return legacy || String(task?.title ?? '').trim();
}

function taskExpectedOutcome(mission:any,task:any){
  const spec=missionPlanTaskSpec(mission,task);
  return String(
    task?.expected_outcome
    || spec?.expectedOutcome
    || spec?.expected_outcome
    || task?.description
    || ''
  ).trim();
}

function taskPrompt(mission:any,task:any) {
  const criteria=parseJson<string[]>(task.acceptance_criteria_json,[]);
  const workMode=resolvedTaskWorkMode(mission,task);
  const agentContext=assignedAgentExecutionContext(mission,task);
  const expectedTargets=workMode==='modify'
    ? inferExpectedTargetPaths(mission,task)
    : [];
  const action=synthesizedTaskInstruction(mission,task,workMode);
  const expectedOutcome=taskExpectedOutcome(mission,task);

  const specialistResolved=agentContext.temporarySpecialist
    ? `RESOLVED ORCHESTRATION STATE:
- AgentFactory requirement is ALREADY resolved.
- You ARE the selected temporary ${agentContext.specialistRole ?? 'specialist'}.
- Do NOT create/recruit/replace agents.
- Do NOT re-plan the mission or evaluate whether another specialist is needed.
- QA and Reviewer routing is Doom Core's responsibility, not yours.`
    : `RESOLVED ORCHESTRATION STATE:
- You are already assigned to this task.
- Team composition, ownership and DAG routing are already resolved.
- Do NOT re-plan the mission unless this task explicitly says planning is the deliverable.`;

  return `ACTION NOW
${action}

TASK:
${task.task_key} · ${task.title}

MODE:
${workMode}

${workMode==='modify'
  ? `MANDATORY EXECUTION SEQUENCE:
1. Inspect the required target.
2. Make the requested REAL repository change with write_file or replace_in_file.
3. Inspect git_status and git_diff.
4. Check the acceptance criteria.
5. Only then return the final report.

REQUIRED TARGETS:
${expectedTargets.length ? expectedTargets.map(x=>`- ${x}`).join('\n') : '- target named by the task'}

A prose-only answer is INVALID.
Do not say the work is complete before the repository mutation exists.`
  : workMode==='validate'
    ? `VALIDATION CONTRACT:
- Repository is read-only for this task.
- Inspect the actual artifact/evidence directly.
- Do not create a report file in the repository.
- Return findings through your final result/artifacts.`
    : ''}

${specialistResolved}

EXPECTED OUTCOME:
${expectedOutcome || 'Satisfy the task acceptance criteria with real evidence.'}

ACCEPTANCE CRITERIA:
${criteria.length ? criteria.map((x,i)=>`${i+1}. ${x}`).join('\n') : 'No explicit criteria were provided.'}

BOSS HARD CONSTRAINTS:
${hardConstraintPrompt(mission.objective)}

AVAILABLE EXECUTION TOOLS:
${agentContext.tools.length ? agentContext.tools.map((x:string)=>`- ${x}`).join('\n') : '- none'}

REFERENCE CONTEXT — claims are NOT proof:
Mission: ${mission.title}

Original Boss objective (reference only; resolved AgentFactory/team clauses must NOT be re-executed):
${mission.objective}

Prerequisite outputs:
${dependencyContext(task.id)}

Structured prerequisite artifacts:
${prerequisiteArtifactContext(task.id)}

Message Bus / live directives:
${taskCommunicationContext(mission.id,task.id,task.assigned_agent_id)}

FINAL RULES:
- Execute the ACTION NOW section; do not reinterpret the task as already completed.
- Boss hard constraints override all lower-level text.
- Work only in the assigned task workspace.
- Use actual tools and actual repository evidence.
- Upstream reports are context, not source-of-truth.
- Never invent a file edit, test result, approval, command or diff.
- Finish with a concise report only after the required work/evidence exists.`;
}

function taskRows(missionId:string) {
  return db.prepare(`
    SELECT mt.*,a.name AS agent_name,a.slug AS agent_slug,
      e.model_id AS execution_model_id,
      em.name AS execution_model_name,
      e.runtime_id AS execution_runtime_id,
      er.name AS execution_runtime_name,
      ep.name AS execution_provider_name,
      e.snapshot_json AS execution_snapshot_json
    FROM mission_tasks mt
    JOIN agents a ON a.id=mt.assigned_agent_id
    LEFT JOIN executions e ON e.id=mt.execution_id
    LEFT JOIN models em ON em.id=e.model_id
    LEFT JOIN model_providers ep ON ep.id=em.provider_id
    LEFT JOIN runtimes er ON er.id=e.runtime_id
    WHERE mt.mission_id=?
    ORDER BY mt.priority,mt.sequence_no
  `).all(missionId) as any[];
}

function latestExecutionEvent(executionId:string|null){
  if(!executionId)return null;
  const row:any=db.prepare(`
    SELECT event_type,payload_json,created_at
    FROM events WHERE execution_id=? ORDER BY id DESC LIMIT 1
  `).get(executionId);
  return row ? {
    eventType:row.event_type,
    payload:parseJson(row.payload_json,{}),
    createdAt:row.created_at
  } : null;
}

function recalcBlockedTasks(missionId:string) {
  const tasks=taskRows(missionId);
  for(const task of tasks){
    if(!['pending','blocked'].includes(task.status))continue;
    const deps=dependencies(task.id);
    const failed=deps.some(d=>d.status==='failed' || d.status==='blocked' || d.status==='cancelled');
    const allDone=deps.every(d=>d.status==='done');

    if(failed && task.status!=='blocked'){
      db.prepare(`UPDATE mission_tasks SET status='blocked',error_text='DEPENDENCY_FAILED',updated_at=? WHERE id=?`)
        .run(now(),task.id);
      event(missionId,'task.blocked',{reason:'DEPENDENCY_FAILED'},task.id);
    }else if(!failed && task.status==='blocked' && allDone && task.error_text==='DEPENDENCY_FAILED'){
      db.prepare(`UPDATE mission_tasks SET status='pending',error_text=NULL,updated_at=? WHERE id=?`)
        .run(now(),task.id);
      event(missionId,'task.unblocked',{},task.id);
    }
  }
}

function nextReadyTask(missionId:string) {
  recalcBlockedTasks(missionId);
  const tasks=taskRows(missionId);
  for(const task of tasks){
    if(task.status!=='pending')continue;
    const deps=dependencies(task.id);
    if(deps.every(d=>d.status==='done'))return task;
  }
  return null;
}

function getMissionWorkspaceRecord(missionId:string){
  return db.prepare(`SELECT * FROM mission_workspaces WHERE mission_id=?`).get(missionId) as any;
}

function ensureLegacyWorkspaceRecord(missionId:string){
  let ws=getMissionWorkspaceRecord(missionId);
  if(ws)return ws;

  const attempts=(db.prepare(`
    SELECT COALESCE(SUM(attempt_no),0) n FROM mission_tasks WHERE mission_id=?
  `).get(missionId) as any)?.n ?? 0;

  // Existing V0.8 missions stay shared so an upgrade never silently changes the
  // workspace underneath an in-progress mission.
  const mode=Number(attempts)>0?'shared':'isolated';
  const t=now();
  db.prepare(`
    INSERT INTO mission_workspaces
    (mission_id,mode,base_workspace,status,created_at,updated_at)
    VALUES (?,?,?,'pending',?,?)
  `).run(missionId,mode,cfg.defaultWorkspace,t,t);
  return getMissionWorkspaceRecord(missionId);
}

async function prepareMissionWorkspace(missionId:string){
  let record=ensureLegacyWorkspaceRecord(missionId);
  if(record.mode==='shared'){
    if(record.status!=='ready'){
      const state=await inspectGitWorkspace(record.base_workspace);
      db.prepare(`
        UPDATE mission_workspaces
        SET base_commit=?,integration_workspace=?,integration_branch=?,status='ready',
            error_text=NULL,updated_at=?
        WHERE mission_id=?
      `).run(state.head,record.base_workspace,state.branch,now(),missionId);
    }
    return getMissionWorkspaceRecord(missionId);
  }

  if(record.status==='ready' && record.integration_workspace){
    return record;
  }

  try{
    const prepared=await ensureMissionIntegrationWorkspace({
      missionId,
      baseWorkspace:record.base_workspace,
      worktreeRoot:cfg.worktreeRoot,
    });

    db.prepare(`
      UPDATE mission_workspaces
      SET base_commit=?,integration_workspace=?,integration_branch=?,status='ready',
          error_text=NULL,updated_at=?
      WHERE mission_id=?
    `).run(
      prepared.baseCommit,
      prepared.integrationWorkspace,
      prepared.integrationBranch,
      now(),missionId
    );
    event(missionId,'workspace.integration_ready',{
      mode:'isolated',
      baseCommit:prepared.baseCommit,
      integrationWorkspace:prepared.integrationWorkspace,
      integrationBranch:prepared.integrationBranch,
      reused:prepared.reused
    });
    return getMissionWorkspaceRecord(missionId);
  }catch(err:any){
    db.prepare(`
      UPDATE mission_workspaces SET status='error',error_text=?,updated_at=? WHERE mission_id=?
    `).run(String(err?.message ?? err),now(),missionId);
    throw err;
  }
}

function taskWorkspaceRow(taskId:string,attemptNo:number){
  return db.prepare(`
    SELECT * FROM mission_task_workspaces WHERE task_id=? AND attempt_no=?
  `).get(taskId,attemptNo) as any;
}

async function prepareTaskWorkspace(mission:any,task:any,attemptNo:number){
  const missionWs=await prepareMissionWorkspace(mission.id);
  if(missionWs.mode==='shared'){
    return {
      mode:'shared',
      workspace:missionWs.base_workspace,
      branch:missionWs.integration_branch,
      baseCommit:missionWs.base_commit,
      row:null
    };
  }

  let existing=taskWorkspaceRow(task.id,attemptNo);
  if(existing){
    return {
      mode:'isolated',
      workspace:existing.workspace_path,
      branch:existing.branch_name,
      baseCommit:existing.base_commit,
      row:existing
    };
  }

  const wt=await createTaskWorktree({
    missionId:mission.id,
    taskKey:task.task_key,
    attemptNo,
    integrationWorkspace:missionWs.integration_workspace,
    integrationBranch:missionWs.integration_branch,
    worktreeRoot:cfg.worktreeRoot
  });

  const workspaceId=id('mtws');
  const t=now();
  db.prepare(`
    INSERT INTO mission_task_workspaces
    (id,mission_id,task_id,attempt_no,workspace_path,branch_name,base_commit,status,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,'active',?,?)
  `).run(workspaceId,mission.id,task.id,attemptNo,wt.workspace,wt.branch,wt.baseCommit,t,t);

  event(mission.id,'workspace.task_ready',{
    taskKey:task.task_key,
    attemptNo,
    workspace:wt.workspace,
    branch:wt.branch,
    baseCommit:wt.baseCommit
  },task.id);

  return {
    mode:'isolated',
    workspace:wt.workspace,
    branch:wt.branch,
    baseCommit:wt.baseCommit,
    row:taskWorkspaceRow(task.id,attemptNo)
  };
}

function collectExecutionEvidence(missionId:string,taskId:string,executionId:string,resultText:string){
  evidence(missionId,taskId,executionId,'execution_result',resultText.trim()?'pass':'fail',{
    length:resultText.length,
    preview:resultText.slice(0,4000)
  });
  publishTaskArtifact({
    missionId,
    taskId,
    executionId,
    artifactType:'report',
    name:'Agent final result',
    contentPreview:resultText.slice(0,8000),
    metadata:{length:resultText.length}
  });

  const events=executionEvents(executionId,500) as any[];
  const changed=events
    .filter(e=>e.event_type==='file.changed')
    .map(e=>parseJson<Record<string,any>>(e.payload_json,{}));

  const completed=events
    .filter(e=>e.event_type==='tool.completed')
    .map(e=>parseJson<any>(e.payload_json,{}));

  for(const change of changed){
    if(!change?.path)continue;
    publishTaskArtifact({
      missionId,
      taskId,
      executionId,
      artifactType:'file',
      name:String(change.path),
      path:String(change.path),
      metadata:change
    });
  }

  const tests=completed.filter(x=>x.tool==='run_tests');
  const git=completed.filter(x=>x.tool==='git_status' || x.tool==='git_diff');

  evidence(missionId,taskId,executionId,'file_changes','info',{
    count:changed.length,
    changes:changed.slice(0,100)
  });
  evidence(missionId,taskId,executionId,'test_tools',tests.length?'info':'unknown',{
    calls:tests.slice(-20)
  });
  for(const test of tests.slice(-10)){
    publishTaskArtifact({
      missionId,
      taskId,
      executionId,
      artifactType:'test',
      name:'Test execution',
      contentPreview:JSON.stringify(test?.output ?? test).slice(0,5000),
      metadata:{tool:'run_tests'}
    });
  }
  evidence(missionId,taskId,executionId,'git_tools',git.length?'info':'unknown',{
    calls:git.slice(-20)
  });
}

function createGate(
  missionId:string,
  taskId:string|null,
  gateType:string,
  agentId:string|null,
  executionId:string|null,
){
  const gateId=id('gate');
  const t=now();
  db.prepare(`
    INSERT INTO mission_gates
    (id,mission_id,task_id,gate_type,agent_id,execution_id,status,detail_json,created_at,updated_at)
    VALUES (?,?,?,?,?,?,'running','{}',?,?)
  `).run(gateId,missionId,taskId,gateType,agentId,executionId,t,t);
  return gateId;
}

function updateGate(gateId:string,status:string,summary:string,detail:any){
  db.prepare(`
    UPDATE mission_gates
    SET status=?,summary=?,detail_json=?,updated_at=? WHERE id=?
  `).run(status,summary,JSON.stringify(detail ?? {}),now(),gateId);
}


function resolvedTaskWorkMode(mission:any,task:any):PlanTask['workMode']{
  const spec=missionPlanTaskSpec(mission,task);
  return resolveTaskWorkModeContract(task,spec) as PlanTask['workMode'];
}

function inferExpectedTargetPaths(mission:any,task:any){
  const spec=missionPlanTaskSpec(mission,task);
  const durableCriteria=parseJson<string[]>(task?.acceptance_criteria_json,[]);
  const local=inferTaskMutationTargetPaths({
    title:task?.title || spec?.title,
    instruction:task?.instruction || spec?.instruction || task?.description,
    expectedOutcome:task?.expected_outcome || spec?.expectedOutcome || spec?.expected_outcome,
    acceptanceCriteria:durableCriteria.length
      ? durableCriteria
      : (Array.isArray(spec?.acceptanceCriteria)?spec.acceptanceCriteria:[]),
  });

  // Do not leak every path in the multi-task Boss objective into a task-local
  // mutation contract. Legacy rows without any semantic task contract retain
  // an objective-only fallback.
  if(local.length)return local;
  const hasLocalContract=Boolean(
    String(task?.instruction || spec?.instruction || task?.description || '').trim()
    || String(task?.expected_outcome || spec?.expectedOutcome || '').trim()
    || durableCriteria.length
  );
  return hasLocalContract
    ? []
    : inferTaskMutationTargetPaths({instruction:String(mission?.objective ?? '')});
}

function pathMatchesExpected(changedPath:string,expectedPath:string){
  const changed=String(changedPath).replace(/\\/g,'/').toLowerCase();
  const expected=String(expectedPath).replace(/\\/g,'/').toLowerCase();
  return changed===expected || changed.endsWith('/'+expected);
}

function taskExecutionToolOverride(mode:PlanTask['workMode']){
  if(mode!=='analysis' && mode!=='validate')return undefined;

  return [
    'list_files',
    'read_file',
    'git_status',
    'git_diff',
    'run_tests',
    'list_skill_files',
    'read_skill_file',
    'send_agent_message',
    'list_agent_messages'
  ];
}

function bindAcceptanceVerdict(criteria:string[],parsed:any){
  if(!Array.isArray(parsed?.criteria)){
    throw new Error('ACCEPTANCE_CRITERIA_ARRAY_REQUIRED');
  }

  const byIndex=new Map<number,any>();
  for(const item of parsed.criteria){
    const index=Number(item?.criterionIndex);
    if(!Number.isInteger(index) || index<1 || index>criteria.length){
      throw new Error(`ACCEPTANCE_CRITERION_INDEX_INVALID:${item?.criterionIndex}`);
    }
    if(byIndex.has(index)){
      throw new Error(`ACCEPTANCE_CRITERION_INDEX_DUPLICATE:${index}`);
    }
    byIndex.set(index,item);
  }

  if(byIndex.size!==criteria.length){
    throw new Error(`ACCEPTANCE_CRITERIA_COUNT_MISMATCH:${byIndex.size}/${criteria.length}`);
  }

  return {
    ...parsed,
    criteria:criteria.map((criterion,index)=>{
      const item=byIndex.get(index+1);
      if(!item)throw new Error(`ACCEPTANCE_CRITERION_MISSING:${index+1}`);
      return {
        criterionIndex:index+1,
        criterion,
        pass:item.pass===true,
        evidence:String(item.evidence ?? '').trim()
      };
    })
  };
}

function taskAcceptancePrompt(mission:any,task:any,resultText:string,workspaceEvidence:any){
  const criteria=parseJson<string[]>(task.acceptance_criteria_json,[]);
  const workMode=resolvedTaskWorkMode(mission,task);
  return `You are the Lead acting as an independent acceptance gate.

MISSION:
${mission.title}

OBJECTIVE:
${mission.objective}

TASK:
${task.task_key} - ${task.title}

DESCRIPTION:
${task.description}

ACCEPTANCE CRITERIA:
${criteria.map((x,i)=>`${i+1}. ${x}`).join('\n') || '(none)'}

IMPLEMENTING AGENT FINAL RESULT:
${resultText.slice(0,9000)}

TASK WORK MODE:
${workMode}

WORKSPACE GIT EVIDENCE:
${JSON.stringify(workspaceEvidence,null,2).slice(0,12000)}

Evidence interpretation:
- analysis: the textual result/report is the primary deliverable; a clean Git workspace is EXPECTED and must not cause failure.
- validate: evaluate the validation report and relevant repository evidence; file modification is not required.
- modify: actual repository/file evidence is required. A clean workspace with no changed/untracked files is a material failure unless the task explicitly required no change.
- general: use the stated criteria and available evidence.

Never accept claims merely because the implementing agent said they happened.
Cross-check claims against workspace/tool evidence and Boss hard constraints.
Inspect the assigned workspace with read/git/test tools when useful.
Do not modify files.

Return ONLY JSON.
You MUST return exactly one criteria item for every ACCEPTANCE CRITERIA entry above.
Use criterionIndex 1..N exactly once each. Do not invent, rename, replace or omit criteria.

{
  "pass": true,
  "summary": "short conclusion",
  "criteria": [
    {"criterionIndex":1,"pass":true,"evidence":"specific concise evidence, max 180 characters"}
  ],
  "findings": []
}

Pass only when the task result and evidence satisfy the stated criteria.
If a criterion cannot be verified, mark it failed rather than assuming.
Keep the entire JSON concise. Evidence strings must be at most 180 characters each.`;
}

async function runTaskAcceptanceGate(
  mission:any,
  task:any,
  execution:any,
  workspace:string,
){
  const criteria=parseJson<string[]>(task.acceptance_criteria_json,[]);
  const workspaceEv=await taskWorkspaceEvidence(workspace);
  evidence(mission.id,task.id,execution.id,'workspace_git','info',workspaceEv);

  const workMode=resolvedTaskWorkMode(mission,task);
  const expectedTargetPaths=inferExpectedTargetPaths(mission,task);
  const changedPaths=Array.isArray(workspaceEv.changedPaths)
    ? workspaceEv.changedPaths.map(String)
    : [];

  const resultConstraintCheck=validateTextAgainstBossConstraints(
    mission.objective,
    String(execution.result_text ?? ''),
    task.task_key
  );

  if(!resultConstraintCheck.ok){
    const gateId=createGate(mission.id,task.id,'acceptance',mission.lead_agent_id,null);
    const summary='Agent final report contradicts a Boss hard constraint.';
    updateGate(gateId,'fail',summary,{
      deterministic:true,
      workMode,
      violations:resultConstraintCheck.violations,
      workspaceEvidence:workspaceEv
    });
    event(mission.id,'gate.acceptance_failed',{
      gateId,
      summary,
      reason:'AGENT_RESULT_CONTRADICTS_BOSS_CONSTRAINT'
    },task.id);
    return {
      pass:false,
      summary,
      gateId,
      detail:{violations:resultConstraintCheck.violations}
    };
  }

  if(
    workMode==='modify'
    && expectedTargetPaths.length
    && !expectedTargetPaths.some(expected=>
      changedPaths.some(changed=>pathMatchesExpected(changed,expected))
    )
  ){
    const gateId=createGate(mission.id,task.id,'acceptance',mission.lead_agent_id,null);
    const summary=`Modify task did not change the required target file(s): ${expectedTargetPaths.join(', ')}`;
    updateGate(gateId,'fail',summary,{
      deterministic:true,
      workMode,
      expectedTargetPaths,
      changedPaths,
      workspaceEvidence:workspaceEv
    });
    event(mission.id,'gate.acceptance_failed',{
      gateId,
      summary,
      reason:'MODIFY_TASK_TARGET_NOT_CHANGED',
      expectedTargetPaths,
      changedPaths
    },task.id);
    return {
      pass:false,
      summary,
      gateId,
      detail:{expectedTargetPaths,changedPaths,workspaceEvidence:workspaceEv}
    };
  }

  if(workMode==='modify' && workspaceEv.clean){
    const gateId=createGate(mission.id,task.id,'acceptance',mission.lead_agent_id,null);
    const summary='Modify task produced no repository changes.';
    updateGate(gateId,'fail',summary,{
      deterministic:true,
      workMode,
      workspaceEvidence:workspaceEv
    });
    event(mission.id,'gate.acceptance_failed',{
      gateId,
      summary,
      reason:'MODIFY_TASK_NO_CHANGES'
    },task.id);
    return {pass:false,summary,gateId,detail:{workspaceEvidence:workspaceEv}};
  }

  if(!String(execution.result_text ?? '').trim()){
    const gateId=createGate(mission.id,task.id,'acceptance',mission.lead_agent_id,null);
    updateGate(gateId,'fail','Execution returned no final result.',{criteria,workspaceEvidence:workspaceEv});
    return {pass:false,summary:'Execution returned no final result.',gateId,detail:{}};
  }

  if(criteria.length===0){
    const gateId=createGate(mission.id,task.id,'acceptance',mission.lead_agent_id,null);
    updateGate(gateId,'pass','No explicit acceptance criteria; execution result/evidence present.',{
      criteria:[],
      workspaceEvidence:workspaceEv
    });
    return {pass:true,summary:'No explicit acceptance criteria; evidence present.',gateId,detail:{}};
  }

  const gateExecution:any=await startExecution(
    mission.lead_agent_id,
    taskAcceptancePrompt(mission,task,execution.result_text ?? '',workspaceEv),
    {
      workspace,
      context:{
        missionId:mission.id,
        taskId:task.id,
        gateType:'acceptance',
        expectedFinalFormat:'acceptance_gate_json',
        acceptanceCriteriaCount:criteria.length,
        structuredFinalStepBudget:4
      }
    }
  );
  const gateId=createGate(mission.id,task.id,'acceptance',mission.lead_agent_id,gateExecution.id);
  event(mission.id,'gate.acceptance_started',{gateId,executionId:gateExecution.id},task.id);

  const finished:any=await waitForExecution(gateExecution.id);
  if(finished.status!=='done'){
    updateGate(gateId,finished.status==='cancelled'?'cancelled':'error',`Acceptance gate execution ${finished.status}.`,{error:finished.error_text});
    return {
      pass:false,
      error:true,
      summary:`Acceptance gate execution ${finished.status}.`,
      gateId,
      detail:{error:finished.error_text}
    };
  }

  try{
    const rawParsed=extractJsonObject(finished.result_text ?? '');
    const parsed=bindAcceptanceVerdict(criteria,rawParsed);
    const allCriteriaPass=parsed.criteria.every((item:any)=>item.pass===true);
    const pass=parsed?.pass===true && allCriteriaPass;
    const summary=String(parsed?.summary ?? (pass?'Acceptance passed.':'Acceptance failed.'));

    const normalized={
      ...parsed,
      pass,
      workMode,
      expectedTargetPaths,
      changedPaths
    };

    updateGate(gateId,pass?'pass':'fail',summary,normalized);
    event(mission.id,pass?'gate.acceptance_passed':'gate.acceptance_failed',{
      gateId,
      summary,
      allCriteriaPass
    },task.id);
    return {pass,summary,gateId,detail:normalized};
  }catch(err:any){
    updateGate(gateId,'error','Acceptance gate returned invalid JSON.',{
      error:String(err?.message ?? err),
      raw:String(finished.result_text ?? '').slice(0,12000)
    });
    return {
      pass:false,
      error:true,
      summary:'Acceptance gate returned invalid JSON after structured recovery.',
      gateId,
      detail:{error:String(err?.message ?? err)}
    };
  }
}

async function finalizeAcceptedTaskWorkspace(
  mission:any,
  task:any,
  attemptNo:number,
  prepared:any
){
  if(prepared.mode==='shared'){
    return {changed:null,commitHash:null,mergeCommit:null,diffStat:null};
  }

  const missionWs=getMissionWorkspaceRecord(mission.id);
  const workMode=resolvedTaskWorkMode(mission,task);

  if(workMode==='analysis' || workMode==='validate'){
    const beforeCleanup=await taskWorkspaceEvidence(prepared.workspace);
    const cleanup=await removeTaskWorktree({
      baseWorkspace:missionWs.base_workspace,
      workspace:prepared.workspace,
      branch:prepared.branch
    });

    db.prepare(`
      UPDATE mission_task_workspaces
      SET commit_hash=NULL,merge_commit=NULL,status=?,diff_stat=?,error_text=?,updated_at=?
      WHERE task_id=? AND attempt_no=?
    `).run(
      cleanup.removed?'cleaned_readonly':'error',
      beforeCleanup.diffStat ?? '',
      cleanup.removed?null:(cleanup.error ?? 'READONLY_WORKTREE_CLEANUP_FAILED'),
      now(),task.id,attemptNo
    );

    evidence(mission.id,task.id,null,'workspace_readonly_cleanup',cleanup.removed?'pass':'fail',{
      workMode,
      discardedWorkspaceChanges:!beforeCleanup.clean,
      changedPaths:beforeCleanup.changedPaths ?? [],
      cleanup
    });

    if(!cleanup.removed){
      throw new Error(`READONLY_WORKTREE_CLEANUP_FAILED:${cleanup.error ?? 'unknown'}`);
    }

    event(mission.id,'workspace.task_readonly_cleaned',{
      taskKey:task.task_key,
      workMode,
      discardedWorkspaceChanges:!beforeCleanup.clean,
      changedPaths:beforeCleanup.changedPaths ?? []
    },task.id);

    return {
      changed:false,
      commitHash:null,
      mergeCommit:null,
      diffStat:beforeCleanup.diffStat ?? '',
      readOnly:true,
      discardedWorkspaceChanges:!beforeCleanup.clean
    };
  }

  const merged=await commitAndMergeTask({
    taskKey:task.task_key,
    title:task.title,
    workspace:prepared.workspace,
    branch:prepared.branch,
    integrationWorkspace:missionWs.integration_workspace
  });

  db.prepare(`
    UPDATE mission_task_workspaces
    SET commit_hash=?,merge_commit=?,status='merged',diff_stat=?,error_text=NULL,updated_at=?
    WHERE task_id=? AND attempt_no=?
  `).run(
    merged.commitHash,
    merged.mergeCommit,
    merged.diffStat,
    now(),task.id,attemptNo
  );

  evidence(mission.id,task.id,null,'git_integration','pass',merged);
  if(merged.commitHash){
    publishTaskArtifact({
      missionId:mission.id,
      taskId:task.id,
      artifactType:'git_commit',
      name:`${task.task_key} task commit`,
      contentPreview:merged.diffStat ?? '',
      metadata:{commitHash:merged.commitHash,branch:prepared.branch}
    });
  }
  if(merged.mergeCommit){
    publishTaskArtifact({
      missionId:mission.id,
      taskId:task.id,
      artifactType:'integration_merge',
      name:`${task.task_key} integration merge`,
      contentPreview:merged.diffStat ?? '',
      metadata:{mergeCommit:merged.mergeCommit}
    });
  }
  event(mission.id,'workspace.task_merged',{
    taskKey:task.task_key,
    commitHash:merged.commitHash,
    mergeCommit:merged.mergeCommit,
    changed:merged.changed
  },task.id);

  const cleanup=await removeTaskWorktree({
    baseWorkspace:missionWs.base_workspace,
    workspace:prepared.workspace,
    branch:prepared.branch
  });
  db.prepare(`
    UPDATE mission_task_workspaces SET status=?,updated_at=? WHERE task_id=? AND attempt_no=?
  `).run(cleanup.removed?'cleaned':'merged',now(),task.id,attemptNo);
  return merged;
}

function recoveryEventCount(
  missionId:string,
  taskId:string|null=null,
  eventType='lead.recovery_started'
){
  const row=taskId
    ? db.prepare(`
        SELECT COUNT(*) c FROM mission_events
        WHERE mission_id=? AND task_id=? AND event_type=?
      `).get(missionId,taskId,eventType) as any
    : db.prepare(`
        SELECT COUNT(*) c FROM mission_events
        WHERE mission_id=? AND event_type=?
      `).get(missionId,eventType) as any;
  return Number(row?.c ?? 0);
}


function appliedTaskRecoveryCount(missionId:string,taskId:string){
  return taskRecoveryAttemptCount(db,missionId,taskId);
}

function appliedMissionRecoveryCount(missionId:string){
  return missionRecoveryAttemptCount(db,missionId);
}

function appliedFinalGateRecoveryCount(missionId:string){
  return finalGateRecoveryAttemptCount(db,missionId);
}

function finalGateRecoveryBudgetExhausted(missionId:string){
  return (
    appliedMissionRecoveryCount(missionId)>=MAX_AUTONOMOUS_RECOVERY_PER_MISSION
    || appliedFinalGateRecoveryCount(missionId)>=MAX_AUTONOMOUS_FINAL_GATE_RECOVERY
  );
}

function latestTaskGate(taskId:string){
  const row=db.prepare(`
    SELECT * FROM mission_gates
    WHERE task_id=? AND gate_type='acceptance'
    ORDER BY created_at DESC LIMIT 1
  `).get(taskId) as any;
  return row ? {...row,detail:parseJson(row.detail_json,{})} : null;
}

function latestFinalGate(missionId:string,gateType:'qa'|'reviewer'){
  const row=db.prepare(`
    SELECT * FROM mission_gates
    WHERE mission_id=? AND task_id IS NULL AND gate_type=?
    ORDER BY created_at DESC LIMIT 1
  `).get(missionId,gateType) as any;
  return row ? {...row,detail:parseJson(row.detail_json,{})} : null;
}

function recoveryTaskSnapshot(missionId:string){
  const mission=missionRow(missionId);
  return taskRows(missionId).map(t=>({
    taskKey:t.task_key,
    title:t.title,
    instruction:synthesizedTaskInstruction(mission,t,resolvedTaskWorkMode(mission,t)),
    expectedOutcome:taskExpectedOutcome(mission,t),
    workMode:resolvedTaskWorkMode(mission,t),
    description:t.description,
    status:t.status,
    agentId:t.assigned_agent_id,
    agentName:t.agent_name,
    error:t.error_text,
    acceptanceCriteria:parseJson<string[]>(t.acceptance_criteria_json,[]),
    dependencies:dependencies(t.id).map(d=>d.task_key)
  }));
}

function recoveryRoster(missionId:string){
  return missionRoster(missionId).map((a:any)=>({
    agentId:a.id,
    name:a.name,
    role:a.role_label,
    temporary:Boolean(a.temporary)
  }));
}

function validateRecoveryPlanObligations(
  mission:any,
  taskId:string,
  patch:{
    title?:string;
    instruction?:string;
    expectedOutcome?:string;
    workMode?:PlanTask['workMode'];
    description?:string;
    assignedAgentId?:string;
    acceptanceCriteria?:string[];
    dependsOnTaskKeys?:string[];
  }
){
  const rows=taskRows(mission.id);
  const plan={
    tasks:rows.map(t=>{
      const isPatched=String(t.id)===taskId;
      const currentMode=resolvedTaskWorkMode(mission,t);
      const currentInstruction=synthesizedTaskInstruction(mission,t,currentMode);
      const instruction=isPatched
        ? String(patch.instruction ?? patch.description ?? currentInstruction)
        : currentInstruction;
      const expectedOutcome=isPatched
        ? String(patch.expectedOutcome ?? taskExpectedOutcome(mission,t))
        : taskExpectedOutcome(mission,t);
      const workMode=isPatched
        ? (patch.workMode ?? currentMode)
        : currentMode;
      return {
        key:t.task_key,
        title:isPatched ? String(patch.title ?? t.title) : t.title,
        instruction,
        expectedOutcome,
        workMode,
        description:instruction,
        assignedAgentId:isPatched ? String(patch.assignedAgentId ?? t.assigned_agent_id) : t.assigned_agent_id,
        acceptanceCriteria:isPatched && patch.acceptanceCriteria
          ? patch.acceptanceCriteria
          : parseJson<string[]>(t.acceptance_criteria_json,[]),
        dependsOn:isPatched && patch.dependsOnTaskKeys
          ? patch.dependsOnTaskKeys
          : dependencies(t.id).map(d=>d.task_key),
        priority:Number(t.priority ?? 100)
      };
    })
  };
  const roleValidation=validatePlanRoleObligations(
    mission.objective,
    plan,
    missionRoster(mission.id)
  );
  if(!roleValidation.ok){
    throw new Error(
      `RECOVERY_ROLE_OBLIGATION_VIOLATION:${roleValidation.violations.map((v:any)=>v.id ?? v.reason).join(',')}`
    );
  }
}

function refreshMissionPlanSnapshot(missionId:string){
  const mission=missionRow(missionId);
  const previous=parseJson<any>(mission?.plan_json,{});
  const previousByKey=new Map(
    (Array.isArray(previous?.tasks)?previous.tasks:[])
      .map((x:any)=>[String(x?.key ?? ''),x])
  );
  const rows=taskRows(missionId);
  const plan={
    summary:'Current DAG after autonomous Lead recovery',
    tasks:rows.map(t=>{
      const old:any=previousByKey.get(String(t.task_key)) ?? {};
      const instruction=String(
        t.instruction
        || old?.instruction
        || t.description
        || ''
      ).trim();
      const expectedOutcome=String(
        t.expected_outcome
        || old?.expectedOutcome
        || old?.expected_outcome
        || t.description
        || ''
      ).trim();
      const rawMode=String(t.work_mode || old?.workMode || old?.work_mode || '').trim();
      const workMode=(['modify','validate','analysis','general'].includes(rawMode)
        ? rawMode
        : inferWorkModeFromPlanText({title:t.title,instruction,expectedOutcome})) as PlanTask['workMode'];
      return {
        key:t.task_key,
        title:t.title,
        instruction,
        expectedOutcome,
        workMode,
        description:instruction,
        assignedAgentId:t.assigned_agent_id,
        acceptanceCriteria:parseJson<string[]>(t.acceptance_criteria_json,[]),
        dependsOn:dependencies(t.id).map(d=>d.task_key),
        priority:Number(t.priority ?? 100)
      };
    })
  };
  db.prepare(`UPDATE missions SET plan_json=?,updated_at=? WHERE id=?`)
    .run(JSON.stringify(plan),now(),missionId);
}


function activeSpecialistRecordForAgent(missionId:string,agentId:string){
  return db.prepare(`
    SELECT ms.*,a.name,a.description
    FROM mission_specialists ms
    JOIN agents a ON a.id=ms.agent_id
    WHERE ms.mission_id=? AND ms.agent_id=? AND ms.status='active'
    LIMIT 1
  `).get(missionId,agentId) as any;
}

function replaceNoOpSpecialist(mission:any,failedTask:any,reason:string){
  const current=activeSpecialistRecordForAgent(
    mission.id,
    failedTask.assigned_agent_id
  );
  if(!current)return null;

  const oldAgentId=String(current.agent_id);
  archiveMissionSpecialist(
    mission.id,
    oldAgentId,
    'autonomous_noop_replacement'
  );

  const created:any=spawnMissionSpecialist({
    missionId:mission.id,
    requestedByType:'lead',
    requestedByAgentId:mission.lead_agent_id,
    name:String(current.name ?? 'Teknik Dokümantasyon Uzmanı'),
    roleName:String(current.role_name ?? 'Technical Documentation Specialist'),
    objective:`Replace a prior specialist after repeated no-op modify executions. Actually modify the repository target for ${failedTask.task_key}. Use native write tools and verify git evidence. ${reason}`,
    scopeType:'task',
    assignTaskKey:failedTask.task_key,
    toolProfiles:['read','write','git','tests','skills','communication'],
    requiredCapabilities:['chat','tools'],
    modelStrategy:'tools_required',
    autonomyLevel:3
  });

  const newAgentId=String(
    created?.agent?.id
    ?? created?.specialist?.agent_id
    ?? ''
  );
  if(!newAgentId)throw new Error('RECOVERY_SPECIALIST_REPLACEMENT_FAILED');

  publishMessage({
    missionId:mission.id,
    taskId:failedTask.id,
    senderType:'lead',
    senderAgentId:mission.lead_agent_id,
    recipientType:'task',
    recipientId:failedTask.id,
    messageType:'directive',
    priority:'high',
    content:`Adaptive recovery replaced a repeatedly no-op specialist.
You are the new assigned specialist.
Actually modify the target file with write_file/replace_in_file and verify git_status/git_diff.
Do not return completion without real repository evidence.`,
    structured:{
      source:'adaptive_specialist_replacement',
      oldAgentId,
      newAgentId
    }
  });

  event(mission.id,'lead.recovery_specialist_replaced',{
    taskKey:failedTask.task_key,
    oldAgentId,
    newAgentId,
    reason,
    modelStrategy:'tools_required'
  },failedTask.id);

  return {
    recovered:true,
    action:'replace_specialist',
    reason,
    oldAgentId,
    newAgentId
  };
}

function criterionLooksLikeInventedApproval(mission:any,criterion:string){
  const boss=String(mission.objective ?? '').toLocaleLowerCase('tr-TR');
  const text=String(criterion ?? '').toLocaleLowerCase('tr-TR');
  const bossAskedApproval=/(onay|onayla|approval|approve|sign[- ]?off)/i.test(boss);
  const criterionRequiresApproval=/(onaylandı|onaylanmış|onay verildi|approved|approval received|signed off|sign[- ]?off completed)/i.test(text);
  return criterionRequiresApproval && !bossAskedApproval;
}

function deterministicTaskRecoveryAction(mission:any,failedTask:any,failure:any){
  const criteria=parseJson<string[]>(failedTask.acceptance_criteria_json,[]);
  const cleanedCriteria=criteria.filter(
    criterion=>!criterionLooksLikeInventedApproval(mission,criterion)
  );

  if(cleanedCriteria.length && cleanedCriteria.length!==criteria.length){
    return {
      action:'revise_task',
      reason:'Acceptance criteria included an internal approval/sign-off that Boss never requested.',
      taskKey:failedTask.task_key,
      patch:{acceptanceCriteria:cleanedCriteria}
    };
  }

  const gate=failure?.gate ?? {};
  const detail=gate?.detail ?? {};
  const changedPaths=Array.isArray(detail?.changedPaths)
    ? detail.changedPaths.map(String)
    : [];
  const workMode=resolvedTaskWorkMode(mission,failedTask);
  const summary=String(gate?.summary ?? '').toLowerCase();
  const result=String(failedTask.result_text ?? '').toLowerCase();

  if(
    workMode==='modify'
    && (
      changedPaths.length===0
      || summary.includes('did not change the required target')
      || summary.includes('workspace clean')
      || summary.includes('no repository changes')
      || summary.includes('değişiklikleri doğrulanamad')
      || summary.includes('değişiklik yapılmadı')
      || String(failedTask.error_text ?? '').includes('MUTATION_CONTRACT_UNSATISFIED')
    )
  ){
    const targets=inferExpectedTargetPaths(mission,failedTask);
    const priorRecoveries=appliedTaskRecoveryCount(
      mission.id,
      failedTask.id
    );

    if(priorRecoveries>=1){
      const activeSpecialist=activeSpecialistRecordForAgent(
        mission.id,
        failedTask.assigned_agent_id
      );

      if(activeSpecialist){
        return {
          action:'replace_specialist',
          reason:`Modify task remained a no-op after ${priorRecoveries} applied recovery cycle(s). Replace the temporary specialist instead of repeating the same agent/model strategy.`,
          taskKey:failedTask.task_key
        };
      }

      return {
        action:'retry_task',
        reason:`Modify task remained a no-op after ${priorRecoveries} applied recovery cycle(s). Keep task ownership but reroute away from mutation-failed model(s).`,
        taskKey:failedTask.task_key,
        guidance:'Retry with Doom mutation routing. Models that failed this task mutation contract are excluded automatically; execute the real write/replace and verify git evidence.'
      };
    }

    return {
      action:'retry_task',
      reason:'Modify task produced no repository changes; this is routine internal recovery.',
      taskKey:failedTask.task_key,
      guidance:[
        'You are already the assigned agent.',
        'If you are a temporary specialist, AgentFactory has already created you; do not create yourself again.',
        `Actually modify the required target(s): ${targets.join(', ') || 'the target named by the task'}.`,
        'Use write_file or replace_in_file, then verify git_status and git_diff.',
        'Do not return a prose-only completion while workspace is clean.',
        'Boss objective and acceptance criteria are already in your task context.'
      ].join(' ')
    };
  }

  if(
    result.includes('agentfactory')
    && (
      result.includes('erişim') || result.includes('access')
      || result.includes('oluşturulamad') || result.includes('cannot create')
    )
    && db.prepare(`
      SELECT 1 FROM mission_specialists
      WHERE mission_id=? AND agent_id=? AND status='active'
      LIMIT 1
    `).get(mission.id,failedTask.assigned_agent_id)
  ){
    return {
      action:'retry_task',
      reason:'Assigned agent is already an active AgentFactory specialist and incorrectly treated specialist creation as a blocker.',
      taskKey:failedTask.task_key,
      guidance:'You are already the active temporary specialist. Do not attempt AgentFactory. Execute the assigned repository task with your available write tools and verify real git evidence.'
    };
  }

  return null;
}

function recoveryEscalationPolicy(
  mission:any,
  action:any,
  failedTask:any|null,
  failure:any,
  contextKind:'task'|'final_gate'
){
  if(String(action?.action ?? '')!=='escalate'){
    return {allowed:true,reason:null};
  }

  const escalationType=String(action?.escalationType ?? '');
  const question=String(action?.bossQuestion ?? '').trim();
  const q=question.toLocaleLowerCase('tr-TR');

  const allowedTypes=new Set([
    'missing_boss_input',
    'safety_approval',
    'production_approval',
    'credential_required',
    'policy_required'
  ]);

  if(!allowedTypes.has(escalationType)){
    return {allowed:false,reason:'ESCALATION_TYPE_INVALID'};
  }

  if(
    escalationType==='missing_boss_input'
    && (
      /(objective|hedef|görev hedef)/i.test(q)
      || /(acceptance criteria|kabul kriter)/i.test(q)
    )
  ){
    const hasObjective=Boolean(String(mission.objective ?? '').trim());
    const hasCriteria=failedTask
      ? parseJson<string[]>(failedTask.acceptance_criteria_json,[]).length>0
      : false;

    if(hasObjective && (hasCriteria || !failedTask)){
      return {
        allowed:false,
        reason:'REQUESTED_OBJECTIVE_OR_CRITERIA_ALREADY_PRESENT'
      };
    }
  }

  if(contextKind==='task'){
    const error=String(failedTask?.error_text ?? '');
    const summary=String(failure?.gate?.summary ?? '').toLowerCase();

    if(
      [
        'ACCEPTANCE_GATE_FAILED',
        'ACCEPTANCE_GATE_ERROR',
        'EXECUTION_FAILED',
        'NO_READY_TASK',
        'MODIFY_AGENT_WRITE_TOOLS_MISSING'
      ].some(code=>error.includes(code))
      || summary.includes('workspace clean')
      || summary.includes('değişiklik yapılmadı')
      || summary.includes('no repository changes')
    ){
      return {
        allowed:false,
        reason:'ROUTINE_INTERNAL_FAILURE_MUST_BE_RECOVERED_AUTONOMOUSLY'
      };
    }
  }

  return {allowed:true,reason:null};
}

function deterministicTaskRecoveryFallback(
  mission:any,
  failedTask:any,
  failure:any,
  rejectionReason:string
){
  return deterministicTaskRecoveryAction(mission,failedTask,failure) ?? {
    action:'retry_task',
    reason:`Lead escalation rejected by autonomous recovery policy (${rejectionReason}).`,
    taskKey:failedTask.task_key,
    guidance:'Re-read the Boss objective, task definition, exact acceptance criteria, available tools and gate evidence. Correct the routine internal failure with real evidence. Do not ask Boss to repeat information already present.'
  };
}

function bestPriorModifyingAgent(missionId:string){
  const mission=missionRow(missionId);
  const rows=taskRows(missionId);
  for(const task of [...rows].reverse()){
    if(
      mission
      && resolvedTaskWorkMode(mission,task)==='modify'
      && missionAgentIsActive(missionId,task.assigned_agent_id)
    ){
      return String(task.assigned_agent_id);
    }
  }
  return null;
}

function deterministicFinalGateRecoveryFallback(
  mission:any,
  gateType:'qa'|'reviewer',
  rejectionReason:string
){
  const agentId=bestPriorModifyingAgent(mission.id);

  if(!agentId){
    return {
      action:'retry_gate',
      reason:`Lead escalation rejected (${rejectionReason}); retry gate with existing evidence.`
    };
  }

  const gate=latestFinalGate(mission.id,gateType);
  const findings=Array.isArray(gate?.detail?.findings)
    ? gate.detail.findings
    : [];

  const criteria=findings.length
    ? findings.slice(0,8).map((finding:any)=>{
        const text=typeof finding==='string'
          ? finding
          : String(finding?.finding ?? finding?.evidence ?? JSON.stringify(finding));
        return `Resolve final ${gateType} finding: ${text}`.slice(0,500);
      })
    : [`Resolve the final ${gateType} gate failure and satisfy the Boss objective.`];

  return {
    action:'add_remediation_task',
    reason:`Lead escalation rejected by policy (${rejectionReason}); final gate findings are team remediation work.`,
    newTask:{
      title:`Remediate final ${gateType} findings`,
      description:`Correct the integrated repository according to the failed final ${gateType} findings and provide direct evidence.`,
      assignedAgentId:agentId,
      acceptanceCriteria:criteria,
      dependsOnTaskKeys:taskRows(mission.id)
        .filter(t=>t.status==='done')
        .map(t=>String(t.task_key))
    }
  };
}

async function cleanupFailedTaskAttempt(mission:any,task:any){
  const ws:any=db.prepare(`
    SELECT * FROM mission_task_workspaces
    WHERE task_id=?
    ORDER BY attempt_no DESC LIMIT 1
  `).get(task.id);

  if(!ws?.workspace_path)return {removed:false,skipped:true};
  if(ws.commit_hash || ws.merge_commit){
    throw new Error(`RECOVERY_REFUSES_INTEGRATED_TASK:${task.task_key}`);
  }
  if(['cleaned','recovery_cleaned','cleaned_readonly'].includes(String(ws.status))){
    return {removed:true,alreadyClean:true};
  }

  const missionWs=getMissionWorkspaceRecord(mission.id);
  const cleanup=await removeTaskWorktree({
    baseWorkspace:missionWs.base_workspace,
    workspace:ws.workspace_path,
    branch:ws.branch_name
  });

  db.prepare(`
    UPDATE mission_task_workspaces SET status=?,error_text=?,updated_at=? WHERE id=?
  `).run(
    cleanup.removed?'recovery_cleaned':'error',
    cleanup.removed?null:(cleanup.error ?? 'RECOVERY_WORKTREE_CLEANUP_FAILED'),
    now(),ws.id
  );

  event(mission.id,'lead.recovery_workspace_cleaned',{
    taskKey:task.task_key,
    attemptNo:ws.attempt_no,
    removed:cleanup.removed,
    error:cleanup.error ?? null
  },task.id);

  if(!cleanup.removed){
    throw new Error(`RECOVERY_WORKTREE_CLEANUP_FAILED:${cleanup.error ?? 'unknown'}`);
  }
  return cleanup;
}

function assertRecoveryAgentActive(mission:any,agentId:string){
  if(!missionRoster(mission.id).some((a:any)=>String(a.id)===String(agentId))){
    throw new Error(`RECOVERY_AGENT_NOT_ACTIVE:${agentId}`);
  }
}

function nextRemediationTaskKey(missionId:string){
  const keys=(db.prepare(`SELECT task_key FROM mission_tasks WHERE mission_id=?`)
    .all(missionId) as any[]).map(x=>String(x.task_key));
  let max=0;
  for(const key of keys){
    const match=key.match(/^T(\d+)$/i);
    if(match)max=Math.max(max,Number(match[1]));
  }
  return `T${max+1}`;
}

function validateDependencyPatch(missionId:string,taskId:string,dependsOnTaskKeys:string[]){
  const rows=taskRows(missionId);
  const byKey=new Map(rows.map(t=>[String(t.task_key),String(t.id)]));
  const depIds=dependsOnTaskKeys.map(key=>{
    const depId=byKey.get(String(key));
    if(!depId)throw new Error(`RECOVERY_UNKNOWN_DEPENDENCY:${key}`);
    if(depId===taskId)throw new Error('RECOVERY_SELF_DEPENDENCY');
    return depId;
  });

  const graph=new Map<string,string[]>();
  for(const row of rows){
    const id=String(row.id);
    graph.set(id,id===taskId ? depIds : dependencies(id).map(d=>String(d.id)));
  }
  const visiting=new Set<string>();
  const visited=new Set<string>();
  const visit=(id:string)=>{
    if(visited.has(id))return;
    if(visiting.has(id))throw new Error('RECOVERY_DEPENDENCY_CYCLE');
    visiting.add(id);
    for(const dep of graph.get(id) ?? [])visit(dep);
    visiting.delete(id);
    visited.add(id);
  };
  for(const id of graph.keys())visit(id);
  return depIds;
}

function applyRecoveryTaskReset(mission:any,task:any,guidance:string,eventType:string){
  db.prepare(`
    UPDATE mission_tasks
    SET status='pending',execution_id=NULL,result_text=NULL,error_text=NULL,
        started_at=NULL,ended_at=NULL,updated_at=?
    WHERE id=?
  `).run(now(),task.id);
  db.prepare(`
    UPDATE missions SET status='running',error_text=NULL,ended_at=NULL,updated_at=? WHERE id=?
  `).run(now(),mission.id);

  if(String(guidance ?? '').trim()){
    publishMessage({
      missionId:mission.id,
      taskId:task.id,
      senderType:'lead',
      senderAgentId:mission.lead_agent_id,
      recipientType:'task',
      recipientId:task.id,
      messageType:'directive',
      priority:'high',
      content:`Autonomous Lead recovery guidance:\n${String(guidance).trim()}`,
      structured:{source:'autonomous_recovery',eventType}
    });
  }
}

function applyRecoveryAction(
  mission:any,
  action:any,
  failedTask:any|null,
  contextKind:'task'|'final_gate'
):RecoveryResult{
  const type=String(action?.action ?? '');
  const reason=String(action?.reason ?? '').trim();

  if(type==='escalate'){
    const policy=recoveryEscalationPolicy(
      mission,
      action,
      failedTask,
      failedTask
        ? {error:failedTask.error_text,gate:latestTaskGate(failedTask.id)}
        : {},
      contextKind
    );

    if(!policy.allowed){
      throw new Error(
        `RECOVERY_ESCALATION_POLICY_DENIED:${policy.reason ?? 'unknown'}`
      );
    }

    publishMessage({
      missionId:mission.id,
      taskId:failedTask?.id ?? null,
      senderType:'lead',
      senderAgentId:mission.lead_agent_id,
      recipientType:'boss',
      recipientId:'boss',
      messageType:'question',
      priority:'high',
      content:String(action?.bossQuestion ?? reason ?? 'Lead requires Boss input.'),
      structured:{
        source:'autonomous_recovery',
        recoveryReason:reason,
        contextKind,
        escalationType:String(action?.escalationType ?? '')
      }
    });
    db.prepare(`
      UPDATE missions SET status='blocked',error_text='LEAD_ESCALATION_REQUIRED',updated_at=? WHERE id=?
    `).run(now(),mission.id);
    event(mission.id,'lead.recovery_escalated',{
      reason,bossQuestion:String(action?.bossQuestion ?? '')
    },failedTask?.id ?? null);
    return {recovered:false,escalated:true,action:type,reason};
  }

  if(type==='retry_gate'){
    if(contextKind!=='final_gate')throw new Error('RECOVERY_RETRY_GATE_ONLY_FINAL_GATE');
    db.prepare(`UPDATE missions SET status='running',error_text=NULL,updated_at=? WHERE id=?`)
      .run(now(),mission.id);
    event(mission.id,'lead.recovery_applied',{action:type,reason});
    return {recovered:true,action:type,reason};
  }

  if(type==='add_remediation_task'){
    if(contextKind!=='final_gate')throw new Error('RECOVERY_REMEDIATION_ONLY_FINAL_GATE');
    const spec=action.newTask ?? {};
    const assignedAgentId=String(spec.assignedAgentId ?? '');
    assertRecoveryAgentActive(mission,assignedAgentId);

    const key=nextRemediationTaskKey(mission.id);
    const title=String(spec.title ?? '').trim();
    const instruction=String(spec.instruction ?? spec.description ?? '').trim();
    const expectedOutcome=String(spec.expectedOutcome ?? spec.expected_outcome ?? spec.description ?? '').trim();
    const rawWorkMode=String(spec.workMode ?? spec.work_mode ?? '').trim();
    const workMode=(['modify','validate','analysis','general'].includes(rawWorkMode)
      ? rawWorkMode
      : inferWorkModeFromPlanText({title,instruction,expectedOutcome})) as PlanTask['workMode'];
    const description=instruction;
    const acceptanceCriteria=Array.isArray(spec.acceptanceCriteria)
      ? spec.acceptanceCriteria.map((x:any)=>String(x).trim()).filter(Boolean).slice(0,12)
      : [];
    if(!title || !instruction || !expectedOutcome || !acceptanceCriteria.length){
      throw new Error('RECOVERY_REMEDIATION_TASK_INVALID');
    }

    const hard=validatePlanAgainstBossConstraints(mission.objective,{
      tasks:[{key,title,instruction,expectedOutcome,workMode,description,acceptanceCriteria}]
    });
    if(!hard.ok){
      throw new Error(`RECOVERY_REMEDIATION_CONSTRAINT_VIOLATION:${hard.violations.map(planViolationLabel).join(',')}`);
    }

    const allTasks=taskRows(mission.id);
    const byKey=new Map(allTasks.map(t=>[String(t.task_key),t]));
    const requestedDeps:string[]=Array.isArray(spec.dependsOnTaskKeys)
      ? spec.dependsOnTaskKeys.map((value:any)=>String(value))
      : [];
    const dependencyTasks:any[]=requestedDeps.length
      ? requestedDeps.map((key:string)=>{
          const dep=byKey.get(key);
          if(!dep)throw new Error(`RECOVERY_UNKNOWN_DEPENDENCY:${key}`);
          return dep;
        })
      : allTasks.filter(t=>t.status==='done');

    const seq=Number((db.prepare(`SELECT COALESCE(MAX(sequence_no),0) m FROM mission_tasks WHERE mission_id=?`)
      .get(mission.id) as any)?.m ?? 0)+1;
    const priority=Number((db.prepare(`SELECT COALESCE(MAX(priority),0) m FROM mission_tasks WHERE mission_id=?`)
      .get(mission.id) as any)?.m ?? 0)+10;
    const taskId=id('mtask');
    const t=now();

    db.transaction(()=>{
      db.prepare(`
        INSERT INTO mission_tasks
        (id,mission_id,task_key,sequence_no,title,instruction,expected_outcome,work_mode,
         description,acceptance_criteria_json,assigned_agent_id,status,priority,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)
      `).run(taskId,mission.id,key,seq,title,instruction,expectedOutcome,workMode,description,
        JSON.stringify(acceptanceCriteria),assignedAgentId,priority,t,t);
      const depInsert=db.prepare(`
        INSERT OR IGNORE INTO mission_task_dependencies(task_id,depends_on_task_id) VALUES (?,?)
      `);
      for(const dep of dependencyTasks)depInsert.run(taskId,dep.id);
      db.prepare(`UPDATE missions SET status='running',error_text=NULL,ended_at=NULL,updated_at=? WHERE id=?`)
        .run(t,mission.id);
    })();

    refreshMissionPlanSnapshot(mission.id);
    publishMessage({
      missionId:mission.id,taskId,senderType:'lead',senderAgentId:mission.lead_agent_id,
      recipientType:'task',recipientId:taskId,messageType:'directive',priority:'high',
      content:`Autonomous remediation created after final gate failure.\nReason: ${reason}`,
      structured:{source:'autonomous_recovery',recoveryAction:type}
    });
    event(mission.id,'lead.recovery_remediation_created',{
      action:type,reason,taskId,taskKey:key,assignedAgentId,
      dependencies:dependencyTasks.map((t:any)=>t.task_key)
    },taskId);
    return {recovered:true,action:type,reason};
  }

  if(!failedTask)throw new Error(`RECOVERY_ACTION_REQUIRES_TASK:${type}`);
  if(!['failed','blocked'].includes(String(failedTask.status))){
    throw new Error(`RECOVERY_TASK_NOT_FAILED:${failedTask.task_key}:${failedTask.status}`);
  }

  if(type==='retry_task'){
    applyRecoveryTaskReset(mission,failedTask,String(action?.guidance ?? reason),type);
    event(mission.id,'lead.recovery_applied',{action:type,reason,taskKey:failedTask.task_key},failedTask.id);
    return {recovered:true,action:type,reason};
  }

  if(type==='reassign_task'){
    const agentId=String(action?.assignedAgentId ?? '');
    assertRecoveryAgentActive(mission,agentId);
    validateRecoveryPlanObligations(mission,failedTask.id,{assignedAgentId:agentId});
    db.prepare(`
      UPDATE mission_tasks
      SET assigned_agent_id=?,status='pending',execution_id=NULL,result_text=NULL,error_text=NULL,
          started_at=NULL,ended_at=NULL,updated_at=? WHERE id=?
    `).run(agentId,now(),failedTask.id);
    db.prepare(`UPDATE missions SET status='running',error_text=NULL,updated_at=? WHERE id=?`)
      .run(now(),mission.id);
    refreshMissionPlanSnapshot(mission.id);
    publishMessage({
      missionId:mission.id,taskId:failedTask.id,senderType:'lead',senderAgentId:mission.lead_agent_id,
      recipientType:'task',recipientId:failedTask.id,messageType:'directive',priority:'high',
      content:`Lead reassigned this task during autonomous recovery.\nReason: ${reason}`,
      structured:{source:'autonomous_recovery',recoveryAction:type}
    });
    event(mission.id,'lead.recovery_applied',{
      action:type,reason,taskKey:failedTask.task_key,
      fromAgentId:failedTask.assigned_agent_id,toAgentId:agentId
    },failedTask.id);
    return {recovered:true,action:type,reason};
  }

  if(type==='revise_task'){
    const patch=action.patch ?? {};
    const title=String(patch.title ?? failedTask.title).trim();
    const currentMode=resolvedTaskWorkMode(mission,failedTask);
    const currentInstruction=synthesizedTaskInstruction(mission,failedTask,currentMode);
    const instruction=String(patch.instruction ?? patch.description ?? currentInstruction).trim();
    const expectedOutcome=String(patch.expectedOutcome ?? taskExpectedOutcome(mission,failedTask)).trim();
    const requestedMode=String(patch.workMode ?? currentMode).trim();
    const workMode=(['modify','validate','analysis','general'].includes(requestedMode)
      ? requestedMode
      : inferWorkModeFromPlanText({title,instruction,expectedOutcome})) as PlanTask['workMode'];
    const description=instruction;
    const acceptanceCriteria=Array.isArray(patch.acceptanceCriteria)
      ? patch.acceptanceCriteria.map((x:any)=>String(x).trim()).filter(Boolean).slice(0,12)
      : parseJson<string[]>(failedTask.acceptance_criteria_json,[]);
    const agentId=String(patch.assignedAgentId ?? failedTask.assigned_agent_id);
    if(!title || !instruction || !expectedOutcome)throw new Error('RECOVERY_TASK_PATCH_INVALID');
    assertRecoveryAgentActive(mission,agentId);

    const hard=validatePlanAgainstBossConstraints(mission.objective,{
      tasks:[{key:failedTask.task_key,title,instruction,expectedOutcome,workMode,description,acceptanceCriteria}]
    });
    if(!hard.ok){
      throw new Error(`RECOVERY_TASK_PATCH_CONSTRAINT_VIOLATION:${hard.violations.map(planViolationLabel).join(',')}`);
    }

    let depIds:string[]|null=null;
    const dependsOnTaskKeys=Array.isArray(patch.dependsOnTaskKeys)
      ? patch.dependsOnTaskKeys.map((value:any)=>String(value))
      : undefined;
    if(dependsOnTaskKeys){
      depIds=validateDependencyPatch(mission.id,failedTask.id,dependsOnTaskKeys);
    }

    validateRecoveryPlanObligations(mission,failedTask.id,{
      title,
      instruction,
      expectedOutcome,
      workMode,
      description,
      assignedAgentId:agentId,
      acceptanceCriteria,
      dependsOnTaskKeys
    });

    db.transaction(()=>{
      db.prepare(`
        UPDATE mission_tasks
        SET title=?,instruction=?,expected_outcome=?,work_mode=?,description=?,
            acceptance_criteria_json=?,assigned_agent_id=?,status='pending',execution_id=NULL,
            result_text=NULL,error_text=NULL,started_at=NULL,ended_at=NULL,updated_at=? WHERE id=?
      `).run(title,instruction,expectedOutcome,workMode,description,
        JSON.stringify(acceptanceCriteria),agentId,now(),failedTask.id);
      if(depIds){
        db.prepare(`DELETE FROM mission_task_dependencies WHERE task_id=?`).run(failedTask.id);
        const ins=db.prepare(`INSERT INTO mission_task_dependencies(task_id,depends_on_task_id) VALUES (?,?)`);
        for(const depId of depIds)ins.run(failedTask.id,depId);
      }
      db.prepare(`UPDATE missions SET status='running',error_text=NULL,updated_at=? WHERE id=?`)
        .run(now(),mission.id);
    })();

    refreshMissionPlanSnapshot(mission.id);
    publishMessage({
      missionId:mission.id,taskId:failedTask.id,senderType:'lead',senderAgentId:mission.lead_agent_id,
      recipientType:'task',recipientId:failedTask.id,messageType:'directive',priority:'high',
      content:`Lead revised this task autonomously after failure.\nReason: ${reason}\nUse the revised task definition and acceptance criteria.`,
      structured:{source:'autonomous_recovery',recoveryAction:type,revisedAcceptanceCriteria:acceptanceCriteria}
    });
    event(mission.id,'lead.recovery_task_revised',{
      action:type,reason,taskKey:failedTask.task_key,title,instruction,expectedOutcome,workMode,acceptanceCriteria,
      assignedAgentId:agentId,dependencies:depIds
    },failedTask.id);
    return {recovered:true,action:type,reason};
  }

  throw new Error(`RECOVERY_ACTION_UNSUPPORTED:${type}`);
}

function leadRecoveryPrompt(mission:any,kind:'task'|'final_gate',failedTask:any|null,failure:any){
  const taskGate=failedTask ? latestTaskGate(failedTask.id) : null;
  return `You are the Lead Supervisor of an autonomous Doom Team mission.

Your job is NOT to ask the Boss to click Retry, Replan, Approve or Start for routine internal failures.
Resolve routine failures autonomously and keep the mission moving.

MISSION:\n${mission.title}

BOSS OBJECTIVE:\n${mission.objective}

BOSS HARD CONSTRAINTS:\n${hardConstraintPrompt(mission.objective)}

FAILURE KIND:\n${kind}

FAILED TASK:\n${failedTask ? JSON.stringify({
  taskKey:failedTask.task_key,title:failedTask.title,
  instruction:synthesizedTaskInstruction(mission,failedTask,resolvedTaskWorkMode(mission,failedTask)),
  expectedOutcome:taskExpectedOutcome(mission,failedTask),
  workMode:resolvedTaskWorkMode(mission,failedTask),description:failedTask.description,
  assignedAgentId:failedTask.assigned_agent_id,status:failedTask.status,error:failedTask.error_text,
  result:String(failedTask.result_text ?? '').slice(0,7000),
  acceptanceCriteria:parseJson(failedTask.acceptance_criteria_json,[]),latestAcceptanceGate:taskGate
},null,2) : 'none'}

FAILURE / GATE CONTEXT:\n${JSON.stringify(failure,null,2).slice(0,16000)}

CURRENT TASK DAG:\n${JSON.stringify(recoveryTaskSnapshot(mission.id),null,2).slice(0,18000)}

ACTIVE ROSTER WITH EXACT IDS AND TOOLS:\n${JSON.stringify(recoveryRoster(mission.id).map((agent:any)=>{
  const row:any=db.prepare(`
    SELECT av.tools_json
    FROM agents a
    JOIN agent_versions av ON av.id=a.current_version_id
    WHERE a.id=?
  `).get(agent.agentId);

  return {
    ...agent,
    tools:resolveToolDefinitions(
      parseJson<string[]>(row?.tools_json,[])
    ).map(t=>t.name)
  };
}),null,2)}

AUTONOMOUS RECOVERY POLICY:
- Routine engineering/planning/execution/acceptance problems MUST be solved by the team, not escalated to Boss.
- If an acceptance criterion is self-referential, requires a future/other actor approval, or was invented by planning and is not required by Boss, use revise_task and replace it with task-local measurable criteria.
- If instruction, expectedOutcome or workMode is wrong, revise that semantic contract explicitly; do not rely on description-only rewrites.
- If the task definition is correct and failure is transient/technical, use retry_task.
- If assigned agent is unsuitable, use reassign_task with an exact active roster agentId.
- For final QA/Reviewer failure that identifies a real defect in integrated work, use add_remediation_task. Assign it to the best modifying agent; final gates will run again automatically.
- Use retry_gate only when the final gate itself failed technically/transiently and no repository remediation is needed.
- Never weaken Boss hard constraints merely to pass a gate.
- Escalate ONLY when input can genuinely only come from Boss, safety/policy requires human approval, credentials are required, or production approval is required.
- An escalation MUST include escalationType: missing_boss_input | safety_approval | production_approval | credential_required | policy_required.
- Never ask Boss to repeat the mission objective or acceptance criteria already included above.
- Do not escalate because a model, agent, plan, tool choice or gate made a routine mistake.
- If an implementation task produced a clean workspace, retry/revise/reassign and require real repository changes.
- If the assigned agent is already a temporary specialist, lack of AgentFactory access is not a blocker; the specialist already exists.

Return ONLY recovery_action_json.`;
}

async function runLeadRecoveryDecision(mission:any,kind:'task'|'final_gate',failedTask:any|null,failure:any){
  const execution:any=await startExecution(
    mission.lead_agent_id,
    leadRecoveryPrompt(mission,kind,failedTask,failure),
    {
      workspace:cfg.defaultWorkspace,
      context:{
        missionId:mission.id,taskId:failedTask?.id ?? null,phase:'lead_recovery',
        expectedFinalFormat:'recovery_action_json',structuredFinalStepBudget:4
      },
      toolGrantsOverride:[]
    }
  );
  event(mission.id,'lead.recovery_started',{
    recoveryExecutionId:execution.id,kind,failedTaskKey:failedTask?.task_key ?? null
  },failedTask?.id ?? null);
  const finished:any=await waitForExecution(execution.id);
  if(finished.status!=='done'){
    throw new Error(`LEAD_RECOVERY_EXECUTION_${String(finished.status).toUpperCase()}:${finished.error_text ?? ''}`);
  }
  const action=extractJsonObject(finished.result_text ?? '');
  event(mission.id,'lead.recovery_decided',{
    recoveryExecutionId:execution.id,kind,action:action.action,reason:action.reason
  },failedTask?.id ?? null);
  return action;
}

async function attemptAutonomousTaskRecovery(mission:any,failedTask:any):Promise<RecoveryResult>{
  const missionAttempts=appliedMissionRecoveryCount(mission.id);
  const taskAttempts=appliedTaskRecoveryCount(mission.id,failedTask.id);
  if(missionAttempts>=MAX_AUTONOMOUS_RECOVERY_PER_MISSION || taskAttempts>=MAX_AUTONOMOUS_RECOVERY_PER_TASK){
    event(mission.id,'lead.recovery_budget_exhausted',{
      missionAttempts,taskAttempts,taskKey:failedTask.task_key
    },failedTask.id);
    return {recovered:false,reason:'RECOVERY_BUDGET_EXHAUSTED'};
  }

  event(mission.id,'lead.recovery_attempt_started',{
    kind:'task',
    taskKey:failedTask.task_key,
    missionAttempt:missionAttempts+1,
    missionBudget:MAX_AUTONOMOUS_RECOVERY_PER_MISSION,
    scopeAttempt:taskAttempts+1,
    scopeBudget:MAX_AUTONOMOUS_RECOVERY_PER_TASK,
  },failedTask.id);

  try{
    await cleanupFailedTaskAttempt(mission,failedTask);

    const failureContext={
      error:failedTask.error_text,
      gate:latestTaskGate(failedTask.id),
      evidence:taskEvidenceRows(failedTask.id).slice(-12),
      assignedAgent:assignedAgentExecutionContext(mission,failedTask)
    };

    let action:any=deterministicTaskRecoveryAction(
      mission,
      failedTask,
      failureContext
    );

    if(action){
      event(mission.id,'lead.recovery_deterministic_decision',{
        action:action.action,
        reason:action.reason,
        taskKey:failedTask.task_key
      },failedTask.id);
    }else{
      action=await runLeadRecoveryDecision(
        mission,'task',failedTask,failureContext
      );
    }

    let policy=recoveryEscalationPolicy(
      mission,action,failedTask,failureContext,'task'
    );

    if(action?.action==='escalate' && !policy.allowed){
      event(mission.id,'lead.recovery_escalation_rejected',{
        action,
        reason:policy.reason,
        taskKey:failedTask.task_key
      },failedTask.id);

      const correctedFailure={
        ...failureContext,
        escalationRejected:{
          reason:policy.reason,
          instruction:'Choose retry_task, revise_task or reassign_task. Boss objective and criteria are already present.'
        }
      };

      action=await runLeadRecoveryDecision(
        mission,'task',failedTask,correctedFailure
      );

      policy=recoveryEscalationPolicy(
        mission,action,failedTask,correctedFailure,'task'
      );

      if(action?.action==='escalate' && !policy.allowed){
        event(mission.id,'lead.recovery_escalation_rejected',{
          action,
          reason:policy.reason,
          taskKey:failedTask.task_key,
          secondAttempt:true
        },failedTask.id);

        action=deterministicTaskRecoveryFallback(
          mission,
          failedTask,
          correctedFailure,
          String(policy.reason ?? 'policy_rejected')
        );
      }
    }

    if(action?.action==='retry_gate'){
      event(mission.id,'lead.recovery_invalid_action_corrected',{
        fromAction:'retry_gate',
        toAction:'retry_task',
        reason:'retry_gate is a final-gate action and cannot recover a failed task.',
        taskKey:failedTask.task_key,
      },failedTask.id);
      action=deterministicTaskRecoveryFallback(
        mission,
        failedTask,
        failureContext,
        'TASK_RECOVERY_RETRY_GATE_INVALID'
      );
    }

    if(action?.action==='replace_specialist'){
      const replacement=replaceNoOpSpecialist(
        mission,
        failedTask,
        String(action?.reason ?? 'Repeated no-op modification execution')
      );
      if(replacement)return replacement;

      action={
        action:'retry_task',
        reason:'Specialist replacement was not applicable; perform one bounded retry.',
        taskKey:failedTask.task_key,
        guidance:'Actually modify the required target with write_file/replace_in_file and verify git evidence.'
      };
    }

    return applyRecoveryAction(mission,action,failedTask,'task');
  }catch(err:any){
    event(mission.id,'lead.recovery_failed',{
      error:String(err?.message ?? err),taskKey:failedTask.task_key
    },failedTask.id);
    return {recovered:false,reason:String(err?.message ?? err)};
  }
}

async function attemptAutonomousFinalGateRecovery(
  mission:any,gateType:'qa'|'reviewer',gateResult:any
):Promise<RecoveryResult>{
  const missionAttempts=appliedMissionRecoveryCount(mission.id);
  const finalAttempts=appliedFinalGateRecoveryCount(mission.id);
  if(missionAttempts>=MAX_AUTONOMOUS_RECOVERY_PER_MISSION || finalAttempts>=MAX_AUTONOMOUS_FINAL_GATE_RECOVERY){
    event(mission.id,'lead.recovery_budget_exhausted',{missionAttempts,finalAttempts,gateType});
    return {recovered:false,reason:'FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED'};
  }

  event(mission.id,'lead.recovery_attempt_started',{
    kind:'final_gate',
    gateType,
    missionAttempt:missionAttempts+1,
    missionBudget:MAX_AUTONOMOUS_RECOVERY_PER_MISSION,
    scopeAttempt:finalAttempts+1,
    scopeBudget:MAX_AUTONOMOUS_FINAL_GATE_RECOVERY,
  });
  event(mission.id,'lead.final_gate_recovery_started',{gateType,gateId:gateResult.gateId});
  try{
    const failureContext={
      gateType,
      gate:latestFinalGate(mission.id,gateType),
      result:gateResult,
      integratedTasks:recoveryTaskSnapshot(mission.id)
    };

    let action:any=await runLeadRecoveryDecision(
      mission,'final_gate',null,failureContext
    );

    let policy=recoveryEscalationPolicy(
      mission,action,null,failureContext,'final_gate'
    );

    if(action?.action==='escalate' && !policy.allowed){
      event(mission.id,'lead.recovery_escalation_rejected',{
        action,
        reason:policy.reason,
        gateType
      });

      const correctedFailure={
        ...failureContext,
        escalationRejected:{
          reason:policy.reason,
          instruction:'Choose retry_gate or add_remediation_task. Routine final-gate findings are team work.'
        }
      };

      action=await runLeadRecoveryDecision(
        mission,'final_gate',null,correctedFailure
      );

      policy=recoveryEscalationPolicy(
        mission,action,null,correctedFailure,'final_gate'
      );

      if(action?.action==='escalate' && !policy.allowed){
        event(mission.id,'lead.recovery_escalation_rejected',{
          action,
          reason:policy.reason,
          gateType,
          secondAttempt:true
        });

        action=deterministicFinalGateRecoveryFallback(
          mission,
          gateType,
          String(policy.reason ?? 'policy_rejected')
        );
      }
    }

    return applyRecoveryAction(mission,action,null,'final_gate');
  }catch(err:any){
    event(mission.id,'lead.recovery_failed',{error:String(err?.message ?? err),gateType});
    return {recovered:false,reason:String(err?.message ?? err)};
  }
}


function priorMutationFailedModelIds(taskId:string){
  const rows=db.prepare(`
    SELECT DISTINCT e.model_id
    FROM mission_task_evidence mte
    JOIN executions e ON e.id=mte.execution_id
    WHERE mte.task_id=?
      AND mte.evidence_type='mutation_model_failure'
      AND mte.status='fail'
      AND e.model_id IS NOT NULL
  `).all(taskId) as any[];

  return rows.map(row=>String(row.model_id)).filter(Boolean);
}

async function executeMissionTask(mission:any,task:any) {
  const constraintCheck=validatePlanAgainstBossConstraints(mission.objective,{
    tasks:[{
      key:task.task_key,
      title:task.title,
      description:task.description,
      acceptanceCriteria:parseJson(task.acceptance_criteria_json,[])
    }]
  });

  if(!constraintCheck.ok){
    const message=`PLAN_CONSTRAINT_VIOLATION:${constraintCheck.violations.map(v=>v.reason).join(' | ')}`;
    db.prepare(`
      UPDATE mission_tasks SET status='failed',error_text=?,ended_at=?,updated_at=? WHERE id=?
    `).run(message,now(),now(),task.id);
    event(mission.id,'task.plan_constraint_blocked',{
      violations:constraintCheck.violations
    },task.id);
    return false;
  }

  const workMode=resolvedTaskWorkMode(mission,task);
  const agentContext=assignedAgentExecutionContext(mission,task);

  if(workMode==='modify' && !isModificationToolAvailable(agentContext)){
    const message='MODIFY_AGENT_WRITE_TOOLS_MISSING';
    db.prepare(`
      UPDATE mission_tasks
      SET status='failed',error_text=?,ended_at=?,updated_at=?
      WHERE id=?
    `).run(message,now(),now(),task.id);

    event(mission.id,'task.capability_preflight_failed',{
      taskKey:task.task_key,
      workMode,
      assignedAgentId:task.assigned_agent_id,
      tools:agentContext.tools,
      reason:message
    },task.id);
    return false;
  }

  event(mission.id,'task.capability_preflight_passed',{
    taskKey:task.task_key,
    workMode,
    assignedAgentId:task.assigned_agent_id,
    tools:agentContext.tools
  },task.id);

  const attemptNo=Number(task.attempt_no ?? 0)+1;
  const prepared=await prepareTaskWorkspace(mission,task,attemptNo);
  const started=now();

  let execution:any;
  try{
    execution=await startExecution(
      task.assigned_agent_id,
      taskPrompt(mission,task),
      {
        workspace:prepared.workspace,
        context:{
          missionId:mission.id,
          taskId:task.id,
          taskKey:task.task_key,
          attemptNo,
          workspaceMode:prepared.mode,
          taskWorkMode:workMode,
          preferMutationModel:workMode==='modify',
          excludeMutationModelIds:workMode==='modify'
            ? priorMutationFailedModelIds(task.id)
            : [],
          requireMutationTool:workMode==='modify',
          requiredMutationTargets:workMode==='modify'
            ? inferExpectedTargetPaths(mission,task)
            : []
        },
        toolGrantsOverride:taskExecutionToolOverride(workMode)
      }
    );
  }catch(err:any){
    const startError=`EXECUTION_START_FAILED:${String(err?.message ?? err)}`;
    db.prepare(`
      UPDATE mission_tasks
      SET status='failed',error_text=?,ended_at=?,updated_at=?
      WHERE id=?
    `).run(startError,now(),now(),task.id);

    event(mission.id,'task.failed',{
      executionId:null,
      error:startError,
      phase:'execution_start'
    },task.id);
    return false;
  }

  db.prepare(`
    UPDATE mission_tasks
    SET status='running',execution_id=?,attempt_no=?,started_at=?,ended_at=NULL,
        result_text=NULL,error_text=NULL,updated_at=?
    WHERE id=?
  `).run(execution.id,attemptNo,started,started,task.id);

  event(mission.id,'task.started',{
    executionId:execution.id,
    agentId:task.assigned_agent_id,
    attempt:attemptNo,
    workspace:prepared.workspace,
    workspaceMode:prepared.mode
  },task.id);

  const finished:any=await waitForExecution(execution.id);
  if(finished.status==='cancelled'){
    db.prepare(`
      UPDATE mission_tasks SET status='cancelled',error_text=?,ended_at=?,updated_at=? WHERE id=?
    `).run(finished.error_text ?? 'EXECUTION_CANCELLED',now(),now(),task.id);
    event(mission.id,'task.cancelled',{executionId:execution.id},task.id);
    return false;
  }
  if(finished.status!=='done'){
    const executionError=String(finished.error_text ?? 'EXECUTION_FAILED');

    if(
      workMode==='modify'
      && executionError.includes('MUTATION_CONTRACT_UNSATISFIED')
    ){
      evidence(
        mission.id,
        task.id,
        execution.id,
        'mutation_model_failure',
        'fail',
        {
          modelId:finished.model_id ?? execution.model_id ?? null,
          runtimeId:finished.runtime_id ?? execution.runtime_id ?? null,
          error:executionError
        }
      );
      event(mission.id,'model.mutation_execution_failed',{
        executionId:execution.id,
        modelId:finished.model_id ?? execution.model_id ?? null,
        error:executionError
      },task.id);
    }

    db.prepare(`
      UPDATE mission_tasks SET status='failed',error_text=?,ended_at=?,updated_at=? WHERE id=?
    `).run(executionError,now(),now(),task.id);
    event(mission.id,'task.failed',{
      executionId:execution.id,
      error:executionError
    },task.id);
    return false;
  }

  collectExecutionEvidence(mission.id,task.id,execution.id,finished.result_text ?? '');

  db.prepare(`UPDATE mission_tasks SET status='validating',updated_at=? WHERE id=?`)
    .run(now(),task.id);
  event(mission.id,'task.validating',{executionId:execution.id},task.id);

  const gate=await runTaskAcceptanceGate(mission,task,finished,prepared.workspace);
  if(!gate.pass){
    const gateError=gate.error===true;
    const errorCode=gateError?'ACCEPTANCE_GATE_ERROR':'ACCEPTANCE_GATE_FAILED';

    db.prepare(`
      UPDATE mission_tasks SET status='failed',result_text=?,error_text=?,
        ended_at=?,updated_at=? WHERE id=?
    `).run(finished.result_text ?? '',errorCode,now(),now(),task.id);

    event(mission.id,'task.failed',{
      executionId:execution.id,
      error:errorCode,
      gateId:gate.gateId,
      summary:gate.summary
    },task.id);
    return false;
  }

  try{
    const integration=await finalizeAcceptedTaskWorkspace(mission,task,attemptNo,prepared);
    db.prepare(`
      UPDATE mission_tasks
      SET status='done',result_text=?,error_text=NULL,ended_at=?,updated_at=?
      WHERE id=?
    `).run(finished.result_text ?? '',now(),now(),task.id);
    event(mission.id,'task.done',{
      executionId:execution.id,
      acceptanceGateId:gate.gateId,
      integration
    },task.id);

    const handoffs=createAutomaticHandoffs(
      mission.id,
      task.id,
      task.assigned_agent_id,
      finished.result_text ?? ''
    );
    if(handoffs.length){
      event(mission.id,'message.handoffs_created',{
        sourceTaskId:task.id,
        count:handoffs.length,
        messageIds:handoffs.map((x:any)=>x.id)
      },task.id);
    }
    return true;
  }catch(err:any){
    const message=String(err?.message ?? err);
    if(prepared.mode==='isolated'){
      db.prepare(`
        UPDATE mission_task_workspaces SET status='error',error_text=?,updated_at=?
        WHERE task_id=? AND attempt_no=?
      `).run(message,now(),task.id,attemptNo);
    }
    db.prepare(`
      UPDATE mission_tasks SET status='failed',result_text=?,error_text=?,ended_at=?,updated_at=?
      WHERE id=?
    `).run(finished.result_text ?? '',`WORKTREE_INTEGRATION_FAILED:${message}`,now(),now(),task.id);
    event(mission.id,'task.failed',{error:`WORKTREE_INTEGRATION_FAILED:${message}`},task.id);
    return false;
  }
}

function missionResultsSummary(missionId:string){
  return taskRows(missionId).map(t=>({
    key:t.task_key,
    title:t.title,
    agent:t.agent_name,
    status:t.status,
    result:String(t.result_text ?? '').slice(0,3500)
  }));
}

function finalGatePrompt(
  gateType:'qa'|'reviewer',
  mission:any,
  tasks:any[],
  integrationEvidence:any,
){
  const role=gateType==='qa'
    ? 'You are Doom Team QA acting as the final mission quality gate.'
    : 'You are Doom Team Reviewer acting as the final independent review gate.';

  const instructions=gateType==='qa'
    ? `Run relevant tests when a test runner exists. Inspect the integrated workspace and verify the Boss objective and regression risk.`
    : `Inspect the integrated diff, architecture/maintainability, acceptance evidence and test state. Do not approve merely because other agents said work is complete.`;

  return `${role}

MISSION:
${mission.title}

BOSS OBJECTIVE:
${mission.objective}

COMPLETED TASK RESULTS (AGENT CLAIMS; NOT SOURCE OF TRUTH):
${JSON.stringify(tasks,null,2).slice(0,16000)}

INTEGRATED REPOSITORY EVIDENCE (SOURCE OF TRUTH):
${JSON.stringify({
  diffStat:integrationEvidence?.diffStat ?? '',
  diff:String(integrationEvidence?.diff ?? '').slice(0,30000),
  integrationHead:integrationEvidence?.integrationHead ?? null,
  integrationClean:integrationEvidence?.integrationClean ?? null
},null,2)}

${instructions}
When the Boss objective names a concrete target file such as README.md, inspect that target directly and judge the integrated content, not an analysis/report file with a similar name.
Agent reports and handoffs are claims. Repository state and direct file reads are evidence.
Do not modify files.

Return ONLY JSON:
{
  "pass": true,
  "summary": "short final gate conclusion",
  "findings": [
    {"severity":"low|medium|high","finding":"...","evidence":"..."}
  ]
}

Fail when a material acceptance, correctness, test or maintainability issue remains.`;
}

async function runFinalMissionGate(
  mission:any,
  gateType:'qa'|'reviewer',
  workspace:string,
){
  const agent=findTeamAgentByRole(mission.team_id,gateType);
  if(!agent){
    throw new Error(`FINAL_GATE_AGENT_NOT_FOUND:${gateType}`);
  }

  const missionWs=getMissionWorkspaceRecord(mission.id);
  const integrationEv=await missionIntegrationEvidence({
    baseWorkspace:missionWs.base_workspace,
    integrationWorkspace:workspace,
    baseCommit:missionWs.base_commit
  });

  const execution:any=await startExecution(
    agent.id,
    finalGatePrompt(
      gateType,
      mission,
      missionResultsSummary(mission.id),
      integrationEv
    ),
    {
      workspace,
      context:{
        missionId:mission.id,
        gateType,
        expectedFinalFormat:'final_gate_json',
        structuredFinalStepBudget:4
      }
    }
  );

  const gateId=createGate(mission.id,null,gateType,agent.id,execution.id);
  event(mission.id,`gate.${gateType}_started`,{gateId,executionId:execution.id,agentId:agent.id});

  const finished:any=await waitForExecution(execution.id);
  if(finished.status!=='done'){
    updateGate(gateId,finished.status==='cancelled'?'cancelled':'error',`${gateType} gate execution ${finished.status}.`,{error:finished.error_text});
    return {pass:false,gateId,summary:`${gateType} gate execution ${finished.status}.`};
  }

  try{
    const parsed=extractJsonObject(finished.result_text ?? '');
    const pass=parsed?.pass===true;
    const summary=String(parsed?.summary ?? `${gateType} gate ${pass?'passed':'failed'}.`);
    updateGate(gateId,pass?'pass':'fail',summary,parsed);
    event(mission.id,pass?`gate.${gateType}_passed`:`gate.${gateType}_failed`,{gateId,summary});
    return {pass,gateId,summary};
  }catch(err:any){
    updateGate(gateId,'error',`${gateType} gate returned invalid JSON.`,{
      error:String(err?.message ?? err),
      raw:String(finished.result_text ?? '').slice(0,12000)
    });
    return {pass:false,gateId,summary:`${gateType} gate returned invalid JSON.`};
  }
}

async function runMissionFinalGates(mission:any):Promise<'passed'|'recovered'|'blocked'>{
  const workspaceRecord=await prepareMissionWorkspace(mission.id);
  const workspace=workspaceRecord.integration_workspace || workspaceRecord.base_workspace;

  db.prepare(`UPDATE missions SET status='validating',updated_at=?,error_text=NULL WHERE id=?`)
    .run(now(),mission.id);
  event(mission.id,'mission.validating',{workspace});

  const qa=await runFinalMissionGate(mission,'qa',workspace);
  if(!qa.pass){
    const recovery=await attemptAutonomousFinalGateRecovery(mission,'qa',qa);
    if(recovery.recovered){
      event(mission.id,'lead.final_gate_recovered',{gateType:'qa',action:recovery.action,reason:recovery.reason});
      return 'recovered';
    }
    if(!recovery.escalated){
      const reason=finalGateRecoveryBudgetExhausted(mission.id)
        ? 'FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:qa'
        : 'QA_GATE_FAILED';
      db.prepare(`UPDATE missions SET status='blocked',error_text=?,updated_at=? WHERE id=?`)
        .run(reason,now(),mission.id);
      event(mission.id,'mission.blocked',{reason,gateId:qa.gateId,recoveryReason:recovery.reason});
    }
    return 'blocked';
  }

  db.prepare(`UPDATE missions SET status='reviewing',updated_at=?,error_text=NULL WHERE id=?`)
    .run(now(),mission.id);
  event(mission.id,'mission.reviewing',{workspace});

  const reviewer=await runFinalMissionGate(mission,'reviewer',workspace);
  if(!reviewer.pass){
    const recovery=await attemptAutonomousFinalGateRecovery(mission,'reviewer',reviewer);
    if(recovery.recovered){
      event(mission.id,'lead.final_gate_recovered',{gateType:'reviewer',action:recovery.action,reason:recovery.reason});
      return 'recovered';
    }
    if(!recovery.escalated){
      const reason=finalGateRecoveryBudgetExhausted(mission.id)
        ? 'FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:reviewer'
        : 'REVIEW_GATE_FAILED';
      db.prepare(`UPDATE missions SET status='blocked',error_text=?,updated_at=? WHERE id=?`)
        .run(reason,now(),mission.id);
      event(mission.id,'mission.blocked',{reason,gateId:reviewer.gateId,recoveryReason:recovery.reason});
    }
    return 'blocked';
  }
  return 'passed';
}

async function missionLoop(missionId:string) {
  if(activeMissionLoops.has(missionId))return;
  activeMissionLoops.add(missionId);

  try{
    await prepareMissionWorkspace(missionId);

    while(true){
      const mission=missionRow(missionId);
      if(!mission)return;
      if(mission.status==='paused' || mission.status==='cancelled')return;

      const running=(db.prepare(`
        SELECT * FROM mission_tasks WHERE mission_id=? AND status IN ('running','validating') LIMIT 1
      `).get(missionId) as any);

      if(running){
        // V0.9 executes a task atomically inside this process. A running row here is
        // usually a restart recovery case. We do not silently duplicate it.
        const exec:any=running.execution_id ? getExecution(running.execution_id) : null;
        if(exec?.status==='running'){
          throw new Error(`MISSION_RECOVERY_REQUIRED:${running.task_key}`);
        }
        if(exec?.status==='error'){
          db.prepare(`UPDATE mission_tasks SET status='failed',error_text=?,updated_at=? WHERE id=?`)
            .run(exec.error_text ?? 'EXECUTION_FAILED',now(),running.id);
          continue;
        }
      }

      const tasks=taskRows(missionId);
      if(tasks.length===0){
        db.prepare(`UPDATE missions SET status='blocked',error_text='MISSION_HAS_NO_TASKS',updated_at=? WHERE id=?`)
          .run(now(),missionId);
        return;
      }

      if(tasks.every(t=>t.status==='done')){
        const gateState=await runMissionFinalGates(mission);
        if(gateState==='recovered')continue;
        if(gateState!=='passed')return;

        const approval=await ensureMissionMergeApproval(missionId);
        event(missionId,'mission.ready_for_boss',{
          approvalId:approval.id,
          taskCount:tasks.length
        });
        return;
      }

      const failed=tasks.find(t=>t.status==='failed' || t.status==='blocked');
      if(failed){
        const recovery=await attemptAutonomousTaskRecovery(mission,failed);
        if(recovery.recovered){
          event(missionId,'lead.task_recovered',{
            taskKey:failed.task_key,action:recovery.action,reason:recovery.reason
          },failed.id);
          continue;
        }
        if(recovery.escalated)return;
        db.prepare(`UPDATE missions SET status='blocked',error_text=?,updated_at=? WHERE id=?`)
          .run(`AUTONOMOUS_RECOVERY_EXHAUSTED:TASK_FAILED:${failed.task_key}`,now(),missionId);
        event(missionId,'mission.blocked',{
          reason:'AUTONOMOUS_RECOVERY_EXHAUSTED',taskId:failed.id,recoveryReason:recovery.reason
        });
        return;
      }

      const cancelled=tasks.find(t=>t.status==='cancelled');
      if(cancelled){
        db.prepare(`UPDATE missions SET status='blocked',error_text=?,updated_at=? WHERE id=?`)
          .run(`TASK_CANCELLED:${cancelled.task_key}`,now(),missionId);
        event(missionId,'mission.blocked',{reason:'TASK_CANCELLED',taskId:cancelled.id});
        return;
      }

      const ready=nextReadyTask(missionId);
      if(!ready){
        const refreshed=taskRows(missionId);
        const candidate=refreshed.find(t=>['pending','blocked','failed'].includes(t.status));
        if(candidate){
          if(candidate.status==='pending'){
            db.prepare(`UPDATE mission_tasks SET status='blocked',error_text='NO_READY_TASK',updated_at=? WHERE id=?`)
              .run(now(),candidate.id);
            candidate.status='blocked';
            candidate.error_text='NO_READY_TASK';
          }
          const recovery=await attemptAutonomousTaskRecovery(mission,candidate);
          if(recovery.recovered){
            event(mission.id,'lead.dependency_recovered',{
              taskKey:candidate.task_key,action:recovery.action,reason:recovery.reason
            },candidate.id);
            continue;
          }
          if(recovery.escalated)return;
        }
        db.prepare(`UPDATE missions SET status='blocked',error_text='NO_READY_TASK',updated_at=? WHERE id=?`)
          .run(now(),missionId);
        event(mission.id,'mission.blocked',{reason:'NO_READY_TASK'});
        return;
      }

      await executeMissionTask(mission,ready);
    }
  }catch(err:any){
    db.prepare(`UPDATE missions SET status='blocked',error_text=?,updated_at=? WHERE id=?`)
      .run(String(err?.message ?? err),now(),missionId);
    event(missionId,'mission.loop_failed',{error:String(err?.message ?? err)});
  }finally{
    activeMissionLoops.delete(missionId);
  }
}

export function createMission(input:any) {
  const team:any=db.prepare(`SELECT * FROM teams WHERE id=? AND enabled=1`).get(String(input.teamId ?? ''));
  if(!team)throw new Error('TEAM_NOT_FOUND_OR_DISABLED');

  const leadAgentId=String(input.leadAgentId ?? team.lead_agent_id ?? '');
  if(!leadAgentId)throw new Error('MISSION_LEAD_REQUIRED');

  const member:any=db.prepare(`
    SELECT 1 ok FROM team_members tm JOIN agents a ON a.id=tm.agent_id
    WHERE tm.team_id=? AND tm.agent_id=? AND a.enabled=1
  `).get(team.id,leadAgentId);
  if(!member)throw new Error('MISSION_LEAD_MUST_BE_ACTIVE_TEAM_MEMBER');

  const title=String(input.title ?? '').trim();
  const objective=String(input.objective ?? '').trim();
  if(!title)throw new Error('MISSION_TITLE_REQUIRED');
  if(!objective)throw new Error('MISSION_OBJECTIVE_REQUIRED');

  // Normal product flow is autonomous. Internal transactional diagnostics can
  // explicitly suppress async side effects so a rolled-back mission never
  // leaves an orphan planning microtask behind.
  const autoStart=input.autoStart!==false;

  const missionId=id('mission');
  const t=now();
  db.transaction(()=>{
    db.prepare(`
      INSERT INTO missions
      (id,title,objective,team_id,lead_agent_id,status,created_at,updated_at)
      VALUES (?,?,?,?,?,'new',?,?)
    `).run(missionId,title,objective,team.id,leadAgentId,t,t);

    db.prepare(`
      INSERT INTO mission_workspaces
      (mission_id,mode,base_workspace,status,created_at,updated_at)
      VALUES (?,'isolated',?,'pending',?,?)
    `).run(missionId,cfg.defaultWorkspace,t,t);
  })();

  event(missionId,'mission.created',{
    teamId:team.id,
    leadAgentId,
    workspaceMode:'isolated',
    baseWorkspace:cfg.defaultWorkspace,
    autonomous:autoStart
  });

  if(autoStart){
    queueMicrotask(()=>{
      // The mission may have disappeared because an outer transaction rolled
      // back after createMission() returned. Never let an orphan async callback
      // write FK-bound events for a non-existent mission.
      if(!missionRow(missionId))return;

      void planMission(missionId).catch((err:any)=>{
        if(!missionRow(missionId))return;

        db.prepare(`
          UPDATE missions
          SET status='blocked',error_text=?,updated_at=?
          WHERE id=?
        `).run(
          `AUTO_PLAN_FAILED:${String(err?.message ?? err)}`,
          now(),
          missionId
        );

        if(missionRow(missionId)){
          event(missionId,'mission.auto_plan_failed',{
            error:String(err?.message ?? err)
          });
        }
      });
    });
  }

  return getMission(missionId);
}

export function listMissions() {
  return db.prepare(`
    SELECT m.*,t.name AS team_name,a.name AS lead_agent_name,
      mw.mode AS workspace_mode,mw.integration_branch,mw.integration_workspace,mw.status AS workspace_status,
      (SELECT COUNT(*) FROM mission_tasks mt WHERE mt.mission_id=m.id) AS task_count,
      (SELECT COUNT(*) FROM mission_tasks mt WHERE mt.mission_id=m.id AND mt.status='done') AS done_count,
      (SELECT COUNT(*) FROM mission_tasks mt WHERE mt.mission_id=m.id AND mt.status='failed') AS failed_count,
      (SELECT COUNT(*) FROM mission_tasks mt WHERE mt.mission_id=m.id AND mt.status IN ('running','validating')) AS running_count
    FROM missions m
    JOIN teams t ON t.id=m.team_id
    JOIN agents a ON a.id=m.lead_agent_id
    LEFT JOIN mission_workspaces mw ON mw.mission_id=m.id
    ORDER BY m.created_at DESC
  `).all();
}

function taskEvidenceRows(taskId:string){
  return (db.prepare(`
    SELECT * FROM mission_task_evidence WHERE task_id=? ORDER BY id
  `).all(taskId) as any[]).map(x=>({...x,payload:parseJson(x.payload_json,{})}));
}

function taskWorkspaceRows(taskId:string){
  return db.prepare(`
    SELECT * FROM mission_task_workspaces WHERE task_id=? ORDER BY attempt_no DESC
  `).all(taskId);
}

function gateRows(missionId:string){
  return (db.prepare(`
    SELECT g.*,a.name AS agent_name
    FROM mission_gates g
    LEFT JOIN agents a ON a.id=g.agent_id
    WHERE g.mission_id=?
    ORDER BY g.created_at
  `).all(missionId) as any[]).map(x=>({...x,detail:parseJson(x.detail_json,{})}));
}

export function getMission(missionId:string) {
  const mission:any=db.prepare(`
    SELECT m.*,t.name AS team_name,a.name AS lead_agent_name
    FROM missions m
    JOIN teams t ON t.id=m.team_id
    JOIN agents a ON a.id=m.lead_agent_id
    WHERE m.id=?
  `).get(missionId);
  if(!mission)return null;

  const plan=parseJson<any>(mission.plan_json,{});
  const planByKey=new Map(
    (Array.isArray(plan?.tasks)?plan.tasks:[])
      .map((x:any)=>[String(x?.key ?? ''),x])
  );

  const tasks=taskRows(missionId).map(task=>{
    const spec:any=planByKey.get(String(task.task_key)) ?? {};
    const snapshot=parseJson<any>(task.execution_snapshot_json,{});
    return {
    ...task,
    instruction:String(
      task.instruction
      || spec?.instruction
      || synthesizedTaskInstruction(mission,task,resolvedTaskWorkMode(mission,task))
    ),
    expectedOutcome:String(
      task.expected_outcome
      || spec?.expectedOutcome
      || spec?.expected_outcome
      || task.description
      || ''
    ),
    workMode:resolvedTaskWorkMode(mission,task),
    executionRoute:snapshot?.routeDecision ?? null,
    mutationQualification:snapshot?.mutationQualification ?? null,
    acceptanceCriteria:parseJson(task.acceptance_criteria_json,[]),
    dependencies:dependencies(task.id),
    evidence:taskEvidenceRows(task.id),
    artifacts:taskArtifacts(task.id),
    workspaces:taskWorkspaceRows(task.id),
    liveExecution:latestExecutionEvent(task.execution_id),
  };
  });

  const events=(db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? ORDER BY id DESC LIMIT 150
  `).all(missionId) as any[]).reverse().map(x=>({
    ...x,payload:parseJson(x.payload_json,{})
  }));

  const workspace=ensureLegacyWorkspaceRecord(missionId);
  return {
    ...mission,
    plan,
    workspace,
    teamMembers:missionRoster(missionId),
    specialists:listMissionSpecialists(missionId),
    tasks,
    artifacts:missionArtifacts(missionId,300),
    messages:listMessages({missionId,limit:200}).reverse(),
    gates:gateRows(missionId),
    approvals:missionApprovals(missionId),
    events
  };
}

export async function planMission(missionId:string) {
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  const count=(db.prepare(`SELECT COUNT(*) c FROM mission_tasks WHERE mission_id=?`).get(missionId) as any)?.c ?? 0;
  if(Number(count)>0)throw new Error('MISSION_ALREADY_PLANNED');
  if(activePlanning.has(missionId))return getMission(missionId);

  const members=missionRoster(mission.id);
  if(members.length===0)throw new Error('MISSION_TEAM_EMPTY');

  activePlanning.add(missionId);
  try{
    const execution:any=await startExecution(
      mission.lead_agent_id,
      planningPrompt(mission,members),
      {
        workspace:cfg.defaultWorkspace,
        context:{
          missionId,
          phase:'planning',
          expectedFinalFormat:'mission_plan_json',
          planningToolStepBudget:6,
          planningFactoryCallBudget:2
        },
        toolGrantsOverride:['agent_factory']
      }
    );
    db.prepare(`
      UPDATE missions
      SET status='planning',planning_execution_id=?,error_text=NULL,updated_at=?
      WHERE id=?
    `).run(execution.id,now(),missionId);
    event(missionId,'mission.planning_started',{executionId:execution.id});
    void finishPlanning(missionId,execution.id);
    return getMission(missionId);
  }catch(err){
    activePlanning.delete(missionId);
    throw err;
  }
}


export async function replanMission(missionId:string){
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  if(['done','cancelled','awaiting_approval'].includes(mission.status)){
    throw new Error(`MISSION_NOT_REPLANNABLE:${mission.status}`);
  }

  const taskStats:any=db.prepare(`
    SELECT
      COUNT(*) total,
      SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done_count
    FROM mission_tasks
    WHERE mission_id=?
  `).get(missionId);

  const integrated:any=db.prepare(`
    SELECT mt.task_key,mtw.status,mtw.commit_hash,mtw.merge_commit
    FROM mission_task_workspaces mtw
    JOIN mission_tasks mt ON mt.id=mtw.task_id
    WHERE mtw.mission_id=?
      AND (
        mtw.commit_hash IS NOT NULL
        OR mtw.merge_commit IS NOT NULL
      )
    ORDER BY mt.sequence_no,mtw.attempt_no
    LIMIT 1
  `).get(missionId);

  if(integrated){
    throw new Error(`MISSION_REPLAN_INTEGRATED_CHANGES_EXIST:${integrated.task_key}`);
  }

  const executionIds=new Set<string>();

  if(mission.planning_execution_id){
    const planning:any=getExecution(String(mission.planning_execution_id));
    if(planning?.status==='running')executionIds.add(String(mission.planning_execution_id));
  }

  const taskExecutions=db.prepare(`
    SELECT execution_id
    FROM mission_tasks
    WHERE mission_id=? AND execution_id IS NOT NULL
      AND status IN ('running','validating')
  `).all(missionId) as any[];
  for(const row of taskExecutions)executionIds.add(String(row.execution_id));

  const gateExecutions=db.prepare(`
    SELECT execution_id
    FROM mission_gates
    WHERE mission_id=? AND execution_id IS NOT NULL AND status='running'
  `).all(missionId) as any[];
  for(const row of gateExecutions)executionIds.add(String(row.execution_id));

  for(const executionId of executionIds){
    try{await cancelExecution(executionId,'cancel')}catch{}
  }

  const missionWs=getMissionWorkspaceRecord(missionId);
  const taskWorkspaces=db.prepare(`
    SELECT mtw.*,mt.task_key
    FROM mission_task_workspaces mtw
    JOIN mission_tasks mt ON mt.id=mtw.task_id
    WHERE mtw.mission_id=?
    ORDER BY mtw.created_at DESC
  `).all(missionId) as any[];

  const cleanupResults:any[]=[];
  if(missionWs?.mode==='isolated'){
    for(const row of taskWorkspaces){
      if(!row.workspace_path || !row.branch_name)continue;

      const cleanup=await removeTaskWorktree({
        baseWorkspace:missionWs.base_workspace,
        workspace:row.workspace_path,
        branch:row.branch_name
      });

      cleanupResults.push({
        taskKey:row.task_key,
        workspace:row.workspace_path,
        branch:row.branch_name,
        ...cleanup
      });

      if(!cleanup.removed){
        db.prepare(`
          UPDATE missions
          SET status='blocked',error_text='MISSION_REPLAN_WORKTREE_CLEANUP_FAILED',updated_at=?
          WHERE id=?
        `).run(now(),missionId);

        event(missionId,'mission.replan_cleanup_failed',{
          taskKey:row.task_key,
          cleanup
        });

        throw new Error(
          `MISSION_REPLAN_WORKTREE_CLEANUP_FAILED:${row.task_key}:${cleanup.error ?? 'unknown'}`
        );
      }
    }
  }

  const oldTaskCount=Number(taskStats?.total ?? 0);
  const t=now();
  let preservedTaskEvents=0;

  db.transaction(()=>{
    preservedTaskEvents=preserveMissionTaskEventsForReplan(db,missionId);
    db.prepare(`DELETE FROM mission_tasks WHERE mission_id=?`).run(missionId);
    db.prepare(`DELETE FROM mission_gates WHERE mission_id=?`).run(missionId);

    db.prepare(`
      UPDATE approvals
      SET status='cancelled',result_json=?,updated_at=?
      WHERE mission_id=? AND status IN ('pending','approved','merge_failed')
    `).run(JSON.stringify({reason:'mission_replan'}),t,missionId);

    db.prepare(`
      UPDATE missions
      SET status='new',plan_json='{}',planning_execution_id=NULL,
          error_text=NULL,ended_at=NULL,updated_at=?
      WHERE id=?
    `).run(t,missionId);
  })();

  activePlanning.delete(missionId);

  event(missionId,'mission.replan_reset',{
    oldTaskCount,
    discardedCompletedNoIntegrationTasks:Number(taskStats?.done_count ?? 0),
    preservedTaskEvents,
    cancelledExecutions:[...executionIds],
    cleanedTaskWorktrees:cleanupResults
  });

  return await planMission(missionId);
}


function exhaustedTargetEvidenceCanReset(missionId:string){
  const priorReset:any=db.prepare(`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND event_type='lead.target_evidence_epoch_reset'
  `).get(missionId);
  if(Number(priorReset?.c ?? 0)>0 || missionHasIntegratedTaskChanges(missionId))return false;

  const failed:any=db.prepare(`
    SELECT * FROM mission_tasks
    WHERE mission_id=? AND status='failed'
    ORDER BY updated_at DESC LIMIT 1
  `).get(missionId);
  if(!failed || resolvedTaskWorkMode(missionRow(missionId),failed)!=='modify')return false;

  const gate=latestTaskGate(failed.id);
  const expected:string[]=Array.isArray(gate?.detail?.expectedTargetPaths)
    ? gate.detail.expectedTargetPaths.map(String)
    : [];
  const changed:string[]=Array.isArray(gate?.detail?.changedPaths)
    ? gate.detail.changedPaths.map(String)
    : [];
  const untracked:string[]=Array.isArray(gate?.detail?.workspaceEvidence?.untracked)
    ? gate.detail.workspaceEvidence.untracked.map(String)
    : [];

  // V0.12.18 used Git's collapsed `?? directory/` porcelain form for
  // changedPaths even though ls-files had the exact artifact. Grant one
  // explicit policy epoch when the archived gate proves that false negative.
  return expected.some(target=>
    untracked.some(path=>pathMatchesExpected(path,target))
  ) && !expected.some(target=>
    changed.some(path=>pathMatchesExpected(path,target))
  );
}

function exhaustedMissionHasUnusedTaskRecoveryBudget(missionId:string){
  const failed:any=db.prepare(`
    SELECT * FROM mission_tasks
    WHERE mission_id=? AND status IN ('failed','blocked')
    ORDER BY updated_at DESC LIMIT 1
  `).get(missionId);
  if(!failed || missionHasIntegratedTaskChanges(missionId))return false;

  const missionAttempts=appliedMissionRecoveryCount(missionId);
  const taskAttempts=appliedTaskRecoveryCount(missionId,String(failed.id));

  return hasUnusedTaskRecoveryBudget({
    missionAttempts,
    taskAttempts,
    maxMissionAttempts:MAX_AUTONOMOUS_RECOVERY_PER_MISSION,
    maxTaskAttempts:MAX_AUTONOMOUS_RECOVERY_PER_TASK,
  });
}

function exhaustedModifyNoopCanReset(missionId:string){
  const actionFirstReset:any=db.prepare(`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=? AND event_type='lead.action_first_epoch_reset'
  `).get(missionId);

  // V0.12.15 gets one dedicated upgrade epoch even when V0.12.14 already
  // granted its mutation-contract epoch. This is not a generic infinite reset.
  if(Number(actionFirstReset?.c ?? 0)>0)return false;

  const failed:any=db.prepare(`
    SELECT * FROM mission_tasks
    WHERE mission_id=? AND status='failed'
    ORDER BY updated_at DESC LIMIT 1
  `).get(missionId);

  if(!failed || resolvedTaskWorkMode(missionRow(missionId),failed)!=='modify')return false;

  const gate=latestTaskGate(failed.id);
  const summary=String(gate?.summary ?? '').toLowerCase();
  const changedPaths=Array.isArray(gate?.detail?.changedPaths)
    ? gate.detail.changedPaths
    : [];
  const error=String(failed.error_text ?? '');

  return (
    error.includes('MUTATION_CONTRACT_UNSATISFIED')
    || changedPaths.length===0
    || summary.includes('did not change the required target')
    || summary.includes('workspace clean')
    || summary.includes('değişiklik yapılmadı')
  );
}

function latestRecoveryBossQuestion(missionId:string){
  const row:any=db.prepare(`
    SELECT * FROM communication_messages
    WHERE mission_id=?
      AND sender_type='lead'
      AND recipient_type='boss'
      AND message_type='question'
    ORDER BY created_at DESC
    LIMIT 1
  `).get(missionId);

  return row
    ? {...row,structured:parseJson(row.structured_json,{})}
    : null;
}

function escalationCanBeAutoRevoked(missionId:string){
  const mission=missionRow(missionId);
  if(!mission)return false;

  const message=latestRecoveryBossQuestion(missionId);
  if(!message)return false;

  const question=String(message.content ?? '').toLocaleLowerCase('tr-TR');
  const structured=message.structured ?? {};

  const referencedTask:any=message.task_id
    ? db.prepare(`SELECT * FROM mission_tasks WHERE id=?`).get(message.task_id)
    : null;

  const latestProblemTask:any=referencedTask ?? db.prepare(`
    SELECT * FROM mission_tasks
    WHERE mission_id=? AND status IN ('failed','blocked','pending')
    ORDER BY updated_at DESC LIMIT 1
  `).get(missionId);

  if(
    /(objective|hedef|görev hedef)/i.test(question)
    || /(acceptance criteria|kabul kriter)/i.test(question)
  ){
    const objectivePresent=Boolean(String(mission.objective ?? '').trim());
    const criteriaPresent=latestProblemTask
      ? parseJson<string[]>(latestProblemTask.acceptance_criteria_json,[]).length>0
      : false;

    if(objectivePresent && criteriaPresent)return true;
  }

  // Legacy V0.12.12 escalations did not carry a typed escalation category.
  // Routine task failures are re-opened exactly so the new policy can solve them.
  if(!String(structured?.escalationType ?? '').trim()){
    const error=String(latestProblemTask?.error_text ?? '');
    if(
      error.includes('ACCEPTANCE_GATE_FAILED')
      || error.includes('ACCEPTANCE_GATE_ERROR')
      || error.includes('EXECUTION_FAILED')
      || error.includes('MODIFY_AGENT_WRITE_TOOLS_MISSING')
      || latestProblemTask?.status==='pending'
    ){
      return true;
    }
  }

  return false;
}


function semanticPlanReplanReasons(missionId:string){
  const mission=missionRow(missionId);
  if(!mission)return [];

  const plan=parseJson<any>(mission.plan_json,{});
  if(!Array.isArray(plan?.tasks) || !plan.tasks.length)return [];

  const quality=validatePlanRoleObligations(
    String(mission.objective ?? ''),
    plan,
    missionRoster(missionId)
  );

  const semanticIds=new Set([
    'validation_role_mutation_task',
    'validation_role_work_mode_mismatch',
    'task_work_mode_mismatch',
    'completed_state_task_instruction',
    'qa_not_after_all_implementation'
  ]);

  return quality.violations.filter((v:any)=>semanticIds.has(String(v?.id)));
}

function missionHasIntegratedTaskChanges(missionId:string){
  const row:any=db.prepare(`
    SELECT 1
    FROM mission_task_workspaces
    WHERE mission_id=?
      AND (commit_hash IS NOT NULL OR merge_commit IS NOT NULL)
    LIMIT 1
  `).get(missionId);
  return Boolean(row);
}

function backfillMissionTaskSemanticContracts(){
  const missions=db.prepare(`
    SELECT id,plan_json FROM missions
    WHERE EXISTS (SELECT 1 FROM mission_tasks mt WHERE mt.mission_id=missions.id)
    ORDER BY created_at
  `).all() as any[];

  let updatedTasks=0;
  const updatedMissions:string[]=[];

  for(const mission of missions){
    const plan=parseJson<any>(mission.plan_json,{});
    const byKey=new Map(
      (Array.isArray(plan?.tasks)?plan.tasks:[])
        .map((x:any)=>[String(x?.key ?? ''),x])
    );
    const rows=db.prepare(`
      SELECT id,task_key,title,instruction,expected_outcome,work_mode,description
      FROM mission_tasks WHERE mission_id=?
      ORDER BY sequence_no
    `).all(mission.id) as any[];

    let missionUpdates=0;
    for(const task of rows){
      const spec:any=byKey.get(String(task.task_key)) ?? {};
      const instruction=String(
        task.instruction
        || spec?.instruction
        || task.description
        || ''
      ).trim();
      const expectedOutcome=String(
        task.expected_outcome
        || spec?.expectedOutcome
        || spec?.expected_outcome
        || task.description
        || ''
      ).trim();
      const rawMode=String(
        task.work_mode
        || spec?.workMode
        || spec?.work_mode
        || ''
      ).trim();
      const workMode=(['modify','validate','analysis','general'].includes(rawMode)
        ? rawMode
        : inferWorkModeFromPlanText({title:task.title,instruction,expectedOutcome})) as PlanTask['workMode'];

      if(
        String(task.instruction ?? '')===instruction
        && String(task.expected_outcome ?? '')===expectedOutcome
        && String(task.work_mode ?? '')===workMode
      )continue;

      db.prepare(`
        UPDATE mission_tasks
        SET instruction=?,expected_outcome=?,work_mode=?,description=?,updated_at=?
        WHERE id=?
      `).run(instruction,expectedOutcome,workMode,instruction,now(),task.id);
      missionUpdates++;
      updatedTasks++;
    }

    if(missionUpdates){
      updatedMissions.push(String(mission.id));
      event(String(mission.id),'mission.task_contract_backfilled',{
        taskCount:missionUpdates,
        source:'plan_json_or_legacy_task_row'
      });
      refreshMissionPlanSnapshot(String(mission.id));
    }
  }

  return {updatedTasks,updatedMissions};
}

function autonomousPlanningReplanAttemptCount(missionId:string){
  const epochId=recoveryEpochStartId(db,missionId);
  const rows=db.prepare(`
    SELECT event_type,payload_json FROM mission_events
    WHERE mission_id=?
      AND event_type IN (
        'mission.autonomous_resume_started',
        'mission.semantic_replan_started'
      )
      AND id>?
    ORDER BY id
  `).all(missionId,epochId) as any[];

  return rows.filter(row=>{
    if(row.event_type==='mission.semantic_replan_started')return true;
    return parseJson<any>(row.payload_json,{}).mode==='replan_after_planning_failure';
  }).length;
}

function autonomousReplanBudgetExhaustionRecorded(missionId:string){
  const epochId=recoveryEpochStartId(db,missionId);
  const row:any=db.prepare(`
    SELECT COUNT(*) c FROM mission_events
    WHERE mission_id=?
      AND event_type='mission.autonomous_replan_budget_exhausted'
      AND id>?
  `).get(missionId,epochId);
  return Number(row?.c ?? 0)>0;
}

export function resumeAutonomousMissions(){
  const contractBackfill=backfillMissionTaskSemanticContracts();
  const rows=db.prepare(`
    SELECT id,status,error_text FROM missions
    WHERE status IN ('new','ready','running','blocked','validating','reviewing')
    ORDER BY created_at
  `).all() as any[];

  const resumed:string[]=[];
  const skipped:string[]=[];

  for(const row of rows){
    const missionId=String(row.id);
    const status=String(row.status);
    const error=String(row.error_text ?? '');
    let blockedTaskRecoveryAllowed=false;

    const semanticReplanReasons=semanticPlanReplanReasons(missionId);
    if(
      semanticReplanReasons.length
      && !missionHasIntegratedTaskChanges(missionId)
    ){
      const semanticReplanAttempts=autonomousPlanningReplanAttemptCount(missionId);
      if(semanticReplanAttempts>=MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS){
        if(!autonomousReplanBudgetExhaustionRecorded(missionId)){
          event(missionId,'mission.autonomous_replan_budget_exhausted',{
            previousStatus:status,
            previousError:error || null,
            mode:'semantic_replan',
            attempts:semanticReplanAttempts,
            budget:MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
          });
        }
        skipped.push(missionId);
        continue;
      }

      event(missionId,'mission.semantic_replan_started',{
        reason:'V0.12.16 task-mode/role contract detected a legacy semantically invalid DAG.',
        violations:semanticReplanReasons,
        attempt:semanticReplanAttempts+1,
        budget:MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
      });

      queueMicrotask(()=>{
        if(!missionRow(missionId))return;
        void replanMission(missionId).catch((err:any)=>{
          if(!missionRow(missionId))return;
          db.prepare(`
            UPDATE missions
            SET status='blocked',error_text=?,updated_at=?
            WHERE id=?
          `).run(
            `AUTO_SEMANTIC_REPLAN_FAILED:${String(err?.message ?? err)}`,
            now(),
            missionId
          );
          event(missionId,'mission.semantic_replan_failed',{
            error:String(err?.message ?? err)
          });
        });
      });

      resumed.push(missionId);
      continue;
    }

    if(error.startsWith('AUTONOMOUS_RECOVERY_EXHAUSTED')){
      const targetEvidenceReset=exhaustedTargetEvidenceCanReset(missionId);
      const actionFirstReset=!targetEvidenceReset && exhaustedModifyNoopCanReset(missionId);
      const unusedRecoveryBudget=
        !targetEvidenceReset
        && !actionFirstReset
        && exhaustedMissionHasUnusedTaskRecoveryBudget(missionId);
      if(targetEvidenceReset || actionFirstReset || unusedRecoveryBudget){
        blockedTaskRecoveryAllowed=true;
        const resetReason=targetEvidenceReset
          ? 'V0.12.19 exact untracked-file evidence repair grants one bounded recovery epoch for a legacy false-negative target gate.'
          : actionFirstReset
            ? 'V0.12.15 action-first executor prompt starts a fresh bounded recovery epoch.'
            : 'A failed recovery decision left bounded task recovery budget unused; resume without resetting the epoch.';

        if(targetEvidenceReset){
          event(missionId,'lead.target_evidence_epoch_reset',{reason:resetReason});
        }else if(actionFirstReset){
          event(missionId,'lead.action_first_epoch_reset',{
            reason:'V0.12.15 action-first executor prompt grants one fresh bounded recovery epoch for a legacy repeated no-op modify task.'
          });
        }
        if(targetEvidenceReset || actionFirstReset){
          event(missionId,'lead.recovery_budget_epoch_reset',{
            reason:resetReason
          });
        }

        db.prepare(`
          UPDATE missions
          SET status='running',error_text=NULL,updated_at=?
          WHERE id=?
        `).run(now(),missionId);

        event(missionId,'lead.recovery_exhaustion_revoked',{
          previousError:error,
          reason:targetEvidenceReset
            ? 'legacy_untracked_target_evidence_upgrade'
            : actionFirstReset
              ? 'legacy_modify_noop_upgrade'
              : 'unused_bounded_recovery_budget'
        });
      }else{
        skipped.push(missionId);
        continue;
      }
    }

    if(error.startsWith('LEAD_ESCALATION_REQUIRED')){
      if(escalationCanBeAutoRevoked(missionId)){
        blockedTaskRecoveryAllowed=true;
        db.prepare(`
          UPDATE missions
          SET status='running',error_text=NULL,updated_at=?
          WHERE id=?
        `).run(now(),missionId);

        const message=latestRecoveryBossQuestion(missionId);
        if(message?.id){
          db.prepare(`
            UPDATE communication_messages
            SET status='cancelled'
            WHERE id=?
          `).run(message.id);
        }

        event(missionId,'lead.recovery_escalation_revoked',{
          reason:'Previously escalated routine failure is recoverable under V0.12.13 policy.',
          messageId:message?.id ?? null
        });
      }else{
        skipped.push(missionId);
        continue;
      }
    }

    if(status==='blocked' && (error==='QA_GATE_FAILED' || error==='REVIEW_GATE_FAILED')){
      if(finalGateRecoveryBudgetExhausted(missionId)){
        const gateType=error==='QA_GATE_FAILED'?'qa':'reviewer';
        const terminalError=`FINAL_GATE_RECOVERY_BUDGET_EXHAUSTED:${gateType}`;
        db.prepare(`
          UPDATE missions SET error_text=?,updated_at=? WHERE id=?
        `).run(terminalError,now(),missionId);
        event(missionId,'lead.recovery_budget_exhausted',{
          gateType,
          missionAttempts:appliedMissionRecoveryCount(missionId),
          finalAttempts:appliedFinalGateRecoveryCount(missionId),
          source:'startup_recovery_guard',
        });
        skipped.push(missionId);
        continue;
      }
      blockedTaskRecoveryAllowed=true;
    }

    if(status==='blocked' && error==='NO_READY_TASK'){
      blockedTaskRecoveryAllowed=exhaustedMissionHasUnusedTaskRecoveryBudget(missionId);
    }

    const cancelled:any=db.prepare(`
      SELECT COUNT(*) c FROM mission_tasks WHERE mission_id=? AND status='cancelled'
    `).get(missionId);
    if(Number(cancelled?.c ?? 0)>0){
      skipped.push(missionId);
      continue;
    }

    const taskCount:any=db.prepare(`
      SELECT COUNT(*) c FROM mission_tasks WHERE mission_id=?
    `).get(missionId);

    const taskTotal=Number(taskCount?.c ?? 0);
    const autonomousReplanAttempts=autonomousPlanningReplanAttemptCount(missionId);
    const startupDecision=decideStartupRecovery({
      status,
      taskCount:taskTotal,
      errorText:error,
      autonomousReplanAttempts,
      maxAutonomousReplanAttempts:MAX_AUTONOMOUS_ZERO_TASK_REPLAN_ATTEMPTS,
      blockedTaskRecoveryAllowed,
    });

    if(startupDecision.reason==='planning_recovery_budget_exhausted'){
      if(!autonomousReplanBudgetExhaustionRecorded(missionId)){
        event(missionId,'mission.autonomous_replan_budget_exhausted',{
          previousStatus:status,
          previousError:error,
          attempts:autonomousReplanAttempts,
          budget:startupDecision.autonomousReplanBudget,
        });
      }
      skipped.push(missionId);
      continue;
    }

    if(startupDecision.action==='plan' || startupDecision.action==='replan'){
      queueMicrotask(()=>{
        if(!missionRow(missionId))return;

        void planMission(missionId).catch((err:any)=>{
          if(!missionRow(missionId))return;

          db.prepare(`
            UPDATE missions
            SET status='blocked',error_text=?,updated_at=?
            WHERE id=?
          `).run(
            `AUTO_PLAN_FAILED:${String(err?.message ?? err)}`,
            now(),
            missionId
          );

          if(missionRow(missionId)){
            event(missionId,'mission.auto_plan_failed',{
              error:String(err?.message ?? err)
            });
          }
        });
      });

      event(missionId,'mission.autonomous_resume_started',{
        previousStatus:status,
        previousError:error || null,
        mode:startupDecision.mode,
        attempt:startupDecision.autonomousReplanAttempt,
        budget:startupDecision.autonomousReplanBudget,
      });
      resumed.push(missionId);
      continue;
    }

    if(startupDecision.action==='run'){
      db.prepare(`
        UPDATE missions SET status='running',error_text=NULL,updated_at=? WHERE id=?
      `).run(now(),missionId);
      event(missionId,'mission.autonomous_resume_started',{previousStatus:status,mode:'run'});
      void missionLoop(missionId);
      resumed.push(missionId);
    }else{
      skipped.push(missionId);
    }
  }

  return {resumed,skipped,contractBackfill};
}

export function runMission(missionId:string) {
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');
  if(!['ready','running','blocked','paused','validating','reviewing'].includes(mission.status)){
    throw new Error(`MISSION_NOT_RUNNABLE:${mission.status}`);
  }

  const failed=db.prepare(`SELECT COUNT(*) c FROM mission_tasks WHERE mission_id=? AND status='failed'`).get(missionId) as any;
  if(Number(failed?.c ?? 0)>0)throw new Error('MISSION_HAS_FAILED_TASKS_RETRY_FIRST');

  db.prepare(`
    UPDATE missions
    SET status='running',started_at=COALESCE(started_at,?),ended_at=NULL,error_text=NULL,updated_at=?
    WHERE id=?
  `).run(now(),now(),missionId);
  event(missionId,'mission.running',{});
  void missionLoop(missionId);
  return getMission(missionId);
}

export function pauseMission(missionId:string) {
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');
  db.prepare(`UPDATE missions SET status='paused',updated_at=? WHERE id=?`).run(now(),missionId);
  event(missionId,'mission.paused',{});
  return getMission(missionId);
}

export function retryMissionTask(missionId:string,taskId:string) {
  const task:any=db.prepare(`
    SELECT * FROM mission_tasks WHERE id=? AND mission_id=?
  `).get(taskId,missionId);
  if(!task)throw new Error('MISSION_TASK_NOT_FOUND');
  if(!['failed','blocked'].includes(task.status))throw new Error('MISSION_TASK_NOT_RETRYABLE');

  db.prepare(`
    UPDATE mission_tasks
    SET status='pending',execution_id=NULL,result_text=NULL,error_text=NULL,
        started_at=NULL,ended_at=NULL,updated_at=?
    WHERE id=?
  `).run(now(),taskId);
  db.prepare(`
    UPDATE missions SET status='ready',error_text=NULL,ended_at=NULL,updated_at=? WHERE id=?
  `).run(now(),missionId);
  event(missionId,'task.retry_requested',{attempt:task.attempt_no+1},taskId);
  return getMission(missionId);
}

export function reassignMissionTask(missionId:string,taskId:string,agentId:string) {
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  const task:any=db.prepare(`
    SELECT * FROM mission_tasks WHERE id=? AND mission_id=?
  `).get(taskId,missionId);
  if(!task)throw new Error('MISSION_TASK_NOT_FOUND');
  if(task.status==='running' || task.status==='validating')throw new Error('MISSION_TASK_RUNNING');
  if(task.status==='done')throw new Error('MISSION_TASK_ALREADY_DONE');

  const permanent:any=db.prepare(`
    SELECT a.id FROM team_members tm
    JOIN agents a ON a.id=tm.agent_id
    WHERE tm.team_id=? AND tm.agent_id=? AND a.enabled=1
  `).get(mission.team_id,agentId);
  if(!permanent && !missionAgentIsActive(missionId,agentId)){
    throw new Error('MISSION_AGENT_NOT_ACTIVE_ROSTER_MEMBER');
  }

  db.prepare(`
    UPDATE mission_tasks
    SET assigned_agent_id=?,status='pending',execution_id=NULL,result_text=NULL,error_text=NULL,
        started_at=NULL,ended_at=NULL,updated_at=?
    WHERE id=?
  `).run(agentId,now(),taskId);

  db.prepare(`
    UPDATE missions SET status='ready',error_text=NULL,ended_at=NULL,updated_at=? WHERE id=?
  `).run(now(),missionId);

  event(missionId,'task.reassigned',{
    fromAgentId:task.assigned_agent_id,
    toAgentId:agentId
  },taskId);

  return getMission(missionId);
}


export async function cancelMissionTask(
  missionId:string,
  taskId:string,
  mode:'cancel'|'kill'='cancel'
){
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  const task:any=db.prepare(`
    SELECT * FROM mission_tasks WHERE id=? AND mission_id=?
  `).get(taskId,missionId);
  if(!task)throw new Error('MISSION_TASK_NOT_FOUND');
  if(task.status==='done')throw new Error('MISSION_TASK_ALREADY_DONE');
  if(task.status==='cancelled')return getMission(missionId);

  if(task.execution_id && ['running','validating'].includes(task.status)){
    await cancelExecution(task.execution_id,mode);
  }

  db.prepare(`
    UPDATE mission_tasks
    SET status='cancelled',error_text=?,ended_at=COALESCE(ended_at,?),updated_at=?
    WHERE id=?
  `).run(mode==='kill'?'KILLED_BY_BOSS':'CANCELLED_BY_BOSS',now(),now(),taskId);

  db.prepare(`
    UPDATE missions SET status='blocked',error_text=?,updated_at=? WHERE id=?
  `).run(`TASK_CANCELLED:${task.task_key}`,now(),missionId);

  event(missionId,mode==='kill'?'task.killed':'task.cancelled',{mode},taskId);
  return getMission(missionId);
}

export async function stopMission(
  missionId:string,
  mode:'cancel'|'kill'='cancel'
){
  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');
  if(mission.status==='done' || mission.status==='cancelled')return getMission(missionId);

  const executionIds=new Set<string>();
  if(mission.planning_execution_id)executionIds.add(String(mission.planning_execution_id));

  const runningTasks=db.prepare(`
    SELECT execution_id FROM mission_tasks
    WHERE mission_id=? AND status IN ('running','validating') AND execution_id IS NOT NULL
  `).all(missionId) as any[];
  for(const row of runningTasks)executionIds.add(String(row.execution_id));

  const runningGates=db.prepare(`
    SELECT execution_id FROM mission_gates
    WHERE mission_id=? AND status='running' AND execution_id IS NOT NULL
  `).all(missionId) as any[];
  for(const row of runningGates)executionIds.add(String(row.execution_id));

  for(const executionId of executionIds){
    try{await cancelExecution(executionId,mode)}catch{}
  }

  const t=now();
  db.transaction(()=>{
    db.prepare(`
      UPDATE mission_tasks
      SET status='cancelled',error_text=?,ended_at=COALESCE(ended_at,?),updated_at=?
      WHERE mission_id=? AND status NOT IN ('done','cancelled')
    `).run(mode==='kill'?'MISSION_KILLED_BY_BOSS':'MISSION_CANCELLED_BY_BOSS',t,t,missionId);

    db.prepare(`
      UPDATE mission_gates
      SET status='cancelled',summary=?,updated_at=?
      WHERE mission_id=? AND status='running'
    `).run(mode==='kill'?'Mission killed by Boss.':'Mission cancelled by Boss.',t,missionId);

    db.prepare(`
      UPDATE missions
      SET status='cancelled',error_text=?,ended_at=?,updated_at=?
      WHERE id=?
    `).run(mode==='kill'?'KILLED_BY_BOSS':'CANCELLED_BY_BOSS',t,t,missionId);
  })();

  cancelMissionApprovals(missionId,mode);
  const archived=archiveMissionSpecialists(missionId,mode==='kill'?'mission_killed':'mission_cancelled');
  event(missionId,mode==='kill'?'mission.killed':'mission.cancelled',{
    mode,executionIds:[...executionIds],archivedSpecialists:archived.length
  });
  return getMission(missionId);
}

export function missionEvents(missionId:string,limit=200) {
  return (db.prepare(`
    SELECT * FROM mission_events WHERE mission_id=? ORDER BY id DESC LIMIT ?
  `).all(missionId,limit) as any[]).reverse().map(x=>({
    ...x,payload:parseJson(x.payload_json,{})
  }));
}
