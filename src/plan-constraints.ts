export type BossConstraint = {
  id:string;
  type:'forbid_new_dependency';
  source:string;
  normalized:string;
};

export type PlanConstraintViolation = {
  constraintId:string;
  constraintSource:string;
  taskKey:string;
  field:string;
  text:string;
  reason:string;
};

function normalizeText(value:string){
  return String(value ?? '')
    .toLowerCase()
    .replace(/[’']/g,"'")
    .replace(/\s+/g,' ')
    .trim();
}

export function extractBossConstraints(objective:string):BossConstraint[]{
  const source=String(objective ?? '');
  const patterns=[
    /\byeni\s+(dependency|bağımlılık)\s+ekleme\b/i,
    /\byeni\s+(dependency|bağımlılık)\s+eklemeyin\b/i,
    /\bdependency\s+ekleme\b/i,
    /\bbağımlılık\s+ekleme\b/i,
    /\bdo\s+not\s+add\s+(a\s+)?new\s+dependenc(y|ies)\b/i,
    /\bdon't\s+add\s+(a\s+)?new\s+dependenc(y|ies)\b/i,
    /\bno\s+new\s+dependenc(y|ies)\b/i,
    /\bwithout\s+adding\s+(a\s+)?new\s+dependenc(y|ies)\b/i,
  ];
  const match=patterns.map(p=>source.match(p)).find(Boolean);
  return match ? [{
    id:'forbid_new_dependency',
    type:'forbid_new_dependency',
    source:String(match[0]),
    normalized:'Do not add, install, declare, or require any new dependency/package/library.'
  }] : [];
}



export function dependencyAdditionIntent(text:string){
  const value=normalizeText(text);

  const positivePatterns=[
    /\badd\s+(?:a\s+)?new\s+dependenc(?:y|ies)\b/g,
    /\badd\s+dependenc(?:y|ies)\b/g,
    /\binstall\s+(?:a\s+)?new\s+(?:package|dependency|library)\b/g,
    /\bdeclare\s+(?:a\s+)?new\s+dependenc(?:y|ies)\b/g,
    /\bnew\s+dependency\s+(?:declaration|added|installed|required)\b/g,
    /\byeni\s+(?:dependency|bağımlılık)\s+(?:ekle|kur|tanımla)\b/g,
    /\b(?:dependency|bağımlılık)\s+ekle\b/g,

    // Result/evidence claims: a named package or dependency was actually added.
    /@[a-z0-9_.-]+\/[a-z0-9_.-]+.{0,60}\b(?:eklendi|kuruldu|yüklendi|added|installed)\b/g,
    /\b(?:dependency|bağımlılık|package|paket|library|kütüphane)\b.{0,60}\b(?:eklendi|kuruldu|yüklendi|added|installed)\b/g,

    // Explicit package-manager add/install commands that normally mutate dependency declarations.
    /\bnpm\s+(?:install|i)\s+(?:--save(?:-dev)?\s+)?[@a-z0-9_.-][^\s,;]*/g,
    /\bpnpm\s+add\s+[@a-z0-9_.-][^\s,;]*/g,
    /\byarn\s+add\s+[@a-z0-9_.-][^\s,;]*/g,
    /\b(?:pip|pip3)\s+install\s+[a-z0-9_.-][^\s,;]*/g,
    /\bpoetry\s+add\s+[a-z0-9_.-][^\s,;]*/g,
    /\buv\s+add\s+[a-z0-9_.-][^\s,;]*/g,
    /\bcargo\s+add\s+[a-z0-9_.-][^\s,;]*/g,
    /\bcomposer\s+require\s+[a-z0-9_.\/-][^\s,;]*/g,
    /\bgo\s+get\s+[a-z0-9_.\/-][^\s,;]*/g,
  ];

  const negatedBeforePatterns=[
    /(?:^|\s)(?:do\s+not|don't|dont|never|without|must\s+not|should\s+not|cannot|can't|cant)\s+(?:\w+\s+){0,5}$/i,
    /(?:^|\s)no\s+(?:\w+\s+){0,5}$/i,
    /(?:^|\s)(?:sakın|asla)\s+(?:\w+\s+){0,5}$/i,
  ];

  const negatedWholePhrasePatterns=[
    /\bno\s+new\s+dependenc(?:y|ies)\b/i,
    /\bwithout\s+adding\s+(?:a\s+)?new\s+dependenc(?:y|ies)\b/i,
    /\bdo\s+not\s+add\s+(?:a\s+)?new\s+dependenc(?:y|ies)\b/i,
    /\bdon't\s+add\s+(?:a\s+)?new\s+dependenc(?:y|ies)\b/i,
    /\bmust\s+not\s+add\s+(?:a\s+)?new\s+dependenc(?:y|ies)\b/i,
    /\bshould\s+not\s+add\s+(?:a\s+)?new\s+dependenc(?:y|ies)\b/i,
  ];

  const negatedAfterPatterns=[
    /^(?:\s|[:,;-])*(?:was\s+not|wasn't|were\s+not|weren't|not\s+run|not\s+executed|not\s+added|not\s+installed)\b/i,
    /^(?:\s|[:,;-])*(?:yapılmadı|çalıştırılmadı|eklenmedi|kurulmadı|yüklenmedi)\b/i,
  ];

  for(const pattern of positivePatterns){
    pattern.lastIndex=0;
    let match:RegExpExecArray|null;

    while((match=pattern.exec(value))){
      const start=match.index;
      const end=start+match[0].length;
      const before=value.slice(Math.max(0,start-100),start);
      const around=value.slice(Math.max(0,start-60),Math.min(value.length,end+60));
      const after=value.slice(end,Math.min(value.length,end+80));

      const directlyNegated=negatedBeforePatterns.some(p=>p.test(before));
      const phraseNegated=negatedWholePhrasePatterns.some(p=>p.test(around));
      const negatedAfter=negatedAfterPatterns.some(p=>p.test(after));

      if(!directlyNegated && !phraseNegated && !negatedAfter){
        return true;
      }

      if(pattern.lastIndex===match.index)pattern.lastIndex++;
    }
  }

  return false;
}

export function validatePlanAgainstBossConstraints(objective:string,plan:any){
  const constraints=extractBossConstraints(objective);
  const violations:PlanConstraintViolation[]=[];
  for(const constraint of constraints){
    for(const task of Array.isArray(plan?.tasks)?plan.tasks:[]){
      const fields=[
        ['title',task?.title],
        ['instruction',task?.instruction],
        ['expectedOutcome',task?.expectedOutcome],
        ['description',task?.description],
        ['acceptanceCriteria',Array.isArray(task?.acceptanceCriteria)?task.acceptanceCriteria.join('\n'):task?.acceptanceCriteria]
      ];
      for(const [field,value] of fields){
        const text=String(value ?? '');
        if(constraint.type==='forbid_new_dependency' && dependencyAdditionIntent(text)){
          violations.push({
            constraintId:constraint.id,
            constraintSource:constraint.source,
            taskKey:String(task?.key ?? task?.taskKey ?? '?'),
            field:String(field),
            text:text.slice(0,1200),
            reason:'Task requests adding/installing/declaring a new dependency despite the Boss prohibition.'
          });
        }
      }
    }
  }
  return {ok:violations.length===0,constraints,violations};
}

export function hardConstraintPrompt(objective:string){
  const constraints=extractBossConstraints(objective);
  if(!constraints.length)return 'No deterministic Boss hard constraints were extracted.';
  return constraints.map((c,index)=>
    `${index+1}. HARD CONSTRAINT [${c.id}]: ${c.normalized}\n   Boss wording: "${c.source}"`
  ).join('\n');
}


export function validateTextAgainstBossConstraints(
  objective:string,
  text:string,
  sourceLabel='agent_result'
){
  const constraints=extractBossConstraints(objective);
  const violations:PlanConstraintViolation[]=[];

  for(const constraint of constraints){
    if(
      constraint.type==='forbid_new_dependency'
      && dependencyAdditionIntent(String(text ?? ''))
    ){
      violations.push({
        constraintId:constraint.id,
        constraintSource:constraint.source,
        taskKey:sourceLabel,
        field:'result_text',
        text:String(text ?? '').slice(0,4000),
        reason:'Agent result claims a dependency add/install/declare action that is forbidden by the Boss objective.'
      });
    }
  }

  return {ok:violations.length===0,constraints,violations};
}
