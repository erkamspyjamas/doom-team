import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';

type CommandResult = {
  exitCode:number|null;
  stdout:string;
  stderr:string;
};

function run(program:string,args:string[],cwd:string,timeoutMs=30_000):Promise<CommandResult>{
  return new Promise((resolve,reject)=>{
    const child=spawn(program,args,{cwd,env:{...process.env,GIT_TERMINAL_PROMPT:'0'}});
    let stdout='';
    let stderr='';
    const timer=setTimeout(()=>{
      child.kill('SIGKILL');
      reject(new Error(`COMMAND_TIMEOUT:${program} ${args.join(' ')}`));
    },timeoutMs);
    child.stdout.on('data',d=>stdout+=String(d));
    child.stderr.on('data',d=>stderr+=String(d));
    child.on('error',err=>{clearTimeout(timer);reject(err)});
    child.on('close',code=>{clearTimeout(timer);resolve({exitCode:code,stdout,stderr})});
  });
}

async function git(cwd:string,args:string[],timeoutMs=30_000){
  const out=await run('git',args,cwd,timeoutMs);
  if(out.exitCode!==0){
    throw new Error(`GIT_FAILED:${args.join(' ')}:${(out.stderr||out.stdout).trim()}`);
  }
  return out.stdout.trim();
}

function safeSegment(value:string){
  return String(value).replace(/^.*?_/, '').replace(/[^a-zA-Z0-9._-]+/g,'-').slice(0,48) || 'item';
}

export async function inspectGitWorkspace(workspace:string){
  const top=await git(workspace,['rev-parse','--show-toplevel']);
  const head=await git(workspace,['rev-parse','HEAD']);
  const branch=await git(workspace,['rev-parse','--abbrev-ref','HEAD']);
  const status=await git(workspace,['status','--porcelain=v1']);
  const diffStat=await git(workspace,['diff','--stat']);
  const cachedStat=await git(workspace,['diff','--cached','--stat']);
  return {top,head,branch,status,diffStat,cachedStat,clean:status.trim()===''};
}

export async function ensureMissionIntegrationWorkspace(input:{
  missionId:string;
  baseWorkspace:string;
  worktreeRoot:string;
}){
  const base=await inspectGitWorkspace(input.baseWorkspace);
  if(base.top!==input.baseWorkspace){
    // Resolve through git's canonical top-level path. We still require the configured
    // workspace to point at repository root for predictable worktree behavior.
    throw new Error(`BASE_WORKSPACE_NOT_REPO_ROOT:${base.top}`);
  }
  if(!base.clean){
    throw new Error(`BASE_WORKSPACE_NOT_CLEAN:${base.status.replace(/\n/g,' | ')}`);
  }

  const short=safeSegment(input.missionId);
  const missionRoot=join(input.worktreeRoot,short);
  const integrationWorkspace=join(missionRoot,'integration');
  const integrationBranch=`doom/mission/${short}`;

  mkdirSync(missionRoot,{recursive:true});

  if(existsSync(integrationWorkspace)){
    const current=await inspectGitWorkspace(integrationWorkspace);
    return {
      baseCommit:base.head,
      integrationWorkspace,
      integrationBranch:current.branch,
      integrationCommit:current.head,
      reused:true,
    };
  }

  const branchCheck=await run('git',['show-ref','--verify','--quiet',`refs/heads/${integrationBranch}`],input.baseWorkspace);
  if(branchCheck.exitCode===0){
    await git(input.baseWorkspace,['worktree','add',integrationWorkspace,integrationBranch],60_000);
  }else{
    await git(input.baseWorkspace,['worktree','add','-b',integrationBranch,integrationWorkspace,base.head],60_000);
  }

  const integration=await inspectGitWorkspace(integrationWorkspace);
  return {
    baseCommit:base.head,
    integrationWorkspace,
    integrationBranch,
    integrationCommit:integration.head,
    reused:false,
  };
}

export async function createTaskWorktree(input:{
  missionId:string;
  taskKey:string;
  attemptNo:number;
  integrationWorkspace:string;
  integrationBranch:string;
  worktreeRoot:string;
}){
  const missionShort=safeSegment(input.missionId);
  const taskShort=safeSegment(input.taskKey);
  const attempt=`a${input.attemptNo}`;
  const taskRoot=join(input.worktreeRoot,missionShort,'tasks');
  const workspace=join(taskRoot,`${taskShort}-${attempt}`);
  const branch=`doom/task/${missionShort}/${taskShort}-${attempt}`;

  mkdirSync(taskRoot,{recursive:true});
  if(existsSync(workspace)){
    const state=await inspectGitWorkspace(workspace);
    return {workspace,branch:state.branch,baseCommit:state.head,reused:true};
  }

  const integration=await inspectGitWorkspace(input.integrationWorkspace);
  await git(input.integrationWorkspace,['worktree','add','-b',branch,workspace,integration.head],60_000);
  return {workspace,branch,baseCommit:integration.head,reused:false};
}

export async function taskWorkspaceEvidence(workspace:string){
  // Default porcelain output collapses an entirely untracked directory to
  // `?? directory/`, which hides the concrete artifact path from acceptance
  // checks. Keep the existing porcelain shape while asking Git to enumerate
  // every untracked file.
  const status=await git(workspace,['status','--porcelain=v1','--untracked-files=all']);
  const diffStat=await git(workspace,['diff','--stat']);
  const diff=await git(workspace,['diff','--no-ext-diff']);
  const untracked=await git(workspace,['ls-files','--others','--exclude-standard']);

  const changedPaths=status
    .split(/\r?\n/)
    .map(line=>line.trimEnd())
    .filter(Boolean)
    .map(line=>{
      const raw=line.length>=4 ? line.slice(3).trim() : line.trim();
      const renameTarget=raw.includes(' -> ') ? raw.split(' -> ').pop()! : raw;
      return renameTarget.replace(/^"(.*)"$/,'$1');
    })
    .filter(Boolean);

  return {
    status,
    diffStat,
    diff:diff.slice(0,60_000),
    changedPaths:[...new Set(changedPaths)].slice(0,300),
    untracked:untracked.split(/\r?\n/).filter(Boolean).slice(0,200),
    clean:status.trim()==='',
  };
}

export async function commitAndMergeTask(input:{
  taskKey:string;
  title:string;
  workspace:string;
  branch:string;
  integrationWorkspace:string;
}){
  const before=await taskWorkspaceEvidence(input.workspace);
  if(before.clean){
    return {
      changed:false,
      commitHash:null,
      mergeCommit:null,
      diffStat:'',
      status:'',
    };
  }

  await git(input.workspace,['add','-A']);
  const stagedCheck=await run('git',['diff','--cached','--quiet'],input.workspace);
  if(stagedCheck.exitCode===0){
    return {
      changed:false,
      commitHash:null,
      mergeCommit:null,
      diffStat:before.diffStat,
      status:before.status,
    };
  }

  const message=`doom(task): ${input.taskKey} ${input.title}`.slice(0,160);
  await git(input.workspace,['commit','-m',message],60_000);
  const commitHash=await git(input.workspace,['rev-parse','HEAD']);

  const integrationStatus=await git(input.integrationWorkspace,['status','--porcelain=v1']);
  if(integrationStatus.trim()){
    throw new Error(`INTEGRATION_WORKTREE_DIRTY:${integrationStatus.replace(/\n/g,' | ')}`);
  }

  const mergeMessage=`doom(merge): ${input.taskKey} ${input.title}`.slice(0,160);
  await git(input.integrationWorkspace,['merge','--no-ff',input.branch,'-m',mergeMessage],60_000);
  const mergeCommit=await git(input.integrationWorkspace,['rev-parse','HEAD']);

  return {
    changed:true,
    commitHash,
    mergeCommit,
    diffStat:before.diffStat,
    status:before.status,
  };
}

export async function removeTaskWorktree(input:{
  baseWorkspace:string;
  workspace:string;
  branch:string;
}){
  if(existsSync(input.workspace)){
    const result=await run('git',['worktree','remove','--force',input.workspace],input.baseWorkspace,60_000);
    if(result.exitCode!==0){
      // Keep the directory/branch for forensic inspection instead of silently deleting.
      return {removed:false,error:(result.stderr||result.stdout).trim()};
    }
  }

  const del=await run('git',['branch','-D',input.branch],input.baseWorkspace,30_000);
  return {
    removed:true,
    branchDeleted:del.exitCode===0,
    branchDeleteError:del.exitCode===0?null:(del.stderr||del.stdout).trim(),
  };
}


export async function missionIntegrationEvidence(input:{
  baseWorkspace:string;
  integrationWorkspace:string;
  baseCommit:string;
}){
  const base=await inspectGitWorkspace(input.baseWorkspace);
  const integration=await inspectGitWorkspace(input.integrationWorkspace);
  const diffStat=await git(
    input.integrationWorkspace,
    ['diff','--stat',`${input.baseCommit}..${integration.head}`],
    30_000
  );
  const diff=await git(
    input.integrationWorkspace,
    ['diff','--no-ext-diff',`${input.baseCommit}..${integration.head}`],
    60_000
  );

  return {
    baseHead:base.head,
    baseBranch:base.branch,
    baseClean:base.clean,
    integrationHead:integration.head,
    integrationBranch:integration.branch,
    integrationClean:integration.clean,
    diffStat,
    diff:diff.slice(0,80_000),
  };
}

export async function mergeMissionIntegrationToBase(input:{
  missionTitle:string;
  baseWorkspace:string;
  baseCommit:string;
  integrationWorkspace:string;
  integrationBranch:string;
}){
  const base=await inspectGitWorkspace(input.baseWorkspace);
  if(!base.clean){
    throw new Error(`BASE_WORKSPACE_NOT_CLEAN:${base.status.replace(/\n/g,' | ')}`);
  }
  if(base.head!==input.baseCommit){
    throw new Error(`BASE_HEAD_CHANGED:${input.baseCommit}->${base.head}`);
  }

  const integration=await inspectGitWorkspace(input.integrationWorkspace);
  if(!integration.clean){
    throw new Error(`INTEGRATION_WORKTREE_DIRTY:${integration.status.replace(/\n/g,' | ')}`);
  }

  const evidence=await missionIntegrationEvidence({
    baseWorkspace:input.baseWorkspace,
    integrationWorkspace:input.integrationWorkspace,
    baseCommit:input.baseCommit,
  });

  const mergeMessage=`doom(mission): ${input.missionTitle}`.slice(0,180);
  const result=await run(
    'git',
    ['merge','--no-ff',input.integrationBranch,'-m',mergeMessage],
    input.baseWorkspace,
    120_000
  );

  if(result.exitCode!==0){
    await run('git',['merge','--abort'],input.baseWorkspace,30_000).catch(()=>({exitCode:1,stdout:'',stderr:''}));
    throw new Error(`MISSION_MERGE_FAILED:${(result.stderr||result.stdout).trim()}`);
  }

  const after=await inspectGitWorkspace(input.baseWorkspace);
  return {
    ...evidence,
    previousBaseHead:base.head,
    mergeCommit:after.head,
    mergedIntoBranch:after.branch,
  };
}
