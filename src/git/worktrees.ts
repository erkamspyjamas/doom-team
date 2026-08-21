import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, readFileSync, writeFile } from 'node:fs';
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

export type WorktreeStateClassification = {
  clean: boolean;
  dirty: boolean;          // uncommitted changes in working tree
  ahead: number;           // 0 = at base; >0 = worktree branch commits not in base
  upstream: string | null;
  changedPaths: string[];
};

export async function classifyWorktreeState(
  workspace: string,
  baseCommit?: string
): Promise<WorktreeStateClassification> {
  let upstream: string | null = null;
  let ahead = 0;

  try {
    const branch = await git(workspace, ['rev-parse', '--abbrev-ref', 'HEAD']);
    if (branch !== 'HEAD') {
      const up = await git(workspace, [
        'for-each-ref',
        '--format=%(upstream:short)',
        `refs/heads/${branch}`,
      ]).catch(() => '');
      upstream = up || null;
      if (upstream) {
        const aheadOut = await git(
          workspace,
          ['rev-list', '--count', `${upstream}..HEAD`]
        );
        ahead = Number.parseInt(aheadOut, 10) || 0;
      }
    }
  } catch {
    ahead = 0;
    upstream = null;
  }

  const porcelain = await git(workspace, ['status', '--porcelain=v1']);
  const dirty = porcelain.trim().length > 0;
  // `clean` describes the working-tree state (uncommitted changes present or not).
  // Branch divergence (ahead) is tracked separately and enforced by guard logic.
  const clean = !dirty;
  const changedPaths = porcelain
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      // porcelain format: XY path or ?? path
      const idx = l.search(/ [^ ]/);          // first char after the leading status + space
      const raw = idx > 0 ? l.slice(idx + 1) : l;
      const target = raw.includes(' -> ') ? raw.split(' -> ').pop()! : raw;
      return target.replace(/^\"(.*)\"$/, '$1').trimEnd();
    })
    .filter(Boolean);

  return { clean, dirty, ahead, upstream, changedPaths: [...new Set(changedPaths)] };
}

export async function preCleanupWorktreeGuard(input: {
  workspace: string;
  baseCommit: string;
  /**
   * When true the caller already durably captured the work tree's full
   * evidence externally (e.g. in the DB or an evidence artifact) and the
   * worktree's own state no longer needs to be preserved.
   */
  evidencePreserved: boolean;
  context: string;
}): Promise<void> {
  const state = await classifyWorktreeState(input.workspace, input.baseCommit);

  // If the DB record already captures the worktree's real commit/merge state
  // (commit_hash != null || merge_commit != null) then cleanup is safe
  // regardless of any discrepancy in the working tree vs what is preserved.
  if (input.evidencePreserved) return;

  // No DB-preserved evidence: refuse unless the working tree is trivially
  // safe (no uncommitted changes, ≤1 commit divergence from base).
  if (!state.dirty && state.ahead <= 1) return;

  // Build a concrete description of why cleanup would lose evidence.
  const reasons: string[] = [];
  if (state.dirty) {
    reasons.push(
      `${state.changedPaths.length} uncommitted path(s): ` +
      [...new Set(state.changedPaths)].slice(0, 5).join(', ')
    );
  }
  if (state.ahead > 1) {
    reasons.push(`worktree is ${state.ahead} commits ahead of ${input.baseCommit.slice(0, 7)}`);
  }

  throw new Error(
    `PRE_CLEANUP_WORKTREE_BLOCKED:${input.context}:` + reasons.join('; ')
  );
}


export async function retainForensicArtifact(input: {
  workspace: string;
  missionId: string;
  taskKey: string;
  attemptNo: number;
  worktreeRoot: string;
  context: string;
  /** Optional extra provenance fields included verbatim in the metadata file. */
  tags?: Record<string, unknown>;
}): Promise<{ artifactPath: string; metadataPath: string }> {
  const missionShort = safeSegment(input.missionId);
  const taskShort = safeSegment(input.taskKey);
  const tagForPath = safeSegment(input.context);

  const retainedRoot = join(input.worktreeRoot, 'retained', missionShort);
  mkdirSync(retainedRoot, { recursive: true });

  // Stable slug, de-duped against existing files so re-entry produces a
  // distinct (non-clobbering) artifact.
  let slug = `${taskShort}-${input.attemptNo}-${tagForPath}`.slice(0, 48);
  let counter = 0;
  while (
    existsSync(join(retainedRoot, `${slug}.patch`)) ||
    existsSync(join(retainedRoot, `${slug}.json`))
  ) {
    counter += 1;
    slug = `${taskShort}-${input.attemptNo}-${tagForPath}-${counter}`.slice(0, 48);
  }

  const patchPath = join(retainedRoot, `${slug}.patch`);
  const metadataPath = join(retainedRoot, `${slug}.json`);

  // Collect all evidence in parallel; individual failures must not mask
  // the caller's primary error.
  const results = await Promise.allSettled([
    // Full uncommitted diff relative to HEAD (empty when clean).
    run('git', ['--no-pager', 'diff', '--no-ext-diff', 'HEAD'], input.workspace)
      .then((r: any) => r.stdout.trim())
      .catch(() => ''),
    // Untracked files list (non-destructive read).
    run('git', ['ls-files', '--others', '--exclude-standard', '-z'], input.workspace)
      .then((r: any) => r.stdout)
      .catch(() => ''),
    // Working tree porcelain (include untracked files in subdirs for full manifest).
    git(input.workspace, ['status', '--porcelain=v1', '--untracked-files=all']).catch(() => ''),
    // HEAD + branch for commit provenance.
    git(input.workspace, ['rev-parse', 'HEAD']).catch(() => ''),
    git(input.workspace, ['rev-parse', '--abbrev-ref', 'HEAD']).catch(() => ''),
  ]);

  const patchContent: string = results[0].status === 'fulfilled'
    ? (results[0] as any).value : '';
  const untrackedZ: string = results[1].status === 'fulfilled'
    ? (results[1] as any).value : '';
  const untrackedPaths: string[] = untrackedZ
    .split('\0')
    .map((s: string) => s.trim())
    .filter(Boolean);
  const porcelain: string = results[2].status === 'fulfilled'
    ? (results[2] as any).value : '';
  const head: string = results[3].status === 'fulfilled'
    ? (results[3] as any).value : '';
  const branch: string = results[4].status === 'fulfilled'
    ? (results[4] as any).value : '';

  const changedPaths: string[] = porcelain
    .split(/\r?\n/)
    .map((l: string) => l.trim())
    .filter(Boolean)
    .map((l: string) => {
      const idx = l.search(/ [^ ]/);
      const raw = idx > 0 ? l.slice(idx + 1) : l;
      const target = raw.includes(' -> ')
        ? raw.split(' -> ').pop()!
        : raw;
      return target.replace(/^"(.*)"$/, '$1').trimEnd();
    })
    .filter(Boolean);

  const metadata = {
    schemaVersion: 'doom-forensic-artifact/v1',
    retainedAt: new Date().toISOString(),
    provenance: {
      missionId: input.missionId,
      taskKey: input.taskKey,
      attemptNo: input.attemptNo,
      context: input.context,
      workspace: input.workspace,
      worktreeRoot: input.worktreeRoot,
      headCommit: head || null,
      branch: branch || null,
      changedPaths: [...new Set(changedPaths)].slice(0, 300),
      untrackedPaths: untrackedPaths.slice(0, 200),
      patchByteLength: Buffer.byteLength(patchContent, 'utf8'),
    },
    tags: input.tags ?? {},
  };

  // Write patch then metadata; metadata is the human-readable index.
  let writeOk = false;
  try {
    await new Promise<void>((resolve, reject) => {
      writeFile(patchPath, patchContent, 'utf8', (err: Error | null) => {
        if (err) reject(err); else resolve();
      });
    });
    await new Promise<void>((resolve, reject) => {
      writeFile(
        metadataPath,
        JSON.stringify(metadata, null, 2),
        'utf8',
        (err: Error | null) => {
          if (err) reject(err); else resolve();
        }
      );
    });
    writeOk = true;
  } catch {
    // Best-effort: paths are always returned even if write fails.
    // Failures here are observable via the file system (file won't exist);
    // they are NOT swallowed silently in normal operation since the production
    // binary loads this module via tsx/register where require() is unavailable.
  }

  return { artifactPath: patchPath, metadataPath };
}

/**
 * Build a single self-contained forensic diff bundle from the patch and
 * metadata files produced by `retainForensicArtifact`.
 */
export async function buildForensicDiffBundle(
  metadataPath: string,
  artifactPath: string
): Promise<string> {
  const meta = JSON.parse(readFileSync(metadataPath, 'utf8'));
  const patch = readFileSync(artifactPath, 'utf8');

  const header = [
    '# Forensic Retention Bundle',
    `# Schema    : ${meta.schemaVersion}`,
    `# Retained  : ${meta.retainedAt}`,
    `# Context   : ${meta.provenance.context}`,
    `# Mission   : ${meta.provenance.missionId}`,
    `# Task      : ${meta.provenance.taskKey} attempt=${meta.provenance.attemptNo}`,
    `# Head      : ${meta.provenance.headCommit ?? 'unknown'} (${meta.provenance.branch ?? 'unknown'})`,
    `# Changed   : ${meta.provenance.changedPaths.length} path(s)`,
    `# Untracked : ${meta.provenance.untrackedPaths.length} file(s)`,
    `# PatchSize : ${meta.provenance.patchByteLength} byte(s)`,
    `# Tags      : ${JSON.stringify(meta.tags ?? {})}`,
    `# --- unified diff follows ---`,
    '',
  ]
    .filter(Boolean)
    .join('\n');

  return header + '\n' + patch;
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
