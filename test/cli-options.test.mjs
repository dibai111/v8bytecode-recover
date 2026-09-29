import assert from 'node:assert/strict';
import test from 'node:test';

import { parseArguments, parseLanguage } from '../src/cli/options.mjs';

test('accepts English for localized diagnostic output', () => {
  assert.equal(parseLanguage('en'), 'en');
});

test('parses disassembled input and report options', () => {
  const options = parseArguments([
    'sample.disassembly.txt',
    '--input-format', 'disassembled',
    '--emit', 'cfg',
    '--function', 'outer',
    '--func', 'inner',
    '--include-function', 'helper*',
    '--exclude-function', 'ignored',
    '--split-mode', 'calls',
    '--split-depth', '2',
    '--report', 'reports/result.json',
    '--resume',
    '--level', '2',
  ], 'output');

  assert.equal(options.inputFormat, 'disassembled');
  assert.deepEqual(options.emit, ['cfg']);
  assert.equal(options.level, 2);
  assert.deepEqual(options.functionNames, ['outer', 'inner']);
  assert.deepEqual(options.includeFunctions, ['helper*']);
  assert.deepEqual(options.excludeFunctions, ['ignored']);
  assert.equal(options.splitMode, 'calls');
  assert.equal(options.splitDepth, 2);
  assert.equal(options.resume, true);
  assert.match(options.input, /sample\.disassembly\.txt$/);
  assert.match(options.report, /reports[\\/]result\.json$/);
});

test('rejects unsupported input formats', () => {
  assert.throws(
    () => parseArguments(['input.jsc', '--input-format', 'pickle'], 'output'),
    /--input-format must be auto, raw, disassembled, or serialized/,
  );
});

test('uses automatic input detection by default', () => {
  const options = parseArguments(['input.jsc'], 'output');
  assert.equal(options.inputFormat, 'auto');
});

test('accepts hexadecimal payload offsets', () => {
  const options = parseArguments([
    'input.jsc',
    '--profile', '10.2.154.26',
    '--payload-offset', '0x20',
  ], 'output');
  assert.equal(options.payloadOffset, 32);
});

test('resolves an exact external profile directory', () => {
  const options = parseArguments([
    'input.jsc',
    '--profile-dir', 'profiles/14.7.57',
  ], 'output');

  assert.match(options.profileDirectory, /profiles[\\/]14\.7\.57$/);
});

test('accepts serialized recovery artifacts and serialized output', () => {
  const options = parseArguments([
    'sample.v8recovery.json',
    '--input-format', 'serialized',
    '--emit', 'serialized',
  ], 'output');
  assert.equal(options.inputFormat, 'serialized');
  assert.deepEqual(options.emit, ['serialized']);
});

test('accepts repeated and comma-separated analysis output kinds', () => {
  const options = parseArguments([
    'input.jsc',
    '--emit', 'disassembly,translated,cfg',
    '--emit', 'functions',
    '--emit', 'cfg,serialized',
  ], 'output');

  assert.deepEqual(options.emit, [
    'disassembly',
    'translated',
    'cfg',
    'functions',
    'serialized',
  ]);
});

test('parses embedder-aware runtime and research analysis options', () => {
  const options = parseArguments([
    'input.jsc',
    '--d8-dir', 'runtime/d8',
    '--embedder', 'electron',
    '--scope', 'outer',
    '--show-all',
    '--inline-depth', '3',
    '--inline-branch-limit', '8',
    '--research',
  ], 'output');
  assert.match(options.d8Directory, /runtime[\\\\/]d8$/);
  assert.equal(options.embedder, 'electron');
  assert.equal(options.scope, 'outer');
  assert.equal(options.showAll, true);
  assert.equal(options.inlineDepth, 3);
  assert.equal(options.inlineBranchLimit, 8);
  assert.deepEqual(options.emit, ['inline']);
  assert.equal(options.research, true);
});

test('reports missing option values instead of consuming the next flag', () => {
  assert.throws(
    () => parseArguments(['input.jsc', '--profile', '--backend', 'profile'], 'output'),
    /--profile requires a value/,
  );
  assert.throws(
    () => parseArguments(['input.jsc', '--emit', '--research'], 'output'),
    /--emit requires a value/,
  );
});

