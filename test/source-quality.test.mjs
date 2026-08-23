import assert from 'node:assert/strict';
import test from 'node:test';

import { qualityMetrics } from '../src/analysis/source-quality.mjs';

test('does not treat escaped string content as a synthetic register', () => {
  const metrics = qualityMetrics('const pattern = "\\\\r4";');

  assert.equal(metrics.syntheticRegisterReferences, 0);
});

test('keeps real synthetic register references visible', () => {
  const metrics = qualityMetrics('temporary0 = r4;');

  assert.equal(metrics.syntheticRegisterReferences, 1);
});

test('does not treat identifier-like string labels as synthetic registers', () => {
  const metrics = qualityMetrics('const labels = ["r16", "g16", "b16"];');

  assert.equal(metrics.syntheticRegisterReferences, 0);
});

test('hoisted function declarations after return are not unreachable', () => {
  for (const declaration of ['function inner() {}', 'async function inner() {}', 'function* inner() {}']) {
    const metrics = qualityMetrics(`return undefined;\n${declaration}`);

    assert.equal(metrics.unreachableStatements, 0, declaration);
  }
});

test('plain statements after return remain unreachable', () => {
  const metrics = qualityMetrics('return undefined;\nconsole.log(1);');

  assert.equal(metrics.unreachableStatements, 1);
});
