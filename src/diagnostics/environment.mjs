import fs from 'node:fs';
import { spawnSync } from 'node:child_process';

import { loadProfileCatalog } from '../profiles/catalog.mjs';

function checkCommand(command, args) {
  const run = spawnSync(command, args, {
    encoding: 'utf8',
    timeout: 10_000,
    windowsHide: true,
  });
  const version = (run.stdout || run.stderr || '').trim().split(/\r?\n/)[0] ?? '';
  return {
    available: !run.error && run.status === 0,
    version,
    error: run.error?.message ?? (run.status === 0 ? null : `exit code ${run.status}`),
  };
}

function inspectEnvironment({ engineRoot, python = 'python', d8Path = null }) {
  let profiles;
  try {
    profiles = loadProfileCatalog(engineRoot);
  } catch (error) {
    profiles = { format: null, profiles: [], error: error.message };
  }
  const node = { available: true, version: process.version, error: null };
  const pythonStatus = checkCommand(python, ['--version']);
  const d8Status = d8Path
    ? {
      configured: true,
      available: fs.existsSync(d8Path),
      version: null,
      error: fs.existsSync(d8Path) ? null : 'file does not exist',
    }
    : { configured: false, available: false, version: 'not configured', error: null };
  const engineReady = fs.existsSync(engineRoot) && profiles.profiles.length > 0;
  const rawProfileReady = engineReady && pythonStatus.available;
  const artifactReplayReady = node.available;
  return {
    ok: node.available && rawProfileReady && (!d8Status.configured || d8Status.available),
    platform: `${process.platform}/${process.arch}`,
    node,
    python: pythonStatus,
    d8: d8Status,
    capabilities: {
      artifactReplay: artifactReplayReady,
      disassembledAnalysis: node.available,
      rawProfileRecovery: rawProfileReady,
      patchedD8Recovery: d8Status.configured && d8Status.available,
    },
    engine: {
      available: engineReady,
      path: engineRoot,
      profileCount: profiles.profiles.length,
      profileFormat: profiles.format,
      error: profiles.error ?? null,
    },
  };
}

function renderEnvironment(report) {
  const mark = (value) => value ? 'OK' : 'FAIL';
  return [
    'v8blob-to-js 系統檢查',
    '',
    `平台: ${report.platform}`,
    `[${mark(report.node.available)}] Node.js ${report.node.version}`,
    `[${mark(report.python.available)}] Python ${report.python.version || report.python.error}`,
    `[${mark(report.engine.available)}] Engine ${report.engine.path}`,
    `      內建 profiles: ${report.engine.profileCount} (format ${report.engine.profileFormat ?? 'unknown'})`,
    `[${mark(!report.d8.configured || report.d8.available)}] d8: ${report.d8.configured ? (report.d8.version ?? report.d8.error) : '未配置（可選）'}`,
    `能力: artifact replay=${mark(report.capabilities.artifactReplay)}, disassembled=${mark(report.capabilities.disassembledAnalysis)}, raw profile=${mark(report.capabilities.rawProfileRecovery)}, patched d8=${report.d8.configured ? mark(report.capabilities.patchedD8Recovery) : '未配置'}`,
    '',
    report.ok ? '結果: 可以開始使用。' : '結果: 有項目需要處理，請先查看 FAIL 行。',
    '',
  ].join('\n');
}

export { inspectEnvironment, renderEnvironment };
