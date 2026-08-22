import fs from 'node:fs';
import path from 'node:path';

const matrixCache = new Map();

function loadEmbedderMatrix(engineRoot) {
  const filePath = path.join(engineRoot, 'cached_data', 'embedder-matrix.json');
  const key = path.resolve(filePath);
  if (matrixCache.has(key)) return matrixCache.get(key);
  const value = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  if (value?.format !== 1 || !value.embedders || typeof value.embedders !== 'object') {
    throw new Error(`invalid embedder matrix: ${filePath}`);
  }
  matrixCache.set(key, value);
  return value;
}

function assessEmbedderCompatibility(engineRoot, embedder, compatibility) {
  const matrix = loadEmbedderMatrix(engineRoot);
  const selected = matrix.embedders[embedder] ?? matrix.embedders.unknown;
  const selectedId = matrix.embedders[embedder] ? embedder : 'unknown';
  const snapshotVerified = compatibility?.snapshotStatus === 'matched'
    || compatibility?.snapshotStatus === 'not-applicable';
  const runtimeVerified = compatibility?.status === 'exact-runtime-variant';
  const exact = selectedId === 'node'
    ? runtimeVerified && snapshotVerified
    : false;
  const evidence = {
    version_hash: Boolean(compatibility?.version),
    header_layout: compatibility?.status !== 'unsupported-header' && Boolean(compatibility),
    payload_checksum: compatibility?.status === 'exact-runtime-variant'
      || compatibility?.status === 'version-only'
      || compatibility?.status === 'snapshot-unverified'
      || compatibility?.status === 'snapshot-mismatch',
    embedder_flags: compatibility?.flagsStatus === 'known',
    snapshot_checksum: snapshotVerified,
  };
  const missingEvidence = (selected.exact_evidence ?? [])
    .filter((name) => !evidence[name]);
  return {
    embedder: selectedId,
    label: selected.label,
    confidence: exact ? 'exact' : runtimeVerified ? 'version-only' : 'unverified',
    recommendedBackend: exact ? 'profile' : 'matching-d8',
    profileBackend: selected.profile_backend,
    d8Backend: selected.d8_backend,
    snapshot: selected.snapshot,
    exactEvidence: selected.exact_evidence,
    evidence,
    missingEvidence,
    backendReason: exact
      ? 'profile evidence is sufficient for the selected Node runtime'
      : selectedId === 'unknown'
        ? 'embedder is unknown; retain diagnostics and use matching d8 for exact recovery'
        : 'selected embedder needs matching runtime and snapshot evidence; prefer matching d8',
    notes: selected.notes,
  };
}

function listEmbedders(engineRoot) {
  return Object.entries(loadEmbedderMatrix(engineRoot).embedders)
    .map(([id, entry]) => ({ id, label: entry.label }));
}

export { assessEmbedderCompatibility, listEmbedders, loadEmbedderMatrix };
