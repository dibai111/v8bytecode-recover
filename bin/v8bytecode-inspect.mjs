#!/usr/bin/env node

import path from 'node:path';

import { engineRoot } from '../src/cli/paths.mjs';
import { argumentValue } from '../src/cli/options.mjs';
import { parseLanguage } from '../src/cli/options.mjs';
import { inspectInput, renderInspection } from '../src/cli/inspect-input.mjs';
import { listEmbedders } from '../src/backends/embedder-matrix.mjs';

function usage() {
  console.log(`V8 Blob 診斷工具

Usage:
  node bin/v8bytecode-inspect.mjs <blob-or-directory> [--profile-dir <dir>] [--embedder <name>] [--language zh-TW|zh-CN] [--json]

功能:
  顯示 raw blob 的大小、SHA-256、V8 version hash、內建 profile、header、payload
  和附近可用的 snapshot；亦支援 disassembly 及 recovery artifact。`);
}

function parseArguments(argv) {
  const options = {
    input: null,
    profileDirectory: null,
    embedder: 'unknown',
    language: 'zh-TW',
    json: false,
    help: false,
  };
  const embedderNames = new Set(listEmbedders(engineRoot).map((item) => item.id));
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--json') options.json = true;
    else if (argument === '--profile-dir') {
      options.profileDirectory = argumentValue(argv, index, argument);
      index += 1;
      options.profileDirectory = path.resolve(options.profileDirectory);
    }
    else if (argument === '--embedder') {
      options.embedder = argumentValue(argv, index, argument);
      index += 1;
      if (!embedderNames.has(options.embedder)) {
        throw new Error(`--embedder must be one of: ${[...embedderNames].join(', ')}`);
      }
    }
    else if (argument === '--language') {
      options.language = parseLanguage(argumentValue(argv, index, argument), argument);
      index += 1;
    }
    else if (argument === '-h' || argument === '--help') options.help = true;
    else if (!argument.startsWith('-') && !options.input) options.input = path.resolve(argument);
    else throw new Error(`Unknown option: ${argument}`);
  }
  if (!options.input && !options.help) throw new Error('請提供 .v8blob/.jsc 檔案或目錄');
  return options;
}

function main(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  if (options.help) return usage();
  const items = inspectInput(
    options.input,
    engineRoot,
    'auto',
    options.profileDirectory,
    options.embedder,
  );
  process.stdout.write(options.json
    ? `${JSON.stringify(items, null, 2)}\n`
    : renderInspection(items, options.language));
}

try {
  main();
} catch (error) {
  console.error(error.stack ?? error.message);
  process.exitCode = 1;
}

export { main, parseArguments };
