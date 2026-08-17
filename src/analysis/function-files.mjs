import fs from 'node:fs';
import path from 'node:path';

import { selectFunctions } from './function-selection.mjs';
import { functionSource } from './source-index.mjs';

function safeFileName(value) {
  return value.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^\.+/, '_') || 'anonymous';
}

function functionDirectory(outputRoot, relativeOutput) {
  const base = relativeOutput.replace(/\.(?:c?js|mjs)$/i, '');
  return path.join(outputRoot, base);
}

function previousGeneratedFiles(outputRoot, directory, manifestPath) {
  if (!fs.existsSync(manifestPath)) return [];
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const directoryRoot = `${path.resolve(directory)}${path.sep}`;
    return (manifest.functions ?? [])
      .map((item) => item.file)
      .filter((file) => typeof file === 'string')
      .map((file) => path.resolve(outputRoot, file))
      .filter((file) => file.startsWith(directoryRoot));
  } catch {
    return [];
  }
}

function writeFunctionFiles(outputRoot, relativeOutput, source, analysis, options = {}) {
  const directory = functionDirectory(outputRoot, relativeOutput);
  const manifestPath = path.join(directory, 'index.json');
  const previousFiles = previousGeneratedFiles(outputRoot, directory, manifestPath);
  fs.mkdirSync(directory, { recursive: true });
  const selection = selectFunctions(analysis, options);
  if (selection.functions.length === 0) {
    throw new Error('Function selection did not match any function');
  }
  const files = selection.functions.map((item, index) => {
    const fileName = `${String(index + 1).padStart(3, '0')}-${safeFileName(item.id)}.js`;
    const destination = path.join(directory, fileName);
    fs.writeFileSync(destination, functionSource(source, item), 'utf8');
    return {
      ...item,
      file: path.relative(outputRoot, destination),
    };
  });
  fs.writeFileSync(manifestPath, `${JSON.stringify({
    format: 1,
    source: relativeOutput,
    functionCount: files.length,
    totalFunctionCount: analysis.functionCount,
    selection: {
      patterns: selection.patterns,
      include: selection.include,
      exclude: selection.exclude,
      mode: selection.mode,
      maxDepth: selection.maxDepth,
      expanded: selection.expanded,
      roots: selection.roots,
    },
    functions: files,
  }, null, 2)}\n`, 'utf8');
  const currentFiles = new Set(files.map((item) => path.resolve(outputRoot, item.file)));
  for (const previousFile of previousFiles) {
    if (!currentFiles.has(previousFile) && fs.existsSync(previousFile)) {
      fs.rmSync(previousFile, { force: true });
    }
  }
  return {
    directory,
    manifestPath,
    functionCount: files.length,
    files: files.map((item) => item.file),
    selection,
  };
}

export { writeFunctionFiles };
