import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { nearbySnapshotCandidates } from '../src/runtime/snapshot-discovery.mjs';

test('discovers a nearby versioned Node runtime as a legacy snapshot source', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-snapshot-discovery-'));
  const inputDirectory = path.join(root, 'reversed', 'v8blob');
  const runtime = path.join(root, '.node18', 'node-v18.5.0-win-x64');
  const input = path.join(inputDirectory, 'sample.v8blob');
  const nodePath = path.join(runtime, 'node.exe');
  try {
    fs.mkdirSync(inputDirectory, { recursive: true });
    fs.mkdirSync(runtime, { recursive: true });
    fs.writeFileSync(input, 'blob');
    fs.writeFileSync(nodePath, 'runtime');

    assert.deepEqual(nearbySnapshotCandidates(input), [nodePath, process.execPath]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
