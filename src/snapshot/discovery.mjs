import fs from 'node:fs';
import path from 'node:path';

const snapshotNames = ['snapshot_blob.bin', 'v8_context_snapshot.bin'];

function nearbySnapshotCandidates(blobPath) {
  const candidates = [];
  const seen = new Set();
  let directory = path.dirname(blobPath);

  while (true) {
    for (const relativeDirectory of ['', 'resources']) {
      for (const name of snapshotNames) {
        const candidate = path.join(directory, relativeDirectory, name);
        const key = candidate.toLowerCase();
        if (!seen.has(key) && fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
          seen.add(key);
          candidates.push(candidate);
        }
      }
    }
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  return candidates;
}

export { nearbySnapshotCandidates };
