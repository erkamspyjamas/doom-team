import assert from 'node:assert/strict';
import test from 'node:test';
import {
  inferTaskWorkMode,
  inferTaskMutationTargetPaths,
  isCompletedStateTaskInstruction,
  resolveTaskWorkModeContract,
  taskHasStrongMutationIntent,
} from '../src/task-contracts.js';

test('QA post-state file wording does not create mutation intent',()=>{
  const task={
    instruction:'Run the repository health-check tests and verify the implementation.',
    expectedOutcome:'scripts/repo-health-check.mjs and tests/repo-health-check.test.mjs are created and verified.',
    work_mode:'validate',
  };

  assert.equal(taskHasStrongMutationIntent(task),false);
  assert.equal(resolveTaskWorkModeContract(task),'validate');
});

test('explicitly read-only validation language is not a mutation action',()=>{
  assert.equal(taskHasStrongMutationIntent({
    instruction:'Run the actual tests and verify the implementation without modifying repository files.',
  }),false);
  assert.equal(taskHasStrongMutationIntent({
    instruction:'Create reports/qa.json without modifying repository source files.',
  }),true);
});

test('completed-state references are not mutation commands',()=>{
  assert.equal(taskHasStrongMutationIntent({
    instruction:'Review the created file and verify its error handling.',
  }),false);
  assert.equal(taskHasStrongMutationIntent({
    instruction:'Oluşturulan dosyayı incele ve gereksinimlere uyumunu doğrula.',
  }),false);
});

test('imperative artifact creation remains a strong mutation action',()=>{
  assert.equal(taskHasStrongMutationIntent({
    instruction:'Create scripts/repo-health-check.mjs.',
  }),true);
  assert.equal(resolveTaskWorkModeContract({
    instruction:'Create scripts/repo-health-check.mjs.',
    workMode:'general',
  }),'modify');
  assert.equal(taskHasStrongMutationIntent({
    instruction:'Implement scripts/tool.mjs.',
  }),true);
  assert.equal(taskHasStrongMutationIntent({
    instruction:'MODIFY README.md.',
  }),true);
  for(const instruction of [
    'Run codegen to generate src/api.ts.',
    'Change README.md.',
    'Fix scripts/tool.mjs.',
    'scripts/tool.mjs dosyasını oluştur ve çalışmasını doğrula.',
    'README.md dosyasını güncelle ve sonucu kontrol et.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
  }
});

test('validation assertions are not mutation commands',()=>{
  for(const instruction of [
    'Verify that scripts/tool.mjs creates valid JSON output.',
    'Review the code that writes JSON files.',
    'Review how to create reports/qa.json safely.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
    assert.equal(
      resolveTaskWorkModeContract({instruction,workMode:'validate'}),
      'validate',
      instruction,
    );
  }

  for(const instruction of [
    'Verify scripts/tool.mjs, then update README.md.',
    'Run tests and create reports/qa.json.',
    'Verify scripts/tool.mjs and update README.md.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
    assert.equal(
      resolveTaskWorkModeContract({instruction,workMode:'validate'}),
      'modify',
      instruction,
    );
  }
});

test('review deliverables are not repository mutations unless they name an artifact path',()=>{
  for(const instruction of [
    'Run tests and write a concise review.',
    'Review code and write findings in the final response.',
    'Inspect the repository and generate a findings summary.',
    'Check README.md and update the ticket with findings.',
    'Verify that the tool is able to create reports/output.json.',
    'Review the command used to generate src/api.ts.',
    'Write a code review.',
    'Generate an implementation review.',
    'Review README.md and write a code-quality report.',
    'README.md dosyasını inceleyip bulguları yaz.',
    'README.md için bir inceleme yaz.',
    'Kod incelemesi yaz.',
    'Test raporu yaz.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
    assert.equal(
      resolveTaskWorkModeContract({instruction,workMode:'validate'}),
      'validate',
      instruction,
    );
  }
  assert.equal(taskHasStrongMutationIntent({
    instruction:'Run tests and write reports/qa.json.',
  }),true);
});

test('explicit and shared-object mutation actions remain mutations',()=>{
  for(const instruction of [
    'Review code, fix src/tool.ts.',
    'Inspect implementation, update README.md as needed.',
    'Create and test scripts/tool.mjs.',
    'Implement and validate scripts/tool.mjs.',
    'Create, configure, and test scripts/tool.mjs.',
    'Review and update README.md.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
    assert.equal(
      resolveTaskWorkModeContract({instruction,workMode:'validate'}),
      'modify',
      instruction,
    );
  }
});

test('coordinated prohibitions never become mutation actions or targets',()=>{
  for(const instruction of [
    'Do not create or modify README.md; inspect it.',
    'Never add or update package.json.',
    'Without creating or editing README.md, review the repository.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
    assert.equal(
      resolveTaskWorkModeContract({instruction,workMode:'validate'}),
      'validate',
      instruction,
    );
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[],instruction);
  }
});

test('completed-state guard permits commands that validate a post-state',()=>{
  assert.equal(
    isCompletedStateTaskInstruction('scripts/tool.mjs was created.'),
    true,
  );
  assert.equal(
    isCompletedStateTaskInstruction('README.md güncellendi.'),
    true,
  );
  assert.equal(
    isCompletedStateTaskInstruction('Verify that scripts/tool.mjs was created and works.'),
    false,
  );
  assert.equal(
    isCompletedStateTaskInstruction('README.md güncellendi mi kontrol et.'),
    false,
  );
  assert.equal(
    isCompletedStateTaskInstruction('Run tests and verify that scripts/tool.mjs was created.'),
    false,
  );
  assert.equal(
    isCompletedStateTaskInstruction('Run tests to confirm scripts/tool.mjs was created.'),
    false,
  );
  for(const assertion of [
    'Test file was created.',
    'Review was completed.',
    'Check was completed.',
    'Run was completed.',
  ]){
    assert.equal(isCompletedStateTaskInstruction(assertion),true,assertion);
  }
});

test('mutation targets exclude inspected inputs and technology labels',()=>{
  const targets=inferTaskMutationTargetPaths({
    instruction:'scripts/repo-health-check.mjs dosyasını oluştur; README.md varlığını, Git repo durumunu ve çalışma dizininin temiz/dirty olup olmadığını kontrol eden JSON çıktı üreten bir Node.js modülü yaz.',
    expectedOutcome:'scripts/repo-health-check.mjs dosyası oluşturulmuş ve çalıştırılabilir durumda olmalı.',
    acceptanceCriteria:[
      'Dosya scripts/repo-health-check.mjs olarak mevcut olmalı.',
      'Dosya sadece Node.js yerleşik modülleri kullanmalı.',
    ],
  });

  assert.deepEqual(targets,['scripts/repo-health-check.mjs']);

  assert.deepEqual(inferTaskMutationTargetPaths({
    instruction:'scripts/repo-health-check.mjs dosyasını oluştur; README.md varlığını kontrol eden bir Node.js modülü yaz.',
    expectedOutcome:'scripts/repo-health-check.mjs oluşturulmuş olmalı.',
    acceptanceCriteria:['scripts/repo-health-check.mjs mevcut olmalı.'],
  }),['scripts/repo-health-check.mjs']);

  for(const instruction of [
    'Create scripts/tool.mjs that validates README.md.',
    'Create scripts/tool.mjs that reads README.md.',
    'Create scripts/tool.mjs to check README.md.',
  ]){
    assert.deepEqual(
      inferTaskMutationTargetPaths({instruction}),
      ['scripts/tool.mjs'],
      instruction,
    );
  }

  for(const [instruction,target] of [
    ['Create tests/tool.test.mjs for scripts/tool.mjs.','tests/tool.test.mjs'],
    ['Create scripts/tool.mjs using config.json.','scripts/tool.mjs'],
    ['Create scripts/tool.mjs based on config.json.','scripts/tool.mjs'],
  ]){
    assert.deepEqual(
      inferTaskMutationTargetPaths({instruction}),
      [target],
      instruction,
    );
  }

  for(const [instruction,targets] of [
    [
      'Create scripts/tool.mjs using README.md and config.json.',
      ['scripts/tool.mjs'],
    ],
    [
      'Create tests/tool.test.mjs for scripts/tool.mjs and README.md.',
      ['tests/tool.test.mjs'],
    ],
    [
      'config.json dosyasını kullanarak scripts/tool.mjs oluştur.',
      ['scripts/tool.mjs'],
    ],
    [
      'tests/tool.test.mjs dosyasını scripts/tool.mjs için oluştur.',
      ['tests/tool.test.mjs'],
    ],
    [
      'Update package.json without changing README.md.',
      ['package.json'],
    ],
  ] as const){
    assert.deepEqual(
      inferTaskMutationTargetPaths({instruction}),
      [...targets],
      instruction,
    );
  }

  assert.deepEqual(inferTaskMutationTargetPaths({
    instruction:'Create scripts/tool.mjs; then update README.md.',
  }),['scripts/tool.mjs','README.md']);
});

test('validation prose with referenced paths stays read-only',()=>{
  for(const instruction of [
    'Write a review of README.md.',
    'Generate findings about src/tool.ts.',
    'Create a summary of config.json.',
    'Write findings about src/tool.ts in the final response.',
    'Run tests and write a concise review of src/tool.ts.',
    'Generate a review of src/tool.ts.',
    'Test the tool ability to create reports/output.json.',
    'Test whether the tool is able to generate reports/output.json.',
    'Explain how to update README.md.',
    'Analyze how to fix scripts/tool.mjs.',
    'Create a plan to update README.md.',
    'README.md incelemesi yaz.',
    'src/tool.ts hakkında bulguları yaz.',
    'config.json ile ilgili bir rapor yaz.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
    assert.equal(
      resolveTaskWorkModeContract({instruction,workMode:'validate'}),
      'validate',
      instruction,
    );
  }
});

test('generic shared objects and anaphoric artifacts retain mutation intent',()=>{
  for(const instruction of [
    'Create and test the repository health-check tool.',
    'Implement and validate the feature.',
    'Configure and test the module.',
    'Create, test, and document the API endpoint.',
    'Inspect README.md; update it if needed.',
    'Review src/tool.ts, then fix it.',
    'Check config.json. Modify it to satisfy requirements.',
    'Review src/tool.ts and fix it.',
    'Inspect README.md, then update it.',
    'README.md dosyasını incele; gerekirse güncelle.',
    'src/tool.ts dosyasını kontrol et ve gerekirse düzelt.',
    'Patch src/tool.ts.',
    'Replace config.json.',
    'Format src/tool.ts.',
    'Run prettier --write src/tool.ts.',
    'scripts/tool.mjs dosyasını üret.',
    'config.json ayarlarını düzelt.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
  }
});

test('negation scope covers coordinated actions and their objects',()=>{
  for(const instruction of [
    'Do not create README.md or modify package.json.',
    'Without creating README.md or editing package.json.',
    'Never update README.md and delete config.json.',
    'Do not modify README.md or create new files; review the diff.',
    'Without modifying README.md or creating files, inspect the repository.',
    'Avoid modifying README.md and review it.',
    'Asla README.md dosyasını sil.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[],instruction);
  }
});

test('completed-state grammar distinguishes commands from durable assertions',()=>{
  assert.equal(isCompletedStateTaskInstruction('Ensure README.md is updated.'),false);
  for(const assertion of [
    'Review task was completed.',
    'Test task was completed.',
    'Check operation was completed.',
    'Run step was completed.',
    'Test execution successfully completed.',
    'Review completed successfully.',
    'README.md zaten güncellenmiş.',
    'README.md güncellenmiştir.',
  ]){
    assert.equal(isCompletedStateTaskInstruction(assertion),true,assertion);
  }
  assert.equal(inferTaskWorkMode({instruction:'VERIFY README.md and report findings.'}),'validate');
  assert.equal(inferTaskWorkMode({instruction:'Ensure README.md is updated.'}),'validate');
});

test('input relation scope excludes source artifacts across path lists',()=>{
  for(const [instruction,targets] of [
    ['Create scripts/tool.mjs without modifying README.md and config.json.',['scripts/tool.mjs']],
    ['Create scripts/tool.mjs with README.md and config.json as inputs.',['scripts/tool.mjs']],
    ['Create tests/tool.test.mjs according to scripts/tool.mjs and README.md.',['tests/tool.test.mjs']],
    ['config.json ve README.md dosyalarını kullanarak scripts/tool.mjs oluştur.',['scripts/tool.mjs']],
    ['README.md ve config.json dosyalarını değiştirmeden scripts/tool.mjs oluştur.',['scripts/tool.mjs']],
    ['Create tests/tool.test.mjs that test scripts/tool.mjs.',['tests/tool.test.mjs']],
    ['Update tests/tool.test.mjs to cover scripts/tool.mjs.',['tests/tool.test.mjs']],
    ['Create src/out.ts that imports config.json.',['src/out.ts']],
    ['Create src/out.ts following config.json.',['src/out.ts']],
    ['Write reports/review.md about src/tool.ts.',['reports/review.md']],
    ['Create docs/design.md describing src/tool.ts.',['docs/design.md']],
    ['Add tests/tool.test.mjs covering src/tool.ts.',['tests/tool.test.mjs']],
  ] as const){
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[...targets],instruction);
  }
});

test('common repository artifact names and relative paths normalize as targets',()=>{
  for(const [instruction,target] of [
    ['Create Dockerfile.','Dockerfile'],
    ['Update Makefile.','Makefile'],
    ['Create .gitignore.','.gitignore'],
    ['Create run.sh.','run.sh'],
    ['Create scripts/run.sh.','scripts/run.sh'],
    ['Create ./src/tool.ts.','src/tool.ts'],
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[target],instruction);
  }
});

test('polite and purpose-led implementation commands retain mutation intent',()=>{
  for(const instruction of [
    'Please create scripts/tool.mjs.',
    'Please update README.md.',
    'Kindly update README.md.',
    'Carefully fix src/tool.ts.',
    'Safely modify config.json.',
    'Either create or update README.md.',
    'Use built-in Node.js modules to create scripts/tool.mjs.',
    'Avoid duplication by refactoring src/tool.ts.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
  }
});

test('Turkish quoted actions and analysis deliverables stay non-mutating',()=>{
  for(const instruction of [
    'README.md güncelle komutunu incele.',
    'scripts/tool.mjs oluştur talimatını doğrula.',
    'config.json sil komutunu kontrol et.',
    'README.md güncelleme planı oluştur.',
    'src/tool.ts için düzeltme önerisi yaz.',
    'config.json değişiklik taslağı oluştur.',
    'README.md için uygulama planı yaz.',
    'scripts/tool.mjs hakkında test senaryoları yaz.',
    'Test kanıtını yaz.',
    'src/tool.ts hakkında bir değerlendirme yaz.',
    'README.md için önerileri yaz.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
  }
});

test('explicit report destinations are repository mutations',()=>{
  for(const [instruction,target] of [
    ['Write findings in reports/qa.md.','reports/qa.md'],
    ['Write findings to reports/qa.md.','reports/qa.md'],
    ['Write a review to reports/review.md.','reports/review.md'],
    ['Generate a report in docs/report.md.','docs/report.md'],
    ['Create a summary in docs/summary.md.','docs/summary.md'],
    ['Document findings in README.md.','README.md'],
    ['Write reports/qa.md with findings.','reports/qa.md'],
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[target],instruction);
  }
});

test('contrast actions and generic artifact anaphora preserve mutation intent',()=>{
  for(const instruction of [
    'Review src/tool.ts but fix it if needed.',
    'Inspect README.md; instead update it.',
    'Verify config.json but update README.md.',
    'Review the code and update it.',
    'Inspect the file and edit it.',
    'Check the tests and update them.',
    'Kodu incele ve düzelt.',
    'README dosyasını kontrol et ve güncelle.',
    'Do not review README.md; update it.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
  }
});

test('additional validation deliverables remain read-only',()=>{
  for(const instruction of [
    'Write a code-quality assessment.',
    'Generate test evidence.',
    'Write recommendations about src/tool.ts.',
    'Write a security assessment of src/tool.ts.',
    'Write feedback about src/tool.ts.',
    'Write a final verdict on src/tool.ts.',
    'Document code-quality feedback about src/tool.ts.',
    'Write test output.',
    'Generate test output.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
  }
});

test('negation placement and auxiliary completed assertions are normalized',()=>{
  for(const instruction of [
    'README.md dosyasını asla sil.',
    'README.md dosyasını sakın sil.',
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),false,instruction);
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[],instruction);
  }

  assert.equal(isCompletedStateTaskInstruction('Make sure README.md is updated.'),false);
  assert.equal(inferTaskWorkMode({instruction:'Make sure README.md is updated.'}),'validate');
  for(const assertion of [
    'Review task has been completed.',
    'Test has been completed.',
    'Check operation is completed.',
    'Run step has been successfully completed.',
    'README.md is complete.',
    'The implementation is finished.',
    'README.md günceldir.',
    'İş tamam.',
  ]){
    assert.equal(isCompletedStateTaskInstruction(assertion),true,assertion);
  }
});

test('copy, apply, and save constructions bind their output targets',()=>{
  for(const [instruction,targets] of [
    ['Copy config.example.json to config.json.',['config.json']],
    ['Copy config.example.json into config.json.',['config.json']],
    ['Apply a patch to src/tool.ts.',['src/tool.ts']],
    ['Save output to reports/qa.json.',['reports/qa.json']],
    ['Save output based on config.json to reports/out.json.',['reports/out.json']],
  ] as const){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[...targets],instruction);
  }
});

test('Turkish safety constraints preserve later positive implementation actions',()=>{
  for(const [instruction,target] of [
    [
      'README.md dosyasını asla değiştirme; scripts/tool.mjs dosyasını oluştur.',
      'scripts/tool.mjs',
    ],
    [
      'README.md dosyasını sakın değiştirme, yalnız scripts/tool.mjs dosyasını oluştur.',
      'scripts/tool.mjs',
    ],
  ]){
    assert.equal(taskHasStrongMutationIntent({instruction}),true,instruction);
    assert.deepEqual(inferTaskMutationTargetPaths({instruction}),[target],instruction);
  }
});
