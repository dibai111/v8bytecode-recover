import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const recoveryManifestKind = 'v8bytecode-recover-manifest';
const legacyRecoveryManifestKind = 'v8blob-to-js-manifest';
const recoveryManifestFormat = 1;
const recoveryPipelineVersion = 1;
const recoveryManifestFileName = 'recovery-manifest.json';

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stableValue(value[key])]),
    );
  }
  return value;
}

function sha256Text(value) {
  return crypto.createHash('sha256').update(value, 'utf8').digest('hex');
}

function fileFingerprint(filePath) {
  const data = fs.readFileSync(filePath);
  const stat = fs.statSync(filePath);
  return {
    sha256: crypto.createHash('sha256').update(data).digest('hex'),
    sizeBytes: stat.size,
    mtimeMs: stat.mtimeMs,
  };
}

function recoverySettings(options) {
  return {
    pipelineVersion: recoveryPipelineVersion,
    inputFormat: options.inputFormat ?? 'auto',
    backend: options.backend ?? 'auto',
    d8Path: options.d8Path ? path.resolve(options.d8Path) : null,
    profile: options.profile ?? null,
    snapshot: options.snapshot ? path.resolve(options.snapshot) : null,
    snapshotSearch: options.snapshotSearch !== false,
    runtimeVariant: options.runtimeVariant ?? null,
    payloadOffset: options.payloadOffset ?? null,
    level: options.level ?? 4,
    emit: [...(options.emit ?? [])].sort(),
    splitFunctions: options.splitFunctions ? path.resolve(options.splitFunctions) : null,
    functionNames: [...(options.functionNames ?? [])],
    includeFunctions: [...(options.includeFunctions ?? [])],
    excludeFunctions: [...(options.excludeFunctions ?? [])],
    splitMode: options.splitMode ?? 'declarers',
    splitDepth: options.splitDepth ?? null,
    treeRoot: options.treeRoot ?? null,
    treeMode: options.treeMode ?? 'declarers',
    treeDepth: options.treeDepth ?? null,
    normalizeNames: Boolean(options.normalizeNames),
    strict: Boolean(options.strict),
    python: options.python ?? 'python',
  };
}

function recoverySettingsFingerprint(options) {
  return sha256Text(JSON.stringify(stableValue(recoverySettings(options))));
}

function manifestEntryKey(input) {
  return `${input.format ?? 'raw'}:${input.relativePath}`;
}

function createRecoveryManifest({ inputRoot, outputRoot, options, startedAt = new Date() }) {
  const settings = recoverySettings(options);
  return {
    kind: recoveryManifestKind,
    format: recoveryManifestFormat,
    pipelineVersion: recoveryPipelineVersion,
    status: 'running',
    inputRoot: path.resolve(inputRoot),
    outputRoot: path.resolve(outputRoot),
    settings,
    settingsFingerprint: sha256Text(JSON.stringify(stableValue(settings))),
    startedAt: startedAt.toISOString(),
    updatedAt: startedAt.toISOString(),
    counts: { total: 0, succeeded: 0, partial: 0, failed: 0, resumed: 0 },
    entries: [],
  };
}

function parseRecoveryManifest(value) {
  if (!value || typeof value !== 'object') return null;
  if (![recoveryManifestKind, legacyRecoveryManifestKind].includes(value.kind)
    || value.format !== recoveryManifestFormat) return null;
  if (!Array.isArray(value.entries)) return null;
  if (typeof value.inputRoot !== 'string' || typeof value.outputRoot !== 'string') return null;
  if (typeof value.settingsFingerprint !== 'string') return null;
  return value;
}

function readRecoveryManifest(manifestPath) {
  try {
    return parseRecoveryManifest(JSON.parse(fs.readFileSync(manifestPath, 'utf8')));
  } catch {
    return null;
  }
}

function isCompatibleRecoveryManifest(manifest, { inputRoot, outputRoot, options }) {
  if (!manifest) return false;
  return path.resolve(manifest.inputRoot) === path.resolve(inputRoot)
    && path.resolve(manifest.outputRoot) === path.resolve(outputRoot)
    && manifest.settingsFingerprint === recoverySettingsFingerprint(options);
}

function writeRecoveryManifest(manifestPath, manifest) {
  const destination = path.resolve(manifestPath);
  const temporary = `${destination}.${process.pid}.${Date.now()}.tmp`;
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  try {
    fs.rmSync(destination, { force: true });
    fs.renameSync(temporary, destination);
  } finally {
    if (fs.existsSync(temporary)) fs.rmSync(temporary, { force: true });
  }
  return destination;
}

function updateRecoveryManifest(manifest, entries, status = 'running') {
  const succeeded = entries.filter((entry) => entry.success).length;
  const partial = entries.filter((entry) => entry.success && entry.partialRecovery).length;
  const resumed = entries.filter((entry) => entry.success && entry.resumed).length;
  return {
    ...manifest,
    status,
    updatedAt: new Date().toISOString(),
    counts: {
      total: entries.length,
      succeeded,
      partial,
      failed: entries.length - succeeded,
      resumed,
    },
    entries,
  };
}

export {
  createRecoveryManifest,
  fileFingerprint,
  isCompatibleRecoveryManifest,
  manifestEntryKey,
  readRecoveryManifest,
  recoveryManifestFileName,
  recoveryManifestFormat,
  recoveryManifestKind,
  legacyRecoveryManifestKind,
  recoveryPipelineVersion,
  recoverySettingsFingerprint,
  updateRecoveryManifest,
  writeRecoveryManifest,
};
