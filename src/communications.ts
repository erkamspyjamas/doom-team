import { db, id, now, parseJson } from './db/index.js';
import { missionAgentIsActive } from './agent-factory.js';

export type MessageSenderType='boss'|'lead'|'agent'|'system';
export type MessageRecipientType='mission'|'task'|'execution'|'agent'|'boss';
export type MessageType='directive'|'handoff'|'question'|'answer'|'note'|'status';

function executionRow(executionId:string){
  return db.prepare(`SELECT * FROM executions WHERE id=?`).get(executionId) as any;
}
function missionRow(missionId:string){
  return db.prepare(`SELECT * FROM missions WHERE id=?`).get(missionId) as any;
}
function taskRow(taskId:string){
  return db.prepare(`SELECT * FROM mission_tasks WHERE id=?`).get(taskId) as any;
}

function decorateMessage(row:any){
  return row ? {...row,structured:parseJson(row.structured_json,{})} : null;
}

export function publishMessage(input:{
  missionId?:string|null;
  taskId?:string|null;
  executionId?:string|null;
  senderType:MessageSenderType|string;
  senderAgentId?:string|null;
  recipientType:MessageRecipientType|string;
  recipientId:string;
  messageType?:MessageType|string;
  priority?:string;
  content:string;
  structured?:Record<string,unknown>;
  status?:string;
}){
  const content=String(input.content ?? '').trim();
  if(!content)throw new Error('MESSAGE_CONTENT_REQUIRED');

  const messageId=id('msg');
  const t=now();
  db.prepare(`
    INSERT INTO communication_messages
    (id,mission_id,task_id,execution_id,sender_type,sender_agent_id,recipient_type,recipient_id,
     message_type,priority,content,structured_json,status,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    messageId,
    input.missionId ?? null,
    input.taskId ?? null,
    input.executionId ?? null,
    String(input.senderType),
    input.senderAgentId ?? null,
    String(input.recipientType),
    String(input.recipientId),
    String(input.messageType ?? 'note'),
    String(input.priority ?? 'normal'),
    content,
    JSON.stringify(input.structured ?? {}),
    String(input.status ?? 'pending'),
    t
  );
  return getMessage(messageId);
}

export function getMessage(messageId:string){
  return decorateMessage(
    db.prepare(`SELECT * FROM communication_messages WHERE id=?`).get(messageId)
  );
}

export function listMessages(input:{
  missionId?:string;
  taskId?:string;
  executionId?:string;
  recipientType?:string;
  recipientId?:string;
  limit?:number;
}={}){
  const where:string[]=[];
  const args:any[]=[];

  if(input.missionId){where.push('cm.mission_id=?');args.push(input.missionId)}
  if(input.taskId){where.push('cm.task_id=?');args.push(input.taskId)}
  if(input.executionId){where.push('cm.execution_id=?');args.push(input.executionId)}
  if(input.recipientType){where.push('cm.recipient_type=?');args.push(input.recipientType)}
  if(input.recipientId){where.push('cm.recipient_id=?');args.push(input.recipientId)}

  const limit=Math.max(1,Math.min(500,Number(input.limit ?? 150)));
  const rows=db.prepare(`
    SELECT cm.*,
      sa.name AS sender_agent_name
    FROM communication_messages cm
    LEFT JOIN agents sa ON sa.id=cm.sender_agent_id
    ${where.length?'WHERE '+where.join(' AND '):''}
    ORDER BY cm.created_at DESC
    LIMIT ?
  `).all(...args,limit) as any[];

  return rows.map(decorateMessage);
}

export function taskInboxMessages(missionId:string,taskId:string,agentId:string){
  return (db.prepare(`
    SELECT cm.*,sa.name AS sender_agent_name
    FROM communication_messages cm
    LEFT JOIN agents sa ON sa.id=cm.sender_agent_id
    WHERE cm.mission_id=?
      AND (
        (cm.recipient_type='mission' AND cm.recipient_id=?)
        OR (cm.recipient_type='task' AND cm.recipient_id=?)
        OR (cm.recipient_type='agent' AND cm.recipient_id=?)
      )
      AND cm.status <> 'cancelled'
    ORDER BY cm.created_at
    LIMIT 100
  `).all(missionId,missionId,taskId,agentId) as any[]).map(decorateMessage);
}

function activeExecutionForTask(taskId:string){
  return db.prepare(`
    SELECT e.id
    FROM mission_tasks mt
    JOIN executions e ON e.id=mt.execution_id
    WHERE mt.id=? AND mt.status IN ('running','validating') AND e.status='running'
    LIMIT 1
  `).get(taskId) as any;
}

function activeExecutionForAgent(missionId:string,agentId:string){
  return db.prepare(`
    SELECT e.id,mt.id AS task_id
    FROM mission_tasks mt
    JOIN executions e ON e.id=mt.execution_id
    WHERE mt.mission_id=? AND mt.assigned_agent_id=?
      AND mt.status IN ('running','validating') AND e.status='running'
    ORDER BY mt.started_at DESC
    LIMIT 1
  `).get(missionId,agentId) as any;
}

export function queueExecutionDirective(input:{
  executionId:string;
  missionId?:string|null;
  taskId?:string|null;
  senderType:string;
  senderAgentId?:string|null;
  content:string;
  messageId?:string|null;
}){
  const execution=executionRow(input.executionId);
  if(!execution)throw new Error('EXECUTION_NOT_FOUND');
  if(execution.status!=='running')throw new Error(`EXECUTION_NOT_RUNNING:${execution.status}`);

  const directiveId=id('directive');
  const t=now();
  db.prepare(`
    INSERT INTO execution_directives
    (id,execution_id,mission_id,task_id,message_id,sender_type,sender_agent_id,content,status,created_at)
    VALUES (?,?,?,?,?,?,?,?,'pending',?)
  `).run(
    directiveId,
    input.executionId,
    input.missionId ?? null,
    input.taskId ?? null,
    input.messageId ?? null,
    input.senderType,
    input.senderAgentId ?? null,
    String(input.content).trim(),
    t
  );

  db.prepare(`
    INSERT INTO events(execution_id,event_type,payload_json,created_at)
    VALUES (?,?,?,?)
  `).run(
    input.executionId,
    'directive.queued',
    JSON.stringify({directiveId,senderType:input.senderType,messageId:input.messageId ?? null}),
    t
  );

  return db.prepare(`SELECT * FROM execution_directives WHERE id=?`).get(directiveId);
}

export function pullExecutionDirectives(executionId:string){
  return db.transaction(()=>{
    const rows=db.prepare(`
      SELECT * FROM execution_directives
      WHERE execution_id=? AND status='pending'
      ORDER BY created_at,id
      LIMIT 25
    `).all(executionId) as any[];

    if(!rows.length)return [];

    const t=now();
    const update=db.prepare(`
      UPDATE execution_directives SET status='delivered',delivered_at=? WHERE id=? AND status='pending'
    `);
    const markMessage=db.prepare(`
      UPDATE communication_messages
      SET status='delivered',delivered_at=COALESCE(delivered_at,?)
      WHERE id=?
    `);

    for(const row of rows){
      update.run(t,row.id);
      if(row.message_id)markMessage.run(t,row.message_id);
    }

    return rows.map(row=>({
      id:row.id,
      content:row.content,
      senderType:row.sender_type,
      senderAgentId:row.sender_agent_id,
      createdAt:row.created_at,
    }));
  })();
}

export function listExecutionDirectives(executionId:string,limit=100){
  return db.prepare(`
    SELECT * FROM execution_directives
    WHERE execution_id=?
    ORDER BY created_at DESC LIMIT ?
  `).all(executionId,Math.max(1,Math.min(500,limit)));
}

export function sendBossMessage(input:{
  missionId:string;
  recipientType:string;
  recipientId:string;
  content:string;
  priority?:string;
  messageType?:string;
}){
  const mission=missionRow(input.missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  let taskId:string|null=null;
  let executionId:string|null=null;

  if(input.recipientType==='task'){
    const task=taskRow(input.recipientId);
    if(!task || task.mission_id!==input.missionId)throw new Error('MISSION_TASK_NOT_FOUND');
    taskId=task.id;
  }else if(input.recipientType==='execution'){
    const execution=executionRow(input.recipientId);
    if(!execution)throw new Error('EXECUTION_NOT_FOUND');
    executionId=execution.id;
    const task:any=db.prepare(`SELECT id FROM mission_tasks WHERE mission_id=? AND execution_id=?`).get(input.missionId,execution.id);
    taskId=task?.id ?? null;
  }else if(input.recipientType==='agent'){
    const member:any=db.prepare(`
      SELECT 1 ok FROM team_members WHERE team_id=? AND agent_id=?
    `).get(mission.team_id,input.recipientId);
    if(!member && !missionAgentIsActive(input.missionId,input.recipientId)){
      throw new Error('RECIPIENT_AGENT_NOT_IN_MISSION_ROSTER');
    }
  }else if(input.recipientType!=='mission'){
    throw new Error(`RECIPIENT_TYPE_UNSUPPORTED:${input.recipientType}`);
  }

  const message=publishMessage({
    missionId:input.missionId,
    taskId,
    executionId,
    senderType:'boss',
    recipientType:input.recipientType,
    recipientId:input.recipientId,
    messageType:input.messageType ?? 'directive',
    priority:input.priority ?? 'normal',
    content:input.content,
    structured:{source:'control-center'}
  });

  const targets=new Set<string>();

  if(input.recipientType==='execution'){
    targets.add(input.recipientId);
  }else if(input.recipientType==='task'){
    const active=activeExecutionForTask(input.recipientId);
    if(active?.id)targets.add(String(active.id));
  }else if(input.recipientType==='agent'){
    const active=activeExecutionForAgent(input.missionId,input.recipientId);
    if(active?.id)targets.add(String(active.id));
  }else if(input.recipientType==='mission'){
    const rows=db.prepare(`
      SELECT e.id
      FROM mission_tasks mt
      JOIN executions e ON e.id=mt.execution_id
      WHERE mt.mission_id=? AND mt.status IN ('running','validating') AND e.status='running'
    `).all(input.missionId) as any[];
    for(const row of rows)targets.add(String(row.id));
  }

  const directives:any[]=[];
  for(const target of targets){
    directives.push(queueExecutionDirective({
      executionId:target,
      missionId:input.missionId,
      taskId,
      senderType:'boss',
      content:input.content,
      messageId:message.id,
    }));
  }

  return {message,directives};
}

export function steerExecution(
  executionId:string,
  content:string,
  senderType='boss',
  senderAgentId:string|null=null,
){
  const execution=executionRow(executionId);
  if(!execution)throw new Error('EXECUTION_NOT_FOUND');
  const task:any=db.prepare(`
    SELECT mt.id,mt.mission_id FROM mission_tasks mt WHERE mt.execution_id=? LIMIT 1
  `).get(executionId);

  const message=publishMessage({
    missionId:task?.mission_id ?? null,
    taskId:task?.id ?? null,
    executionId,
    senderType,
    senderAgentId,
    recipientType:'execution',
    recipientId:executionId,
    messageType:'directive',
    content,
    structured:{source:'execution-steer'}
  });

  const directive=queueExecutionDirective({
    executionId,
    missionId:task?.mission_id ?? null,
    taskId:task?.id ?? null,
    senderType,
    senderAgentId,
    content,
    messageId:message.id
  });

  return {message,directive};
}

export function publishTaskArtifact(input:{
  missionId:string;
  taskId:string;
  executionId?:string|null;
  artifactType:string;
  name:string;
  path?:string|null;
  contentPreview?:string|null;
  metadata?:Record<string,unknown>;
}){
  const artifactId=id('artifact');
  db.prepare(`
    INSERT INTO task_artifacts
    (id,mission_id,task_id,execution_id,artifact_type,name,path,content_preview,metadata_json,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)
  `).run(
    artifactId,
    input.missionId,
    input.taskId,
    input.executionId ?? null,
    input.artifactType,
    input.name,
    input.path ?? null,
    input.contentPreview ?? null,
    JSON.stringify(input.metadata ?? {}),
    now()
  );
  return getArtifact(artifactId);
}

export function getArtifact(artifactId:string){
  const row:any=db.prepare(`SELECT * FROM task_artifacts WHERE id=?`).get(artifactId);
  return row ? {...row,metadata:parseJson(row.metadata_json,{})} : null;
}

export function taskArtifacts(taskId:string){
  return (db.prepare(`
    SELECT * FROM task_artifacts WHERE task_id=? ORDER BY created_at
  `).all(taskId) as any[]).map(row=>({...row,metadata:parseJson(row.metadata_json,{})}));
}

export function missionArtifacts(missionId:string,limit=300){
  return (db.prepare(`
    SELECT ta.*,mt.task_key,mt.title AS task_title
    FROM task_artifacts ta
    JOIN mission_tasks mt ON mt.id=ta.task_id
    WHERE ta.mission_id=?
    ORDER BY ta.created_at DESC LIMIT ?
  `).all(missionId,Math.max(1,Math.min(1000,limit))) as any[]).map(row=>({
    ...row,metadata:parseJson(row.metadata_json,{})
  }));
}

export function createAutomaticHandoffs(
  missionId:string,
  sourceTaskId:string,
  sourceAgentId:string,
  resultText:string,
){
  const source:any=db.prepare(`
    SELECT * FROM mission_tasks WHERE id=? AND mission_id=?
  `).get(sourceTaskId,missionId);
  if(!source)return [];

  const dependents=db.prepare(`
    SELECT mt.*
    FROM mission_task_dependencies d
    JOIN mission_tasks mt ON mt.id=d.task_id
    WHERE d.depends_on_task_id=?
    ORDER BY mt.sequence_no
  `).all(sourceTaskId) as any[];

  const artifacts=taskArtifacts(sourceTaskId).slice(-25).map(a=>({
    id:a.id,type:a.artifact_type,name:a.name,path:a.path,metadata:a.metadata
  }));

  const created:any[]=[];
  for(const target of dependents){
    created.push(publishMessage({
      missionId,
      taskId:target.id,
      senderType:'agent',
      senderAgentId:sourceAgentId,
      recipientType:'task',
      recipientId:target.id,
      messageType:'handoff',
      priority:'normal',
      content:`${source.task_key} completed by upstream agent.\n\n${String(resultText ?? '').slice(0,6500)}`,
      structured:{
        sourceTaskId,
        sourceTaskKey:source.task_key,
        sourceTitle:source.title,
        targetTaskId:target.id,
        targetTaskKey:target.task_key,
        artifacts,
      }
    }));
  }
  return created;
}

export function taskCommunicationContext(missionId:string,taskId:string,agentId:string){
  const messages=taskInboxMessages(missionId,taskId,agentId);
  if(!messages.length)return 'No additional Doom Message Bus messages.';
  return messages.map((m:any)=>{
    const sender=m.sender_agent_name || m.sender_type;
    return `[${m.message_type.toUpperCase()} | ${sender} | ${m.created_at}]\n${m.content}`;
  }).join('\n\n---\n\n').slice(0,18000);
}

export function prerequisiteArtifactContext(taskId:string){
  const dependencies=db.prepare(`
    SELECT mt.id,mt.task_key,mt.title
    FROM mission_task_dependencies d
    JOIN mission_tasks mt ON mt.id=d.depends_on_task_id
    WHERE d.task_id=?
    ORDER BY mt.sequence_no
  `).all(taskId) as any[];

  const blocks:string[]=[];
  for(const dep of dependencies){
    const artifacts=taskArtifacts(dep.id);
    if(!artifacts.length)continue;
    blocks.push(
      `### ${dep.task_key} - ${dep.title}\n`+
      artifacts.map(a=>
        `- ${a.artifact_type}: ${a.name}`+
        (a.path?` [${a.path}]`:'')+
        (a.content_preview?`\n  ${String(a.content_preview).slice(0,800)}`:'')
      ).join('\n')
    );
  }
  return blocks.length?blocks.join('\n\n'):'No prerequisite artifacts were published.';
}

export function sendAgentMessageFromExecution(
  executionId:string,
  executionContext:Record<string,unknown>|undefined,
  input:any,
){
  const execution=executionRow(executionId);
  if(!execution)throw new Error('EXECUTION_NOT_FOUND');

  const missionId=String(executionContext?.missionId ?? '');
  const taskId=String(executionContext?.taskId ?? '');
  if(!missionId)throw new Error('COMMUNICATION_REQUIRES_MISSION_CONTEXT');

  const mission=missionRow(missionId);
  if(!mission)throw new Error('MISSION_NOT_FOUND');

  const recipient=String(input?.recipient ?? '').trim();
  const content=String(input?.content ?? '').trim();
  const messageType=String(input?.message_type ?? 'note');
  const priority=String(input?.priority ?? 'normal');
  if(!recipient || !content)throw new Error('MESSAGE_RECIPIENT_AND_CONTENT_REQUIRED');

  let recipientType:string;
  let recipientId:string;
  let recipientTaskId:string|null=null;

  if(recipient==='boss'){
    recipientType='boss';
    recipientId='boss';
  }else if(recipient==='lead'){
    recipientType='agent';
    recipientId=mission.lead_agent_id;
  }else if(recipient.startsWith('task:')){
    const key=recipient.slice(5);
    const target:any=db.prepare(`
      SELECT id FROM mission_tasks WHERE mission_id=? AND task_key=?
    `).get(missionId,key);
    if(!target)throw new Error(`MESSAGE_TASK_NOT_FOUND:${key}`);
    recipientType='task';
    recipientId=target.id;
    recipientTaskId=target.id;
  }else if(recipient.startsWith('agent:')){
    const agentId=recipient.slice(6);
    const member:any=db.prepare(`
      SELECT 1 ok FROM team_members WHERE team_id=? AND agent_id=?
    `).get(mission.team_id,agentId);
    if(!member && !missionAgentIsActive(missionId,agentId)){
      throw new Error('MESSAGE_AGENT_NOT_IN_MISSION_ROSTER');
    }
    recipientType='agent';
    recipientId=agentId;
  }else{
    throw new Error('MESSAGE_RECIPIENT_FORMAT_INVALID');
  }

  const message=publishMessage({
    missionId,
    taskId:taskId || recipientTaskId,
    executionId,
    senderType:'agent',
    senderAgentId:execution.agent_id,
    recipientType,
    recipientId,
    messageType,
    priority,
    content,
    structured:{
      sourceExecutionId:executionId,
      sourceTaskId:taskId || null,
      ...(input?.structured && typeof input.structured==='object'?input.structured:{})
    }
  });

  // If the target agent/task is actively executing, promote the message to a live directive.
  if(recipientType==='task'){
    const active=activeExecutionForTask(recipientId);
    if(active?.id){
      queueExecutionDirective({
        executionId:String(active.id),
        missionId,
        taskId:recipientId,
        senderType:'agent',
        senderAgentId:execution.agent_id,
        content:`Message from another Doom agent:\n${content}`,
        messageId:message.id
      });
    }
  }else if(recipientType==='agent'){
    const active=activeExecutionForAgent(missionId,recipientId);
    if(active?.id){
      queueExecutionDirective({
        executionId:String(active.id),
        missionId,
        taskId:active.task_id ?? null,
        senderType:'agent',
        senderAgentId:execution.agent_id,
        content:`Message from another Doom agent:\n${content}`,
        messageId:message.id
      });
    }
  }

  return message;
}

export function executionMessageInbox(
  executionId:string,
  executionContext:Record<string,unknown>|undefined,
  limit=50
){
  const missionId=String(executionContext?.missionId ?? '');
  const taskId=String(executionContext?.taskId ?? '');
  const execution=executionRow(executionId);
  if(!execution)return [];

  if(missionId && taskId){
    return taskInboxMessages(missionId,taskId,execution.agent_id).slice(-limit);
  }

  return listMessages({executionId,limit});
}
