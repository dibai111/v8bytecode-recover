import assert from 'node:assert/strict';
import test from 'node:test';

import { parseArguments } from '../src/cli/options.mjs';

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

test('accepts serialized recovery artifacts and serialized output', () => {
  const options = parseArguments([
    'sample.v8recovery.json',
    '--input-format', 'serialized',
    '--emit', 'serialized',
  ], 'output');
  assert.equal(options.inputFormat, 'serialized');
  assert.deepEqual(options.emit, ['serialized']);
});
