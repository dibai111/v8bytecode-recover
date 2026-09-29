#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { engineRoot as configuredEngineRoot } from '../src/cli/paths.mjs';
import { positionalArgument } from '../src/cli/options.mjs';
import { argumentValue } from '../src/cli/options.mjs';
import { parseLanguage } from '../src/cli/options.mjs';
import { inspectFile, renderInspection } from '../src/cli/inspect-input.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const engineRoot = path.join(root, 'engine', 'v8asm');
const command = process.argv[2] ?? 'validate';
const commandArguments = process.argv.slice(3);

function usageError() {
  throw new Error(
    'Usage: v8bytecode-profiles [list|validate|identify|coverage|generate|discover] [options]\n'
      + '  generate --version VERSION [--output-dir DIR] [--report FILE]\n'
      + '  discover [--all] [--output FILE] [--report FILE]\n'
      + '  identify INPUT [--profile-dir DIR] [--language zh-TW|zh-CN|en] [--json]\n'
      + '  coverage --corpus DIR [--profile-dir DIR] [--json]',
  );
}

if (!['validate', 'list', 'identify', 'coverage', 'generate', 'discover'].includes(command)) usageError();

function optionValue(argumentsList, name) {
  const values = [];
  for (let index = 0; index < argumentsList.length; index += 1) {
    if (argumentsList[index] === name) {
      const value = argumentsList[index + 1];
      argumentValue(argumentsList, index, name);
      values.push(value);
      index += 1;
    }
  }
  return values;
}

function normalizeGeneratorArguments(argumentsList) {
  const pathOptions = new Set([
    '--cache-dir',
    '--output-dir',
    '--report',
    '--output',
    '--versions-file',
    '--v8-repo',
    '--corpus',
    '--profile-dir',
  ]);
  const normalized = [];
  for (let index = 0; index < argumentsList.length; index += 1) {
    const argument = argumentsList[index];
    normalized.push(argument);
    if (!pathOptions.has(argument)) continue;
    const value = argumentValue(argumentsList, index, argument);
    index += 1;
    normalized.push(path.resolve(value));
  }
  return normalized;
}

function defaultGeneratorDirectory(argumentsList) {
  if (optionValue(argumentsList, '--output-dir').length > 0) return null;
  const versions = optionValue(argumentsList, '--version');
  const suffix = versions.length === 1 ? versions[0] : 'generated';
  return path.join(process.cwd(), 'output', 'profiles', suffix);
}

function generatorArguments() {
  const argumentsList = [...commandArguments];
  const outputDirectory = defaultGeneratorDirectory(argumentsList);
  if (outputDirectory) argumentsList.push('--output-dir', outputDirectory);
  if (optionValue(argumentsList, '--report').length === 0) {
    const reportDirectory = optionValue(argumentsList, '--output-dir')[0];
    argumentsList.push('--report', path.join(reportDirectory, 'profile-generation-report.json'));
  }
  return normalizeGeneratorArguments(argumentsList);
}

const profileDirectory = optionValue(commandArguments, '--profile-dir')[0] ?? null;

if (command === 'identify') {
  const languageValue = optionValue(commandArguments, '--language')[0] ?? 'zh-TW';
  const language = parseLanguage(languageValue);
  const input = positionalArgument(commandArguments, ['--profile-dir', '--language']);
  if (!input) throw new Error('identify requires a .v8blob or .jsc input path');
  const report = inspectFile(
    path.resolve(input),
    configuredEngineRoot,
    'raw',
    profileDirectory ? path.resolve(profileDirectory) : null,
  );
  const json = commandArguments.includes('--json');
  process.stdout.write(json
    ? `${JSON.stringify(report, null, 2)}\n`
    : renderInspection([report], language));
  process.exitCode = report.profileKnown ? 0 : 2;
} else {
const generatedArguments = command === 'generate' ? generatorArguments() : [];
const discoverArguments = command === 'discover' ? normalizeGeneratorArguments(commandArguments) : [];
const coverageArguments = command === 'coverage' ? normalizeGeneratorArguments(commandArguments) : [];
const args = command === 'generate'
  ? ['-B', path.join(engineRoot, 'cached_data', 'tooling', 'generate_profiles.py'), ...generatedArguments]
  : command === 'discover'
    ? ['-B', path.join(engineRoot, 'cached_data', 'tooling', 'discover_versions.py'), ...discoverArguments]
  : ['-B', '-m', 'cached_data.profile_cli', command, ...coverageArguments];
if (command !== 'generate' && command !== 'coverage' && profileDirectory) {
  args.push('--profile-dir', path.resolve(profileDirectory));
}
const run = spawnSync('python', args, {
  cwd: command === 'generate' ? root : engineRoot,
  encoding: 'utf8',
  stdio: 'inherit',
  windowsHide: true,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' },
});
if (run.error) throw run.error;
if (command === 'generate') {
  const reportPath = optionValue(generatedArguments, '--report')[0];
  if (reportPath && fs.existsSync(reportPath)) {
    try {
      const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
      const summary = report.summary ?? {};
      console.log(
        `Generated ${summary.succeeded ?? 0}/${summary.requested ?? 0} V8 profile(s). `
          + `Report: ${reportPath}`,
      );
      for (const result of report.results ?? []) {
        if (result.status === 'failed') {
          console.log(`FAILED ${result.version}: ${result.error}`);
        }
      }
    } catch {
      console.log(`Profile generation finished. Report: ${reportPath}`);
    }
  }
}
process.exitCode = Number.isInteger(run.status) ? run.status : 1;
}
