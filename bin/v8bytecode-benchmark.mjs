#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { benchmark } from '../src/benchmark/runner.mjs';
import { loadCorpusManifest } from '../src/benchmark/corpus.mjs';
import { argumentValue, commaSeparatedValues } from '../src/cli/options.mjs';

function parseArguments(argv) {
  const options = {
    input: null,
    output: null,
    backends: ['profile'],
    d8Path: null,
    d8Directory: null,
    profile: null,
    profileDirectory: null,
    embedder: 'unknown',
    adapters: [],
    corpusManifest: null,
    failOnRegression: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--output') {
      options.output = argumentValue(argv, index, argument);
      index += 1;
    }
    else if (argument === '--backends') {
      options.backends = commaSeparatedValues(argumentValue(argv, index, argument), argument);
      index += 1;
    }
    else if (argument === '--d8') {
      options.d8Path = path.resolve(argumentValue(argv, index, argument));
      index += 1;
    }
    else if (argument === '--d8-dir') {
      options.d8Directory = argumentValue(argv, index, argument);
      index += 1;
      options.d8Directory = path.resolve(options.d8Directory);
    }
    else if (argument === '--profile') {
      options.profile = argumentValue(argv, index, argument);
      index += 1;
    }
    else if (argument === '--embedder') {
      options.embedder = argumentValue(argv, index, argument);
      index += 1;
      if (!['unknown', 'node', 'electron', 'chromium', 'custom'].includes(options.embedder)) {
        throw new Error('--embedder must be unknown, node, electron, chromium, or custom');
      }
    }
    else if (argument === '--profile-dir') {
      options.profileDirectory = argumentValue(argv, index, argument);
      index += 1;
      options.profileDirectory = path.resolve(options.profileDirectory);
    }
    else if (argument === '--adapter') {
      const adapterPath = argumentValue(argv, index, argument);
      index += 1;
      const resolved = path.resolve(adapterPath);
      const adapter = JSON.parse(fs.readFileSync(resolved, 'utf8'));
      adapter.cwd = adapter.cwd && !path.isAbsolute(adapter.cwd)
        ? path.resolve(path.dirname(resolved), adapter.cwd)
        : adapter.cwd;
      options.adapters.push(adapter);
    }
    else if (argument === '--corpus-manifest') {
      const manifestPath = argumentValue(argv, index, argument);
      index += 1;
      options.corpusManifest = loadCorpusManifest(manifestPath);
    }
    else if (argument === '--fail-on-regression') options.failOnRegression = true;
    else if (!argument.startsWith('-') && !options.input) options.input = path.resolve(argument);
    else throw new Error(`Unexpected benchmark argument: ${argument}`);
  }
  if (!options.input) throw new Error('Usage: v8bytecode-benchmark INPUT [--backends profile,d8] [--adapter JSON] [--profile-dir DIR]');
  for (const backend of options.backends) {
    if (!['profile', 'd8'].includes(backend)) throw new Error(`Unknown backend: ${backend}`);
  }
  return options;
}

const options = parseArguments(process.argv.slice(2));
  const entryPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'v8bytecode-recover.mjs');
const report = benchmark(entryPath, options.input, options.backends, options);
const serialized = `${JSON.stringify(report, null, 2)}\n`;
if (options.output) {
  fs.mkdirSync(path.dirname(path.resolve(options.output)), { recursive: true });
  fs.writeFileSync(path.resolve(options.output), serialized);
  console.log(`Benchmark report: ${path.resolve(options.output)}`);
} else {
  process.stdout.write(serialized);
}

if (report.results.some((result) => result.failed > 0 || result.exitCode !== 0)
  || options.failOnRegression && report.regression && !report.regression.passed) {
  process.exitCode = 2;
}
