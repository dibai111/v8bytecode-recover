import fs from 'node:fs';
import path from 'node:path';

const snapshotNames = ['snapshot_blob.bin', 'v8_context_snapshot.bin'];

function addFileCandidate(candidate, candidates, seen) {
  const key = candidate.toLowerCase();
  if (seen.has(key) || !fs.existsSync(candidate)) return;
  try {
    if (!fs.statSync(candidate).isFile()) return;
  } catch {
    return;
  }
  seen.add(key);
  candidates.push(candidate);
}

function nearbyNodeCandidates(directory, candidates, seen) {
  let entries;
  try {
    entries = fs.readdirSync(directory, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (!entry.isDirectory() || !/^(?:\.node\d*|node-v)/i.test(entry.name)) {
      continue;
    }
    const runtimeDirectory = path.join(directory, entry.name);
    addFileCandidate(path.join(runtimeDirectory, 'node.exe'), candidates, seen);
    if (!/^\.node/i.test(entry.name)) continue;

    let nestedEntries;
    try {
      nestedEntries = fs.readdirSync(runtimeDirectory, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const nested of nestedEntries) {
      if (!nested.isDirectory() || !/^node-v/i.test(nested.name)) continue;
      addFileCandidate(
        path.join(runtimeDirectory, nested.name, 'node.exe'),
        candidates,
        seen,
      );
    }
  }
}

function nearbySnapshotCandidates(blobPath) {
  const candidates = [];
  const seen = new Set();
  let directory = path.dirname(blobPath);

  while (true) {
    for (const relativeDirectory of ['', 'resources']) {
      for (const name of snapshotNames) {
        const candidate = path.join(directory, relativeDirectory, name);
        addFileCandidate(candidate, candidates, seen);
      }
    }
    nearbyNodeCandidates(directory, candidates, seen);
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  // The running Node binary embeds its own read-only heap. For blobs produced
  // by the same Node build it resolves `<read_only_...>` values; mismatched
  // builds fail the checksum probe and are skipped like any other candidate.
  addFileCandidate(process.execPath, candidates, seen);
  return candidates;
}

export { nearbySnapshotCandidates };
