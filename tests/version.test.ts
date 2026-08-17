import assert from 'node:assert/strict';
import test from 'node:test';
import { version } from '../src/server.js';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

test('Version from package.json', async (t) => {
  const packageJson = JSON.parse(
    readFileSync(join(process.cwd(), 'package.json'), 'utf-8')
  );
  const expectedVersion = packageJson.version;
  assert.strictEqual(version, expectedVersion);
});