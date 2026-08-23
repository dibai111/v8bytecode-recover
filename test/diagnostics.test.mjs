import assert from 'node:assert/strict';
import test from 'node:test';

import { renderEnvironment } from '../src/backends/environment.mjs';

test('renders optional d8 separately from the built-in recovery capabilities', () => {
  const report = {
    ok: true,
    platform: 'win32/x64',
    node: { available: true, version: 'v22.0.0' },
    python: { available: true, version: 'Python 3.12' },
    d8: { configured: false, available: false, version: 'not configured', error: null },
    capabilities: {
      artifactReplay: true,
      disassembledAnalysis: true,
      rawProfileRecovery: true,
      patchedD8Recovery: false,
    },
    engine: {
      available: true,
      path: 'engine',
      profileCount: 17,
      profileFormat: 1,
      error: null,
    },
  };
  const output = renderEnvironment(report);
  assert.match(output, /d8: 未配置（可選）/);
  assert.match(output, /raw profile=OK/);
  assert.match(output, /patched d8=未配置/);
  assert.match(renderEnvironment(report, 'zh-CN'), /系统检查/);
});
