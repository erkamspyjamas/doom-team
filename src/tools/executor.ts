import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, relative, sep } from 'node:path';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { safeExistingPath, safeWritePath, realWorkspace } from './path-safety.js';
import { resolveToolDefinitions } from './catalog.js';
import type { ToolExecutionContext, ToolExecutionResult } from './types.js';
import { sendAgentMessageFromExecution,executionMessageInbox } from '../communications.js';
import { spawnSpecialistFromExecution,listMissionSpecialists } from '../agent-factory.js';

const MAX_FILE_BYTES = 1024 * 1024;
const MAX_TOOL_OUTPUT = 40_000;

function clip(value: string, limit = MAX_TOOL_OUTPUT) {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n...[truncated ${value.length - limit} chars]`;
}

function normalizeRel(root: string, absolute: string) {
  const rel = relative(root, absolute);
  return rel === '' ? '.' : rel.split(sep).join('/');
}


function sha256Text(value: string) {
  return createHash('sha256').update(value, 'utf-8').digest('hex');
}

async function gitDiffEvidence(
  workspace: string,
  options: { path?: string; cached?: boolean; includeUntracked?: boolean },
  signal?: AbortSignal,
) {
  const root = realWorkspace(workspace);
  const trackedArgs = ['diff'];
  if (options.cached) trackedArgs.push('--cached');

  if (options.path) {
    // Validate the requested path without requiring it to already exist.
    safeWritePath(root, options.path);
    trackedArgs.push('--', options.path);
  }

  const tracked = await runProgram('git', trackedArgs, root, 20_000, {}, signal);

  const untracked: Array<{
    path: string;
    diff: string;
    gitExitCode: number | null;
    sha256?: string;
    bytes?: number;
  }> = [];

  const includeUntracked = !options.cached && options.includeUntracked !== false;
  if (includeUntracked) {
    const lsArgs = ['ls-files', '--others', '--exclude-standard'];
    if (options.path) lsArgs.push('--', options.path);

    const listed = await runProgram('git', lsArgs, root, 20_000, {}, signal);
    const paths = listed.stdout
      .split(/\r?\n/)
      .map(x => x.trim())
      .filter(Boolean)
      .slice(0, 20);

    for (const relativePath of paths) {
      const { path } = safeExistingPath(root, relativePath);
      if (!statSync(path).isFile()) continue;

      const fileSize = statSync(path).size;
      let hash: string | undefined;
      if (fileSize <= MAX_FILE_BYTES) {
        hash = sha256Text(readFileSync(path, 'utf-8'));
      }

      // git diff --no-index returns exit code 1 when a difference is found.
      // That is expected and is evidence, not a tool failure.
      const synthetic = await runProgram(
        'git',
        ['diff', '--no-index', '--no-ext-diff', '--', '/dev/null', relativePath],
        root,
        20_000,
        {},
        signal,
      );

      untracked.push({
        path: relativePath,
        diff: synthetic.stdout || synthetic.stderr,
        gitExitCode: synthetic.exitCode,
        sha256: hash,
        bytes: fileSize,
      });
    }
  }

  return {
    tracked,
    untracked,
    semantics: {
      trackedDiff: options.cached
        ? 'git diff --cached; staged changes only'
        : 'git diff; tracked unstaged changes only',
      untrackedDiff:
        'Untracked files are not shown by normal git diff. Doom synthesizes new-file diffs with git diff --no-index when include_untracked=true.',
    },
  };
}

function runProgram(
  program: string,
  args: string[],
  cwd: string,
  timeoutMs: number,
  extraEnv: Record<string, string> = {},
  abortSignal?: AbortSignal,
): Promise<{ exitCode: number | null; signal: string | null; stdout: string; stderr: string; timedOut: boolean }> {
  return new Promise((resolve, reject) => {
    if(abortSignal?.aborted){
      reject(new Error('EXECUTION_CANCELLED'));
      return;
    }

    const child = spawn(program, args, {
      cwd,
      shell: false,
      windowsHide: true,
      env: { ...process.env, ...extraEnv },
    });

    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let cancelled = false;
    let settled = false;

    const append = (target: 'stdout' | 'stderr', chunk: any) => {
      const str = chunk.toString();
      if (target === 'stdout') stdout = (stdout + str).slice(-MAX_TOOL_OUTPUT * 2);
      else stderr = (stderr + str).slice(-MAX_TOOL_OUTPUT * 2);
    };

    const terminate = () => {
      child.kill('SIGTERM');
      setTimeout(() => {
        if(!settled) child.kill('SIGKILL');
      }, 1200);
    };

    const onAbort = () => {
      cancelled = true;
      terminate();
    };
    abortSignal?.addEventListener('abort', onAbort, {once:true});

    const timer = setTimeout(() => {
      timedOut = true;
      terminate();
    }, timeoutMs);

    child.stdout?.on('data', c => append('stdout', c));
    child.stderr?.on('data', c => append('stderr', c));
    child.on('error', err => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      abortSignal?.removeEventListener('abort', onAbort);
      reject(err);
    });

    child.on('close', (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      abortSignal?.removeEventListener('abort', onAbort);

      if(cancelled){
        reject(new Error('EXECUTION_CANCELLED'));
        return;
      }

      resolve({
        exitCode: code,
        signal,
        stdout: clip(stdout),
        stderr: clip(stderr),
        timedOut,
      });
    });
  });
}

function collectFiles(root: string, current: string, recursive: boolean, maxEntries: number, output: string[]) {
  if (output.length >= maxEntries) return;
  for (const entry of readdirSync(current, { withFileTypes: true })) {
    if (output.length >= maxEntries) break;
    if (entry.name === '.git' || entry.name === 'node_modules' || entry.name === '.venv' || entry.name === 'venv') continue;
    const abs = `${current}${sep}${entry.name}`;
    const rel = normalizeRel(root, abs);
    output.push(entry.isDirectory() ? `${rel}/` : rel);
    if (recursive && entry.isDirectory()) collectFiles(root, abs, true, maxEntries, output);
  }
}

function parseArgs(raw: unknown) {
  if (typeof raw === 'object' && raw !== null) return raw as Record<string, any>;
  if (typeof raw !== 'string' || raw.trim() === '') return {};
  return JSON.parse(raw) as Record<string, any>;
}


function resolveRuntimeSkill(ctx: ToolExecutionContext, ref: string) {
  const skill=ctx.skills.find(s=>s.skillId===ref || s.slug===ref || s.name===ref);
  if(!skill) throw new Error(`SKILL_NOT_BOUND:${ref}`);
  return skill;
}

function safeSkillFilePath(raw:string){
  const path=raw.replace(/\\/g,'/').replace(/^\.\//,'');
  if(!path || path.startsWith('/') || path.split('/').some(p=>p==='..' || p==='')) throw new Error('SKILL_FILE_PATH_INVALID');
  return path;
}

function ensureString(value: unknown, field: string) {
  if (typeof value !== 'string') throw new Error(`INVALID_ARGUMENT:${field}`);
  return value;
}

function ensureSafeCommand(program: string, args: string[]) {
  if (!/^[a-zA-Z0-9._+-]+$/.test(program)) throw new Error('COMMAND_NOT_ALLOWED');

  const allowed = new Set([
    'npm', 'pnpm', 'yarn',
    'python', 'python3', 'pytest',
    'dotnet', 'cargo', 'go',
    'tsc',
  ]);
  if (!allowed.has(program)) throw new Error(`COMMAND_NOT_ALLOWED:${program}`);

  const forbiddenExact = new Set(['sudo', 'su', 'shutdown', 'reboot', 'systemctl', 'mount', 'umount', 'dd', 'mkfs']);
  if (forbiddenExact.has(program)) throw new Error(`COMMAND_NOT_ALLOWED:${program}`);

  for (const arg of args) {
    if (arg.includes('\0')) throw new Error('INVALID_COMMAND_ARGUMENT');
    if (arg.startsWith('/')) throw new Error('ABSOLUTE_ARGUMENT_PATH_BLOCKED');
    if (arg.includes('../') || arg === '..') throw new Error('PARENT_PATH_ARGUMENT_BLOCKED');
  }

  if ((program === 'python' || program === 'python3') && (args.includes('-c') || args.includes('-m') && !['pytest','unittest','compileall'].includes(args[args.indexOf('-m') + 1] ?? ''))) {
    throw new Error('PYTHON_DYNAMIC_CODE_BLOCKED');
  }
  if ((program === 'python' || program === 'python3') && args.length > 0 && args[0] !== '-m') {
    if (args[0] !== 'manage.py' || !['test','check'].includes(args[1] ?? '')) {
      throw new Error('PYTHON_SCRIPT_OPERATION_BLOCKED');
    }
  }
  if (program === 'npm') {
    const op = args[0] ?? '';
    if (!['test', 'run'].includes(op)) throw new Error(`NPM_OPERATION_BLOCKED:${op}`);
  }
  if (program === 'pnpm') {
    const op = args[0] ?? '';
    if (!['test', 'run'].includes(op)) throw new Error(`PNPM_OPERATION_BLOCKED:${op}`);
  }
  if (program === 'yarn') {
    const op = args[0] ?? '';
    if (!['test', 'run'].includes(op)) throw new Error(`YARN_OPERATION_BLOCKED:${op}`);
  }
  if (program === 'dotnet') {
    const op = args[0] ?? '';
    if (!['test', 'build'].includes(op)) throw new Error(`DOTNET_OPERATION_BLOCKED:${op}`);
  }
  if (program === 'cargo') {
    const op = args[0] ?? '';
    if (!['test', 'check', 'build', 'fmt', 'clippy'].includes(op)) throw new Error(`CARGO_OPERATION_BLOCKED:${op}`);
  }
  if (program === 'go') {
    const op = args[0] ?? '';
    if (!['test', 'build', 'vet', 'fmt'].includes(op)) throw new Error(`GO_OPERATION_BLOCKED:${op}`);
  }
}

async function autoRunTests(workspace: string, target?: string, timeoutMs = 120_000, signal?:AbortSignal) {
  const root = realWorkspace(workspace);
  const suffix = target ? [target] : [];

  const pyproject = existsSync(`${root}/pyproject.toml`) ? readFileSync(`${root}/pyproject.toml`, 'utf-8') : '';
  const setupCfg = existsSync(`${root}/setup.cfg`) ? readFileSync(`${root}/setup.cfg`, 'utf-8') : '';
  const hasPytestConfig = existsSync(`${root}/pytest.ini`) || pyproject.includes('[tool.pytest') || setupCfg.includes('[tool:pytest]');
  if (hasPytestConfig) {
    return { runner: 'python -m pytest', ...(await runProgram('python', ['-m', 'pytest', ...suffix], root, timeoutMs, { CI: '1' }, signal)) };
  }
  if (existsSync(`${root}/manage.py`)) {
    return { runner: 'python manage.py test', ...(await runProgram('python', ['manage.py', 'test', ...suffix], root, timeoutMs, { CI: '1' }, signal)) };
  }
  if (existsSync(`${root}/package.json`)) {
    const pkg = JSON.parse(readFileSync(`${root}/package.json`, 'utf-8'));
    if (pkg?.scripts?.test) {
      return { runner: 'npm test', ...(await runProgram('npm', ['test'], root, timeoutMs, { CI: '1' }, signal)) };
    }
  }
  if (readdirSync(root).some(x => x.endsWith('.sln') || x.endsWith('.csproj'))) {
    return { runner: 'dotnet test', ...(await runProgram('dotnet', ['test'], root, timeoutMs, { CI: '1' }, signal)) };
  }
  if (existsSync(`${root}/Cargo.toml`)) {
    return { runner: 'cargo test', ...(await runProgram('cargo', ['test'], root, timeoutMs, { CI: '1' }, signal)) };
  }
  if (existsSync(`${root}/go.mod`)) {
    return { runner: 'go test ./...', ...(await runProgram('go', ['test', './...'], root, timeoutMs, { CI: '1' }, signal)) };
  }
  throw new Error('NO_TEST_RUNNER_DETECTED');
}

export async function executeTool(
  grants: string[],
  toolName: string,
  rawArgs: unknown,
  ctx: ToolExecutionContext,
): Promise<ToolExecutionResult> {
  if(ctx.signal?.aborted) throw new Error('EXECUTION_CANCELLED');
  const allowed = resolveToolDefinitions(grants).find(x => x.name === toolName);
  if (!allowed) {
    ctx.onEvent('tool.denied', { tool: toolName, reason: 'NOT_GRANTED' });
    return { ok: false, error: 'TOOL_NOT_GRANTED' };
  }

  let args: Record<string, any>;
  try {
    args = parseArgs(rawArgs);
  } catch (err: any) {
    return { ok: false, error: `INVALID_TOOL_ARGUMENTS:${String(err?.message ?? err)}` };
  }

  ctx.onEvent('tool.started', { tool: toolName, arguments: args, risk: allowed.risk });

  try {
    let output: unknown;

if (toolName === 'request_specialist_agent') {
  output = spawnSpecialistFromExecution(
    ctx.executionId,
    ctx.context,
    args
  );
  ctx.onEvent('agent_factory.specialist_requested',{
    requestId:(output as any)?.request?.id,
    agentId:(output as any)?.specialist?.agent_id,
    name:(output as any)?.agent?.name
  });
} else if (toolName === 'list_mission_specialists') {
  const missionId=String(ctx.context?.missionId ?? '');
  if(!missionId)throw new Error('AGENT_FACTORY_REQUIRES_MISSION_CONTEXT');
  output={items:listMissionSpecialists(missionId)};
} else if (toolName === 'send_agent_message') {
  output = sendAgentMessageFromExecution(
    ctx.executionId,
    ctx.context,
    args
  );
  ctx.onEvent('message.sent', {
    messageId:(output as any)?.id,
    recipient:(args as any)?.recipient,
    messageType:(args as any)?.message_type ?? 'note'
  });
} else if (toolName === 'list_agent_messages') {
  output = {
    items:executionMessageInbox(
      ctx.executionId,
      ctx.context,
      Math.min(Number((args as any)?.limit ?? 25),50)
    )
  };
} else if (toolName === 'list_skill_files') {
      const ref=ensureString(args.skill,'skill');
      const skill=resolveRuntimeSkill(ctx,ref);
      output={skillId:skill.skillId,slug:skill.slug,versionNo:skill.versionNo,files:Object.keys(skill.files).sort()};
    } else if (toolName === 'read_skill_file') {
      const ref=ensureString(args.skill,'skill');
      const requested=safeSkillFilePath(ensureString(args.path,'path'));
      const skill=resolveRuntimeSkill(ctx,ref);
      const content=skill.files[requested];
      if(typeof content!=='string') throw new Error(`SKILL_FILE_NOT_FOUND:${requested}`);
      const lines=content.split(/\r?\n/);
      const start=Math.max(1,Number(args.start_line ?? 1));
      const end=Math.min(lines.length,Number(args.end_line ?? Math.min(lines.length,start+499)));
      output={skillId:skill.skillId,slug:skill.slug,path:requested,startLine:start,endLine:end,totalLines:lines.length,
        content:lines.slice(start-1,end).map((line,index)=>`${start+index}: ${line}`).join('\n')};
    } else if (toolName === 'list_files') {
      const requested = typeof args.path === 'string' ? args.path : '.';
      const { root, path } = safeExistingPath(ctx.workspace, requested);
      if (!statSync(path).isDirectory()) throw new Error('NOT_A_DIRECTORY');
      const entries: string[] = [];
      collectFiles(root, path, Boolean(args.recursive), Math.min(Number(args.max_entries ?? 200), 500), entries);
      output = { path: requested, entries, truncated: entries.length >= Number(args.max_entries ?? 200) };
    } else if (toolName === 'read_file') {
      const requested = ensureString(args.path, 'path');
      const { path } = safeExistingPath(ctx.workspace, requested);
      if (!statSync(path).isFile()) throw new Error('NOT_A_FILE');
      const size = statSync(path).size;
      if (size > MAX_FILE_BYTES) throw new Error(`FILE_TOO_LARGE:${size}`);
      const lines = readFileSync(path, 'utf-8').split(/\r?\n/);
      const start = Math.max(1, Number(args.start_line ?? 1));
      const end = Math.min(lines.length, Number(args.end_line ?? Math.min(lines.length, start + 499)));
      output = {
        path: requested,
        startLine: start,
        endLine: end,
        totalLines: lines.length,
        content: lines.slice(start - 1, end).map((line, index) => `${start + index}: ${line}`).join('\n'),
      };
    } else if (toolName === 'write_file') {
      const requested = ensureString(args.path, 'path');
      const content = ensureString(args.content, 'content');
      if (Buffer.byteLength(content, 'utf-8') > MAX_FILE_BYTES) throw new Error('CONTENT_TOO_LARGE');
      const { path } = safeWritePath(ctx.workspace, requested);
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, content, 'utf-8');
      const bytes = Buffer.byteLength(content, 'utf-8');
      const sha256 = sha256Text(content);
      ctx.onEvent('file.changed', { path: requested, operation: 'write', bytes, sha256 });
      output = { path: requested, bytes, sha256 };
    } else if (toolName === 'replace_in_file') {
      const requested = ensureString(args.path, 'path');
      const oldText = ensureString(args.old_text, 'old_text');
      const newText = ensureString(args.new_text, 'new_text');
      if (!oldText) throw new Error('OLD_TEXT_EMPTY');
      const { path } = safeExistingPath(ctx.workspace, requested);
      const original = readFileSync(path, 'utf-8');
      if (!original.includes(oldText)) throw new Error('OLD_TEXT_NOT_FOUND');
      const next = args.replace_all ? original.split(oldText).join(newText) : original.replace(oldText, newText);
      if (Buffer.byteLength(next, 'utf-8') > MAX_FILE_BYTES) throw new Error('CONTENT_TOO_LARGE');
      writeFileSync(path, next, 'utf-8');
      const count = args.replace_all ? original.split(oldText).length - 1 : 1;
      const bytes = Buffer.byteLength(next, 'utf-8');
      const sha256 = sha256Text(next);
      ctx.onEvent('file.changed', { path: requested, operation: 'replace', replacements: count, bytes, sha256 });
      output = { path: requested, replacements: count, bytes, sha256 };
    } else if (toolName === 'git_status') {
      const root = realWorkspace(ctx.workspace);
      output = await runProgram('git', ['status', '--short', '--branch'], root, 20_000, {}, ctx.signal);
    } else if (toolName === 'git_diff') {
      output = await gitDiffEvidence(ctx.workspace, {
        path: typeof args.path === 'string' ? args.path : undefined,
        cached: Boolean(args.cached),
        includeUntracked: args.include_untracked === undefined ? true : Boolean(args.include_untracked),
      }, ctx.signal);
    } else if (toolName === 'run_tests') {
      output = await autoRunTests(ctx.workspace, typeof args.target === 'string' ? args.target : undefined, Math.min(Number(args.timeout_ms ?? 120000), 180000), ctx.signal);
      ctx.onEvent('test.completed', output);
    } else if (toolName === 'run_command') {
      const program = ensureString(args.program, 'program');
      const commandArgs = Array.isArray(args.args) ? args.args.map((x:any) => String(x)) : [];
      ensureSafeCommand(program, commandArgs);
      output = await runProgram(program, commandArgs, realWorkspace(ctx.workspace), Math.min(Number(args.timeout_ms ?? 60_000), 120_000), { CI: '1' }, ctx.signal);
    } else {
      throw new Error('TOOL_NOT_IMPLEMENTED');
    }

    ctx.onEvent('tool.completed', { tool: toolName, output });
    return { ok: true, output };
  } catch (err: any) {
    const error = String(err?.message ?? err);
    if(ctx.signal?.aborted || error==='EXECUTION_CANCELLED'){
      ctx.onEvent('tool.cancelled', { tool: toolName });
      throw new Error('EXECUTION_CANCELLED');
    }
    ctx.onEvent('tool.failed', { tool: toolName, error });
    return { ok: false, error };
  }
}
