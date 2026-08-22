import assert from 'node:assert/strict';
import test from 'node:test';

import { buildFunctionTree } from '../src/analysis/function-tree.mjs';
import { buildInlineView } from '../src/analysis/inline-view.mjs';
import { restrictAnalysisToScope } from '../src/analysis/scope-view.mjs';
import { analyzeSource } from '../src/analysis/source-index.mjs';

const source = [
  'function outer() {',
  '  helper();',
  '  missing();',
  '  function helper() { return 1; }',
  '}',
  'function other() { return 2; }',
].join('\n');

test('keeps unresolved calls in show-all tree and inline views', () => {
  const analysis = analyzeSource(source);
  const tree = buildFunctionTree(analysis, {
    root: 'outer',
    mode: 'calls',
    includeUnresolved: true,
  });
  const inline = buildInlineView(analysis, {
    root: 'outer',
    maxDepth: 2,
    branchLimit: 2,
    showAll: true,
  });

  assert.equal(tree.nodes.some((node) => node.unresolved), true);
  assert.equal(inline.branchLimit, 2);
  assert.equal(inline.nodes.some((node) => node.unresolved), true);
});

test('restricts analysis to a lexical scope without changing source offsets', () => {
  const analysis = analyzeSource(source);
  const restricted = restrictAnalysisToScope(analysis, 'outer');
  assert.equal(restricted.scope.name, 'outer');
  assert.deepEqual(restricted.analysis.functions.map((item) => item.name), ['outer', 'helper']);
  assert.equal(restricted.analysis.functions[0].startOffset, analysis.functions[0].startOffset);
  assert.equal(restricted.analysis.callGraph.edges.every((edge) => edge.from === 'outer' || edge.from === 'helper'), true);
});
