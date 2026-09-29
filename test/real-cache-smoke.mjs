import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const recoveryEntry = path.join(projectRoot, 'bin', 'v8bytecode-recover.mjs');
const v8ProfileVersion = process.versions.v8.split('-')[0];

test('recovers and executes a real Node-generated V8 cache', {
  skip: v8ProfileVersion === '12.4.254.21'
    ? false
    : `requires bundled profile 12.4.254.21; runtime provides ${v8ProfileVersion}`,
}, () => {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8bytecode-real-cache-'));

  try {
    const inputPath = path.join(temporaryRoot, 'sample.jsc');
    const outputPath = path.join(temporaryRoot, 'output');
    const reportPath = path.join(temporaryRoot, 'recovery-report.json');
    const source = [
      'function add(left, right) { return left + right; }',
      'function double(value) { return add(value, value); }',
    ].join('\n');
    const compiled = new vm.Script(source, { filename: 'sample.js' });
    const fixtureContext = vm.createContext({});
    compiled.runInContext(fixtureContext);
    assert.equal(fixtureContext.double(21), 42);
    fs.writeFileSync(inputPath, compiled.createCachedData());

    const recovery = spawnSync(process.execPath, [
      recoveryEntry,
      inputPath,
      '--backend', 'profile',
      '--snapshot', process.execPath,
      '--strict',
      '--output', outputPath,
      '--report', reportPath,
    ], {
      cwd: projectRoot,
      encoding: 'utf8',
      timeout: 120_000,
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: '1',
        PYTHONIOENCODING: 'utf-8',
      },
    });

    assert.equal(
      recovery.status,
      0,
      [recovery.stdout, recovery.stderr].filter(Boolean).join('\n'),
    );

    const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
    const file = report.files.find((item) => item.input === path.basename(inputPath));
    assert.ok(file, 'recovery report should include the generated cache');
    assert.equal(file.profileVersion, v8ProfileVersion);
    assert.equal(file.residueFree, true);
    assert.ok(file.functionCount >= 2, JSON.stringify(file));

    const recoveredPath = path.join(outputPath, 'sample.js');
    const recoveredSource = fs.readFileSync(recoveredPath, 'utf8');
    const recoveredFunctions = vm.runInNewContext(
      `${recoveredSource}\n;({ add, double });`,
      {},
    );
    assert.equal(recoveredFunctions.add(19, 23), 42);
    assert.equal(recoveredFunctions.double(21), 42);
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});
