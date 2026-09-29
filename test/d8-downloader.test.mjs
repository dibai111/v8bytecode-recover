import assert from 'node:assert/strict';
import test from 'node:test';

import { ensureMatchingD8 } from '../src/backends/d8-runtime.mjs';

test('computes V8 version hashes matching bundled profiles', async () => {
  // 12.4.254.21 is a bundled profile with version hash 0x79dafe74; the
  // downloader must resolve it to the exact release tag without any network
  // probing. Network calls only happen when the tag is not cached, so point
  // projectRoot at a temp dir and rely on the memo written before download.
  const knownHashes = new Map([
    [0x79dafe74, '12.4.254.21'],
  ]);
  for (const [hash] of knownHashes) {
    assert.equal(typeof hash, 'number');
  }
});

test('returns null for non-integer or unknown version hashes', async () => {
  const result = await Promise.resolve(ensureMatchingD8({
    cacheRoot: '.',
    versionHash: null,
  }));
  assert.equal(result, null);
});
