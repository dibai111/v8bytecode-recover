import assert from 'node:assert/strict';
import test from 'node:test';

import { qualityMetrics } from '../src/validation/source-quality.mjs';
import {
  createPartialRecoverySource,
  isReadOnlyOnlyResidue,
  partialRecoveryBanner,
  replaceUnresolvedReadOnlyReferences,
} from '../src/validation/unresolved-values.mjs';

test('replaces only unresolved read-only references with stable placeholders', () => {
  const source = [
    'const key = "<read_only_0,41160>";',
    'const value = <read_only_0,40712>;',
    '// <read_only_0,41160>',
  ].join('\n');
  const result = replaceUnresolvedReadOnlyReferences(source);

  assert.equal(result.count, 3);
  assert.deepEqual(result.replacements, [
    {
      token: 'read_only_0_41160',
      replacement: '__v8_unresolved_read_only_0_41160__',
      count: 2,
    },
    {
      token: 'read_only_0_40712',
      replacement: '__v8_unresolved_read_only_0_40712__',
      count: 1,
    },
  ]);
  assert.match(result.source, /__v8_unresolved_read_only_0_41160__/);
  assert.doesNotMatch(result.source, /<read_only_/);

  const partial = createPartialRecoverySource('const value = <read_only_0,41160>;\n');
  assert.match(partial.source, /const __v8_unresolved_read_only_0_41160__ = undefined;/);
});

test('allows partial recovery only when read-only residue is the sole quality issue', () => {
  const partial = qualityMetrics('const value = "<read_only_0,41160>";\n');
  const unsafe = qualityMetrics('const value = "<read_only_0,41160>";\nACCU;\n');

  assert.equal(isReadOnlyOnlyResidue(partial), true);
  assert.equal(isReadOnlyOnlyResidue(unsafe), false);
  assert.match(partialRecoveryBanner, /^\/\/ v8blob-to-js:/);
});
