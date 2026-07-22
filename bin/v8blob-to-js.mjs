#!/usr/bin/env node

import { parseArguments, usage } from '../src/cli/options.mjs';
import { createBackend } from '../src/backends/index.mjs';
import {
  defaultOutputPath,
  engineRoot,
  sourceRecoveryEntryPath,
} from '../src/config/paths.mjs';
import { recoverSources } from '../src/pipeline/recover-source.mjs';

function main() {
  const options = parseArguments(process.argv.slice(2), defaultOutputPath);
  if (options.help) return usage();
  const backend = createBackend(options, {
    engineRoot,
    python: options.python,
  });
  return recoverSources(options, { engineRoot, sourceRecoveryEntryPath }, backend);
}

try {
  main();
} catch (error) {
  console.error(error.stack ?? error.message);
  process.exitCode = 1;
}

export { main };
