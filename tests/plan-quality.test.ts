import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canonicalizePlanRoleContracts,
  validatePlanRoleObligations,
} from '../src/plan-quality.js';

test('canonicalization repairs an imperfect implementation, QA, and Reviewer DAG',()=>{
  const objective='Create the repository health-check tool and its tests. QA should run the actual tests and verify the implementation. Reviewer performs the final code-quality and requirement review.';
  const roster=[
    {id:'backend',name:'Backend',slug:'backend',role_label:'Backend',priority:20,temporary:false,tools:['read_file','write_file','replace_in_file','run_tests']},
    {id:'qa',name:'QA',slug:'qa',role_label:'QA',priority:80,temporary:false,tools:['read_file','run_tests']},
    {id:'reviewer',name:'Reviewer',slug:'reviewer',role_label:'Reviewer',priority:90,temporary:false,tools:['read_file','run_tests']},
  ];
  const imperfect={tasks:[
    {key:'T1',title:'Tool',instruction:'Create scripts/repo-health-check.mjs.',expectedOutcome:'The tool is created.',workMode:'general',assignedAgentId:'backend',acceptanceCriteria:['Tool exists.'],dependsOn:[],priority:10},
    {key:'T2',title:'Tests',instruction:'Create tests/repo-health-check.test.mjs.',expectedOutcome:'The tests are created.',workMode:'modify',assignedAgentId:'qa',acceptanceCriteria:['At least three tests exist.'],dependsOn:['T1'],priority:20},
    {key:'T3',title:'QA',instruction:'Run the tests and verify the implementation.',expectedOutcome:'scripts/repo-health-check.mjs and tests/repo-health-check.test.mjs are created and verified.',workMode:'general',assignedAgentId:'qa',acceptanceCriteria:['Actual test evidence exists.'],dependsOn:[],priority:30},
    {key:'T4',title:'Review',instruction:'Review code quality, error handling, and Boss requirements.',expectedOutcome:'Final review is complete.',workMode:'general',assignedAgentId:'reviewer',acceptanceCriteria:['Final review evidence exists.'],dependsOn:[],priority:40},
  ]};

  const repaired=canonicalizePlanRoleContracts(objective,imperfect,roster);
  const byKey=new Map(repaired.plan.tasks.map((task:any)=>[task.key,task]));
  const validation=validatePlanRoleObligations(objective,repaired.plan,roster);

  assert.equal((byKey.get('T1') as any)?.workMode,'modify');
  assert.equal((byKey.get('T2') as any)?.assignedAgentId,'backend');
  assert.equal((byKey.get('T2') as any)?.workMode,'modify');
  assert.equal((byKey.get('T3') as any)?.assignedAgentId,'qa');
  assert.equal((byKey.get('T3') as any)?.workMode,'validate');
  assert.deepEqual(new Set((byKey.get('T3') as any)?.dependsOn),new Set(['T1','T2']));
  assert.equal((byKey.get('T4') as any)?.assignedAgentId,'reviewer');
  assert.equal((byKey.get('T4') as any)?.workMode,'validate');
  assert.ok((byKey.get('T4') as any)?.dependsOn.includes('T3'));
  assert.deepEqual(validation.violations,[]);
});

test('synthesized read-only QA task is never reclassified as mutation',()=>{
  const objective='Create scripts/tool.mjs. QA should verify the actual tests.';
  const roster=[
    {id:'backend',name:'Backend',role_label:'Backend',tools:['write_file']},
    {id:'qa',name:'QA',role_label:'QA',tools:['read_file','run_tests']},
  ];
  const plan={tasks:[
    {key:'T1',title:'Tool',instruction:'Create scripts/tool.mjs.',expectedOutcome:'Tool exists.',workMode:'general',assignedAgentId:'backend',acceptanceCriteria:['Tool exists.'],dependsOn:[]},
  ]};

  const repaired=canonicalizePlanRoleContracts(objective,plan,roster);
  const validation=validatePlanRoleObligations(objective,repaired.plan,roster);
  const qaTask=repaired.plan.tasks.find((task:any)=>task.assignedAgentId==='qa');

  assert.equal(qaTask?.workMode,'validate');
  assert.deepEqual(validation.violations,[]);
});

test('validation commands may inspect an explicitly completed post-state',()=>{
  const objective='QA should verify the implementation.';
  const roster=[
    {id:'qa',name:'QA',role_label:'QA',tools:['read_file','run_tests']},
  ];
  const plan={tasks:[
    {
      key:'T1',
      title:'Validate created tool',
      instruction:'Verify that scripts/tool.mjs was created and works.',
      expectedOutcome:'The tool is verified.',
      workMode:'validate',
      assignedAgentId:'qa',
      acceptanceCriteria:['Actual repository evidence is recorded.'],
      dependsOn:[],
    },
    {
      key:'T2',
      title:'README validation',
      instruction:'README.md güncellendi mi kontrol et.',
      expectedOutcome:'README state is verified.',
      workMode:'validate',
      assignedAgentId:'qa',
      acceptanceCriteria:['README state is checked.'],
      dependsOn:[],
    },
    {
      key:'T3',
      title:'Run validation',
      instruction:'Run tests and verify that scripts/tool.mjs was created.',
      expectedOutcome:'The tool state is verified.',
      workMode:'validate',
      assignedAgentId:'qa',
      acceptanceCriteria:['Actual test evidence is recorded.'],
      dependsOn:[],
    },
    {
      key:'T4',
      title:'Run confirmation',
      instruction:'Run tests to confirm scripts/tool.mjs was created.',
      expectedOutcome:'The tool state is confirmed.',
      workMode:'validate',
      assignedAgentId:'qa',
      acceptanceCriteria:['Actual test evidence is recorded.'],
      dependsOn:[],
    },
  ]};

  const validation=validatePlanRoleObligations(objective,plan,roster);
  assert.equal(
    validation.violations.some(v=>v.id==='completed_state_task_instruction'),
    false,
  );
  assert.equal(
    validation.violations.some(v=>v.id==='validation_role_mutation_task'),
    false,
  );
});
