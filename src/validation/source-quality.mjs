import { guardedLoopShape } from '../recovery/source-transforms.mjs';

const runtimeHelperPattern = /\b(?:HOLE|__v8ctx|context_slot|script_context|create_array_literal|create_object_literal|create_closure|create_function_context|create_block_context|create_catch_context|pushContext|resolveContext|get_context_slot|set_context_slot|GetIterator|DeclareGlobals|DefineClass|DefineAccessorPropertyUnchecked|ensureDefined|ThrowIteratorResultNotAnObject|ThrowSymbol(?:Async)?IteratorInvalid|ForInEnumerate|ForInPrepare|ForInNext|ForInStep|ForInDone|GetEnumeratedKeyedProperty|_CopyDataPropertiesWithExcludedPropertiesOnStack|truthy|isNullish|isJSReceiver|isUndetectable|CreateAsyncFromSyncIterator|CreateJSGeneratorObject|AsyncGenerator(?:AwaitUncaught|Yield|Resolve|Reject)|Generator(?:GetResumeMode|Close)|ResumeGenerator)\b/g;

function countMatches(source, expression) {
  return (source.match(expression) ?? []).length;
}

function countSyntheticRegistersOutsideLiterals(source) {
  let count = 0;
  let index = 0;
  while (index < source.length) {
    const char = source[index];
    const next = source[index + 1];
    if (char === '"' || char === "'" || char === '`') {
      const quote = char;
      index += 1;
      while (index < source.length) {
        if (source[index] === '\\') index += 2;
        else if (source[index++] === quote) break;
      }
      continue;
    }
    if (char === '/' && next === '/') {
      const end = source.indexOf('\n', index);
      index = end < 0 ? source.length : end + 1;
      continue;
    }
    if (char === '/' && next === '*') {
      const end = source.indexOf('*/', index + 2);
      index = end < 0 ? source.length : end + 2;
      continue;
    }
    if (char === 'r' && /\d/.test(next ?? '') && !/[A-Za-z0-9_$]/.test(source[index - 1] ?? '')) {
      let end = index + 2;
      while (end < source.length && /\d/.test(source[end])) end += 1;
      if (!/[A-Za-z0-9_$]/.test(source[end] ?? '')) count += 1;
      index = end;
      continue;
    }
    index += 1;
  }
  return count;
}

function duplicateFunctionNames(source) {
  const names = [...source.matchAll(/^(?:async\s+)?function(?:\s*\*)?\s+([A-Za-z_$][\w$]*)\s*\(/gm)]
    .map((match) => match[1]);
  return names.length - new Set(names).size;
}

function unreachableStatementCount(source) {
  const lines = source.split('\n');
  let count = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const current = lines[index];
    if (!/^\s*(?:return\b|throw\b)/.test(current)) continue;
    const indent = current.match(/^\s*/)?.[0].length ?? 0;
    for (let next = index + 1; next < lines.length; next += 1) {
      const stripped = lines[next].trim();
      if (!stripped) continue;
      const nextIndent = lines[next].match(/^\s*/)?.[0].length ?? 0;
      if (nextIndent < indent) break;
      if (nextIndent === indent) {
        if (!/^(?:}|else\s*{|catch\b|finally\b|function\b|case\b|default\s*:)/.test(stripped)) count += 1;
        break;
      }
    }
  }
  return count;
}

function loopBindingIdentityAssignmentCount(source) {
  const lines = source.split('\n');
  let count = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const loop = lines[index].match(
      /^(\s*)for\s*\(\s*const\s+([A-Za-z_$][\w$]*)\s+(?:of|in)\b.*\)\s*\{\s*$/,
    );
    if (!loop) continue;
    const loopIndent = loop[1].length;
    const binding = loop[2];
    const identity = new RegExp(`^\\s*${binding}\\s*=\\s*${binding}\\s*;?\\s*$`);
    for (let bodyIndex = index + 1; bodyIndex < lines.length; bodyIndex += 1) {
      if (!lines[bodyIndex].trim()) continue;
      const bodyIndent = lines[bodyIndex].match(/^\s*/)?.[0].length ?? 0;
      if (bodyIndent <= loopIndent) break;
      if (identity.test(lines[bodyIndex])) count += 1;
    }
  }
  return count;
}

function guardedLoopResidueCount(source) {
  const lines = source.split('\n');
  let count = 0;
  for (let index = 0; index < lines.length; index += 1) {
    if (guardedLoopShape(lines, index)) count += 1;
  }
  return count;
}

function qualityMetrics(source) {
  const metrics = {
    accumulatorReferences: countMatches(source, /\bACCU\b/g),
    // Escaped replacement strings such as "\\r4" are not V8 registers.
    syntheticRegisterReferences: countSyntheticRegistersOutsideLiterals(source),
    rawGotos: countMatches(source, /\bgoto offset_|\boffset_\d+\b/g),
    unknownOpcodes: countMatches(source, /^\s*\/\/ 0x[0-9a-f]+ @/gmi),
    unresolvedObjects: countMatches(source, /<undefined:|<read_only_|\bread_only_\d+\b/g),
    pseudoValues: countMatches(
      source,
      /<(?:uninitialized_value|scope_info_map|ScopeInfo\b|Bytecode\b|ArrayBoilerplate\b|ObjectBoilerplate\b)/g,
    ),
    fallbackFunctions: countMatches(source, /WARNING: .*fallback to level-1 linear output/g),
    runtimeHelpers: countMatches(source, runtimeHelperPattern),
    bytecodeMetadata: countMatches(
      source,
      /Bytecode-derived|Blob SHA-256|V8 profile|Constant pool:|\/\/ Bytecode\b/g,
    ),
    orphanBytecodeFunctions: countMatches(source, /^function\s+bytecode_[0-9a-f]+\s*\(/gmi),
    internalOpcodes: countMatches(
      source,
      /^\s*\/\/\s*(?:Wide operand-scale prefix|ExtraWide operand-scale prefix|SetPendingMessage|Throw\w*|SwitchOnSmiNoFeedback)\b/gmi,
    ),
    unresolvedHeapValues: countMatches(
      source,
      /<(?:heap_number_map|read_only_[^>]*|undefined:[^>]*|uninitialized_value|scope_info_map|ScopeInfo\b)[^>]*>/gi,
    ),
    unreachableStatements: unreachableStatementCount(source),
    internalCaptureMarkers: countMatches(source, /\/\/ V8 (?:capture parent|context locals):/g),
    duplicateFunctionNames: duplicateFunctionNames(source),
    constantLoops: countMatches(
      source,
      /^\s*while\s*\([!()\s]*(?:true|false|0|1|null|undefined)[!()\s]*\)\s*\{/gm,
    ),
    syntheticIdentityAssignments: countMatches(
      source,
      /^\s*(temporary\d+)\s*=\s*\1\s*;?\s*$/gm,
    ),
    loopBindingIdentityAssignments: loopBindingIdentityAssignmentCount(source),
    guardedLoopResidues: guardedLoopResidueCount(source),
    asiHazards: countMatches(source, /^\s*[([]/gm),
  };
  metrics.residueFree = Object.values(metrics).every((value) => value === 0);
  return metrics;
}

function describeResidue(metrics, source = '') {
  const summary = Object.entries(metrics)
    .filter(([name, value]) => name !== 'residueFree' && value > 0)
    .map(([name, value]) => `${name}=${value}`)
    .join(', ');
  if (!metrics.runtimeHelpers || !source) return summary;
  const helpers = [...new Set(
    [...source.matchAll(runtimeHelperPattern)].map((match) => match[0]),
  )].sort();
  return `${summary}; helpers=${helpers.join(',')}`;
}

export { describeResidue, qualityMetrics };
