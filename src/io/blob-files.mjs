import fs from 'node:fs';
import path from 'node:path';

const blobExtensionPattern = /\.(?:v8blob|jsc)$/i;

function findBlobs(inputPath) {
  const stat = fs.statSync(inputPath);
  if (stat.isFile()) {
    if (!blobExtensionPattern.test(inputPath)) {
      throw new Error(`Input file must end in .v8blob or .jsc: ${inputPath}`);
    }
    return [{ path: inputPath, relativePath: path.basename(inputPath) }];
  }
  if (!stat.isDirectory()) throw new Error(`Input is not a file or directory: ${inputPath}`);

  const blobs = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const itemPath = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(itemPath);
      else if (entry.isFile() && blobExtensionPattern.test(entry.name)) {
        blobs.push({ path: itemPath, relativePath: path.relative(inputPath, itemPath) });
      }
    }
  };
  visit(inputPath);
  return blobs.sort((left, right) => left.relativePath.localeCompare(right.relativePath));
}

function outputRelativePath(blobRelativePath) {
  let relativePath = blobRelativePath.replace(blobExtensionPattern, '');
  if (!/\.(?:c?js|mjs)$/i.test(relativePath)) relativePath = `${relativePath}.js`;
  return relativePath;
}

function assertUniqueOutputs(blobs) {
  const seen = new Map();
  for (const blob of blobs) {
    const relativeOutput = outputRelativePath(blob.relativePath).toLowerCase();
    const previous = seen.get(relativeOutput);
    if (previous) {
      throw new Error(
        `Output collision: ${previous} and ${blob.relativePath} both map to ${relativeOutput}`,
      );
    }
    seen.set(relativeOutput, blob.relativePath);
  }
}

function removeLegacyArtifacts(outputPath) {
  const reportPath = path.join(outputPath, 'V8BLOB_TO_JS_REPORT.json');
  const evidencePath = path.join(outputPath, '.evidence');
  const analysisPath = path.join(outputPath, '.analysis');
  if (fs.existsSync(reportPath)) fs.rmSync(reportPath, { force: true });
  if (fs.existsSync(evidencePath)) fs.rmSync(evidencePath, { recursive: true, force: true });
  if (fs.existsSync(analysisPath)) fs.rmSync(analysisPath, { recursive: true, force: true });
}

export {
  assertUniqueOutputs,
  findBlobs,
  outputRelativePath,
  removeLegacyArtifacts,
};
