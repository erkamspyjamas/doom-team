import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export function openAICompatibleHeaders(provider:any) {
  const headers:Record<string,string> = {
    'Content-Type':'application/json',
    'Accept':'application/json',
  };

  const authType=String(provider?.auth_type ?? 'none');
  if(authType==='none') return headers;

  if(authType==='bearer'){
    const secretRef=String(provider?.secret_ref ?? '').trim();
    if(!secretRef) throw new Error('PROVIDER_SECRET_REF_REQUIRED');
    const secret=process.env[secretRef];
    if(!secret) throw new Error(`PROVIDER_SECRET_ENV_NOT_FOUND:${secretRef}`);
    headers.Authorization=`Bearer ${secret}`;
    return headers;
  }

  throw new Error(`PROVIDER_AUTH_UNSUPPORTED:${authType}`);
}

function base(provider:any) {
  if(String(provider?.protocol)!=='openai-compatible'){
    throw new Error(`PROVIDER_PROTOCOL_UNSUPPORTED:${provider?.protocol}`);
  }
  const value=String(provider?.base_url ?? '').trim().replace(/\/+$/,'');
  if(!/^https?:\/\//i.test(value)) throw new Error('PROVIDER_BASE_URL_INVALID');
  return value;
}

function assistantText(message:any) {
  const content=message?.content;
  if(typeof content==='string') return content.trim();

  if(Array.isArray(content)){
    return content
      .map((part:any)=>{
        if(typeof part==='string') return part;
        if(typeof part?.text==='string') return part.text;
        return '';
      })
      .filter(Boolean)
      .join('\n')
      .trim();
  }

  return '';
}

async function jsonResponse(res:Response) {
  const text=await res.text();
  let body:any=text;
  try{body=text ? JSON.parse(text) : {}}catch{}
  if(!res.ok){
    throw new Error(`PROVIDER_HTTP_${res.status}:${typeof body==='string'?body:JSON.stringify(body)}`);
  }
  return body;
}

export async function listRemoteModels(provider:any) {
  const started=Date.now();
  const res=await fetch(`${base(provider)}/models`,{
    method:'GET',
    headers:openAICompatibleHeaders(provider),
  });
  const body=await jsonResponse(res);
  const models=Array.isArray(body?.data)
    ? body.data.map((x:any)=>({
        id:String(x?.id ?? ''),
        ownedBy:x?.owned_by ? String(x.owned_by) : undefined,
        raw:x,
      })).filter((x:any)=>x.id)
    : [];
  return {models,latencyMs:Date.now()-started,raw:body};
}

export async function chatProbe(provider:any, providerModelId:string) {
  const started=Date.now();

  async function attempt(disableReasoning:boolean) {
    const payload:any={
      model:providerModelId,
      messages:[{
        role:'user',
        content:'This is a Doom Core chat capability probe. Reply briefly with DOOM_OK.'
      }],
      temperature:0,
      max_tokens:disableReasoning ? 64 : 1024,
      stream:false,
    };

    // Ollama's OpenAI-compatible API supports reasoning_effort:"none".
    // Other OpenAI-compatible providers may not, so this request is retried
    // without the field if the provider rejects it.
    if(disableReasoning) payload.reasoning_effort='none';

    const res=await fetch(`${base(provider)}/chat/completions`,{
      method:'POST',
      headers:openAICompatibleHeaders(provider),
      body:JSON.stringify(payload),
    });

    const rawText=await res.text();
    let body:any=rawText;
    try{body=rawText ? JSON.parse(rawText) : {}}catch{}

    return {res,body};
  }

  let attemptResult=await attempt(true);

  // Some OpenAI-compatible providers reject Ollama/OpenAI reasoning controls.
  // Fall back to a plain request instead of incorrectly declaring chat broken.
  if(!attemptResult.res.ok && [400,404,422].includes(attemptResult.res.status)){
    attemptResult=await attempt(false);
  }

  const {res,body}=attemptResult;
  if(!res.ok){
    throw new Error(
      `PROVIDER_HTTP_${res.status}:${
        typeof body==='string' ? body : JSON.stringify(body)
      }`
    );
  }

  const choice=body?.choices?.[0];
  const message=choice?.message;
  const content=assistantText(message);
  const reasoning=
    typeof message?.reasoning==='string'
      ? message.reasoning.trim()
      : '';

  return {
    pass:content.length>0,
    expectedTokenMatch:content.includes('DOOM_OK'),
    content,
    reasoningPresent:reasoning.length>0,
    reasoningLength:reasoning.length,
    finishReason:choice?.finish_reason ?? null,
    latencyMs:Date.now()-started,
    usage:body?.usage,
    raw:body,
  };
}

export async function toolProbe(provider:any, providerModelId:string) {
  const started=Date.now();
  const res=await fetch(`${base(provider)}/chat/completions`,{
    method:'POST',
    headers:openAICompatibleHeaders(provider),
    body:JSON.stringify({
      model:providerModelId,
      messages:[{
        role:'user',
        content:'Call the doom_probe tool with value DOOM_OK. Do not answer in normal text before calling the tool.'
      }],
      tools:[{
        type:'function',
        function:{
          name:'doom_probe',
          description:'Capability probe used by Doom Core.',
          parameters:{
            type:'object',
            properties:{value:{type:'string'}},
            required:['value'],
            additionalProperties:false,
          },
        },
      }],
      tool_choice:'auto',
      temperature:0,
      stream:false,
    }),
  });
  const body=await jsonResponse(res);
  const calls=body?.choices?.[0]?.message?.tool_calls;
  const call=Array.isArray(calls) ? calls.find((x:any)=>x?.function?.name==='doom_probe') : undefined;
  let args:any={};
  try{args=JSON.parse(call?.function?.arguments ?? '{}')}catch{}
  return {
    pass:Boolean(call) && String(args?.value ?? '').includes('DOOM_OK'),
    toolCall:call ?? null,
    latencyMs:Date.now()-started,
    usage:body?.usage,
    raw:body,
  };
}

export async function mutationProbe(provider:any, providerModelId:string) {
  const started=Date.now();
  const dir=mkdtempSync(join(tmpdir(),'doom-mutation-probe-'));
  const target=join(dir,'test.txt');
  writeFileSync(target,'before','utf8');

  const tools=[
    {
      type:'function',
      function:{
        name:'read_file',
        description:'Read test.txt from the isolated mutation probe workspace.',
        parameters:{
          type:'object',
          properties:{path:{type:'string'}},
          required:['path'],
          additionalProperties:false
        }
      }
    },
    {
      type:'function',
      function:{
        name:'write_file',
        description:'Write test.txt in the isolated mutation probe workspace.',
        parameters:{
          type:'object',
          properties:{
            path:{type:'string'},
            content:{type:'string'}
          },
          required:['path','content'],
          additionalProperties:false
        }
      }
    }
  ];

  const messages:any[]=[
    {
      role:'system',
      content:`You are running a Doom Core mutation capability probe.
You MUST use native function tools.
Do not answer with prose until the real file mutation is complete.`
    },
    {
      role:'user',
      content:`In the isolated workspace, change test.txt content from exactly "before" to exactly "after".
Use read_file if needed, then MUST call write_file.
Do not merely describe the edit.`
    }
  ];

  const observedCalls:any[]=[];

  try{
    for(let step=1;step<=5;step++){
      const requestBody:any={
        model:providerModelId,
        messages,
        tools,
        tool_choice:'required',
        temperature:0,
        stream:false
      };

      let res=await fetch(`${base(provider)}/chat/completions`,{
        method:'POST',
        headers:openAICompatibleHeaders(provider),
        body:JSON.stringify(requestBody)
      });

      // Retry once with auto when the provider does not implement
      // tool_choice=required. The probe still requires an actual write.
      if(!res.ok && [400,404,422].includes(Number(res.status))){
        requestBody.tool_choice='auto';
        res=await fetch(`${base(provider)}/chat/completions`,{
          method:'POST',
          headers:openAICompatibleHeaders(provider),
          body:JSON.stringify(requestBody)
        });
      }

      const body=await jsonResponse(res);
      const message=body?.choices?.[0]?.message ?? {};
      const calls=Array.isArray(message?.tool_calls)?message.tool_calls:[];

      messages.push({
        role:'assistant',
        content:message?.content ?? '',
        tool_calls:calls
      });

      if(!calls.length){
        return {
          pass:false,
          latencyMs:Date.now()-started,
          steps:step,
          finalContent:readFileSync(target,'utf8'),
          observedCalls,
          reason:'MODEL_RETURNED_FINAL_WITHOUT_MUTATION_TOOL',
          raw:body
        };
      }

      for(const call of calls){
        const name=String(call?.function?.name ?? '');
        let args:any={};
        try{args=JSON.parse(call?.function?.arguments ?? '{}')}catch{}

        observedCalls.push({step,name,args});

        const requested=String(args?.path ?? '').replace(/\\/g,'/').replace(/^\.\//,'');
        let result:any;

        if(requested!=='test.txt'){
          result={ok:false,error:'PROBE_PATH_FORBIDDEN'};
        }else if(name==='read_file'){
          result={
            ok:true,
            path:'test.txt',
            content:readFileSync(target,'utf8')
          };
        }else if(name==='write_file'){
          writeFileSync(target,String(args?.content ?? ''),'utf8');
          result={
            ok:true,
            path:'test.txt',
            bytes:Buffer.byteLength(String(args?.content ?? ''),'utf8')
          };
        }else{
          result={ok:false,error:'PROBE_TOOL_UNKNOWN'};
        }

        messages.push({
          role:'tool',
          tool_call_id:String(call?.id ?? `probe_${step}_${name}`),
          name,
          content:JSON.stringify(result)
        });
      }

      const content=readFileSync(target,'utf8');
      if(content==='after'){
        return {
          pass:true,
          latencyMs:Date.now()-started,
          steps:step,
          finalContent:content,
          observedCalls
        };
      }
    }

    return {
      pass:false,
      latencyMs:Date.now()-started,
      steps:5,
      finalContent:readFileSync(target,'utf8'),
      observedCalls,
      reason:'MUTATION_NOT_COMPLETED_WITHIN_TOOL_LOOP'
    };
  }finally{
    rmSync(dir,{recursive:true,force:true});
  }
}
