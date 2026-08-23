import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { writeFunctionFiles } from '../src/analysis/function-files.mjs';
import { buildFunctionTree } from '../src/analysis/function-tree.mjs';
import { selectFunctions } from '../src/analysis/function-selection.mjs';
import { normalizeSourceNames } from '../src/analysis/name-normalization.mjs';
import { analyzeSource, functionSource } from '../src/analysis/source-index.mjs';

const source = `function outer(value) {
  const text = "function ignored() {}";
  function inner() { return helper(value); }
  return inner();
}
function helper(value) {
  return value;
}
`;

test('indexes nested functions without leaking nested calls to parents', () => {
  const analysis = analyzeSource(source);
  const outer = analysis.functions.find((item) => item.name === 'outer');
  const inner = analysis.functions.find((item) => item.name === 'inner');

  assert.equal(analysis.functionCount, 3);
  assert.equal(inner.parentId, 'outer');
  assert.deepEqual(outer.calls, ['inner']);
  assert.deepEqual(inner.calls, ['helper']);
  assert.equal(analysis.callGraph.edges.length, 2);
  assert.match(functionSource(source, outer), /^function outer/);
  assert.doesNotMatch(functionSource(source, outer), /function helper/);
});

test('writes a function directory and manifest', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-functions-test-'));
  try {
    const analysis = analyzeSource(source);
    const result = writeFunctionFiles(root, 'nested/sample.js', source, analysis);
    assert.equal(result.files.length, 3);
    assert.equal(fs.existsSync(result.manifestPath), true);
    assert.equal(fs.readdirSync(result.directory).length, 4);
    const manifest = JSON.parse(fs.readFileSync(result.manifestPath, 'utf8'));
    assert.equal(manifest.functionCount, 3);
    assert.match(fs.readFileSync(path.join(root, result.files[0]), 'utf8'), /^function /);

    const selected = writeFunctionFiles(root, 'nested/selected.js', source, analysis, {
      functionNames: ['outer'],
      mode: 'declarers',
      maxDepth: 1,
    });
    const selectedManifest = JSON.parse(fs.readFileSync(selected.manifestPath, 'utf8'));
    assert.equal(selected.functionCount, 2);
    assert.deepEqual(selectedManifest.functions.map((item) => item.name), ['outer', 'inner']);
    assert.equal(selectedManifest.selection.maxDepth, 1);
    fs.writeFileSync(path.join(selected.directory, 'keep-me.txt'), 'user file', 'utf8');
    const narrowed = writeFunctionFiles(root, 'nested/selected.js', source, analysis, {
      functionNames: ['inner'],
    });
    assert.equal(narrowed.functionCount, 1);
    assert.equal(fs.existsSync(path.join(selected.directory, 'keep-me.txt')), true);
    assert.equal(selected.files.some((file) => fs.existsSync(path.join(root, file))), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('keeps nested calls scoped to their declaring function', () => {
  const analysis = analyzeSource(`
function outer(a) {
  function inner() { return helper(a); }
  if (a) inner();
}
function helper(x) { return x; }
`);
  const outer = analysis.functions.find((item) => item.name === 'outer');
  const inner = analysis.functions.find((item) => item.name === 'inner');
  assert.equal(inner.parentId, outer.id);
  assert.deepEqual(outer.calls, ['inner']);
  assert.deepEqual(inner.calls, ['helper']);
  assert.equal(analysis.referenceGraph.edges.some((edge) => (
    edge.from === 'inner' && edge.to === 'helper' && edge.resolved
  )), true);
});

test('builds declarer and call trees with a bounded depth', () => {
  const analysis = analyzeSource(`
function outer() {
  function inner() { return helper(); }
  return inner();
}
function helper() { return 1; }
`);
  const declarers = buildFunctionTree(analysis, { root: 'outer', mode: 'declarers' });
  assert.deepEqual(declarers.nodes.map((item) => item.id), ['outer', 'inner']);
  assert.deepEqual(declarers.edges.map((edge) => [edge.from, edge.to]), [['outer', 'inner']]);

  const calls = buildFunctionTree(analysis, { root: 'outer', mode: 'calls', maxDepth: 1 });
  assert.deepEqual(calls.nodes.map((item) => item.id), ['outer', 'inner']);
  assert.deepEqual(calls.edges.map((edge) => [edge.from, edge.to]), [['outer', 'inner']]);
});

test('normalizes address-derived names without touching literals, comments, or properties', () => {
  const source = `
// bytecode_a must remain in this comment
function bytecode_a(bytecode_b) {
  const text = "bytecode_a";
  const pattern = /bytecode_a/;
  return bytecode_b + object.bytecode_a + bytecode_a;
}
`;
  const result = normalizeSourceNames(source);
  assert.deepEqual(result.mappings, [
    { original: 'bytecode_a', normalized: 'function_001' },
    { original: 'bytecode_b', normalized: 'function_002' },
  ]);
  assert.match(result.source, /function function_001\(function_002\)/);
  assert.match(result.source, /object\.bytecode_a/);
  assert.match(result.source, /"bytecode_a"/);
  assert.match(result.source, /\/bytecode_a\//);
  assert.match(result.source, /return function_002 \+ object\.bytecode_a \+ function_001/);
  assert.match(result.source, /comment/);
});

test('selects functions by name, wildcard, relation mode, and depth', () => {
  const analysis = analyzeSource(`
function outer() {
  function inner() { return helper(); }
  return inner();
}
function helper() { return 1; }
function ignored() { return 2; }
`);
  assert.deepEqual(
    selectFunctions(analysis, { functionNames: ['outer'] }).ids,
    ['outer'],
  );
  assert.deepEqual(
    selectFunctions(analysis, { functionNames: ['outer'], mode: 'declarers', maxDepth: 1 }).ids,
    ['outer', 'inner'],
  );
  assert.deepEqual(
    selectFunctions(analysis, { includeFunctions: ['*er'], excludeFunctions: ['outer', 'ignored'] }).ids,
    ['inner', 'helper'],
  );
  assert.deepEqual(
    selectFunctions(analysis, { functionNames: ['outer'], mode: 'calls', maxDepth: 2 }).ids,
    ['outer', 'inner', 'helper'],
  );
});

test('resolves duplicate function names in their nearest lexical scope', () => {
  const analysis = analyzeSource(`
function first() {
  function same() { return 1; }
  return same();
}
function second() {
  function same() { return 2; }
  return same();
}
`);
  const edges = analysis.callGraph.edges.filter((edge) => edge.name === 'same');
  assert.deepEqual(edges.map((edge) => [edge.from, edge.to, edge.resolved]), [
    ['first', 'same', true],
    ['second', 'same#2', true],
  ]);
});

test('indexes named block and expression arrow functions', () => {
  const analysisSource = `
const top = (value) => { return helper(value); };
const shorthand = value => helper(value);
function outer() {
  const nested = () => { return top(); };
  return nested();
}
function helper(value) { return value; }
`;
  const analysis = analyzeSource(analysisSource);
  const top = analysis.functions.find((item) => item.name === 'top');
  const shorthand = analysis.functions.find((item) => item.name === 'shorthand');
  const nested = analysis.functions.find((item) => item.name === 'nested');
  assert.equal(analysis.functionCount, 5);
  assert.equal(nested.parentId, 'outer');
  assert.deepEqual(top.calls, ['helper']);
  assert.deepEqual(shorthand.calls, ['helper']);
  assert.deepEqual(nested.calls, ['top']);
  assert.match(functionSource(analysisSource, top), /^const top/);
});

test('infers function-expression names and keeps anonymous callbacks valid', () => {
  const source = `
const callback = function(value) { return helper(value); };
const object = { property: function(value) { return helper(value); } };
const asyncCallback = async () => helper(1);
function use(items) {
  return items.map(function(item) { return helper(item); });
}
function helper(value) { return value; }
`;
  const analysis = analyzeSource(source);
  const callback = analysis.functions.find((item) => item.name === 'callback');
  const property = analysis.functions.find((item) => item.name === 'property');
  const asyncCallback = analysis.functions.find((item) => item.name === 'asyncCallback');
  const anonymous = analysis.functions.find((item) => item.anonymous && item.parentId === 'use');
  assert.equal(callback.kind, 'function');
  assert.equal(callback.parentId, null);
  assert.equal(property.anonymous, true);
  assert.match(functionSource(source, asyncCallback), /^const asyncCallback = async/);
  assert.equal(anonymous.parentId, 'use');
  assert.match(functionSource(source, callback), /^const callback = function/);
  assert.match(functionSource(source, anonymous), /^\(function/);
});
