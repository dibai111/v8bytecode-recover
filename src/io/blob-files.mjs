import fs from 'node:fs';
import path from 'node:path';

const blobExtensionPattern = /\.(?:v8blob|jsc)$/i;
const disassemblyExtensionPattern = /\.(?:disassembly|disasm)\.txt$/i;
const recoveryArtifactExtensionPattern = /\.v8recovery\.json$/i;
const plainTextExtensionPattern = /\.txt$/i;

function inputPattern(format) {
  if (format === 'raw') return blobExtensionPattern;
  if (format === 'disassembled') return disassemblyExtensionPattern;
  if (format === 'serialized') return recoveryArtifactExtensionPattern;
  throw new Error(`Unsupported input format: ${format}`);
}

function detectInputFormat(inputPath) {
  const fileName = path.basename(inputPath);
  if (recoveryArtifactExtensionPattern.test(fileName)) return 'serialized';
  if (disassemblyExtensionPattern.test(fileName) || plainTextExtensionPattern.test(fileName)) {
    return 'disassembled';
  }
  if (blobExtensionPattern.test(fileName)) return 'raw';
  return null;
}

function expectedInputExtensions() {
  return '.v8blob, .jsc, .disassembly.txt, .disasm.txt, .txt, or .v8recovery.json';
}

function findInputs(inputPath, format = 'raw') {
  const stat = fs.statSync(inputPath);
  if (stat.isFile()) {
    const detectedFormat = format === 'auto' ? detectInputFormat(inputPath) : format;
    if (!detectedFormat) {
      throw new Error(`Input file must end in ${expectedInputExtensions()}: ${inputPath}`);
    }
    const extensionPattern = inputPattern(detectedFormat);
    if (
      !extensionPattern.test(inputPath)
      && !(detectedFormat === 'disassembled' && plainTextExtensionPattern.test(inputPath))
    ) {
      const expected = format === 'auto'
        ? expectedInputExtensions()
        : detectedFormat === 'raw'
          ? '.v8blob or .jsc'
          : detectedFormat === 'serialized'
            ? '.v8recovery.json'
            : '.disassembly.txt, .disasm.txt, or .txt';
      throw new Error(`Input file must end in ${expected}: ${inputPath}`);
    }
    return [{ path: inputPath, relativePath: path.basename(inputPath), format: detectedFormat }];
  }
  if (!stat.isDirectory()) throw new Error(`Input is not a file or directory: ${inputPath}`);

  const inputs = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const itemPath = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(itemPath);
      else if (entry.isFile()) {
        const detectedFormat = format === 'auto' ? detectInputFormat(itemPath) : format;
        if (!detectedFormat) continue;
        const extensionPattern = inputPattern(detectedFormat);
        if (
          extensionPattern.test(entry.name)
          || (detectedFormat === 'disassembled' && plainTextExtensionPattern.test(entry.name))
        ) {
          inputs.push({
            path: itemPath,
            relativePath: path.relative(inputPath, itemPath),
            format: detectedFormat,
          });
        }
      }
    }
  };
  visit(inputPath);
  return inputs.sort((left, right) => left.relativePath.localeCompare(right.relativePath));
}

function findBlobs(inputPath) {
  return findInputs(inputPath, 'raw');
}

function outputRelativePath(inputRelativePath, format = 'raw') {
  const resolvedFormat = format === 'auto'
    ? detectInputFormat(inputRelativePath)
    : format;
  if (!resolvedFormat) throw new Error(`Unable to detect input format: ${inputRelativePath}`);
  const extensionPattern = inputPattern(resolvedFormat);
  let relativePath = inputRelativePath.replace(extensionPattern, '');
  if (resolvedFormat === 'disassembled' && plainTextExtensionPattern.test(relativePath)) {
    relativePath = relativePath.replace(plainTextExtensionPattern, '');
  }
  if (resolvedFormat === 'serialized') {
    relativePath = relativePath.replace(recoveryArtifactExtensionPattern, '');
  }
  if (!/\.(?:c?js|mjs)$/i.test(relativePath)) relativePath = `${relativePath}.js`;
  return relativePath;
}

function assertUniqueOutputs(inputs, format = 'raw') {
  const seen = new Map();
  for (const input of inputs) {
    const relativeOutput = outputRelativePath(
      input.relativePath,
      input.format ?? format,
    ).toLowerCase();
    const previous = seen.get(relativeOutput);
    if (previous) {
      throw new Error(
        `Output collision: ${previous} and ${input.relativePath} both map to ${relativeOutput}`,
      );
    }
    seen.set(relativeOutput, input.relativePath);
  }
}

function containsPath(parentPath, childPath) {
  const relative = path.relative(path.resolve(parentPath), path.resolve(childPath));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function removeLegacyArtifacts(outputPath, protectedPaths = [], options = {}) {
  const reportPath = path.join(outputPath, 'V8BLOB_TO_JS_REPORT.json');
  const recoveryReportPath = path.join(outputPath, 'recovery-report.json');
  const evidencePath = path.join(outputPath, '.evidence');
  const analysisPath = path.join(outputPath, '.analysis');
  const targets = [
    [reportPath, false],
    [recoveryReportPath, false],
    [evidencePath, true],
    [analysisPath, true, options.preserveAnalysis === true],
  ];
  for (const [target, recursive, preserved] of targets) {
    if (preserved) continue;
    if (protectedPaths.some((protectedPath) => containsPath(target, protectedPath))) continue;
    if (fs.existsSync(target)) fs.rmSync(target, { recursive, force: true });
  }
}

export {
  assertUniqueOutputs,
  detectInputFormat,
  findBlobs,
  findInputs,
  outputRelativePath,
  removeLegacyArtifacts,
};
