import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  createRecoveryArtifact,
  parseRecoveryArtifact,
  readRecoveryArtifact,
  serializeRecoveryArtifact,
} from '../src/recovery/artifact.mjs';

test('round-trips a validated recovery artifact', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-artifact-test-'));
  const file = path.join(root, 'sample.v8recovery.json');
  try {
    const artifact = createRecoveryArtifact({
      input: { path: 'sample.jsc', format: 'raw' },
      backend: 'profile',
      snapshot: null,
      disassembly: 'disassembly\n',
      source: 'function sample() { return 1; }\n',
      analysis: { format: 1, functionCount: 1 },
      normalization: { normalized: false, mappings: [] },
    });
    fs.writeFileSync(file, serializeRecoveryArtifact(artifact), 'utf8');
    assert.deepEqual(readRecoveryArtifact(file), artifact);
    assert.equal(parseRecoveryArtifact(artifact).kind, 'v8bytecode-recover-recovery');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('rejects untrusted or incomplete artifact shapes', () => {
  assert.throws(
    () => parseRecoveryArtifact({ kind: 'other', format: 1, source: 'x' }),
    /not a supported v8bytecode-recover-recovery format/,
  );
  assert.throws(
    () => parseRecoveryArtifact({ kind: 'v8bytecode-recover-recovery', format: 1 }),
    /does not contain recovered source/,
  );
});

test('reads legacy recovery artifacts after the project rename', () => {
  assert.equal(parseRecoveryArtifact({
    kind: 'v8blob-to-js-recovery',
    format: 1,
    source: 'const legacy = true;\n',
  }).kind, 'v8blob-to-js-recovery');
});
