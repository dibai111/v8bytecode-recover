import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import test from 'node:test';

import { candidateD8Paths, discoverD8, probeD8 } from '../src/runtime/d8-discovery.mjs';

test('discovers configured d8 candidates without invoking a shell', () => {
  const candidates = candidateD8Paths({
    requestedPath: process.execPath,
    projectRoot: process.cwd(),
  });
  assert.equal(candidates[0], process.execPath);
  const probe = probeD8(process.execPath);
  assert.equal(probe.available, false);
  assert.match(probe.error, /loadjsc|does not expose/i);
  const discovery = discoverD8({ requestedPath: process.execPath });
  assert.equal(discovery.available, false);
  assert.equal(discovery.candidates[0].path, process.execPath);
});

test('discovers versioned d8 names inside a runtime directory', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-d8-directory-test-'));
  try {
    fs.writeFileSync(path.join(root, 'd8-14.7.exe'), '', 'utf8');
    fs.writeFileSync(path.join(root, 'd8-not-a-runtime.txt'), '', 'utf8');
    const candidates = candidateD8Paths({ d8Directory: root, projectRoot: root });
    assert.equal(
      candidates.some((candidate) => candidate.endsWith('d8-14.7.exe')),
      true,
    );
    assert.equal(
      candidates.some((candidate) => candidate.endsWith('d8-not-a-runtime.txt')),
      false,
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
