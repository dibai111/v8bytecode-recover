#!/usr/bin/env node

import fs from 'node:fs';

import { parseArguments, usage } from '../src/cli/options.mjs';
import { createBackend } from '../src/backends/registry.mjs';
import {
  defaultOutputPath,
  engineRoot,
  sourceRecoveryEntryPath,
} from '../src/cli/paths.mjs';
import { recoverSources } from '../src/recovery/pipeline.mjs';
import { ensureMatchingD8 } from '../src/backends/d8-runtime.mjs';
import { findInputs } from '../src/recovery/blob-files.mjs';

function firstRawInput(options) {
  const format = options.inputFormat ?? 'auto';
  if (format === 'disassembled' || format === 'serialized') return null;
  try {
    const inputs = findInputs(options.input, format);
    return inputs.find((input) => input.format === 'raw') ?? null;
  } catch {
    return null;
  }
}

function blobVersionHash(inputPath) {
  // The version hash sits at the header offset shared by all supported
  // cache-header layouts; a raw read is enough to memoize and match builds.
  try {
    const handle = fs.openSync(inputPath, 'r');
    const buffer = Buffer.alloc(8);
    fs.readSync(handle, buffer, 0, 8, 0);
    fs.closeSync(handle);
    return buffer.readUInt32LE(4);
  } catch {
    return null;
  }
}

function resolveDownloadedD8(options) {
  if (options.backend === 'profile' || options.noD8Download) return null;
  const input = firstRawInput(options);
  if (!input) return null;
  const versionHash = blobVersionHash(input.path);
  if (!versionHash) return null;
  process.stdout.write('No bundled profile for this V8 build; resolving matching d8 release...\n');
  return ensureMatchingD8({
    versionHash,
  });
}

function main() {
  const options = parseArguments(process.argv.slice(2), defaultOutputPath);
  if (options.help) return usage();
  let backend = createBackend(options, {
    engineRoot,
    python: options.python,
  });
  if (backend.id !== 'profile' && !options.d8Path && !options.d8Directory
    && !(backend.available?.() ?? false)) {
    const downloaded = resolveDownloadedD8(options);
    if (downloaded) {
      console.log(`Using downloaded d8: ${downloaded}`);
      options.d8Path = downloaded;
      backend = createBackend(options, {
        engineRoot,
        python: options.python,
      });
    }
  }
  return recoverSources(options, { engineRoot, sourceRecoveryEntryPath }, backend);
}

try {
  main();
} catch (error) {
  console.error(error.stack ?? error.message);
  process.exitCode = 1;
}
