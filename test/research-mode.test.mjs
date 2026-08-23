import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { sourceRecoveryEntryPath } from '../src/cli/paths.mjs';
import { recoverSources } from '../src/recovery/pipeline.mjs';

test('preserves a non-clean candidate in research mode without publishing it as JavaScript', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-research-test-'));
  const input = path.join(root, 'input');
  const output = path.join(root, 'output');
  fs.mkdirSync(input);
  fs.writeFileSync(path.join(input, 'sample.disassembly.txt'), 'placeholder\n', 'utf8');
  try {
    const result = recoverSources({
      input,
      output,
      inputFormat: 'disassembled',
      backend: 'profile',
      profile: null,
      d8Path: null,
      d8Directory: null,
      snapshot: null,
      snapshotSearch: true,
      runtimeVariant: null,
      embedder: 'unknown',
      payloadOffset: null,
      level: 4,
      emit: [],
      splitFunctions: null,
      report: null,
      python: 'missing-python',
      strict: false,
      research: true,
      resume: false,
    }, { sourceRecoveryEntryPath: path.join(root, 'missing-engine.py') }, {
      decompile() {
        return 'const value = "<undefined:0>";\n';
      },
    });

    assert.equal(result.failed, 1);
    assert.equal(fs.existsSync(path.join(output, 'sample.js')), false);
    assert.equal(
      fs.existsSync(path.join(output, '.research', 'sample.candidate.js')),
      true,
    );
    assert.equal(result.files[0].researchReason, 'quality-residue');
  } finally {
    process.exitCode = undefined;
    fs.rmSync(root, { recursive: true, force: true });
  }
});
