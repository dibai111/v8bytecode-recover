import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeDerivedSource } from '../src/recovery/source-transforms.mjs';

test('removes top-level script bootstrap scaffolding', () => {
  const recovered = normalizeDerivedSource([
    'function anonymous() {',
    '  let temporary0, temporary1, temporary2',
    '  temporary1 = pushContext(create_block_context(ScopeInfo_0))',
    '  temporary2 = Calculator',
    '  Object.defineProperty(temporary2.prototype, "add", '
      + '{ value: add, writable: true, configurable: true })',
    '  context = temporary1',
    '  script_context[3] = temporary2',
    '  try {',
    '    console.log(((new Calculator(5)).add(3)))',
    '  } catch (err) {',
    '    temporary0 = console.error("failed", err)',
    '  }',
    '  return temporary0',
    '}',
  ].join('\n'));

  assert.doesNotMatch(recovered, /pushContext|create_block_context|script_context/);
  assert.match(recovered, /new Calculator\(5\)/);
  assert.match(recovered, /console\.log\(\(?\(?new Calculator\(5\)\)?\.add\(3\)\)?\)/);
});

test('drops DeclareGlobals hoisting calls and resolves script slot reads', () => {
  const recovered = normalizeDerivedSource(
    'function anonymous() {\n'
    + '  script_context[3] = hello("v8")\n'
    + '  return console.log(context_slot[3])\n'
    + '}',
  );

  assert.doesNotMatch(recovered, /DeclareGlobals|context_slot|script_context/);
  assert.match(recovered, /hello\("v8"\)/);
});
