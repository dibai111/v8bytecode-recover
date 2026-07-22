#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { benchmark } from '../src/benchmark/runner.mjs';

function parseArguments(argv) {
  const options = {
    input: null,
    output: null,
    backends: ['profile'],
    d8Path: null,
    profile: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--output') options.output = argv[++index];
    else if (argument === '--backends') options.backends = argv[++index].split(',');
    else if (argument === '--d8') options.d8Path = path.resolve(argv[++index]);
    else if (argument === '--profile') options.profile = argv[++index];
    else if (!argument.startsWith('-') && !options.input) options.input = path.resolve(argument);
    else throw new Error(`Unexpected benchmark argument: ${argument}`);
  }
  if (!options.input) throw new Error('Usage: v8blob-benchmark INPUT [--backends profile,d8]');
  for (const backend of options.backends) {
    if (!['profile', 'd8'].includes(backend)) throw new Error(`Unknown backend: ${backend}`);
  }
  return options;
}

const options = parseArguments(process.argv.slice(2));
const entryPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'v8blob-to-js.mjs');
const report = benchmark(entryPath, options.input, options.backends, options);
const serialized = `${JSON.stringify(report, null, 2)}\n`;
if (options.output) {
  fs.mkdirSync(path.dirname(path.resolve(options.output)), { recursive: true });
  fs.writeFileSync(path.resolve(options.output), serialized);
  console.log(`Benchmark report: ${path.resolve(options.output)}`);
} else {
  process.stdout.write(serialized);
}

if (report.results.some((result) => result.failed > 0 || result.exitCode !== 0)) {
  process.exitCode = 2;
}
