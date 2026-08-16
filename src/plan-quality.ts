import {
  executableTaskInstruction,
  isCompletedStateTaskInstruction,
  taskHasStrongMutationIntent,
} from './task-contracts.js';

export type PlanQualityViolation={
  id:string;
  taskKey?:string;
  reason:string;
  detail?:any;
};

function n(value:any){
  return String(value ?? '')
    .toLocaleLowerCase('tr-TR')
    .replace(/\s+/g,' ')
    .trim();
}

function hasAny(text:string,patterns:RegExp[]){
  return patterns.some(p=>p.test(text));
}

function roleAgent(roster:any[],role:'qa'|'reviewer'){
  const patterns=role==='qa'
    ? [/\bqa\b/,/quality assurance/,/test kalite/]
    : [/\breviewer\b/,/\bcode review/,/\binceleme\b/];

  return roster.find(a=>{
    const value=n(`${a?.name} ${a?.slug} ${a?.role_label}`);
    return hasAny(value,patterns);
  }) ?? null;
}

function requiresQa(objective:string){
  const text=n(objective);
  return hasAny(text,[
    /\bqa\b.{0,80}\b(doğrula|doğrulasın|kontrol|incele|test)/,
    /\bqa\b.{0,80}\b(validate|verify|review|test)/,
  ]);
}

function requiresReviewer(objective:string){
  const text=n(objective);
  return hasAny(text,[
    /\breviewer\b.{0,100}\b(son|nihai|final|incele|incelemeyi|review)/,
    /\bfinal\s+review\b.{0,60}\breviewer\b/,
  ]);
}

function requiresSpecialistReadmeAssignment(objective:string){
  const text=n(objective);
  return (
    text.includes('readme')
    && (
      /bu\s+uzmana\s+ata/.test(text)
      || /uzmana\s+ata/.test(text)
      || /assign.{0,60}(specialist|expert)/.test(text)
    )
  );
}

function taskIntentText(task:any){
  // Work-mode/mutation intent belongs to the executable instruction contract.
  // expectedOutcome is post-state evidence and must never turn a read-only QA/Reviewer
  // instruction into a mutation task merely because it mentions files being created/updated.
  return n(executableTaskInstruction(task));
}

function strongMutationTask(task:any){
  return taskHasStrongMutationIntent(task);
}

function declaredWorkMode(task:any){
  const mode=String(task?.workMode ?? task?.work_mode ?? '').trim();
  return ['modify','validate','analysis','general'].includes(mode)
    ? mode
    : '';
}

function readmeModifyTask(task:any){
  const text=taskIntentText(task);
  return text.includes('readme') && strongMutationTask(task);
}


function objectiveExplicitlyRequiresApproval(objective:string){
  const text=n(objective);
  return hasAny(text,[
    /\bonay\b/,/\bonayla/,/\bonaylasın/,/\bonaylansın/,
    /\bapproval\b/,/\bapprove\b/,/\bsign[- ]?off\b/
  ]);
}

function inventedApprovalCriterion(objective:string,criterion:string){
  if(objectiveExplicitlyRequiresApproval(objective))return false;
  const text=n(criterion);

  // JavaScript \b is ASCII-oriented. Turkish dotless "ı" is not treated as
  // a word character, so patterns such as /\bonaylandı\b/ can incorrectly
  // miss a real "onaylandı" criterion. Use normalized substring checks for
  // Turkish phrases and regex only for ASCII English phrases.
  const turkishApprovalPhrases=[
    'onaylandı',
    'onaylanmış',
    'onay verildi',
    'tarafından onay',
  ];

  if(turkishApprovalPhrases.some(phrase=>text.includes(phrase))){
    return true;
  }

  return hasAny(text,[
    /\bapproved\b/,
    /\bapproval received\b/,
    /\bsigned off\b/,
    /\bsign[- ]?off completed\b/
  ]);
}

function taskMap(plan:any){
  return new Map(
    (Array.isArray(plan?.tasks)?plan.tasks:[])
      .map((t:any)=>[String(t?.key ?? ''),t])
  );
}

function ancestors(plan:any,taskKey:string){
  const map=taskMap(plan);
  const seen=new Set<string>();
  const walk=(key:string)=>{
    const task:any=map.get(key);
    if(!task)return;
    for(const dep of Array.isArray(task?.dependsOn)?task.dependsOn:[]){
      const d=String(dep);
      if(seen.has(d))continue;
      seen.add(d);
      walk(d);
    }
  };
  walk(taskKey);
  return seen;
}


function uniqueTaskKey(tasks:any[],base:string){
  const used=new Set(tasks.map((t:any)=>String(t?.key ?? '')));
  if(!used.has(base))return base;
  let i=2;
  while(used.has(`${base}_${i}`))i++;
  return `${base}_${i}`;
}

function hasWriteCapability(agent:any){
  const tools=Array.isArray(agent?.tools)?agent.tools.map((x:any)=>String(x)):[];
  return tools.includes('write_file') || tools.includes('replace_in_file');
}

function safeAddDependency(plan:any,task:any,depKey:string){
  const taskKey=String(task?.key ?? '');
  if(!taskKey || !depKey || taskKey===depKey)return false;
  const deps=Array.isArray(task.dependsOn)?task.dependsOn.map((x:any)=>String(x)):[];
  if(deps.includes(depKey))return false;
  if(ancestors(plan,depKey).has(taskKey))return false;
  task.dependsOn=[...deps,depKey];
  return true;
}

export function canonicalizePlanRoleContracts(objective:string,plan:any,roster:any[]){
  const out=JSON.parse(JSON.stringify(plan ?? {}));
  const tasks=Array.isArray(out.tasks)?out.tasks:[];
  const changes:any[]=[];
  const qa=roleAgent(roster,'qa');
  const reviewer=roleAgent(roster,'reviewer');
  const qaId=qa?String(qa.id):'';
  const reviewerId=reviewer?String(reviewer.id):'';

  const implementationCandidates=roster.filter((a:any)=>{
    const id=String(a?.id ?? '');
    return id && id!==qaId && id!==reviewerId && hasWriteCapability(a);
  });
  implementationCandidates.sort((a:any,b:any)=>{
    const ta=Boolean(a?.temporary)?0:1;
    const tb=Boolean(b?.temporary)?0:1;
    if(ta!==tb)return ta-tb;
    return Number(a?.priority ?? 999)-Number(b?.priority ?? 999);
  });

  const rosterIds=new Set(roster.map(a=>String(a?.id ?? '')));

  for(const task of tasks){
    const assignedId=String(task?.assignedAgentId ?? '');
    
    if(!rosterIds.has(assignedId)){
      const replacement=implementationCandidates[0];
      if(replacement){
        const previous=assignedId;
        task.assignedAgentId=String(replacement.id);
        task.workMode='modify';
        changes.push({type:'invalid_agent_reassigned',taskKey:String(task?.key ?? ''),fromAgentId:previous,toAgentId:String(replacement.id)});
      }
      continue;
    }

    const validationRole=assignedId===qaId || assignedId===reviewerId;
    const mutation=strongMutationTask(task);
    const mode=declaredWorkMode(task);

    if(validationRole && mutation){
      const replacement=implementationCandidates[0];
      if(replacement){
        const previous=assignedId;
        task.assignedAgentId=String(replacement.id);
        task.workMode='modify';
        changes.push({type:'validation_mutation_reassigned',taskKey:String(task?.key ?? ''),fromAgentId:previous,toAgentId:String(replacement.id)});
      }
      continue;
    }

    if(validationRole && !mutation && mode!=='validate'){
      task.workMode='validate';
      changes.push({type:'validation_mode_canonicalized',taskKey:String(task?.key ?? ''),from:mode||null,to:'validate'});
      continue;
    }

    if(!validationRole && mutation && mode!=='modify'){
      task.workMode='modify';
      changes.push({type:'mutation_mode_canonicalized',taskKey:String(task?.key ?? ''),from:mode||null,to:'modify'});
    }
  }

  const mutationKeys=()=>tasks
    .filter((t:any)=>{
      const id=String(t?.assignedAgentId ?? '');
      return id!==qaId && id!==reviewerId && (declaredWorkMode(t)==='modify' || strongMutationTask(t));
    })
    .map((t:any)=>String(t.key));

  let qaTasks=qaId?tasks.filter((t:any)=>String(t?.assignedAgentId ?? '')===qaId):[];
  if(requiresQa(objective) && qaId && !qaTasks.length){
    const key=uniqueTaskKey(tasks,'AUTO_QA');
    const task={
      key,
      title:'QA validation',
      instruction:'Run the actual repository commands and automated tests, then verify the implemented change against the Boss requirements without modifying repository files.',
      expectedOutcome:'The implementation is independently verified with command/test evidence.',
      workMode:'validate',
      assignedAgentId:qaId,
      acceptanceCriteria:[
        'The actual automated tests are executed and their result is recorded.',
        'The implemented behavior is checked against the Boss requirements using repository evidence.'
      ],
      dependsOn:[...mutationKeys()],
      priority:900
    };
    tasks.push(task);
    qaTasks=[task];
    changes.push({type:'qa_task_synthesized',taskKey:key,agentId:qaId});
  }

  if(requiresQa(objective) && qaTasks.length){
    for(const qaTask of qaTasks){
      if(strongMutationTask(qaTask))continue;
      for(const key of mutationKeys()){
        if(safeAddDependency(out,qaTask,key)){
          changes.push({type:'qa_dependency_added',taskKey:String(qaTask.key),dependsOn:key});
        }
      }
    }
  }

  let reviewerTasks=reviewerId?tasks.filter((t:any)=>String(t?.assignedAgentId ?? '')===reviewerId):[];
  if(requiresReviewer(objective) && reviewerId && !reviewerTasks.length){
    const key=uniqueTaskKey(tasks,'AUTO_REVIEW');
    const qaKeys=qaTasks.map((t:any)=>String(t.key));
    const task={
      key,
      title:'Final Reviewer check',
      instruction:'Review the final implementation, tests, error handling, and compliance with the Boss requirements without modifying repository files.',
      expectedOutcome:'A final independent code-quality and requirement-compliance review is completed.',
      workMode:'validate',
      assignedAgentId:reviewerId,
      acceptanceCriteria:[
        'Code quality and error handling are reviewed against the implemented diff.',
        'Boss requirements are checked against repository and test evidence.'
      ],
      dependsOn:qaKeys.length?qaKeys:[...mutationKeys()],
      priority:950
    };
    tasks.push(task);
    reviewerTasks=[task];
    changes.push({type:'reviewer_task_synthesized',taskKey:key,agentId:reviewerId});
  }

  if(requiresReviewer(objective) && reviewerTasks.length && qaTasks.length){
    for(const reviewTask of reviewerTasks){
      if(strongMutationTask(reviewTask))continue;
      for(const qaTask of qaTasks){
        const key=String(qaTask.key);
        if(safeAddDependency(out,reviewTask,key)){
          changes.push({type:'reviewer_dependency_added',taskKey:String(reviewTask.key),dependsOn:key});
        }
      }
    }
  }

  return {plan:out,changes};
}


export function documentationSpecialistContract(
  objective:string,
  roster:any[]
){
  const text=n(objective);

  const explicitFactoryRequest=
    text.includes('agentfactory')
    && (
      /teknik\s+dokümantasyon\s+uzmanı/.test(text)
      || /technical\s+documentation\s+specialist/.test(text)
    );

  if(!explicitFactoryRequest){
    return {
      required:false,
      needsCreation:false,
      satisfiedBy:null,
      name:null,
      roleName:null,
      reason:null
    };
  }

  const objectiveSource=String(objective ?? '');
  const quotedCandidates=[
    ...objectiveSource.matchAll(/["“]([^"”]+)["”]/g)
  ].map(m=>String(m[1] ?? '').trim());

  const quotedName=
    quotedCandidates.find(candidate=>
      /dokümantasyon|documentation/i.test(candidate)
    )
    || (
      /teknik\s+dokümantasyon\s+uzmanı/i.test(objectiveSource)
        ? 'Teknik Dokümantasyon Uzmanı'
        : 'Technical Documentation Specialist'
    );

  const permanentDoc=roster.find(a=>{
    if(Boolean(a?.temporary))return false;
    const value=n(`${a?.name} ${a?.slug} ${a?.role_label} ${a?.description}`);
    return (
      /dokümantasyon/.test(value)
      || /documentation/.test(value)
      || /technical\s+writer/.test(value)
      || /docs?\s+specialist/.test(value)
    );
  }) ?? null;

  if(permanentDoc){
    return {
      required:true,
      needsCreation:false,
      satisfiedBy:String(permanentDoc.id),
      name:quotedName,
      roleName:'Technical Documentation Specialist',
      reason:'A permanent documentation-specific agent already exists.'
    };
  }

  const temporaryDoc=roster.find(a=>{
    if(!Boolean(a?.temporary))return false;
    const value=n(`${a?.name} ${a?.slug} ${a?.role_label} ${a?.description}`);
    return (
      /dokümantasyon/.test(value)
      || /documentation/.test(value)
      || /technical\s+writer/.test(value)
      || /docs?\s+specialist/.test(value)
    );
  }) ?? null;

  if(temporaryDoc){
    return {
      required:true,
      needsCreation:false,
      satisfiedBy:String(temporaryDoc.id),
      name:quotedName,
      roleName:'Technical Documentation Specialist',
      reason:'An active temporary documentation specialist already exists.'
    };
  }

  return {
    required:true,
    needsCreation:true,
    satisfiedBy:null,
    name:quotedName,
    roleName:'Technical Documentation Specialist',
    reason:'Boss explicitly requested AgentFactory when no permanent documentation specialist exists, and no suitable permanent/temporary documentation specialist is present.'
  };
}

export function validatePlanRoleObligations(
  objective:string,
  plan:any,
  roster:any[]
){
  const violations:PlanQualityViolation[]=[];
  const tasks=Array.isArray(plan?.tasks)?plan.tasks:[];
  const qa=roleAgent(roster,'qa');
  const reviewer=roleAgent(roster,'reviewer');
  const specialistContract=documentationSpecialistContract(objective,roster);

  if(specialistContract.required && specialistContract.needsCreation){
    violations.push({
      id:'required_documentation_specialist_missing',
      reason:specialistContract.reason ?? 'Required documentation specialist is missing.',
      detail:specialistContract
    });
  }

  const qaTasks=qa
    ? tasks.filter((t:any)=>String(t?.assignedAgentId ?? '')===String(qa.id))
    : [];
  const reviewerTasks=reviewer
    ? tasks.filter((t:any)=>String(t?.assignedAgentId ?? '')===String(reviewer.id))
    : [];

  if(requiresQa(objective)){
    if(!qa){
      violations.push({
        id:'required_qa_agent_missing',
        reason:'Boss explicitly requires QA validation but no QA agent exists in the mission roster.'
      });
    }else if(!qaTasks.length){
      violations.push({
        id:'required_qa_task_missing',
        reason:`Boss explicitly requires QA validation, but no task is assigned to QA agent ${qa.id}.`,
        detail:{qaAgentId:qa.id}
      });
    }
  }

  if(requiresReviewer(objective)){
    if(!reviewer){
      violations.push({
        id:'required_reviewer_agent_missing',
        reason:'Boss explicitly requires final Reviewer review but no Reviewer agent exists in the mission roster.'
      });
    }else if(!reviewerTasks.length){
      violations.push({
        id:'required_reviewer_task_missing',
        reason:`Boss explicitly requires Reviewer final review, but no task is assigned to Reviewer agent ${reviewer.id}.`,
        detail:{reviewerAgentId:reviewer.id}
      });
    }
  }

  const mutationTasks=tasks.filter((t:any)=>
    declaredWorkMode(t)==='modify' || strongMutationTask(t)
  );
  const modifyTasks=tasks.filter(readmeModifyTask);

  if(requiresQa(objective) && qaTasks.length && mutationTasks.length){
    const implementationKeys=new Set<string>(
      mutationTasks
        .filter((t:any)=>
          !qa || String(t?.assignedAgentId ?? '')!==String(qa.id)
        )
        .filter((t:any)=>
          !reviewer || String(t?.assignedAgentId ?? '')!==String(reviewer.id)
        )
        .map((t:any)=>String(t.key))
    );

    if(implementationKeys.size){
      const qaCoversAllImplementation=qaTasks.some((t:any)=>{
        const deps=ancestors(plan,String(t.key));
        return [...implementationKeys].every(k=>deps.has(k));
      });
      if(!qaCoversAllImplementation){
        violations.push({
          id:'qa_not_after_all_implementation',
          reason:'QA validation must depend on all repository-modifying implementation tasks so it validates the complete implemented change set.',
          detail:{implementationTaskKeys:[...implementationKeys]}
        });
      }
    }
  }

  if(requiresReviewer(objective) && reviewerTasks.length && qaTasks.length){
    const qaKeys=new Set<string>(qaTasks.map((t:any)=>String(t.key)));
    const reviewerAfterQa=reviewerTasks.some((t:any)=>{
      const deps=ancestors(plan,String(t.key));
      return [...qaKeys].some(k=>deps.has(k));
    });
    if(!reviewerAfterQa){
      violations.push({
        id:'reviewer_not_after_qa',
        reason:'Final Reviewer task must run after QA validation.'
      });
    }
  }

  for(const task of tasks){
    const executableInstruction=String(
      task?.instruction
      ?? task?.description
      ?? ''
    ).trim();

    if(isCompletedStateTaskInstruction(executableInstruction)){
      violations.push({
        id:'completed_state_task_instruction',
        taskKey:String(task?.key ?? '?'),
        reason:'Task instruction is phrased as an already-completed state instead of an action command. Lead must rewrite it as an imperative executable instruction.',
        detail:{instruction:executableInstruction}
      });
    }

    const mode=declaredWorkMode(task);
    const mutationIntent=strongMutationTask(task);

    if(mutationIntent && mode && mode!=='modify'){
      violations.push({
        id:'task_work_mode_mismatch',
        taskKey:String(task?.key ?? '?'),
        reason:'Task instruction requires a real repository mutation, so workMode must be modify.',
        detail:{workMode:mode,instruction:executableInstruction}
      });
    }

    const assignedId=String(task?.assignedAgentId ?? '');
    const assignedToQa=Boolean(qa && assignedId===String(qa.id));
    const assignedToReviewer=Boolean(reviewer && assignedId===String(reviewer.id));

    if((assignedToQa || assignedToReviewer) && (mutationIntent || mode==='modify')){
      violations.push({
        id:'validation_role_mutation_task',
        taskKey:String(task?.key ?? '?'),
        reason:`${assignedToQa?'QA':'Reviewer'} is a read-only validation role and must not author or modify repository artifacts. Create a separate implementation task, then make validation depend on it.`,
        detail:{
          role:assignedToQa?'qa':'reviewer',
          workMode:mode || null,
          instruction:executableInstruction
        }
      });
    }

    if((assignedToQa || assignedToReviewer) && mode && mode!=='validate'){
      violations.push({
        id:'validation_role_work_mode_mismatch',
        taskKey:String(task?.key ?? '?'),
        reason:`${assignedToQa?'QA':'Reviewer'} tasks must use workMode=validate.`,
        detail:{workMode:mode}
      });
    }

    const criteria=Array.isArray(task?.acceptanceCriteria)
      ? task.acceptanceCriteria
      : [];

    for(const criterion of criteria){
      if(inventedApprovalCriterion(objective,String(criterion))){
        violations.push({
          id:'invented_approval_acceptance_criterion',
          taskKey:String(task?.key ?? '?'),
          reason:'Plan invented an approval/sign-off acceptance criterion that the Boss objective did not request. Acceptance criteria must be task-local and directly verifiable.',
          detail:{criterion:String(criterion)}
        });
      }
    }
  }

  if(requiresSpecialistReadmeAssignment(objective)){
    const specialists=roster.filter(a=>Boolean(a?.temporary));
    if(specialists.length){
      const specialistIds=new Set<string>(specialists.map(a=>String(a.id)));
      const specialistOwnsReadme=modifyTasks.some(
        (t:any)=>specialistIds.has(String(t?.assignedAgentId ?? ''))
      );
      if(!specialistOwnsReadme){
        violations.push({
          id:'readme_not_assigned_to_specialist',
          reason:'Boss requested the README editing task to be assigned to the temporary specialist, but no README modification task is assigned to a temporary specialist.',
          detail:{specialistIds:[...specialistIds]}
        });
      }
    }
  }

  return {
    ok:violations.length===0,
    obligations:{
      qaRequired:requiresQa(objective),
      reviewerRequired:requiresReviewer(objective),
      specialistReadmeAssignmentRequired:requiresSpecialistReadmeAssignment(objective),
      qaAgentId:qa?.id ?? null,
      reviewerAgentId:reviewer?.id ?? null,
      documentationSpecialist:specialistContract,
      mutationTaskKeys:mutationTasks.map((t:any)=>String(t.key))
    },
    violations
  };
}
