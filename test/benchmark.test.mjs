import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { runExternalAdapter } from '../src/analysis/benchmark-adapter.mjs';
import { evaluateCorpus } from '../src/analysis/benchmark-corpus.mjs';
import { functionCount } from '../src/analysis/benchmark.mjs';

test('runs a two-stage external adapter without a shell', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-external-adapter-test-'));
  try {
    fs.writeFileSync(path.join(root, 'sample.jsc'), Buffer.from('fixture'));
    const output = path.join(root, 'output');
    const adapter = {
      format: 1,
      id: 'fixture-adapter',
      disassemble: {
        command: process.execPath,
        args: ['-e', 'process.stdout.write("Bytecode fixture\\n")'],
      },
      recover: {
        command: process.execPath,
        args: [
          '-e',
          'require("fs").writeFileSync(process.argv[1], "function fixture() {}\\n")',
          '{output}',
        ],
      },
    };
    const result = runExternalAdapter(path.join(root, 'sample.jsc'), output, adapter);

    assert.equal(result.exitCode, 0);
    assert.equal(result.files[0].success, true);
    assert.equal(fs.readFileSync(path.join(output, 'sample.js'), 'utf8'), 'function fixture() {}\n');
    assert.equal(fs.readFileSync(path.join(output, '.external', 'sample.js.disassembly.txt'), 'utf8'), 'Bytecode fixture\n');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('evaluates a corpus manifest against backend output without requiring proprietary blobs', () => {
  const report = {
    results: [{
      backend: 'profile',
      files: [{
        input: 'node/sample.jsc',
        success: true,
        functions: 3,
        bytes: 128,
        profileVersion: '14.7.57',
        embedder: 'node',
        compatibilityStatus: 'exact-runtime-variant',
        residueFree: true,
      }],
    }],
  };
  const regression = evaluateCorpus(report, {
    format: 1,
    cases: [{
      id: 'node-v14',
      path: 'node/sample.jsc',
      backend: 'profile',
      profile: '14.7.57',
      embedder: 'node',
      compatibilityStatus: 'exact-runtime-variant',
      minFunctions: 2,
      minBytes: 100,
      residueFree: true,
    }],
  });
  assert.equal(regression.passed, true);
  assert.equal(regression.passedCases, 1);
});

test('counts arrow functions structurally for benchmark thresholds', () => {
  assert.equal(functionCount('const main = () => 1;\n'), 1);
});
