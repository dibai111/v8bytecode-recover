import fs from 'node:fs';
import { spawnSync } from 'node:child_process';

import { loadProfileCatalog } from '../inspection/profile-catalog.mjs';
import { discoverD8 } from '../runtime/d8-discovery.mjs';

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

function inspectEnvironment({
  engineRoot,
  python = 'python',
  d8Path = null,
  d8Directory = null,
  profileDirectory = null,
}) {
  let profiles;
  try {
    profiles = loadProfileCatalog(engineRoot, profileDirectory);
  } catch (error) {
    profiles = { format: null, profiles: [], error: error.message };
  }
  const node = { available: true, version: process.version, error: null };
  const pythonStatus = checkCommand(python, ['--version']);
  const d8Discovery = discoverD8({
    requestedPath: d8Path,
    d8Directory,
    projectRoot: process.cwd(),
  });
  const configured = Boolean(
    d8Path
      || d8Directory
      || process.env.V8BYTECODE_D8
      || process.env.V8BLOB_D8,
  );
  const d8Status = {
    configured,
    available: d8Discovery.available,
    version: d8Discovery.version ?? (configured ? null : 'not found'),
    loadjsc: d8Discovery.loadjsc,
    path: d8Discovery.path,
    source: d8Discovery.source,
    candidates: d8Discovery.candidates,
    error: d8Discovery.available || !configured ? null : d8Discovery.error,
  };
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
      patchedD8Recovery: d8Status.available,
    },
    engine: {
      available: engineReady,
      path: engineRoot,
      profileCount: profiles.profiles.length,
      profileFormat: profiles.format,
      profileDirectory: profiles.directory ?? null,
      profileSource: profiles.source ?? 'bundled',
      error: profiles.error ?? null,
    },
  };
}

const ENVIRONMENT_LABELS = Object.freeze({
  'zh-TW': {
    title: 'v8bytecode-recover 系統檢查',
    platform: '平台',
    engine: 'Engine',
    profilePack: '內建',
    externalProfilePack: '指定',
    profiles: 'profiles',
    pack: 'Profile pack',
    optionalD8: '未配置（可選）',
    d8: 'd8',
    loadjsc: 'loadjsc()',
    capability: '能力',
    resultOk: '結果: 可以開始使用。',
    resultFail: '結果: 有項目需要處理，請先查看 FAIL 行。',
  },
  'zh-CN': {
    title: 'v8bytecode-recover 系统检查',
    platform: '平台',
    engine: 'Engine',
    profilePack: '内置',
    externalProfilePack: '指定',
    profiles: 'profiles',
    pack: 'Profile pack',
    optionalD8: '未配置（可选）',
    d8: 'd8',
    loadjsc: 'loadjsc()',
    capability: '能力',
    resultOk: '结果: 可以开始使用。',
    resultFail: '结果: 有项目需要处理，请先查看 FAIL 行。',
  },
});

function renderEnvironment(report, language = 'zh-TW') {
  const labels = ENVIRONMENT_LABELS[language] ?? ENVIRONMENT_LABELS['zh-TW'];
  const mark = (value) => value ? 'OK' : 'FAIL';
  return [
    labels.title,
    '',
    `${labels.platform}: ${report.platform}`,
    `[${mark(report.node.available)}] Node.js ${report.node.version}`,
    `[${mark(report.python.available)}] Python ${report.python.version || report.python.error}`,
    `[${mark(report.engine.available)}] ${labels.engine} ${report.engine.path}`,
    `      ${report.engine.profileSource === 'external' ? labels.externalProfilePack : labels.profilePack} ${labels.profiles}: ${report.engine.profileCount} (format ${report.engine.profileFormat ?? 'unknown'})`,
    `      ${labels.pack}: ${report.engine.profileDirectory ?? 'unknown'}`,
    `[${mark(!report.d8.configured || report.d8.available)}] ${labels.d8}: ${report.d8.available ? `${report.d8.version ?? 'unknown'} (${report.d8.source})` : report.d8.configured ? (report.d8.error ?? 'unavailable') : labels.optionalD8}`,
    report.d8.configured || report.d8.available
      ? `      ${labels.loadjsc}: ${mark(report.d8.loadjsc)}${report.d8.path ? ` [${report.d8.path}]` : ''}`
      : '',
    `${labels.capability}: artifact replay=${mark(report.capabilities.artifactReplay)}, disassembled=${mark(report.capabilities.disassembledAnalysis)}, raw profile=${mark(report.capabilities.rawProfileRecovery)}, patched d8=${report.capabilities.patchedD8Recovery ? 'OK' : labels.optionalD8}`,
    '',
    report.ok ? labels.resultOk : labels.resultFail,
    '',
  ].join('\n');
}

export { inspectEnvironment, renderEnvironment };
