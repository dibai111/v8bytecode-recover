import assert from 'node:assert/strict';
import test from 'node:test';

import { qualityMetrics } from '../src/validation/source-quality.mjs';

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
