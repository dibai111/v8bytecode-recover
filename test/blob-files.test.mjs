import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  assertUniqueOutputs,
  detectInputFormat,
  findInputs,
  outputRelativePath,
  removeLegacyArtifacts,
} from '../src/io/blob-files.mjs';

test('finds raw and disassembled inputs independently', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-files-test-'));
  try {
    fs.writeFileSync(path.join(root, 'nested.jsc'), 'raw');
    fs.writeFileSync(path.join(root, 'nested.disassembly.txt'), 'text');
    fs.writeFileSync(path.join(root, 'nested.v8recovery.json'), '{}');
    fs.writeFileSync(path.join(root, 'ignored.js'), 'source');

    const raw = findInputs(root, 'raw');
    const disassembled = findInputs(root, 'disassembled');
    const serialized = findInputs(root, 'serialized');
    assert.deepEqual(raw.map((item) => item.relativePath), ['nested.jsc']);
    assert.deepEqual(disassembled.map((item) => item.relativePath), ['nested.disassembly.txt']);
    assert.deepEqual(serialized.map((item) => item.relativePath), ['nested.v8recovery.json']);
    assert.equal(outputRelativePath('nested.disassembly.txt', 'disassembled'), 'nested.js');
    assert.equal(outputRelativePath('nested.v8recovery.json', 'serialized'), 'nested.js');
    assert.doesNotThrow(() => assertUniqueOutputs(disassembled, 'disassembled'));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('auto-detects mixed input directories and preserves output names', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-auto-input-test-'));
  try {
    fs.mkdirSync(path.join(root, 'nested'));
    fs.writeFileSync(path.join(root, 'raw.jsc'), 'raw');
    fs.writeFileSync(path.join(root, 'nested', 'source.txt'), 'text');
    fs.writeFileSync(path.join(root, 'nested', 'saved.v8recovery.json'), '{}');

    const inputs = findInputs(root, 'auto');
    assert.deepEqual(
      inputs.map((item) => [item.relativePath, item.format]),
      [
        [path.join('nested', 'saved.v8recovery.json'), 'serialized'],
        [path.join('nested', 'source.txt'), 'disassembled'],
        ['raw.jsc', 'raw'],
      ],
    );
    assert.equal(detectInputFormat('sample.disasm.txt'), 'disassembled');
    assert.equal(outputRelativePath('nested/saved.v8recovery.json', 'auto'), 'nested/saved.js');
    assert.doesNotThrow(() => assertUniqueOutputs(inputs, 'auto'));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('detects disassembled output collisions', () => {
  assert.throws(
    () => assertUniqueOutputs([
      { relativePath: 'sample.js.disassembly.txt' },
      { relativePath: 'sample.js.txt' },
    ], 'disassembled'),
    /Output collision/,
  );
});

test('does not remove an input artifact nested under the output analysis directory', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-cleanup-test-'));
  const analysis = path.join(root, '.analysis');
  const input = path.join(analysis, 'sample.v8recovery.json');
  try {
    fs.mkdirSync(analysis, { recursive: true });
    fs.writeFileSync(input, '{}', 'utf8');
    fs.writeFileSync(path.join(analysis, 'old.functions.json'), '{}', 'utf8');
    removeLegacyArtifacts(root, [input]);
    assert.equal(fs.existsSync(input), true);
    assert.equal(fs.existsSync(path.join(analysis, 'old.functions.json')), true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('removes a stale default recovery report before a new run', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-report-cleanup-test-'));
  const reportPath = path.join(root, 'recovery-report.json');
  try {
    fs.writeFileSync(reportPath, 'stale', 'utf8');
    removeLegacyArtifacts(root);
    assert.equal(fs.existsSync(reportPath), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
