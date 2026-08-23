import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  createRecoveryManifest,
  fileFingerprint,
  isCompatibleRecoveryManifest,
  manifestEntryKey,
  readRecoveryManifest,
  recoverySettingsFingerprint,
  updateRecoveryManifest,
  writeRecoveryManifest,
} from '../src/recovery/manifest.mjs';

function options(overrides = {}) {
  return {
    inputFormat: 'disassembled',
    backend: 'profile',
    level: 4,
    emit: ['functions'],
    snapshotSearch: true,
    strict: false,
    resume: true,
    ...overrides,
  };
}

test('writes a validated manifest with stable settings fingerprints', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-manifest-test-'));
  const input = path.join(root, 'sample.txt');
  const manifestPath = path.join(root, 'recovery-manifest.json');
  fs.writeFileSync(input, 'sample\n', 'utf8');
  try {
    const settings = options();
    const manifest = createRecoveryManifest({
      inputRoot: root,
      outputRoot: path.join(root, 'output'),
      options: settings,
    });
    const fingerprint = fileFingerprint(input);
    const entry = {
      key: manifestEntryKey({ format: 'disassembled', relativePath: 'sample.txt' }),
      inputFormat: 'disassembled',
      inputFingerprint: fingerprint,
      output: 'sample.js',
      success: true,
      partialRecovery: null,
      resumed: false,
    };
    const complete = updateRecoveryManifest(manifest, [entry], 'complete');
    writeRecoveryManifest(manifestPath, complete);
    const loaded = readRecoveryManifest(manifestPath);
    assert.equal(loaded.status, 'complete');
    assert.equal(loaded.counts.succeeded, 1);
    assert.equal(loaded.counts.resumed, 0);
    assert.equal(isCompatibleRecoveryManifest(loaded, {
      inputRoot: root,
      outputRoot: path.join(root, 'output'),
      options: settings,
    }), true);
    assert.notEqual(
      recoverySettingsFingerprint(settings),
      recoverySettingsFingerprint(options({ level: 3 })),
    );
    assert.notEqual(
      recoverySettingsFingerprint(settings),
      recoverySettingsFingerprint(options({ profileDirectory: 'profiles/14.7.57' })),
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
