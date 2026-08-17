#!/usr/bin/env node

import path from 'node:path';

import { engineRoot } from '../src/config/paths.mjs';
import { inspectInput, renderInspection } from '../src/inspection/inspect-input.mjs';

function usage() {
  console.log(`V8 Blob 診斷工具

Usage:
  node bin/v8bytecode-inspect.mjs <blob-or-directory> [--json]

功能:
  顯示 raw blob 的大小、SHA-256、V8 version hash、內建 profile、header、payload
  和附近可用的 snapshot；亦支援 disassembly 及 recovery artifact。`);
}

function parseArguments(argv) {
  const options = { input: null, json: false, help: false };
  for (const argument of argv) {
    if (argument === '--json') options.json = true;
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
  const items = inspectInput(options.input, engineRoot);
  process.stdout.write(options.json
    ? `${JSON.stringify(items, null, 2)}\n`
    : renderInspection(items));
}

try {
  main();
} catch (error) {
  console.error(error.stack ?? error.message);
  process.exitCode = 1;
}

export { main, parseArguments };
