export type TaskWorkMode='modify'|'validate'|'analysis'|'general';

const WORK_MODES=new Set<TaskWorkMode>([
  'modify','validate','analysis','general'
]);

const ARTIFACT_PATH_PATTERN=/(?:^|[\s"'`(])((?:[A-Za-z0-9_.-]+\/)*(?:(?:[A-Za-z0-9_.-]+\.(?:md|markdown|json|ya?ml|toml|txt|py|mjs|cjs|ts|tsx|js|jsx|css|html|cs|java|go|rs|php|sql|xml|ini|cfg|conf|sh|bash))|(?:Dockerfile|Makefile|\.gitignore)))(?=$|[\s"'`),.;:])/gi;

const TECHNOLOGY_LABELS=new Set([
  'node.js','react.js','next.js','vue.js','deno.js',
]);

const ENGLISH_MUTATION_WORD='(?:implement(?:ing)?|creat(?:e|ing)|add(?:ing)?|writ(?:e|ing)|updat(?:e|ing)|edit(?:ing)?|rewrit(?:e|ing)|modif(?:y|ying)|generat(?:e|ing)|chang(?:e|ing)|fix(?:ing)?|remov(?:e|ing)|delet(?:e|ing)|renam(?:e|ing)|mov(?:e|ing)|refactor(?:ing)?|configur(?:e|ing)|patch(?:ing)?|replac(?:e|ing)|format(?:ting)?|document(?:ing)?|cop(?:y|ying)|appl(?:y|ying)|sav(?:e|ing))';
const ENGLISH_ACTION_WORD=`(?:${ENGLISH_MUTATION_WORD}|run|execute|test|verify|validate|review|inspect|check|confirm)`;

function englishMutationPattern(flags=''){
  return new RegExp(`\\b${ENGLISH_MUTATION_WORD}\\b`,flags);
}

function normalizedArtifactPath(value:unknown){
  return String(value ?? '')
    .replace(/\\/g,'/')
    .replace(/^\.\/+/, '');
}

function stripEnglishActionPrefix(value:string){
  return value
    .replace(/^(?:(?:please|kindly|carefully|safely|either)\s*,?\s*)+/,'')
    .replace(/^(?:(?:if|as)\s+needed\s*,?\s*)+/,'')
    .trim();
}

function normalized(value:unknown){
  return String(value ?? '')
    .toLocaleLowerCase('tr-TR')
    .replace(/\s+/g,' ')
    .trim();
}

function normalizedEnglish(value:unknown){
  return String(value ?? '')
    .toLowerCase()
    .replace(/\u0307/g,'')
    .replace(/\s+/g,' ')
    .trim();
}

function firstNonEmpty(...values:unknown[]){
  for(const value of values){
    const text=String(value ?? '').trim();
    if(text)return text;
  }
  return '';
}

export function executableTaskInstruction(task:any){
  return firstNonEmpty(task?.instruction,task?.description,task?.title);
}

function hasTurkishMutationVerb(text:string){
  return /(?:^|[\s"'`(])(?:oluştur(?:un|unuz)?|yarat(?:ın|ınız)?|üret(?:in|iniz)?|ekle(?:yin|yiniz)?|yaz(?:ın|ınız)?|güncelle(?:yin|yiniz)?|düzenle(?:yin|yiniz)?|düzelt(?:in|iniz)?|değiştir(?:in|iniz)?|iyileştir(?:in|iniz)?|kaldır(?:ın|ınız)?|sil(?:in|iniz)?|taşı(?:yın|yınız)?|yapılandır(?:ın|ınız)?|biçimlendir(?:in|iniz)?|yeniden\s+adlandır(?:ın|ınız)?)(?=$|[\s"'`),.;:])/.test(text);
}

function positiveEnglishActionText(text:string){
  let positive=text.trim();
  const leadingNegative=/^(?:please\s+)?(?:do\s+not|must\s+not|never)\b/.test(positive)
    || /^(?:please\s+)?avoid\s+(?:implementing|changing|modifying|editing|writing|creating|adding|updating|generating|fixing|removing|deleting|renaming|moving|refactoring|configuring|patching|replacing|formatting|documenting|copying|applying|saving)\b/.test(positive);
  if(leadingNegative){
    const contrast=positive.match(/(?:\b(?:but|instead|then)\b|;)/);
    if(contrast){
      const splitAt=Number(contrast.index ?? 0);
      const antecedents=extractArtifactPaths(positive.slice(0,splitAt)).join(' ');
      positive=`${antecedents}; ${positive.slice(splitAt+String(contrast[0]).length)}`;
    }else{
      positive='';
    }
  }
  positive=positive.replace(/^(?:please\s+)?without\b[^,;]*(?:[,;]\s*|$)/,' ');
  return positive
    .replace(/\bwithout\s+(?:implementing|changing|modifying|editing|writing|creating|adding|updating|generating|fixing|removing|deleting|renaming|moving|refactoring|configuring|patching|replacing|formatting|documenting)(?:\s+(?:and|or)\s+(?:implementing|changing|modifying|editing|writing|creating|adding|updating|generating|fixing|removing|deleting|renaming|moving|refactoring|configuring|patching|replacing|formatting|documenting))*\b/g,' ')
    .replace(/\b(?:do\s+not|must\s+not|never|avoid)\s+(?:implement|change|modify|edit|write|create|add|update|generate|fix|remove|delete|rename|move|refactor|configure|patch|replace|format|document)(?:\s+(?:and|or)\s+(?:implement|change|modify|edit|write|create|add|update|generate|fix|remove|delete|rename|move|refactor|configure|patch|replace|format|document))*\b/g,' ')
    .replace(/\bread[- ]only\b/g,' ');
}

function positiveTurkishActionText(text:string){
  let positive=text;
  const negative=positive.match(/(?:^|\s)(?:asla|sakın)(?:\s|$)/);
  if(negative && hasTurkishMutationVerb(positive)){
    const markerStart=Number(negative.index ?? 0);
    const priorBoundary=positive.lastIndexOf(';',markerStart);
    const safePrefix=priorBoundary>=0 ? positive.slice(0,priorBoundary+1) : '';
    const tail=positive.slice(markerStart+String(negative[0]).length);
    const scopeEnd=tail.match(/(?:;|,\s*(?:yalnız|ancak|ama|yerine)\b|\b(?:yalnız|ancak|ama|yerine)\b)/);
    const suffix=scopeEnd
      ? tail.slice(Number(scopeEnd.index ?? 0)+String(scopeEnd[0]).length)
      : '';
    positive=`${safePrefix} ${suffix}`.trim();
  }
  return positive
    .replace(/(?:salt\s+okunur|değiştirmeden|düzenlemeden|güncellemeden|yazmadan|oluşturmadan|eklemeden)/g,' ');
}

function repositoryArtifactObject(value:string){
  if(extractArtifactPaths(value).length)return true;
  if(/\btests?\b(?!\s+(?:results?|findings?|summary|report))/.test(value))return true;
  return /\b(?:readme|files?|code|source|scripts?|configs?|configuration|repository|repo|modules?|implementation|features?|bugs?|endpoints?|api|components?|tools?|packages?|manifests?|schemas?|migrations?|documentation)\b/.test(value)
    || /(?:dosya|kod|kaynak|betik|test|yapılandırma|depo|repo|modül|uygulama|özellik|hata|bileşen|araç|şema|migrasyon|dokümantasyon)/.test(value);
}

function englishNonRepositoryOutput(value:string){
  const text=value.replace(/[.,;:]+$/,'').trim();
  return /(?:^|\s)(?:reviews?|findings?|summar(?:y|ies)|tickets?|responses?|reports?|messages?|comments?|notes?|plans?|assessments?|evidence|recommendations?|proposals?|drafts?|scenarios?|feedback|verdicts?|outputs?|results?|observations?)(?:\s+(?:in|to|at|into|with|containing|about|regarding|on|for|of)\s+.*)?$/.test(text);
}

function englishExplicitOutputPath(value:string){
  const text=value.trim();
  const direct=extractArtifactPaths(text)[0];
  if(direct && normalizedEnglish(text).startsWith(direct.toLowerCase()))return true;
  return /(?:^|\s)(?:in|to|at|into)\s+(?:(?:[A-Za-z0-9_.-]+\/)*(?:(?:[A-Za-z0-9_.-]+\.(?:md|markdown|json|ya?ml|toml|txt|py|mjs|cjs|ts|tsx|js|jsx|css|html|cs|java|go|rs|php|sql|xml|ini|cfg|conf|sh|bash))|(?:Dockerfile|Makefile|\.gitignore)))(?=$|[\s"'`),.;:])/i.test(text);
}

function leadingSharedObjectMutation(text:string){
  text=stripEnglishActionPrefix(text);
  const chainPattern=new RegExp(
    `^(?:please\\s+)?(${ENGLISH_ACTION_WORD}(?:(?:\\s*,\\s*(?:(?:and|or)\\s+)?|\\s+(?:and|or)\\s+)${ENGLISH_ACTION_WORD})+)\\s+(.+)$`,
  );
  const match=text.match(chainPattern);
  if(!match)return false;
  const actions=[...String(match[1]).matchAll(new RegExp(`\\b${ENGLISH_ACTION_WORD}\\b`,'g'))]
    .map(item=>String(item[0]));
  const objectText=String(match[2] ?? '').trim();
  return actions.some(action=>englishMutationPattern().test(action))
    && !englishNonRepositoryOutput(objectText)
    && repositoryArtifactObject(objectText);
}

function englishMutationTargetsArtifact(text:string){
  if(leadingSharedObjectMutation(text))return true;

  const spanPattern=new RegExp(
    `\\s*(?:;|,|\\band(?:\\s+then)?\\b|\\b(?:then|but|instead)\\b|\\.\\s+(?=(?:(?:please|kindly|carefully|safely|either)\\s+)*${ENGLISH_ACTION_WORD}\\b))\\s*`,
  );
  const spans=text.split(spanPattern).filter(Boolean);
  let priorArtifact=false;
  for(const span of spans){
    const clean=stripEnglishActionPrefix(span);
    if(leadingSharedObjectMutation(clean))return true;

    const direct=clean.match(new RegExp(`^(${ENGLISH_MUTATION_WORD})\\b`));
    if(direct){
      const objectText=clean.slice(String(direct[0]).length).trim();
      if(englishExplicitOutputPath(objectText))return true;
      if(!englishNonRepositoryOutput(objectText)){
        if(repositoryArtifactObject(objectText))return true;
        if(priorArtifact && /^(?:it|them|that\s+file|those\s+files)\b/.test(objectText)){
          return true;
        }
      }
    }

    const producerMutation=
      /^(?:run|execute)\b.*(?:--write|--fix)\b/.test(clean)
      || new RegExp(`^(?:run|execute)\\b.*\\b(?:codegen|generator|compiler|formatter|script|command|tool)\\b.*\\b(?:to|in\\s+order\\s+to)\\s+${ENGLISH_MUTATION_WORD}\\b`).test(clean)
      || new RegExp(`^(?:use|apply|invoke)\\b.*\\b(?:to|in\\s+order\\s+to)\\s+${ENGLISH_MUTATION_WORD}\\b`).test(clean)
      || new RegExp(`^avoid\\b.*\\bby\\s+${ENGLISH_MUTATION_WORD}\\b`).test(clean);
    if(producerMutation && repositoryArtifactObject(clean))return true;

    if(repositoryArtifactObject(span))priorArtifact=true;
  }
  return false;
}

function turkishNonRepositoryOutput(value:string){
  const text=value.replace(/[.,;:]+$/,'').trim();
  return /(?:^|\s)(?:inceleme(?:si|yi|sini)?|bulgu(?:lar)?(?:ı|ları)?|özet(?:i|ini)?|rapor(?:u|unu)?|mesaj(?:ı|ını)?|yorum(?:u|unu)?|not(?:u|unu)?|plan(?:ı|ını)?|öneri(?:si|yi|leri|lerini)?|taslağ(?:ı|ını)?|taslak|senaryo(?:su|yu|ları|larını)?|kanıt(?:ı|ını)?|değerlendirme(?:si|yi|sini)?)(?:\s+.*)?$/.test(text);
}

function turkishHasDirectOutputPath(value:string){
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  for(const match of value.matchAll(ARTIFACT_PATH_PATTERN)){
    const index=Number(match.index ?? 0);
    const offset=String(match[0] ?? '').indexOf(String(match[1] ?? ''));
    const start=index+Math.max(0,offset);
    const end=start+String(match[1] ?? '').length;
    const after=normalized(value.slice(end));
    const explicitOutput=/^(?:dosya(?:sına|ya)\s+|üzerine\s+|çıktı\s+olarak\s+)/.test(after);
    if(explicitOutput){
      ARTIFACT_PATH_PATTERN.lastIndex=0;
      return true;
    }
  }
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  return false;
}

function turkishMutationTargetsArtifact(text:string){
  const spans=text.split(/\s*(?:;|,|\b(?:ve|sonra|ardından)\b|\b(?:inceleyip|inceleyerek|doğrulayıp|doğrulayarak|kontrol\s+edip|kontrol\s+ederek)\b)\s*/).filter(Boolean);
  let priorArtifact=false;
  for(const span of spans){
    const quotedMutationCommand=/(?:üret|oluştur|ekle|yaz|güncelle|düzenle|düzelt|değiştir|iyileştir|kaldır|sil|taşı|yapılandır|biçimlendir)\s+(?:komut|talimat|işlem)[^\s]*\s+(?:incele[^\s]*|doğrula[^\s]*|kontrol\s+et[^\s]*)/.test(span);
    if(quotedMutationCommand){
      if(repositoryArtifactObject(span))priorArtifact=true;
      continue;
    }
    if(!hasTurkishMutationVerb(span)){
      if(repositoryArtifactObject(span))priorArtifact=true;
      continue;
    }
    if(turkishNonRepositoryOutput(span) && !turkishHasDirectOutputPath(span)){
      if(repositoryArtifactObject(span))priorArtifact=true;
      continue;
    }
    if(repositoryArtifactObject(span))return true;
    if(priorArtifact && /(?:^|\s)(?:gerekirse\s+)?(?:üret|oluştur|ekle|yaz|güncelle|düzenle|düzelt|değiştir|iyileştir|kaldır|sil|taşı|yapılandır|biçimlendir)(?:in|iniz|yin|yiniz)?(?:\s|[.,;:]|$)/.test(span)){
      return true;
    }
    if(repositoryArtifactObject(span))priorArtifact=true;
  }
  return false;
}

function clauseHasStrongMutationIntent(value:unknown){
  const turkish=positiveTurkishActionText(normalized(value));
  const english=positiveEnglishActionText(normalizedEnglish(value));
  if(!turkish && !english)return false;
  return englishMutationTargetsArtifact(english)
    || turkishMutationTargetsArtifact(turkish);
}

/** Mutation intent follows the executable action, never expected post-state. */
export function taskHasStrongMutationIntent(task:any){
  const instruction=executableTaskInstruction(task);
  if(!instruction)return false;
  return clauseHasStrongMutationIntent(instruction);
}

/** Reject only an already-completed assertion, not a command to validate it. */
export function isCompletedStateTaskInstruction(value:unknown){
  const english=normalizedEnglish(value);
  const turkish=normalized(value);
  if(!english && !turkish)return false;

  const turkishCompleted=/(güncellendi|yazıldı|düzenlendi|oluşturuldu|tamamlandı|incelendi|doğrulandı|kontrol edildi|hazırlandı|iyileştirildi|değiştirildi|güncellenmiş(?:tir)?|yazılmış(?:tır)?|oluşturulmuş(?:tur)?|tamamlanmış(?:tır)?|günceldir|iş\s+tamam)(?=$|[\s,.;:])/.test(turkish);
  const englishCompleted=
    /\b(?:was|were|is|are|has\s+been|have\s+been)(?:\s+successfully)?\s+(?:updated|written|edited|created|completed|reviewed|verified|prepared|improved|changed|complete|finished)\b/.test(english)
    || /\b(?:updated|written|edited|created|completed|reviewed|verified|prepared|improved|changed|complete|finished)(?:\s+successfully)?\.?$/.test(english);
  const passiveActionSubject=englishCompleted
    && /^(?:test|review|check|run)(?:\s+(?:task|file|suite|report|result|results|execution|command|operation|step|job|process|activity|phase))?\s+(?:(?:was|were|is|are|has\s+been|have\s+been)(?:\s+successfully)?\s+)?(?:successfully\s+)?(?:updated|written|edited|created|completed|reviewed|verified|prepared|improved|changed|complete|finished)(?:\s+successfully)?\.?$/.test(english);
  const validationCommand=
    (!passiveActionSubject
      && /^(?:please\s+)?(?:(?:make\s+sure)|run|execute|test|verify|validate|review|inspect|check|confirm|ensure)\b/.test(english))
    || /\b(?:mi|mı|mu|mü)\s+(?:diye\s+)?(?:doğrula|kontrol\s+et|incele)(?:yin|yınız|yiniz)?(?=$|[\s,.;:])/.test(turkish)
    || /(?:^|[\s,])(?:doğrula|kontrol\s+et|incele)(?:yin|yınız|yiniz)?(?=$|[\s,.;:])/.test(turkish);
  if(validationCommand)return false;
  return turkishCompleted || englishCompleted;
}

function validationIntent(value:unknown){
  const text=normalized(value);
  const english=normalizedEnglish(value);
  return (
    /\bqa\b/.test(text)
    || /\breviewer\b/.test(text)
    || /(doğrula\w*|kontrol\s+et\w*|incele\w*)/.test(text)
    || /\b(?:verify|validate|review|inspect|check|confirm|ensure)\w*\b/.test(english)
    || /\bmake\s+sure\b/.test(english)
  );
}

function analysisIntent(value:unknown){
  const text=normalized(value);
  return /(analiz\w*|planla\w*|gereksinim\w*|inceleme\s+planı|analy[sz]\w*|plan\w*|requirements?\b)/.test(text);
}

export function inferTaskWorkMode(task:any):TaskWorkMode{
  const instruction=executableTaskInstruction(task);
  if(taskHasStrongMutationIntent({instruction}))return 'modify';
  if(validationIntent(instruction))return 'validate';
  if(analysisIntent(instruction))return 'analysis';
  return 'general';
}

/**
 * Resolve a durable row plus its plan snapshot. Explicit modes are preserved
 * except when the executable instruction itself unambiguously requires a
 * repository mutation. expectedOutcome is deliberately excluded.
 */
export function resolveTaskWorkModeContract(task:any,spec:any={}):TaskWorkMode{
  const explicit=firstNonEmpty(
    task?.work_mode,task?.workMode,spec?.workMode,spec?.work_mode
  );
  const instruction=firstNonEmpty(
    task?.instruction,spec?.instruction,task?.description,spec?.description,
    task?.title,spec?.title
  );

  if(WORK_MODES.has(explicit as TaskWorkMode)){
    if(explicit!=='modify' && taskHasStrongMutationIntent({instruction})){
      return 'modify';
    }
    return explicit as TaskWorkMode;
  }

  return inferTaskWorkMode({instruction});
}

export function extractArtifactPaths(value:unknown){
  const text=String(value ?? '');
  const paths:string[]=[];
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  for(const match of text.matchAll(ARTIFACT_PATH_PATTERN)){
    const path=normalizedArtifactPath(match[1]);
    if(path && !TECHNOLOGY_LABELS.has(path.toLowerCase()))paths.push(path);
  }
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  return [...new Set(paths)];
}

function englishPathInputOrNegatedRelation(before:string,after:string){
  const leadingNegation=(/^(?:please\s+)?(?:do\s+not|must\s+not|never)\b/.test(before)
    || /^(?:please\s+)?avoid\s+(?:implementing|changing|modifying|editing|writing|creating|adding|updating|generating|fixing|removing|deleting|renaming|moving|refactoring|configuring|patching|replacing|formatting|documenting|copying|applying|saving)\b/.test(before))
    && !/\b(?:but|instead|then)\b/.test(before);
  const leadingWithout=/^(?:please\s+)?without\b/.test(before)
    && !/[,;]/.test(before);
  if(leadingNegation || leadingWithout)return true;

  let lastRelation=-1;
  let lastRelationEnd=-1;
  const relationPattern=/\b(?:using|with|based\s+on|according\s+to|following|against|from|for|about|regarding|on|of|describing|covering|that\s+(?:tests?|covers?|imports?)|to\s+(?:test|cover|import)|without\s+(?:implementing|changing|modifying|editing|writing|creating|adding|updating|generating|fixing|removing|deleting|renaming|moving|refactoring|configuring|patching|replacing|formatting|documenting|copying|applying|saving))\b/g;
  for(const match of before.matchAll(relationPattern)){
    lastRelation=Number(match.index ?? -1);
    lastRelationEnd=lastRelation+String(match[0]).length;
  }

  if(lastRelation>=0){
    const tail=before.slice(lastRelationEnd);
    if(extractArtifactPaths(tail).length>0 && /\b(?:to|into|in|at)\s*$/.test(tail)){
      return false;
    }
    const positiveActionReset=new RegExp(
      `(?:\\b(?:and|then|but|instead)\\s+|[,;]\\s*)${ENGLISH_MUTATION_WORD}\\b`,
    ).test(tail);
    if(!positiveActionReset)return true;
  }

  return /^(?:as\s+(?:an?\s+)?inputs?|as\s+(?:a\s+)?references?)\b/.test(after);
}

function turkishPathInputRelation(after:string){
  const relation='(?:için|hakkında|ile\\s+ilgili|inceleme(?:si|sini)?|kullanarak|baz\\s+alarak|referans\\s+alarak|girdi\\s+olarak|referans\\s+olarak|üzerinden|değiştirmeden|düzenlemeden|güncellemeden|asla|sakın)';
  return new RegExp(`^(?:dosya(?:sı|yı|sını|sına|sından|ları|larını)?\\s+)?${relation}\\b`).test(after)
    || new RegExp(`^ve\\s+.*?\\b(?:dosyalarını\\s+)?${relation}\\b`).test(after);
}

function anaphoricMutationFollows(value:string){
  const english=normalizedEnglish(value);
  const turkish=normalized(value);
  const englishMutation=new RegExp(
    `(?:^|[,;.]\\s*|\\b(?:and|then|but|instead)\\s+)(?:(?:if|as)\\s+needed\\s*,?\\s*)?${ENGLISH_MUTATION_WORD}\\s+(?:it|them|that\\s+file|those\\s+files)\\b`,
  );
  const turkishMutation=/(?:^|[,;.]\s*|\bve\s+)(?:gerekirse\s+)?(?:üret|oluştur|ekle|yaz|güncelle|düzenle|düzelt|değiştir|iyileştir|kaldır|sil|taşı|yapılandır|biçimlendir)(?:in|iniz|yin|yiniz)?(?:\s|[.,;]|$)/;
  return englishMutation.test(english) || turkishMutation.test(turkish);
}

function instructionMutationTargets(instruction:string){
  const targets:string[]=[];
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  for(const match of instruction.matchAll(ARTIFACT_PATH_PATTERN)){
    const rawPath=String(match[1] ?? '');
    const path=normalizedArtifactPath(rawPath);
    if(!path || TECHNOLOGY_LABELS.has(path.toLowerCase()))continue;

    const index=Number(match.index ?? 0);
    const pathOffset=String(match[0] ?? '').indexOf(rawPath);
    const pathStart=index+Math.max(0,pathOffset);
    const pathEnd=pathStart+rawPath.length;
    const prefix=instruction.slice(0,index);
    const clauseStart=Math.max(
      prefix.lastIndexOf(';'),prefix.lastIndexOf('\n'),
      prefix.lastIndexOf('!'),prefix.lastIndexOf('?')
    )+1;
    const suffix=instruction.slice(index);
    const nextBoundaries=[';','\n','!','?']
      .map(char=>suffix.indexOf(char))
      .filter(offset=>offset>=0);
    const clauseEnd=nextBoundaries.length
      ? index+Math.min(...nextBoundaries)
      : instruction.length;
    const beforeRaw=instruction.slice(Math.max(clauseStart,pathStart-80),pathStart);
    const afterRaw=instruction.slice(pathEnd,Math.min(clauseEnd,pathEnd+96));
    const afterContext=instruction.slice(pathEnd,Math.min(instruction.length,pathEnd+180));
    const beforeEnglish=normalizedEnglish(beforeRaw);
    const beforeTurkish=normalized(beforeRaw);
    const afterEnglish=normalizedEnglish(afterRaw);
    const afterTurkish=normalized(afterRaw);
    const inputRelation=englishPathInputOrNegatedRelation(beforeEnglish,afterEnglish)
      || turkishPathInputRelation(afterTurkish)
      || (/\bcopy\s*$/.test(beforeEnglish)
        && /^\s*(?:to|into)\s+/.test(afterEnglish));
    const readOnlyReference=
      /(?:verif(?:y|ies|ying)|check(?:s|ing)?|inspect(?:s|ing)?|read(?:s|ing)?|validat(?:e|es|ing)|confirm(?:s|ing)?|detect(?:s|ing)?|writ(?:es|ing))(?:\s+(?:that|whether|how|the))?\s*$/.test(beforeEnglish)
      || /(?:doğrula\w*|kontrol\s+et\w*|incele\w*|oku\w*)\s*$/.test(beforeTurkish)
      || /^(?:existence\b|exists?\b|presence\b|availability\b|is\s+(?:verified|checked|read|detected)\b|was\s+(?:verified|checked|read|detected)\b)/.test(afterEnglish)
      || /^(?:varlığ\w*|mevcut\w*|okun\w*|doğrulan\w*|kontrol\w*)/.test(afterTurkish);
    if(anaphoricMutationFollows(afterContext)){
      targets.push(path);
      continue;
    }
    if(inputRelation || readOnlyReference)continue;

    const start=Math.max(clauseStart,index-80);
    const end=Math.min(clauseEnd,index+String(match[0] ?? '').length+80);
    const nearby=instruction.slice(start,end);
    if(clauseHasStrongMutationIntent(nearby))targets.push(path);
  }
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  return [...new Set(targets)];
}

function contextualArtifactPaths(value:unknown){
  const text=String(value ?? '');
  const paths:string[]=[];
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  for(const match of text.matchAll(ARTIFACT_PATH_PATTERN)){
    const rawPath=String(match[1] ?? '');
    const path=normalizedArtifactPath(rawPath);
    if(!path || TECHNOLOGY_LABELS.has(path.toLowerCase()))continue;
    const index=Number(match.index ?? 0);
    const offset=String(match[0] ?? '').indexOf(rawPath);
    const start=index+Math.max(0,offset);
    const end=start+rawPath.length;
    const beforeEnglish=normalizedEnglish(text.slice(Math.max(0,start-80),start));
    const afterEnglish=normalizedEnglish(text.slice(end,Math.min(text.length,end+100)));
    const afterTurkish=normalized(text.slice(end,Math.min(text.length,end+100)));
    const inputRelation=englishPathInputOrNegatedRelation(beforeEnglish,afterEnglish)
      || turkishPathInputRelation(afterTurkish);
    const mutationPostState=/^(?:is|was|are|were|has\s+been|have\s+been)?\s*(?:created|written|updated|edited|modified|implemented|generated)\b/.test(afterEnglish)
      || /^(?:dosya\w*\s+)?(?:oluşturul\w*|yazıl\w*|güncellen\w*|düzenlen\w*|değiştiril\w*)/.test(afterTurkish);
    const readOnlyReference=
      /(?:verif(?:y|ies|ying)|check(?:s|ing)?|inspect(?:s|ing)?|read(?:s|ing)?|validat(?:e|es|ing)|confirm(?:s|ing)?|detect(?:s|ing)?)\s*$/.test(beforeEnglish)
      || /^(?:existence\b|exists?\b|presence\b|availability\b|is\s+(?:verified|checked|read|detected)\b|was\s+(?:verified|checked|read|detected)\b)/.test(afterEnglish)
      || /^(?:varlığ\w*|mevcut\w*|okun\w*|doğrulan\w*|kontrol\w*)/.test(afterTurkish);
    if(inputRelation || (readOnlyReference && !mutationPostState))continue;
    paths.push(path);
  }
  ARTIFACT_PATH_PATTERN.lastIndex=0;
  return [...new Set(paths)];
}

export function inferTaskMutationTargetPaths(task:any):string[]{
  const instruction=executableTaskInstruction(task);
  const direct=instructionMutationTargets(instruction);
  if(direct.length)return direct;

  const outcome=contextualArtifactPaths(
    firstNonEmpty(task?.expectedOutcome,task?.expected_outcome)
  );
  const criteria:string[]=Array.isArray(task?.acceptanceCriteria)
    ? task.acceptanceCriteria.flatMap((value:unknown)=>contextualArtifactPaths(value))
    : [];
  const criterionSet=new Set(criteria.map(path=>path.toLowerCase()));
  const corroborated=outcome.filter(path=>criterionSet.has(path.toLowerCase()));

  if(corroborated.length)return [...new Set(corroborated)];
  if(outcome.length)return outcome;
  return [...new Set(criteria)];
}
