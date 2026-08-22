import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeDerivedSource } from '../src/recovery/source-transforms.mjs';
import { qualityMetrics } from '../src/validation/source-quality.mjs';

test('recovers a nested guarded loop without leaving temporary residue', () => {
  const source = [
    'function trimPath() {',
    '  let temporary0, temporary1, temporary2',
    '  while (temporary0) {',
    '    if ((((this.path).length) > 1)) {',
    '      temporary1 = (this.path)[0]',
    '      temporary0 = ""',
    '      temporary0 = (temporary1 === temporary0)',
    '      if (temporary0) {',
    '        this.path.shift()',
    '      }',
    '    }',
    '  }',
    '}',
  ].join('\n');

  const recovered = normalizeDerivedSource(source);
  const metrics = qualityMetrics(recovered);

  assert.match(recovered, /while \(.*this\.path.*length.*this\.path.*\[0\].*\) \{/);
  assert.doesNotMatch(recovered, /while \(temporary0\)/);
  assert.equal(metrics.guardedLoopResidues, 0);
  assert.equal(metrics.syntheticIdentityAssignments, 0);
});

test('lowers unresolved shared-function map closures to undefined', () => {
  const recovered = normalizeDerivedSource(
    'function init() {\n  temporary0 = create_closure("<shared_function_info_map>")\n}',
  );

  assert.match(recovered, /temporary0 = undefined/);
  assert.equal(qualityMetrics(recovered).runtimeHelpers, 0);
});

test('recovers sentinel index loops with a real bound and increment', () => {
  const source = [
    'function readArray() {',
    '  let temporary0, temporary1, temporary3, temporary5, temporary6, temporary7, temporary8',
    '  temporary5 = 0',
    '  temporary6 = 1',
    '  while (true) {',
    '    temporary8 = temporary5',
    '    if ((temporary6 == 1)) {',
    '      temporary6 = 0',
    '    }',
    '    else {',
    '      temporary8 += 1',
    '    }',
    '    temporary7 = 1',
    '    temporary0 = (temporary8 < temporary3)',
    '    if (temporary0) {',
    '    }',
    '    else {',
    '    }',
    '    while ((temporary7 == (1))) {',
    '      temporary1.push(temporary8)',
    '      temporary7 = 0',
    '      temporary5 = temporary8',
    '      temporary0 = temporary8',
    '    }',
    '    if ((temporary7 == 1)) {',
    '    }',
    '  }',
    '}',
  ].join('\n');

  const recovered = normalizeDerivedSource(source);
  const metrics = qualityMetrics(recovered);

  assert.match(recovered, /for \(temporary8 = temporary5; temporary8 < temporary3; temporary8 \+= 1\) \{/);
  assert.match(recovered, /temporary1\.push\(temporary8\)/);
  assert.doesNotMatch(recovered, /while \(true\)/);
  assert.equal(metrics.constantLoops, 0);
  assert.equal(metrics.guardedLoopResidues, 0);
  assert.equal(new Function(recovered) instanceof Function, true);
});

test('rebuilds a lexer termination jump as a valid token loop', () => {
  const source = [
    'function feed(arg0) {',
    '  let temporary0, temporary1, temporary2, temporary5',
    '  temporary1 = this.lexer',
    '  temporary1.reset(arg0, this.lexerState)',
    '  try {',
    '    temporary2 = temporary1.next()',
    '    if (!(temporary2)) {',
    '    }',
    '    else {',
    '      temporary0 = temporary1.next()',
    '      temporary2 = temporary1.next()',
    '      if (temporary0) goto offset_39',
    '    }',
    '  } catch (e) {',
    '    throw e',
    '  }',
    '  temporary5 = this.table[this.current]',
    '  this.current = (this.current + 1)',
    '  this.results = this.finish()',
    '  return this',
    '}',
  ].join('\n');

  const recovered = normalizeDerivedSource(source);
  const metrics = qualityMetrics(recovered);

  assert.doesNotMatch(recovered, /goto offset_/);
  assert.match(recovered, /for \(\;\;\) \{/);
  assert.match(recovered, /if \(!temporary2\) break/);
  assert.equal((recovered.match(/temporary1\.next\(\)/g) ?? []).length, 1);
  assert.equal(metrics.rawGotos, 0);
  assert.equal(new Function(recovered) instanceof Function, true);
});

test('folds a guarded temporary loop into its real condition', () => {
  const recovered = normalizeDerivedSource([
    'function consume(arg0) {',
    '  let temporary0',
    '  while (temporary0) {',
    '    if (arg0.length) {',
    '      arg0.shift()',
    '    }',
    '  }',
    '}',
  ].join('\n'));

  assert.match(recovered, /while \(arg0\.length\) \{/);
  assert.doesNotMatch(recovered, /while \(temporary0\)/);
  assert.equal(qualityMetrics(recovered).guardedLoopResidues, 0);
});

test('lowers safe async iterator scaffolding but preserves unproven generator residue', () => {
  const recovered = normalizeDerivedSource([
    'let fromReadable',
    'fromReadable = fromReadable',
    'function fromReadable() {',
    '  while (true) {',
    '    return CreateAsyncFromSyncIterator(iterator)',
    '  }',
    '}',
    'function resume() {',
    '  let temporary0, temporary1',
    '  temporary1 = temporary0',
    '  temporary0 = ResumeGenerator(temporary1)',
    '  throw temporary0',
    '}',
  ].join('\n'));

  assert.match(recovered, /for \(\;\;\) \{/);
  assert.match(recovered, /async function\* \(\) \{ yield\* iterator \}/);
  assert.match(recovered, /ResumeGenerator/);
  assert.doesNotMatch(recovered, /CreateAsyncFromSyncIterator/);
  assert.equal(qualityMetrics(recovered).constantLoops, 0);
  assert.equal(qualityMetrics(recovered).runtimeHelpers, 1);
  assert.doesNotThrow(() => new Function(recovered));
});

test('recovers a proven async yield delegation without resume-mode residue', () => {
  const recovered = normalizeDerivedSource([
    'async function* fromReadable(arg0) {',
    '  temporary9 = getIteratorSource(arg0)',
    '  temporary0 = temporary9[Symbol_asyncIterator]',
    '  if (!((temporary0 == null))) {',
    '    temporary0 = temporary0.call(temporary9)',
    '  }',
    '  else {',
    '    temporary0 = CreateAsyncFromSyncIterator((temporary9[Symbol_iterator]).call(temporary9))',
    '  }',
    '  temporary7 = temporary0',
    '  temporary8 = undefined',
    '  temporary0 = 0',
    '  temporary6 = 0',
    '  while (!(temporary0)) {',
    '    temporary0 = temporary7.next(temporary8)',
    '    temporary5 = await temporary0',
    '    if (!(temporary5.done)) {',
    '      temporary8 = yield temporary5.value',
    '      temporary0 = GeneratorGetResumeMode(temporary1)',
    '      temporary6 = GeneratorGetResumeMode(temporary1)',
    '    }',
    '  }',
    '  if (temporary6 === 1) {',
    '  }',
    '  else {',
    '    temporary2 = 1',
    '  }',
    '  return undefined',
    '}',
  ].join('\n'));

  assert.match(recovered, /temporary0 = yield\* temporary9/);
  assert.doesNotMatch(recovered, /GeneratorGetResumeMode|\.next\(/);
  assert.equal(qualityMetrics(recovered).runtimeHelpers, 0);
  assert.doesNotThrow(() => new Function(recovered));
});

test('preserves resume-mode residue when async delegation is not proven', () => {
  const recovered = normalizeDerivedSource([
    'async function* uncertain() {',
    '  temporary2 = yield value',
    '  temporary0 = GeneratorGetResumeMode(temporary1)',
    '  temporary3 = GeneratorGetResumeMode(temporary1)',
    '}',
  ].join('\n'));

  assert.match(recovered, /GeneratorGetResumeMode/);
  assert.equal(qualityMetrics(recovered).runtimeHelpers, 2);
  assert.doesNotThrow(() => new Function(recovered));
});
