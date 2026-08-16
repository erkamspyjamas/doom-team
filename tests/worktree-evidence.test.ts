import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { taskWorkspaceEvidence } from '../src/git/worktrees.js';

function git(workspace:string,args:string[]){
  execFileSync('git',args,{
    cwd:workspace,
    encoding:'utf-8',
    env:{...process.env,GIT_TERMINAL_PROMPT:'0'}
  });
}

test('task workspace evidence exposes files inside an untracked directory',async t=>{
  const workspace=mkdtempSync(join(tmpdir(),'doom-worktree-evidence-'));
  t.after(()=>rmSync(workspace,{recursive:true,force:true}));

  git(workspace,['init','--quiet','--initial-branch=main']);
  git(workspace,['config','user.name','Doom Test']);
  git(workspace,['config','user.email','doom-test@example.invalid']);
  writeFileSync(join(workspace,'README.md'),'# Fixture\n');
  git(workspace,['add','README.md']);
  git(workspace,['commit','--quiet','-m','fixture']);

  mkdirSync(join(workspace,'scripts'));
  writeFileSync(
    join(workspace,'scripts','repo-health-check.mjs'),
    'console.log(JSON.stringify({ok:true}));\n'
  );

  const evidence=await taskWorkspaceEvidence(workspace);

  assert.match(evidence.status,/^\?\? scripts\/repo-health-check\.mjs$/m);
  assert.deepEqual(evidence.untracked,['scripts/repo-health-check.mjs']);
  assert.ok(evidence.changedPaths.includes('scripts/repo-health-check.mjs'));
  assert.ok(!evidence.changedPaths.includes('scripts/'));
});
