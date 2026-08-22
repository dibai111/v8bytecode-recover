#!/usr/bin/env node

import path from 'node:path';

import { inspectEnvironment, renderEnvironment } from '../src/inspection/environment.mjs';
import { engineRoot } from '../src/cli/paths.mjs';
import { argumentValue } from '../src/cli/options.mjs';
import { parseLanguage } from '../src/cli/options.mjs';

function parseArguments(argv) {
  const options = {
    json: false,
    language: 'zh-TW',
    python: 'python',
    d8Path: null,
    d8Directory: null,
    profileDirectory: null,
    help: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--json') options.json = true;
    else if (argument === '--language') {
      options.language = parseLanguage(argumentValue(argv, index, argument), argument);
      index += 1;
    }
    else if (argument === '--python') {
      options.python = argumentValue(argv, index, argument);
      index += 1;
    }
    else if (argument === '--d8') {
      options.d8Path = argumentValue(argv, index, argument);
      index += 1;
      options.d8Path = path.resolve(options.d8Path);
    }
    else if (argument === '--d8-dir') {
      options.d8Directory = argumentValue(argv, index, argument);
      index += 1;
      options.d8Directory = path.resolve(options.d8Directory);
    }
    else if (argument === '--profile-dir') {
      options.profileDirectory = argumentValue(argv, index, argument);
      index += 1;
      options.profileDirectory = path.resolve(options.profileDirectory);
    }
    else if (argument === '-h' || argument === '--help') options.help = true;
    else throw new Error(`Unknown option: ${argument}`);
  }
  return options;
}

function usage() {
  console.log('Usage: node bin/v8bytecode-doctor.mjs [--json] [--language zh-TW|zh-CN] [--python <exe>] [--d8 <path>] [--d8-dir <dir>] [--profile-dir <dir>]');
}

function main(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  if (options.help) return usage();
  const report = inspectEnvironment({ engineRoot, ...options });
  process.stdout.write(options.json
    ? `${JSON.stringify(report, null, 2)}\n`
    : renderEnvironment(report, options.language));
  if (!report.ok) process.exitCode = 2;
}

try {
  main();
} catch (error) {
  console.error(error.stack ?? error.message);
  process.exitCode = 1;
}

export { main, parseArguments };
