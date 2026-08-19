import assert from 'node:assert/strict';
import test from 'node:test';
import {
  extractJsonCandidate,
  tryExtractMissionPlanJson,
  attemptJsonRepair,
  validMissionPlanJson,
  validMissionPlanJsonLenient,
} from '../src/runtime/generic-openai.js';

test('extractJsonCandidate handles valid JSON', () => {
  const text = '{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"]}]}';
  const result = extractJsonCandidate(text);
  assert.ok(result);
  assert.equal(result.tasks.length, 1);
  assert.equal(result.tasks[0].key, 'T1');
});

test('extractJsonCandidate handles markdown fenced JSON', () => {
  const text = '```json\n{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"]}]}\n```';
  const result = extractJsonCandidate(text);
  assert.ok(result);
  assert.equal(result.tasks[0].key, 'T1');
});

test('extractJsonCandidate fails on trailing comma', () => {
  const text = '{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"],}]}';
  const result = extractJsonCandidate(text);
  assert.equal(result, null);
});

test('extractJsonCandidate fails on unclosed brace', () => {
  const text = '{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"]}';
  const result = extractJsonCandidate(text);
  assert.equal(result, null);
});

test('attemptJsonRepair fixes trailing comma', () => {
  const text = '{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"],}]}';
  const repaired = attemptJsonRepair(text);
  assert.ok(repaired);
  const parsed = JSON.parse(repaired);
  assert.equal(parsed.tasks[0].key, 'T1');
});

test('attemptJsonRepair fixes unclosed brace', () => {
  const text = '{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"]}';
  const repaired = attemptJsonRepair(text);
  assert.ok(repaired);
  const parsed = JSON.parse(repaired);
  assert.equal(parsed.tasks[0].key, 'T1');
});

test('attemptJsonRepair fixes unclosed bracket', () => {
  const text = '{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"]}';
  const repaired = attemptJsonRepair(text);
  assert.ok(repaired);
  assert.ok(repaired.endsWith(']}'));
});

test('attemptJsonRepair returns null for irreparable JSON', () => {
  const text = 'this is not json at all';
  const repaired = attemptJsonRepair(text);
  assert.equal(repaired, null);
});

test('attemptJsonRepair handles excess closing braces', () => {
  // Test case: JSON with excess closing braces that should be repairable
  const text = '{"tasks":[{"key":"T1","title":"Test","instruction":"Create file","expectedOutcome":"File created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Done"]}]} }';
  const repaired = attemptJsonRepair(text);
  assert.ok(repaired, 'Should repair JSON with excess closing brace');
  const parsed = JSON.parse(repaired);
  assert.equal(parsed.tasks.length, 1);
  assert.equal(parsed.tasks[0].key, 'T1');
});

test('attemptJsonRepair handles multiple excess closing braces', () => {
  // Test case with multiple excess closing braces
  const text = '{"tasks":[{"key":"T1"}]} } }';
  const repaired = attemptJsonRepair(text);
  assert.ok(repaired, 'Should repair JSON with multiple excess closing braces');
  const parsed = JSON.parse(repaired);
  assert.equal(parsed.tasks.length, 1);
  assert.equal(parsed.tasks[0].key, 'T1');
});

test('tryExtractMissionPlanJson extracts valid plan from reasoning text', () => {
  const text = `Here is my reasoning for the plan.
  
  {"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}]}
  
  This plan looks good.`;
  const result = tryExtractMissionPlanJson(text);
  assert.ok(result);
  assert.equal(result.tasks.length, 1);
  assert.equal(result.tasks[0].key, 'T1');
});

test('tryExtractMissionPlanJson extracts valid plan with trailing comma', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"],}]}`;
  const result = tryExtractMissionPlanJson(text);
  assert.ok(result);
  assert.equal(result.tasks[0].key, 'T1');
});

test('tryExtractMissionPlanJson extracts valid plan with unclosed brace', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}`;
  const result = tryExtractMissionPlanJson(text);
  assert.ok(result);
  assert.equal(result.tasks[0].key, 'T1');
});

test('tryExtractMissionPlanJson handles array-only output', () => {
  const text = `[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}]`;
  const result = tryExtractMissionPlanJson(text);
  assert.ok(result);
  assert.equal(result.tasks.length, 1);
  assert.equal(result.tasks[0].key, 'T1');
});

test('tryExtractMissionPlanJson handles plan wrapper object', () => {
  const text = `{"plan":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}]}`;
  const result = tryExtractMissionPlanJson(text);
  assert.ok(result);
  assert.equal(result.tasks.length, 1);
  assert.equal(result.tasks[0].key, 'T1');
});

test('tryExtractMissionPlanJson picks largest valid candidate', () => {
  const text = `{"tasks":[{"key":"T1"}]}
  
  {"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}]}`;
  const result = tryExtractMissionPlanJson(text);
  assert.ok(result);
  assert.equal(result.tasks[0].key, 'T1');
  assert.equal(result.tasks[0].title, 'Tool');
});

test('validMissionPlanJsonLenient accepts recoverable JSON', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"],}]}`;
  assert.ok(validMissionPlanJsonLenient(text));
});

test('validMissionPlanJsonLenient accepts unclosed brace', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}`;
  assert.ok(validMissionPlanJsonLenient(text));
});

test('validMissionPlanJsonLenient rejects semantically invalid plan', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs"}]}`;
  assert.equal(validMissionPlanJsonLenient(text), false);
});

test('validMissionPlanJsonLenient rejects missing tasks array', () => {
  const text = `{"summary":"test"}`;
  assert.equal(validMissionPlanJsonLenient(text), false);
});

test('validMissionPlanJsonLenient rejects empty tasks', () => {
  const text = `{"tasks":[]}`;
  assert.equal(validMissionPlanJsonLenient(text), false);
});

test('validMissionPlanJsonLenient rejects invalid workMode', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"invalid","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}]}`;
  assert.equal(validMissionPlanJsonLenient(text), false);
});

test('validMissionPlanJsonLenient rejects missing assignedAgentId', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"","dependsOn":[],"acceptanceCriteria":["Tool exists"]}]}`;
  assert.equal(validMissionPlanJsonLenient(text), false);
});

test('validMissionPlanJson strict mode rejects trailing comma', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"],}]}`;
  assert.equal(validMissionPlanJson(text), false);
});

test('validMissionPlanJson strict mode rejects unclosed brace', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}`;
  assert.equal(validMissionPlanJson(text), false);
});

test('validMissionPlanJson strict mode accepts valid JSON', () => {
  const text = `{"tasks":[{"key":"T1","title":"Tool","instruction":"Create scripts/tool.mjs","expectedOutcome":"Tool created","workMode":"modify","assignedAgentId":"backend","dependsOn":[],"acceptanceCriteria":["Tool exists"]}]}`;
  assert.ok(validMissionPlanJson(text));
});