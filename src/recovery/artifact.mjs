import fs from 'node:fs';
import path from 'node:path';

const recoveryArtifactKind = 'v8bytecode-recover-recovery';
const legacyRecoveryArtifactKind = 'v8blob-to-js-recovery';
const recoveryArtifactFormat = 1;

function createRecoveryArtifact({
  input,
  backend,
  snapshot,
  disassembly,
  source,
  analysis,
  normalization,
}) {
  if (typeof source !== 'string' || source.length === 0) {
    throw new Error('Recovery artifact requires a non-empty recovered source');
  }
  return {
    kind: recoveryArtifactKind,
    format: recoveryArtifactFormat,
    input: input ?? null,
    backend: backend ?? null,
    snapshot: snapshot ?? null,
    disassembly: disassembly ?? null,
    source,
    analysis: analysis ?? null,
    normalization: normalization ?? { normalized: false, mappings: [] },
  };
}

function serializeRecoveryArtifact(values) {
  return `${JSON.stringify(createRecoveryArtifact(values), null, 2)}\n`;
}

function parseRecoveryArtifact(value, sourceName = 'recovery artifact') {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${sourceName} must contain a JSON object`);
  }
  if (![recoveryArtifactKind, legacyRecoveryArtifactKind].includes(value.kind)
    || value.format !== recoveryArtifactFormat) {
    throw new Error(
      `${sourceName} is not a supported ${recoveryArtifactKind} format (expected ${recoveryArtifactFormat})`,
    );
  }
  if (typeof value.source !== 'string' || value.source.length === 0) {
    throw new Error(`${sourceName} does not contain recovered source`);
  }
  if (value.disassembly !== null && value.disassembly !== undefined
    && typeof value.disassembly !== 'string') {
    throw new Error(`${sourceName} has an invalid disassembly field`);
  }
  return value;
}

function readRecoveryArtifact(filePath) {
  const resolvedPath = path.resolve(filePath);
  let value;
  try {
    value = JSON.parse(fs.readFileSync(resolvedPath, 'utf8'));
  } catch (error) {
    throw new Error(`Unable to read recovery artifact ${resolvedPath}: ${error.message}`);
  }
  return parseRecoveryArtifact(value, resolvedPath);
}

export {
  createRecoveryArtifact,
  parseRecoveryArtifact,
  readRecoveryArtifact,
  serializeRecoveryArtifact,
};
