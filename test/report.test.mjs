import assert from 'node:assert/strict';
import test from 'node:test';

import { createRecoveryReport, sourceSummary } from '../src/recovery/report.mjs';

test('creates a compact recovery report with source metrics', () => {
  const source = 'function main() { return 1; }\n';
  const metrics = { residueFree: true, runtimeHelpers: 0 };
  const summary = sourceSummary(source, metrics);
  const report = createRecoveryReport({
    input: 'input',
    inputFormat: 'raw',
    output: 'output',
    backend: 'auto',
    profile: null,
    level: 4,
    emit: [],
    snapshotSearch: true,
    runtimeVariant: null,
  }, [{ input: 'sample.jsc', output: 'sample.js', success: true, ...summary }], new Date(0), new Date(1000));

  assert.equal(report.counts.succeeded, 1);
  assert.equal(report.counts.failed, 0);
  assert.equal(report.elapsedMs, 1000);
  assert.equal(report.files[0].functions, 1);
  assert.equal(report.files[0].sha256.length, 64);
});

test('uses indexed function counts when analysis includes arrow functions', () => {
  const source = 'const main = () => 1;\n';
  const metrics = { residueFree: true, runtimeHelpers: 0 };
  const summary = sourceSummary(source, metrics, { functionCount: 1 });
  assert.equal(summary.functions, 1);
});
