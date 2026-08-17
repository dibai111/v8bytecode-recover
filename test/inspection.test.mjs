import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { engineRoot } from '../src/config/paths.mjs';
import { inspectFile, inspectInput, renderInspection } from '../src/inspection/inspect-input.mjs';

test('inspects a legacy V8 cached-data header', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-inspect-test-'));
  const filePath = path.join(root, 'sample.jsc');
  try {
    const data = Buffer.alloc(28);
    data.writeUInt32LE(0xdec0ded0, 0);
    data.writeUInt32LE(0xcd991825, 4);
    data.writeUInt32LE(0x11223344, 8);
    data.writeUInt32LE(0x55667788, 12);
    data.writeUInt32LE(4, 16);
    data.writeUInt32LE(0xaabbccdd, 20);
    data.writeUInt32LE(0x01020304, 24);
    fs.writeFileSync(filePath, data);

    const item = inspectFile(filePath, engineRoot);
    assert.equal(item.profileVersion, '9.4.146.24');
    assert.equal(item.header.name, 'legacy');
    assert.equal(item.header.payloadLength, 4);
    assert.equal(item.sizeBytes, 28);
    assert.match(renderInspection([item]), /V8 Blob/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('inspects disassembly and recovery artifacts with automatic format detection', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-inspect-mixed-test-'));
  try {
    fs.writeFileSync(path.join(root, 'sample.disassembly.txt'), 'Bytecode\nreturn\n', 'utf8');
    fs.writeFileSync(path.join(root, 'sample.v8recovery.json'), JSON.stringify({
      kind: 'v8bytecode-recover-recovery',
      format: 1,
      source: 'function sample() {}\n',
    }), 'utf8');
    const items = inspectInput(root, engineRoot);
    assert.deepEqual(items.map((item) => item.format), ['disassembled', 'serialized']);
    assert.equal(items[0].textLines, 2);
    assert.equal(items[1].artifact.valid, true);
    assert.match(renderInspection(items), /Artifact: 有效/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
