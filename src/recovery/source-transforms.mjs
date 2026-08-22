import { recoverDelegatedAsyncGeneratorLoops } from './async-generator-transforms.mjs';

const commonJsBindings = new Set(['exports', 'require', 'module', '__filename', '__dirname']);

function unresolvedClosureBindings(source) {
  const declarations = new Set(
    [...source.matchAll(
      /^(?:async\s+)?function(?:\s*\*)?\s+([A-Za-z_$][\w$]*)\s*\(/gm,
    )].map((match) => match[1]),
  );
  return [...new Set(
    [...source.matchAll(/\bcreate_closure\(([A-Za-z_$][\w$]*)\)/g)]
      .map((match) => match[1])
      // A closure over a named function that is also constructed or called
      // elsewhere refers to a hoisted declaration of the original script; the
      // recovered source keeps the reference, which stays valid JavaScript.
      .filter((name) => !declarations.has(name))
      .filter((name) => {
        const usedElsewhere = new RegExp(`\\b(?:new\\s+${name}\\b|${name}\\s*\\()`)
          .test(source.replace(new RegExp(`\\bcreate_closure\\(${name}\\)`, 'g'), ''));
        return !usedElsewhere;
      }),
  )].sort();
}

function lowerScriptContextBootstrap(source) {
  // Top-level scripts wrap their body in an anonymous bootstrap that pushes a
  // block context, binds hoisted closures/classes to registers, and stores
  // them in script context slots. When the stored values name recovered
  // functions or classes, the scaffolding is pure setup and can be removed.
  if (!/\bcreate_block_context\(/.test(source)) return source;

  const lines = source.split('\n');
  const blockStart = lines.findIndex((line) => (
    /^\s*[A-Za-z_$][\w$]* = pushContext\(create_block_context\(.*\)\)\s*$/.test(line)
  ));
  if (blockStart < 0) return source;

  const registerNames = new Map();
  for (let index = blockStart + 1; index < lines.length; index += 1) {
    const binding = lines[index].match(
      /^(\s*)([A-Za-z_$][\w$]*) = ([A-Za-z_$][\w$]*)\s*;?\s*$/,
    );
    if (binding && !commonJsBindings.has(binding[3])) {
      registerNames.set(binding[2], binding[3]);
    }
  }
  if (registerNames.size === 0) return source;

  let cursor = blockStart + 1;
  let slotsSeen = 0;
  while (cursor < lines.length) {
    // 'context = <register>' restores the parent context and is scaffolding,
    // not a script slot; only script_context assignments carry values.
    const slot = lines[cursor].match(
      /^\s*script_context\[(\d+)\] = ([A-Za-z_$][\w$]*)\s*;?\s*$/,
    );
    if (slot) {
      if (!registerNames.has(slot[2])) return source;
      slotsSeen += 1;
    }
    cursor += 1;
  }
  if (slotsSeen === 0) return source;
  for (const name of registerNames.values()) {
    if (!/^[A-Za-z_$][\w$]*$/.test(name)) return source;
  }

  const setupLines = new Set();
  setupLines.add(blockStart);
  for (let index = blockStart + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\s*[A-Za-z_$][\w$]* = ([A-Za-z_$][\w$]*)\s*;?\s*$/.test(line)
      && !commonJsBindings.has(line.match(/=\s*([A-Za-z_$][\w$]*)/)?.[1] ?? '')) {
      setupLines.add(index);
      continue;
    }
    if (/^\s*(?:script_context\[\d+\]|context) = [A-Za-z_$][\w$]*\s*;?\s*$/.test(line)) {
      setupLines.add(index);
    }
  }

  const bodyLines = [];
  for (let index = blockStart + 1; index < lines.length; index += 1) {
    if (setupLines.has(index)) continue;
    const line = lines[index];
    if (/^\s*[A-Za-z_$][\w$]* = context\s*;?\s*$/.test(line)) continue;
    if (/\bcontext\b/.test(line.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/g, ''))) {
      return source;
    }
    bodyLines.push(line);
  }

  return [
    ...lines.slice(0, blockStart),
    ...bodyLines,
  ].join('\n');
}

function dropResidualScriptContextHelpers(source) {
  // When the strict bootstrap cleanup cannot prove every script slot binding,
  // a top-level `pushContext(create_block_context(...))` line still leaks into
  // output and fails the residue gate. Once the body no longer reads the bare
  // `context` register, the helper pair and its parent-context restore are
  // pure scaffolding and can go.
  const helperPattern = /^\s*[A-Za-z_$][\w$]* = pushContext\(create_(?:block|function)_context\(.*\)\)\s*;?\s*$/;
  const restorePattern = /^\s*context\s*=\s*[A-Za-z_$][\w$]*\s*;?\s*$/;
  const lines = source.split('\n');
  if (!lines.some((line) => helperPattern.test(line))) return source;

  const strippedStrings = (line) => (
    line.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/g, '')
  );
  const readsBareContext = lines.some((line) => {
    if (helperPattern.test(line) || restorePattern.test(line)) return false;
    return /\bcontext\b/.test(strippedStrings(line));
  });
  if (readsBareContext) return source;
  return lines.filter((line) => !helperPattern.test(line) && !restorePattern.test(line)).join('\n');
}

function topLevelFunctionBlocks(lines) {
  const starts = [];
  for (let index = 0; index < lines.length; index += 1) {
    if (/^function\s+[A-Za-z_$][\w$]*\s*\(/.test(lines[index])) starts.push(index);
  }
  return starts.map((start, position) => {
    let end = (starts[position + 1] ?? lines.length) - 1;
    while (end > start && lines[end].trim() === '') end -= 1;
    return { start, end, header: lines[start] };
  });
}

function lowerTopLevelScriptScaffolding(source) {
  const declaredFunctions = new Set(
    [...source.matchAll(/^(?:async\s+)?function(?:\s*\*)?\s+([A-Za-z_$][\w$]*)\s*\(/gm)]
      .map((match) => match[1]),
  );

  const lines = source.split('\n');
  const slotExpressions = new Map();
  for (const line of lines) {
    const assignment = line.match(/^\s*script_context\[(\d+)\]\s*=\s*(.+?)\s*;?\s*$/);
    if (!assignment) continue;
    if (slotExpressions.has(assignment[1])) continue;
    slotExpressions.set(assignment[1], assignment[2].trim());
  }

  let lowered = lines
    .filter((line) => {
      // DeclareGlobals only performs hoisting; the declared functions are
      // already present as recovered function declarations.
      if (/^\s*(?:[A-Za-z_$][\w$]*\s*=\s*)?DeclareGlobals\(/.test(line)) return false;
      return true;
    })
    .join('\n');

  lowered = lowerScriptContextBootstrap(lowered);

  lowered = lowered.replace(
    /^(\s*)script_context\[(\d+)\]\s*=\s*(.+?)\s*;?\s*$/gm,
    (match, indent, slot, rawExpression) => {
      const expression = stripBalancedOuterParentheses(rawExpression);
      if (expression === 'HOLE') return '';
      const identifier = expression.match(/^[A-Za-z_$][\w$]*$/)?.[0];
      if (identifier && declaredFunctions.has(identifier)) return match;
      return `${indent}scriptVar${slot} = ${expression}`;
    },
  );

  const usedSlots = [...lowered.matchAll(/\bscriptVar(\d+)\b/g)].map((m) => m[1]);
  const readSlots = [];
  for (const match of lowered.matchAll(/\bcontext_slot\[(\d+)\]/g)) {
    const expression = slotExpressions.get(match[1]);
    if (expression !== undefined) readSlots.push(match[1]);
  }
  const declaredSlots = new Set([...usedSlots, ...readSlots]);

  lowered = lowered.replace(/\bcontext_slot\[(\d+)\]/g, (match, slot) => {
    const expression = slotExpressions.get(slot);
    if (expression === undefined || expression === 'HOLE') return match;
    return expression;
  });

  const names = [...declaredSlots].sort((left, right) => Number(left) - Number(right)).map(
    (slot) => `scriptVar${slot}`,
  );
  if (names.length > 0 && /\bscriptVar\d+\b/.test(lowered)) {
    const bodyLines = lowered.split('\n');
    const firstBodyLine = bodyLines.findIndex((line) => line.trim());
    if (firstBodyLine === 0) {
      // The anonymous script bootstrap was unwrapped; declarations sit at
      // top level and must not carry the function-body indent.
      bodyLines.splice(firstBodyLine, 0, `let ${names.join(', ')}`);
    } else if (firstBodyLine > 0) {
      bodyLines.splice(firstBodyLine, 0, `  let ${names.join(', ')}`);
    }
    lowered = bodyLines.join('\n');
  }
  return lowered;
}

function unwrapTopLevelScriptBootstrap(source) {
  // V8 compiles a whole script file as a parameterless bootstrap function.
  // When that anonymous wrapper is the first top-level block, holds no
  // nested function declarations of its own scope beyond the recovered ones,
  // and its body never touches `arguments` or `this`, the wrapper is pure
  // scaffolding and its body can be inlined at top level.
  const lines = source.split('\n');
  const blocks = topLevelFunctionBlocks(lines);
  const wrapper = blocks[0];
  if (
    !wrapper
    || blocks.length < 1
    || !/^function anonymous\(\) \{$/.test(wrapper.header)
    || lines[wrapper.end]?.trim() !== '}'
  ) {
    return source;
  }

  const body = lines.slice(wrapper.start + 1, wrapper.end);
  if (body.some((line) => /\b(?:arguments|this)\b/.test(
    line.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/g, ''),
  ))) {
    return source;
  }
  while (body.at(-1)?.trim() === '') body.pop();
  // A top-level script's completion value is unobservable in recovered
  // source, so return statements inside the wrapper become plain drops.
  const unwrapped = [];
  for (const rawLine of body) {
    const line = rawLine.startsWith('  ') ? rawLine.slice(2) : rawLine;
    const returned = line.trim().match(/^return (?:undefined\s*)?;?$/);
    const valueReturn = line.trim().match(/^return (.+?);?$/);
    if (returned) continue;
    if (valueReturn) {
      unwrapped.push(`${valueReturn[1]};`);
      continue;
    }
    unwrapped.push(line);
  }
  // The wrapper's two-space indent leaks into inserted declarations; strip
  // leading blank-indent lines so the top level starts clean.
  while (unwrapped.length > 0 && unwrapped[0].trim() === '') unwrapped.shift();

  lines.splice(wrapper.start, wrapper.end - wrapper.start + 1, ...unwrapped);
  return lines.join('\n');
}

function unwrapCommonJsBootstrap(source) {
  let lines = source.split('\n');
  let blocks = topLevelFunctionBlocks(lines);
  let bootstrapRemoved = false;
  let wrapperName = null;

  if (blocks.length >= 2 && /^function anonymous\(\) \{$/.test(blocks[0].header)) {
    const first = blocks[0];
    const body = lines.slice(first.start + 1, first.end).filter((line) => line.trim() !== '');
    const returnedClosure = body.length === 1
      ? body[0].trim().match(/^return (anonymous\d*)$/)
      : null;
    if (returnedClosure) {
      wrapperName = returnedClosure[1];
      lines.splice(first.start, first.end - first.start + 1);
      while (lines[0]?.trim() === '') lines.shift();
      bootstrapRemoved = true;
    }
  }

  if (!bootstrapRemoved) return source;
  blocks = topLevelFunctionBlocks(lines);
  const wrapper = blocks.find((block) => (
    block.header === `function ${wrapperName}(arg0, arg1, arg2, arg3, arg4) {`
  ));
  if (!wrapper || lines[wrapper.end]?.trim() !== '}') return lines.join('\n');

  let body = lines.slice(wrapper.start + 1, wrapper.end);
  while (body.at(-1)?.trim() === '') body.pop();
  if (body.at(-1)?.trim() === 'return undefined') body.pop();
  body = body.map((line) => {
    const dedented = line.startsWith('  ') ? line.slice(2) : line;
    return dedented
      .replace(/\barg0\b/g, 'exports')
      .replace(/\barg1\b/g, 'require')
      .replace(/\barg2\b/g, 'module')
      .replace(/\barg3\b/g, '__filename')
      .replace(/\barg4\b/g, '__dirname');
  }).filter((line) => {
    const selfAssignment = line.trim().match(
      /^(exports|require|module|__filename|__dirname)\s*=\s*\1\s*;?$/,
    );
    return !selfAssignment;
  });
  lines.splice(wrapper.start, wrapper.end - wrapper.start + 1, ...body);
  return lines.join('\n');
}

function assignmentRightHandSide(line) {
  const match = line.trim().match(/^(?!if\b|while\b|for\b)(?:.+?)\s=\s(.+)$/);
  return match?.[1]?.replace(/;$/, '').trim() ?? null;
}

function removeDuplicatedAccumulatorExpressions(lines) {
  const output = [];
  for (let index = 0; index < lines.length; index += 1) {
    const current = lines[index];
    const expression = current.trim().replace(/;$/, '');
    const next = lines[index + 1];
    const currentLooksLikeExpression = expression
      && !/[=;]/.test(expression)
      && !/^(?:return|throw|if|while|for|switch|case|break|continue|function|const|let|var)\b/.test(expression)
      && !/[{}]$/.test(expression);
    if (currentLooksLikeExpression && next) {
      const nextExpression = assignmentRightHandSide(next)
        ?? next.trim().match(/^return\s+(.+?);?$/)?.[1]?.replace(/;$/, '').trim();
      const sameIndent = current.match(/^\s*/)?.[0] === next.match(/^\s*/)?.[0];
      if (sameIndent && nextExpression === expression) continue;
    }
    output.push(current);
  }
  return output;
}

function expressionForSubstitution(expression) {
  if (/^(?:[A-Za-z_$][\w$]*|-?\d+(?:\.\d+)?|true|false|null|undefined|NaN|Infinity)$/.test(expression)) {
    return expression;
  }
  if (expression.startsWith('(') && expression.endsWith(')')) return expression;
  return `(${expression})`;
}

function isLoopCarriedTemporary(lines, definitionIndex, consumerIndex, name) {
  const token = new RegExp(`\\b${name}\\b`);
  const assignment = new RegExp(`^\\s*${name}\\s*=`);
  const definitionIndent = lines[definitionIndex].match(/^\s*/)?.[0].length ?? 0;

  for (let start = definitionIndex - 1; start >= 0; start -= 1) {
    const line = lines[start];
    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    if (indent >= definitionIndent || !/^\s*while\s*\(.+\)\s*\{\s*$/.test(line)) continue;
    const end = endOfConditional(lines, start);
    if (end !== null && end >= definitionIndex && token.test(line)) return true;
  }

  if (!/^\s*while\s*\(.+\)\s*\{\s*$/.test(lines[consumerIndex])) return false;
  const loopEnd = endOfConditional(lines, consumerIndex);
  if (loopEnd === null) return false;
  return lines.slice(consumerIndex + 1, loopEnd).some((line) => assignment.test(line));
}

function inlineAdjacentTemporaries(lines) {
  const temporary = /\b(?:ACCU|r\d+)\b/g;
  for (let pass = 0; pass < 500; pass += 1) {
    let changed = false;
    for (let index = lines.length - 2; index >= 0; index -= 1) {
      const definition = lines[index].match(/^(\s*)(ACCU|r\d+)\s*=\s*(.+?);?\s*$/);
      if (!definition) continue;
      const [, indent, name, rawExpression] = definition;
      const expression = rawExpression.replace(/;$/, '').trim();
      if (new RegExp(`\\b${name}\\b`).test(expression)) continue;

      let consumerIndex = index + 1;
      while (consumerIndex < lines.length && lines[consumerIndex].trim() === '') consumerIndex += 1;
      if (consumerIndex >= lines.length) continue;
      if (isLoopCarriedTemporary(lines, index, consumerIndex, name)) continue;
      const consumer = lines[consumerIndex];
      if ((consumer.match(/^\s*/)?.[0] ?? '') !== indent) continue;
      if (/^(?:function\b|}\s*(?:else|catch|finally)?\b|case\b|default:)/.test(consumer.trim())) continue;
      if (/^(?:ACCU|r\d+)\s*(?:\+\+|--|\*\*=|<<=|>>>=|>>=|\+=|-=|\*=|\/=|%=|&=|\|=|\^=|&&=|\|\|=|\?\?=)/.test(consumer.trim())) {
        continue;
      }

      const consumerAssignment = consumer.match(/^(\s*)([^=]+?)\s=\s(.+?);?\s*$/);
      if (consumerAssignment && /[+\-*/%&|^!<>?]\s*$/.test(consumerAssignment[2])) continue;
      const searchable = consumerAssignment ? consumerAssignment[3] : consumer.trim();
      const uses = searchable.match(temporary)?.filter((value) => value === name).length ?? 0;
      if (uses !== 1) continue;
      const consumerRedefinesName = consumerAssignment?.[2].trim() === name;
      const consumerExits = /^(?:return|throw)\b/.test(consumer.trim());
      const conditionalEnd = /^if\s*\(.+\)\s*\{$/.test(consumer.trim())
        ? endOfConditional(lines, consumerIndex)
        : null;
      if (
        !consumerRedefinesName
        && !consumerExits
        && (
          hasUseBeforeRedefinition(lines, name, consumerIndex + 1)
          || (conditionalEnd !== null && hasUseBeforeRedefinition(lines, name, conditionalEnd + 1))
        )
      ) continue;

      const replacement = expressionForSubstitution(expression);
      if (consumerAssignment) {
        const rewrittenRhs = consumerAssignment[3].replace(new RegExp(`\\b${name}\\b`), replacement);
        lines[consumerIndex] = `${consumerAssignment[1]}${consumerAssignment[2].trimEnd()} = ${rewrittenRhs}`;
      } else {
        lines[consumerIndex] = consumer.replace(new RegExp(`\\b${name}\\b`), replacement);
      }
      lines.splice(index, 1);
      changed = true;
    }
    if (!changed) break;
  }
  return lines;
}

function endOfConditional(lines, start) {
  let depth = 0;
  let entered = false;
  let end = start;
  for (let index = start; index < lines.length; index += 1) {
    const structural = rewriteIdentifiersOutsideLiterals(lines[index], (identifier) => identifier)
      .replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/g, '');
    depth += (structural.match(/\{/g) ?? []).length;
    depth -= (structural.match(/\}/g) ?? []).length;
    if (depth > 0) entered = true;
    if (entered && depth === 0) {
      end = index;
      let next = index + 1;
      while (next < lines.length && lines[next].trim() === '') next += 1;
      if (lines[next]?.trim() === 'else {') {
        index = next - 1;
        continue;
      }
      return end;
    }
  }
  return null;
}

function hasUseBeforeRedefinition(lines, name, start) {
  const token = new RegExp(`\\b${name}\\b`);
  const assignment = new RegExp(`^\\s*${name}\\s*=`);
  for (let index = start; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^function\s+[A-Za-z_$][\w$]*\s*\(/.test(line)) return false;
    if (assignment.test(line)) {
      const rhs = line.slice(line.indexOf('=') + 1);
      return token.test(rhs);
    }
    if (token.test(line)) return true;
  }
  return false;
}

function rewriteIdentifiersOutsideLiterals(source, rewrite) {
  let output = '';
  let index = 0;
  while (index < source.length) {
    const char = source[index];
    const next = source[index + 1];

    if (char === '"' || char === "'" || char === '`') {
      const quote = char;
      output += char;
      index += 1;
      while (index < source.length) {
        const current = source[index];
        output += current;
        index += 1;
        if (current === '\\' && index < source.length) {
          output += source[index];
          index += 1;
        } else if (current === quote) break;
      }
      continue;
    }

    if (char === '/' && next === '/') {
      const end = source.indexOf('\n', index);
      if (end === -1) return output + source.slice(index);
      output += source.slice(index, end + 1);
      index = end + 1;
      continue;
    }

    if (char === '/' && next === '*') {
      const end = source.indexOf('*/', index + 2);
      if (end === -1) return output + source.slice(index);
      output += source.slice(index, end + 2);
      index = end + 2;
      continue;
    }

    if (/[A-Za-z_$]/.test(char)) {
      let end = index + 1;
      while (end < source.length && /[A-Za-z0-9_$]/.test(source[end])) end += 1;
      const identifier = source.slice(index, end);
      output += rewrite(identifier);
      index = end;
      continue;
    }

    output += char;
    index += 1;
  }
  return output;
}

function matchingParenthesis(text, openIndex) {
  let depth = 0;
  let quote = null;
  for (let index = openIndex; index < text.length; index += 1) {
    const char = text[index];
    if (quote !== null) {
      if (char === '\\') index += 1;
      else if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char;
      continue;
    }
    if (char === '(') depth += 1;
    else if (char === ')') {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

function replaceHelperCallsOnLine(line, helper, format) {
  let output = '';
  let index = 0;
  while (index < line.length) {
    const char = line[index];
    if (char === '"' || char === "'" || char === '`') {
      const quote = char;
      const start = index;
      index += 1;
      while (index < line.length) {
        if (line[index] === '\\') index += 2;
        else if (line[index++] === quote) break;
      }
      output += line.slice(start, index);
      continue;
    }
    if (line.startsWith(helper, index)) {
      const before = line[index - 1];
      const afterName = line[index + helper.length];
      if ((!before || !/[A-Za-z0-9_$]/.test(before)) && afterName === '(') {
        const close = matchingParenthesis(line, index + helper.length);
        if (close >= 0) {
          const argument = line.slice(index + helper.length + 1, close);
          output += format(argument, index, close, line);
          index = close + 1;
          continue;
        }
      }
    }
    output += char;
    index += 1;
  }
  return output;
}

function splitTopLevelArguments(source) {
  const argumentsList = [];
  let start = 0;
  let depth = 0;
  let quote = null;
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quote !== null) {
      if (char === '\\') index += 1;
      else if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char;
      continue;
    }
    if (char === '(' || char === '[' || char === '{') depth += 1;
    else if (char === ')' || char === ']' || char === '}') depth -= 1;
    else if (char === ',' && depth === 0) {
      argumentsList.push(source.slice(start, index).trim());
      start = index + 1;
    }
  }
  argumentsList.push(source.slice(start).trim());
  return argumentsList;
}

function lowerClassDefinitions(source) {
  const output = [];
  for (const line of source.split('\n')) {
    const callStart = line.indexOf('DefineClass(');
    if (callStart < 0) {
      output.push(line);
      continue;
    }
    const open = callStart + 'DefineClass'.length;
    const close = matchingParenthesis(line, open);
    if (close < 0 || line.slice(close + 1).trim().replace(/;$/, '') !== '') {
      output.push(line);
      continue;
    }
    const prefix = line.slice(0, callStart);
    const assignment = prefix.match(/^(\s*)(?:(.+?)\s*=\s*)?$/);
    if (!assignment) {
      output.push(line);
      continue;
    }
    const args = splitTopLevelArguments(line.slice(open + 1, close));
    let metadata;
    try {
      metadata = JSON.parse(args[0]);
    } catch {
      output.push(line);
      continue;
    }
    if (
      metadata?.__v8ClassBoilerplate !== true
      || !Array.isArray(metadata.properties)
      || metadata.argumentsCount !== args.length
      || args.length < 3
    ) {
      output.push(line);
      continue;
    }

    const indent = assignment[1];
    const resultTarget = assignment[2]?.trim() ?? null;
    const constructor = args[1];
    const superclass = args[2];
    const definitions = [];
    if (superclass === 'null') {
      definitions.push(`${indent}Object.setPrototypeOf(${constructor}.prototype, null)`);
    } else if (superclass !== 'HOLE') {
      definitions.push(`${indent}Object.setPrototypeOf(${constructor}, ${superclass})`);
      definitions.push(
        `${indent}Object.setPrototypeOf(${constructor}.prototype, ${superclass}.prototype)`,
      );
    }

    const properties = [...metadata.properties].sort(
      (left, right) => left.valueIndex - right.valueIndex,
    );
    let valid = true;
    for (const property of properties) {
      const value = args[property.valueIndex];
      const key = property.keyArgument === undefined
        ? property.keyExpression
        : args[property.keyArgument];
      if (
        !value
        || !key
        || !['static', 'instance'].includes(property.placement)
        || !['data', 'getter', 'setter'].includes(property.kind)
      ) {
        valid = false;
        break;
      }
      const target = property.placement === 'static'
        ? constructor
        : `${constructor}.prototype`;
      let descriptor;
      if (property.kind === 'data') {
        descriptor = `{ value: ${value}, writable: true, configurable: true }`;
      } else {
        const other = property.kind === 'getter' ? 'set' : 'get';
        const current = `Object.getOwnPropertyDescriptor(${target}, ${key})?.${other}`;
        descriptor = property.kind === 'getter'
          ? `{ get: ${value}, set: ${current}, configurable: true }`
          : `{ get: ${current}, set: ${value}, configurable: true }`;
      }
      definitions.push(
        `${indent}Object.defineProperty(${target}, ${key}, ${descriptor})`,
      );
    }
    if (!valid) {
      output.push(line);
      continue;
    }
    output.push(...definitions);
    if (resultTarget) {
      output.push(`${indent}${resultTarget} = ${constructor}.prototype`);
    }
  }
  return output.join('\n');
}

function lowerInternalHelperCalls(source) {
  return source.split('\n').map((rawLine) => {
    const condition = /^\s*(?:if|while)\s*\(/.test(rawLine);
    let line = rawLine;
    for (let pass = 0; pass < 20; pass += 1) {
      const previous = line;
      line = replaceHelperCallsOnLine(
        line,
        'truthy',
        (argument, start, close, currentLine) => {
          if (!condition) return `Boolean(${argument})`;
          const alreadyParenthesized = currentLine[start - 1] === '('
            && currentLine[close + 1] === ')';
          return alreadyParenthesized ? argument : `(${argument})`;
        },
      );
      line = replaceHelperCallsOnLine(line, 'isNullish', (argument) => `(${argument} == null)`);
      line = replaceHelperCallsOnLine(line, 'isUndetectable', (argument) => `(${argument} == null)`);
      line = replaceHelperCallsOnLine(
        line,
        'isJSReceiver',
        (argument) => `((value) => Object(value) === value)(${argument})`,
      );
      line = replaceHelperCallsOnLine(line, 'clone_object', (argument) => `({ ...${argument} })`);
      if (line === previous) break;
    }
    return line.replace(
      /^(\s*)(?:(?:[A-Za-z_$][\w$]*)\s*=\s*)?Throw(?:IteratorResultNotAnObject|Symbol(?:Async)?IteratorInvalid)\([^\n]*\)\s*;?$/,
      '$1throw new TypeError()',
    );
  }).join('\n');
}

function simplifyProvenBoundMethodCalls(source) {
  const ident = '[A-Za-z_$][\\w$]*';
  // `owner.method.call(owner, args)` is exactly `owner.method(args)`. The
  // callee may appear bare or parenthesized.
  const call = new RegExp(
    `\\(?(${ident}(?:\\.${ident})+)\\)?\\.call\\((${ident}(?:\\.${ident})*),\\s*`,
    'g',
  );
  const collapse = (text) => text.replace(call, (match, callee, receiver) => {
    const owner = callee.slice(0, callee.lastIndexOf('.'));
    return owner === receiver ? `${callee}(` : match;
  });

  let lowered = collapse(source);
  // V8 emits `CallProperty` as `callee.call(receiver, args)` where the callee
  // register was loaded by a separate `receiver.method` line. When that
  // definition is visible, substitute it so the call reads naturally.
  const definition = new RegExp(`^\\s*(${ident})\\s*=\\s*(${ident}(?:\\.${ident})+)\\s*;?\\s*$`);
  for (let pass = 0; pass < 20; pass += 1) {
    const lines = lowered.split('\n');
    const boundMethods = new Map();
    for (const line of lines) {
      const match = line.match(definition);
      if (!match) continue;
      const [, name, expression] = match;
      const existing = boundMethods.get(name);
      if (existing === undefined) boundMethods.set(name, expression);
      else if (existing !== expression) boundMethods.set(name, null);
    }
    if (boundMethods.size === 0) break;

    let changed = false;
    const rewritten = lines.map((line) => {
      if (/^\s*[A-Za-z_$][\w$]*\s*=/.test(line) && definition.test(line)) return line;
      // `callee.call(receiver, args...)` with a proven `callee = owner.method`
      // and owner === receiver reads as `owner.method(args...)`.
      const pattern = new RegExp(
        `\\b(${ident})\\.call\\((${ident}),\\s*([\\s\\S]*?)\\)`,
        'g',
      );
      return line.replace(pattern, (match, name, receiver, rest) => {
        const expression = boundMethods.get(name);
        if (!expression) return match;
        const owner = expression.slice(0, expression.lastIndexOf('.'));
        if (owner !== receiver) return match;
        changed = true;
        return `${expression}(${rest})`;
      });
    });
    lowered = collapse(rewritten.join('\n'));
    if (!changed) break;
  }
  return lowered;
}

function legalizeSyntheticTemporaries(source) {
  const lines = source.split('\n');
  const functionStarts = [];
  for (let index = 0; index < lines.length; index += 1) {
    if (/^function\s+[A-Za-z_$][\w$]*\s*\(/.test(lines[index])) functionStarts.push(index);
  }

  const regions = [];
  const firstFunction = functionStarts[0] ?? lines.length;
  if (firstFunction > 0) regions.push({ lines: lines.slice(0, firstFunction), functionBody: false });
  for (let position = 0; position < functionStarts.length; position += 1) {
    const start = functionStarts[position];
    const end = functionStarts[position + 1] ?? lines.length;
    regions.push({ lines: lines.slice(start, end), functionBody: true });
  }
  if (regions.length === 0) regions.push({ lines, functionBody: false });

  const rewrittenRegions = regions.map((region) => {
    const text = region.lines.join('\n');
    const allIdentifiers = new Set();
    const synthetic = new Set();
    rewriteIdentifiersOutsideLiterals(text, (identifier) => {
      allIdentifiers.add(identifier);
      if (identifier === 'ACCU' || /^r\d+$/.test(identifier)) synthetic.add(identifier);
      return identifier;
    });
    if (synthetic.size === 0) return region.lines;

    const ordered = [...synthetic].sort((left, right) => {
      const leftIndex = left === 'ACCU' ? -1 : Number(left.slice(1));
      const rightIndex = right === 'ACCU' ? -1 : Number(right.slice(1));
      return leftIndex - rightIndex;
    });
    const replacements = new Map();
    let temporaryIndex = 0;
    for (const identifier of ordered) {
      while (allIdentifiers.has(`temporary${temporaryIndex}`)) temporaryIndex += 1;
      const name = `temporary${temporaryIndex}`;
      replacements.set(identifier, name);
      allIdentifiers.add(name);
      temporaryIndex += 1;
    }

    const rewritten = rewriteIdentifiersOutsideLiterals(
      text,
      (identifier) => replacements.get(identifier) ?? identifier,
    ).split('\n');
    const names = [...replacements.values()];
    const indent = region.functionBody ? '  ' : '';
    const declarationLines = [];
    for (let index = 0; index < names.length; index += 8) {
      declarationLines.push(`${indent}let ${names.slice(index, index + 8).join(', ')}`);
    }
    rewritten.splice(region.functionBody ? 1 : 0, 0, ...declarationLines);
    return rewritten;
  });

  return rewrittenRegions.flat().join('\n');
}

function lowerHoleInitializersAndClosureNoops(source) {
  const functionNames = new Set(
    [...source.matchAll(/^function\s+([A-Za-z_$][\w$]*)\s*\(/gm)].map((match) => match[1]),
  );
  const lines = source.split('\n');
  const lowered = [];
  for (const line of lines) {
    const hole = line.match(/^(\s*)([A-Za-z_$][\w$]*)\s*=\s*HOLE\s*;?\s*$/);
    if (hole) {
      if (functionNames.has(hole[2])) {
        continue;
      } else if (hole[2] === 'ACCU' || /^r\d+$/.test(hole[2]) || /^temporary\d+$/.test(hole[2])) {
        lowered.push(`${hole[1]}${hole[2]} = undefined`);
      } else {
        lowered.push(`${hole[1]}let ${hole[2]}`);
      }
      continue;
    }
    const selfAssignment = line.match(/^\s*([A-Za-z_$][\w$]*)\s*=\s*\1\s*;?\s*$/);
    if (selfAssignment && functionNames.has(selfAssignment[1])) continue;
    lowered.push(line);
  }
  return lowered.join('\n');
}

function removeResolvedContextScaffolding(source) {
  if (/\b(?:context_slot|script_context|get_context_slot|set_context_slot|resolveContext)\b/.test(source)) {
    return source;
  }
  const lines = source.split('\n').filter((line) => {
    if (/^\s*context\s*=\s*(?:r\d+|temporary\d+)\s*;?\s*$/.test(line)) return false;
    if (/^\s*(?:r\d+|temporary\d+)\s*=\s*create_catch_context\(.+\)\s*;?\s*$/.test(line)) return false;
    if (/^\s*(?:r\d+|temporary\d+)\s*=\s*pushContext\((?:r\d+|temporary\d+)\)\s*;?\s*$/.test(line)) return false;
    return true;
  });
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const scaffold = lines[index].match(
      /^\s*((?:r\d+|temporary\d+))\s*=\s*pushContext\(create_(?:function|block)_context\(.+\)\)\s*;?\s*$/,
    );
    if (!scaffold) continue;
    lines.splice(index, 1);
  }
  return lines.join('\n');
}

function stripBalancedOuterParentheses(expression) {
  let result = expression.trim();
  while (result.startsWith('(') && matchingParenthesis(result, 0) === result.length - 1) {
    result = result.slice(1, -1).trim();
  }
  return result;
}

function recoverAsyncFunctions(source) {
  let lines = source.split('\n');
  const asyncFunctionStarts = new Set();
  for (let index = 0; index < lines.length; index += 1) {
    if (!/\bAsyncFunctionEnter\(/.test(lines[index])) continue;
    for (let header = index; header >= 0; header -= 1) {
      if (/^\s*function\s+[A-Za-z_$][\w$]*\s*\(/.test(lines[header])) {
        asyncFunctionStarts.add(header);
        break;
      }
    }
  }
  for (const index of asyncFunctionStarts) {
    lines[index] = lines[index].replace(/^(\s*)function\b/, '$1async function');
  }

  const recovered = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\s*(?:r\d+|temporary\d+)\s*=\s*AsyncFunctionEnter\([^)]*\)\s*$/.test(line)) {
      continue;
    }
    const awaitCall = line.match(
      /^(\s*)AsyncFunctionAwait(?:Caught|Uncaught)\(([^,]+),\s*(.+)\)\s*$/,
    );
    if (awaitCall) {
      const suspend = lines[index + 1]?.trim() ?? '';
      const resume = lines[index + 2]?.trim().match(
        /^((?:r\d+|temporary\d+))\s*=\s*ResumeGenerator\(([^)]+)\)$/,
      );
      const guard = lines[index + 3]?.trim() ?? '';
      const thrown = lines[index + 4]?.trim() ?? '';
      const close = lines[index + 5]?.trim() ?? '';
      const awaitedGenerator = awaitCall[2].trim();
      const guardMentionsGenerator = guard.includes(`GeneratorGetResumeMode(${awaitedGenerator})`);
      if (
        /^\/\/ SuspendGenerator\b/.test(suspend)
        && resume
        && resume[2].trim() === awaitedGenerator
        && /^if \(.+\) \{$/.test(guard)
        && guardMentionsGenerator
        && thrown === `throw ${resume[1]}`
        && close === '}'
      ) {
        const expression = stripBalancedOuterParentheses(awaitCall[3]);
        recovered.push(`${awaitCall[1]}${resume[1]} = await ${expression}`);
        index += 5;
        continue;
      }

      // Some V8 versions place a context/register save between the suspend
      // marker and ResumeGenerator. Keep those setup statements and lower
      // only the resume protocol to a native await expression.
      let resumeIndex = index + 2;
      let extendedResume = null;
      while (resumeIndex < Math.min(lines.length, index + 8)) {
        extendedResume = lines[resumeIndex]?.trim().match(
          /^((?:r\d+|temporary\d+))\s*=\s*ResumeGenerator\(([^)]+)\)$/,
        );
        if (extendedResume) break;
        resumeIndex += 1;
      }
      const extendedGuard = lines[resumeIndex + 1]?.trim() ?? '';
      const extendedThrown = lines[resumeIndex + 2]?.trim() ?? '';
      const extendedClose = lines[resumeIndex + 3]?.trim() ?? '';
      const bridge = lines.slice(index + 1, resumeIndex);
      const usesResume = lines
        .slice(resumeIndex + 4, Math.min(lines.length, resumeIndex + 8))
        .some((nextLine) => new RegExp(`\\b${extendedResume?.[1] ?? 'never'}\\b`).test(nextLine));
      if (
        extendedResume
        && bridge.some((bridgeLine) => /^\s*\/\/ SuspendGenerator\b/.test(bridgeLine))
        && extendedResume[2].trim() === awaitedGenerator
        && /^if \(.+\) \{$/.test(extendedGuard)
        && extendedGuard.includes(`GeneratorGetResumeMode(${awaitedGenerator})`)
        && extendedThrown === `throw ${extendedResume[1]}`
        && extendedClose === '}'
        && usesResume
      ) {
        const expression = stripBalancedOuterParentheses(awaitCall[3]);
        recovered.push(
          ...bridge.filter((bridgeLine) => !/^\s*\/\/ SuspendGenerator\b/.test(bridgeLine)),
        );
        recovered.push(`${awaitCall[1]}${extendedResume[1]} = await ${expression}`);
        index = resumeIndex + 3;
        continue;
      }
    }
    recovered.push(line);
  }

  return recovered.join('\n')
    .replace(/\breturn\s+AsyncFunctionResolve\([^,]+,\s*([^\n]+)\)/g, 'return $1')
    .replace(/\breturn\s+AsyncFunctionReject\([^,]+,\s*([^\n]+)\)/g, 'throw $1');
}

function emptyConditionalEnd(lines, start) {
  const condition = lines[start]?.trim().match(/^if\s*\((.+)\)\s*\{\s*$/);
  if (!condition) return null;
  const thenEnd = endOfBlockBeforeElse(lines, start);
  if (thenEnd === null || lines.slice(start + 1, thenEnd).some((line) => line.trim())) return null;
  let next = thenEnd + 1;
  while (next < lines.length && !lines[next].trim()) next += 1;
  if (lines[next]?.trim() !== 'else {') return thenEnd;
  const end = endOfConditional(lines, start);
  if (end === null) return null;
  const elseStart = next;
  if (lines.slice(elseStart + 1, end).some((line) => line.trim())) return null;
  return end;
}

function parseSentinelLoopCondition(line) {
  const match = line?.trim().match(/^while\s*\((.+)\)\s*\{\s*$/);
  if (!match) return null;
  const expression = stripBalancedOuterParentheses(match[1]);
  const direct = expression.match(/^([A-Za-z_$][\w$]*)\s*==\s*(.+)$/);
  if (direct && stripBalancedOuterParentheses(direct[2]) === '1') return { flag: direct[1] };

  const parts = splitTopLevelArguments(expression);
  if (parts.length !== 2) return null;
  const initializer = parts[0].match(/^([A-Za-z_$][\w$]*)\s*=\s*1$/);
  if (!initializer || initializer[1] !== parts[1].match(/^([A-Za-z_$][\w$]*)\s*=/)?.[1]) return null;
  const second = parts[1].match(/^([A-Za-z_$][\w$]*)\s*=\s*(.+)$/);
  if (!second || second[1] !== initializer[1]) return null;
  const comparison = stripBalancedOuterParentheses(second[2]).match(
    /^([A-Za-z_$][\w$]*)\s*==\s*([A-Za-z_$][\w$]*)$/,
  );
  if (!comparison || ![comparison[1], comparison[2]].includes(initializer[1])) return null;
  const flag = comparison[1] === initializer[1] ? comparison[2] : comparison[1];
  return { flag };
}

function recoverSentinelIndexLoops(source) {
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let start = 0; start < lines.length; start += 1) {
      const loop = lines[start].match(/^(\s*)while\s*\(\s*true\s*\)\s*\{\s*$/);
      if (!loop) continue;
      const outerEnd = endOfConditional(lines, start);
      if (outerEnd === null) continue;

      let cursor = start + 1;
      while (cursor < outerEnd && !lines[cursor].trim()) cursor += 1;
      const indexAssignment = lines[cursor]?.trim().match(
        /^([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\s*$/,
      );
      if (!indexAssignment) continue;
      const [, indexName, seedName] = indexAssignment;
      if (indexName === seedName) continue;

      const firstGuard = lines[cursor + 1]?.trim().match(/^if\s*\((.+)\)\s*\{\s*$/);
      if (!firstGuard) continue;
      const firstExpression = stripBalancedOuterParentheses(firstGuard[1]);
      const firstMatch = firstExpression.match(/^([A-Za-z_$][\w$]*)\s*==\s*1$/);
      if (!firstMatch) continue;
      const firstEnd = endOfBlockBeforeElse(lines, cursor + 1);
      const firstBody = firstEnd === null
        ? []
        : lines.slice(cursor + 2, firstEnd).map((line) => line.trim()).filter(Boolean);
      if (firstEnd === null || firstBody.length !== 1 || firstBody[0] !== `${firstMatch[1]} = 0`) continue;
      if (lines[firstEnd + 1]?.trim() !== 'else {') continue;
      const firstConditionalEnd = endOfConditional(lines, cursor + 1);
      if (firstConditionalEnd === null) continue;
      const firstElseBody = lines.slice(firstEnd + 2, firstConditionalEnd)
        .map((line) => line.trim())
        .filter(Boolean);
      if (firstElseBody.length !== 1) continue;
      const increment = firstElseBody[0].match(
        new RegExp(`^${indexName}\\s*(?:\\+=\\s*1|=\\s*\\(\\s*${indexName}\\s*\\+\\s*1\\s*\\))$`),
      );
      if (!increment) continue;

      cursor = firstConditionalEnd + 1;
      while (cursor < outerEnd && !lines[cursor].trim()) cursor += 1;
      const flagAssignment = lines[cursor]?.trim().match(
        /^([A-Za-z_$][\w$]*)\s*=\s*1\s*;?$/,
      );
      if (!flagAssignment) continue;
      const flagName = flagAssignment[1];
      cursor += 1;
      const conditionAssignment = lines[cursor]?.trim().match(
        /^([A-Za-z_$][\w$]*)\s*=\s*(.+?)\s*;?$/,
      );
      if (!conditionAssignment) continue;
      const conditionName = conditionAssignment[1];
      const condition = stripBalancedOuterParentheses(conditionAssignment[2]);
      cursor += 1;
      const conditionEnd = emptyConditionalEnd(lines, cursor);
      if (conditionEnd === null) continue;

      let innerStart = conditionEnd + 1;
      while (innerStart < outerEnd && !lines[innerStart].trim()) innerStart += 1;
      const innerCondition = parseSentinelLoopCondition(lines[innerStart]);
      if (!innerCondition || innerCondition.flag !== flagName) continue;
      const innerEnd = endOfConditional(lines, innerStart);
      if (innerEnd === null || innerEnd >= outerEnd) continue;

      let trailingStart = innerEnd + 1;
      while (trailingStart < outerEnd && !lines[trailingStart].trim()) trailingStart += 1;
      const trailingEnd = emptyConditionalEnd(lines, trailingStart);
      if (
        trailingEnd === null
        || lines.slice(trailingEnd + 1, outerEnd).some((line) => line.trim())
      ) continue;

      const body = lines.slice(innerStart + 1, innerEnd);
      const flagResetIndex = body.findIndex((line) => line.trim() === `${flagName} = 0`);
      const seedUpdateIndex = body.findIndex((line) => line.trim() === `${seedName} = ${indexName}`);
      if (flagResetIndex < 0 || seedUpdateIndex < 0) continue;
      if (body.some((line, index) => {
        if (index === flagResetIndex || index === seedUpdateIndex) return false;
        return new RegExp(`\\b${flagName}\\b`).test(line);
      })) continue;
      if (body.some((line) => new RegExp(`\\b${firstMatch[1]}\\b`).test(line))) continue;

      const bodyWithoutControl = body.filter((line, index) => (
        index !== flagResetIndex && index !== seedUpdateIndex
      ));
      if (!bodyWithoutControl.some((line) => line.trim())) continue;

      const loopIndent = loop[1];
      const innerIndent = lines[innerStart].match(/^\s*/)?.[0] ?? '';
      const dedent = Math.max(0, innerIndent.length - loopIndent.length);
      const recoveredBody = bodyWithoutControl.map((line) => (
        line.trim() ? line.slice(Math.min(dedent, line.length)) : line
      ));
      const recovered = [
        `${loopIndent}for (${indexName} = ${seedName}; ${condition}; ${indexName} += 1) {`,
        ...recoveredBody,
        `${loopIndent}}`,
        `${loopIndent}${firstMatch[1]} = 0`,
      ];
      lines.splice(start, outerEnd - start + 1, ...recovered);
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function recoverRawLexerLoops(source) {
  const lines = source.split('\n');
  for (let start = 0; start < lines.length; start += 1) {
    if (!/^\s*try\s*\{\s*$/.test(lines[start])) continue;
    const tryEnd = endOfConditional(lines, start);
    if (tryEnd === null) continue;
    const catchStart = lines.findIndex((line, index) => (
      index > start && index < tryEnd && /^\s*\}\s*catch\s*\([^)]*\)\s*\{\s*$/.test(line)
    ));
    if (catchStart < 0) continue;
    const rawGotoIndex = lines.findIndex((line, index) => (
      index > start && index < catchStart && /^\s*if\s*\(.+\)\s*goto offset_\d+\s*$/.test(line)
    ));
    if (rawGotoIndex < 0) continue;

    const read = lines[start + 1]?.trim().match(
      /^([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\.next\(\)\s*;?$/,
    );
    const emptyGuard = lines[start + 2]?.trim().match(/^if\s*\(!\((.+)\)\)\s*\{\s*$/);
    if (!read || !emptyGuard || emptyGuard[1].trim() !== read[1]) continue;
    const emptyGuardEnd = endOfBlockBeforeElse(lines, start + 2);
    if (emptyGuardEnd === null || lines.slice(start + 3, emptyGuardEnd).some((line) => line.trim())) continue;
    if (lines[emptyGuardEnd + 1]?.trim() !== 'else {') continue;
    const elseEnd = endOfConditional(lines, start + 2);
    if (elseEnd === null || rawGotoIndex >= elseEnd) continue;
    const receiver = read[2];
    const elseBody = lines.slice(emptyGuardEnd + 2, elseEnd).map((line) => line.trim()).filter(Boolean);
    if (elseBody.some((line) => (
      line !== lines[rawGotoIndex].trim()
      && !new RegExp(`^[A-Za-z_$][\\w$]*\\s*=\\s*${receiver}\\.next\\(\\)\\s*;?$`).test(line)
    ))) continue;

    const incrementIndex = lines.findIndex((line, index) => (
      index > catchStart && index < lines.length
      && /^\s*this\.current\s*=\s*\(this\.current\s*\+\s*1\)\s*;?\s*$/.test(line)
    ));
    const resultsIndex = lines.findIndex((line, index) => (
      index > incrementIndex && /^\s*this\.results\s*=\s*this\.finish\(\)\s*;?\s*$/.test(line)
    ));
    if (incrementIndex < 0 || resultsIndex < 0) continue;

    const indent = lines[start].match(/^\s*/)?.[0] ?? '';
    const shifted = (line) => (line.trim() ? `  ${line}` : line);
    const catchBlock = lines.slice(catchStart, tryEnd + 1).map(shifted);
    const processing = lines.slice(tryEnd + 1, incrementIndex + 1).map(shifted);
    const replacement = [
      `${indent}for (;;) {`,
      `${indent}  try {`,
      `${indent}    ${read[1]} = ${receiver}.next()`,
      `${indent}    if (!${read[1]}) break`,
      ...catchBlock,
      ...processing,
      `${indent}}`,
    ];
    lines.splice(start, incrementIndex - start + 1, ...replacement);
    break;
  }
  return lines.join('\n');
}

function removeInternalMetadataComments(source) {
  return source.split('\n').filter((line) => !/^\s*\/\/\s*(?:goto offset_|loop goto offset_|SwitchOnGeneratorState\b|SwitchOnSmiNoFeedback\b|SuspendGenerator\b|SetPendingMessage\b|Wide operand-scale prefix\b|ExtraWide operand-scale prefix\b)/i.test(line)).join('\n');
}

function removeUnreferencedOrphanBytecodeFunctions(source) {
  const counts = new Map();
  rewriteIdentifiersOutsideLiterals(source, (identifier) => {
    if (/^bytecode_[0-9a-f]+$/i.test(identifier)) {
      counts.set(identifier, (counts.get(identifier) ?? 0) + 1);
    }
    return identifier;
  });
  const lines = source.split('\n');
  const blocks = topLevelFunctionBlocks(lines);
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    const match = block.header.match(/^function\s+(bytecode_[0-9a-f]+)\s*\(/i);
    if (!match || counts.get(match[1]) !== 1) continue;
    lines.splice(block.start, block.end - block.start + 1);
  }
  return lines.join('\n').replace(/\n{3,}/g, '\n\n');
}

function materializeContextLocals(source) {
  const lines = source.split('\n');
  const functionNames = new Set(
    [...source.matchAll(/^function\s+([A-Za-z_$][\w$]*)\s*\(/gm)].map((match) => match[1]),
  );
  const reserved = new Set([
    'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger',
    'default', 'delete', 'do', 'else', 'enum', 'export', 'extends', 'false',
    'finally', 'for', 'function', 'if', 'import', 'in', 'instanceof', 'let',
    'new', 'null', 'return', 'static', 'super', 'switch', 'this', 'throw',
    'true', 'try', 'typeof', 'var', 'void', 'while', 'with', 'yield',
  ]);

  for (let markerIndex = 0; markerIndex < lines.length; markerIndex += 1) {
    const marker = lines[markerIndex].match(/^(\s*)\/\/ V8 context locals: (\[.*\])$/);
    if (!marker) continue;
    let names;
    try {
      names = JSON.parse(marker[2]);
    } catch {
      lines.splice(markerIndex, 1);
      markerIndex -= 1;
      continue;
    }

    let regionStart = 0;
    let header = null;
    for (let index = markerIndex - 1; index >= 0; index -= 1) {
      if (/^function\s+[A-Za-z_$][\w$]*\s*\(/.test(lines[index])) {
        regionStart = index;
        header = lines[index];
        break;
      }
    }
    let regionEnd = lines.length;
    for (let index = markerIndex + 1; index < lines.length; index += 1) {
      if (/^function\s+[A-Za-z_$][\w$]*\s*\(/.test(lines[index])) {
        regionEnd = index;
        break;
      }
    }
    if (marker[1] === '') {
      header = null;
      regionStart = 0;
      regionEnd = lines.findIndex((line) => /^function\s+/.test(line));
      if (regionEnd < 0) regionEnd = lines.length;
    }

    const declared = new Set();
    for (const line of lines.slice(regionStart, regionEnd)) {
      const declaration = line.trim().match(/^(?:let|const|var)\s+(.+)$/);
      if (!declaration) continue;
      for (const name of declaration[1].split(',')) {
        const identifier = name.trim().match(/^([A-Za-z_$][\w$]*)/)?.[1];
        if (identifier) declared.add(identifier);
      }
    }
    const parameters = new Set(
      (header?.match(/\(([^)]*)\)/)?.[1] ?? '')
        .split(',')
        .map((name) => name.trim())
        .filter(Boolean),
    );
    const missing = names.filter((name) => (
      /^[A-Za-z_$][\w$]*$/.test(name)
      && !reserved.has(name)
      && !(header === null && commonJsBindings.has(name))
      && !declared.has(name)
      && !parameters.has(name)
      && !functionNames.has(name)
    ));
    if (missing.length > 0) lines[markerIndex] = `${marker[1]}let ${missing.join(', ')}`;
    else {
      lines.splice(markerIndex, 1);
      markerIndex -= 1;
    }
  }
  return lines.join('\n');
}

function nestCapturedFunctions(source) {
  let lines = source.split('\n');
  while (true) {
    const blocks = topLevelFunctionBlocks(lines);
    const names = new Map();
    const captures = [];
    for (const block of blocks) {
      const name = block.header.match(/^function\s+([A-Za-z_$][\w$]*)\s*\(/)?.[1];
      if (name) names.set(name, block);
      const markerIndex = lines.findIndex((line, index) => (
        index > block.start
        && index <= block.end
        && /^\s*\/\/ V8 capture parent: /.test(line)
      ));
      if (markerIndex < 0) continue;
      const parent = lines[markerIndex].trim().slice('// V8 capture parent: '.length);
      captures.push({ block, markerIndex, name, parent });
    }
    const parentNames = new Set(captures.map((capture) => capture.parent));
    const capture = captures.find((item) => (
      item.name && names.has(item.parent) && !parentNames.has(item.name)
    ));
    if (!capture) break;

    const childLines = lines.slice(capture.block.start, capture.block.end + 1);
    childLines.splice(capture.markerIndex - capture.block.start, 1);
    lines.splice(capture.block.start, capture.block.end - capture.block.start + 1);
    while (lines[capture.block.start]?.trim() === '') lines.splice(capture.block.start, 1);

    const refreshedParent = topLevelFunctionBlocks(lines).find(
      (block) => block.header.match(/^function\s+([A-Za-z_$][\w$]*)\s*\(/)?.[1] === capture.parent,
    );
    if (!refreshedParent) continue;
    const indented = childLines.map((line) => (line ? `  ${line}` : line));
    lines.splice(refreshedParent.end, 0, '', ...indented);
  }
  lines = lines.filter((line) => !/^\s*\/\/ V8 (?:capture parent|context locals): /.test(line));
  return lines.join('\n').replace(/\n{3,}/g, '\n\n');
}

function guardedLoopShape(lines, start) {
  if (!/^\s*while\s*\(.+\)\s*\{\s*$/.test(lines[start] ?? '')) return null;
  const outerEnd = endOfConditional(lines, start);
  if (outerEnd === null) return null;
  let innerStart = start + 1;
  while (innerStart < outerEnd && !lines[innerStart].trim()) innerStart += 1;
  const prefixStart = innerStart;
  let conditionTargets = [];
  let condition = lines[innerStart]?.trim().match(/^if\s*\((.+)\)\s*\{$/);
  if (!condition) {
    const assignments = [];
    const targets = [];
    while (innerStart < outerEnd) {
      const assignment = lines[innerStart]?.trim().match(/^(temporary\d+)\s*=\s*(.+)$/);
      if (!assignment) break;
      assignments.push(`${assignment[1]} = ${assignment[2].trim()}`);
      targets.push(assignment[1]);
      innerStart += 1;
    }
    const guard = lines[innerStart]?.trim().match(/^if\s*\((.+)\)\s*\{$/);
    if (assignments.length === 0 || !guard || guard[1].trim() !== targets.at(-1)) return null;
    conditionTargets = [...new Set(targets)];
    condition = [guard[0], `(${assignments.join(', ')})`];
  }
  if (!condition) return null;
  const innerEnd = endOfConditional(lines, innerStart);
  if (innerEnd === null || innerEnd >= outerEnd) return null;
  const innerIndent = lines[innerStart].match(/^\s*/)?.[0] ?? '';
  if (lines.slice(innerStart + 1, innerEnd).some(
    (line) => line.startsWith(`${innerIndent}else {`),
  )) return null;
  if (lines.slice(innerEnd + 1, outerEnd).some((line) => line.trim())) return null;
  return {
    outerEnd,
    prefixStart,
    innerStart,
    innerEnd,
    condition: condition[1].trim(),
    conditionTargets,
  };
}

function endOfBlockBeforeElse(lines, start) {
  let depth = 0;
  for (let index = start; index < lines.length; index += 1) {
    const structural = rewriteIdentifiersOutsideLiterals(lines[index], (identifier) => identifier)
      .replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/g, '');
    depth += (structural.match(/\{/g) ?? []).length;
    depth -= (structural.match(/\}/g) ?? []).length;
    if (index > start && depth === 0) return index;
  }
  return null;
}

function recoverDuplicatedShortCircuitJoins(source) {
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let outerStart = 0; outerStart < lines.length; outerStart += 1) {
      const outer = lines[outerStart].match(/^(\s*)if\s*\(.+\)\s*\{$/);
      if (!outer) continue;
      const outerEnd = endOfBlockBeforeElse(lines, outerStart);
      if (outerEnd === null || lines[outerEnd + 1]?.trim() === 'else {') continue;

      let nestedStart = outerStart + 1;
      while (nestedStart < outerEnd && !/^\s*if\s*\(.+\)\s*\{$/.test(lines[nestedStart])) {
        if (lines[nestedStart].trim().endsWith('{')) break;
        nestedStart += 1;
      }
      if (nestedStart >= outerEnd || !/^\s*if\s*\(.+\)\s*\{$/.test(lines[nestedStart])) continue;
      const nestedEnd = endOfConditional(lines, nestedStart);
      const nestedThenEnd = endOfBlockBeforeElse(lines, nestedStart);
      if (nestedEnd === null || nestedThenEnd === null || nestedEnd !== outerEnd - 1) continue;
      if (lines[nestedThenEnd + 1]?.trim() !== 'else {') continue;

      let outsideStart = outerEnd + 1;
      while (outsideStart < lines.length && !lines[outsideStart].trim()) outsideStart += 1;
      let outsideIf = outsideStart;
      while (outsideIf < lines.length && !/^\s*if\s*\(.+\)\s*\{$/.test(lines[outsideIf])) {
        if (lines[outsideIf].trim().endsWith('{')) break;
        outsideIf += 1;
      }
      if (outsideIf >= lines.length || !/^\s*if\s*\(.+\)\s*\{$/.test(lines[outsideIf])) continue;
      const outsideEnd = endOfConditional(lines, outsideIf);
      if (outsideEnd === null) continue;

      const nestedCommon = lines.slice(nestedStart + 1, nestedThenEnd).map((line) => line.trim());
      const outsideCommon = lines.slice(outsideStart, outsideEnd + 1).map((line) => line.trim());
      if (nestedCommon.length !== outsideCommon.length) continue;
      if (nestedCommon.some((line, index) => line !== outsideCommon[index])) continue;

      const outsideSegment = lines.slice(outsideStart, outsideEnd + 1);
      lines.splice(
        outsideStart,
        outsideSegment.length,
        `${outer[1]}else {`,
        ...outsideSegment.map((line) => (line ? `  ${line}` : line)),
        `${outer[1]}}`,
      );
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function recoverSharedJoinLeafBlocks(source) {
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let outerStart = 0; outerStart < lines.length; outerStart += 1) {
      const outer = lines[outerStart].match(/^(\s*)if\s*\(.+\)\s*\{$/);
      if (!outer) continue;
      const outerEnd = endOfConditional(lines, outerStart);
      const outerThenEnd = endOfBlockBeforeElse(lines, outerStart);
      if (outerEnd === null || outerThenEnd === null || outerEnd !== outerThenEnd) continue;

      const ancestors = [];
      let current = outerStart;
      let leafThenBody = null;
      while (true) {
        const thenEnd = endOfBlockBeforeElse(lines, current);
        const fullEnd = endOfConditional(lines, current);
        if (thenEnd === null || fullEnd === null) break;
        if (lines[thenEnd + 1]?.trim() === 'else {') {
          leafThenBody = lines.slice(current + 1, thenEnd);
          break;
        }

        ancestors.push({ start: current, end: thenEnd });
        let nestedStart = current + 1;
        while (nestedStart < thenEnd && !lines[nestedStart].trim()) nestedStart += 1;
        if (!/^\s*if\s*\(.+\)\s*\{$/.test(lines[nestedStart] ?? '')) break;
        const nestedEnd = endOfConditional(lines, nestedStart);
        if (
          nestedEnd === null
          || lines.slice(nestedEnd + 1, thenEnd).some((line) => line.trim())
        ) break;
        current = nestedStart;
      }
      if (!leafThenBody || ancestors.length === 0 || leafThenBody.length === 0) continue;

      let outsideStart = outerEnd + 1;
      while (outsideStart < lines.length && !lines[outsideStart].trim()) outsideStart += 1;
      const outsideEnd = outsideStart + leafThenBody.length;
      const outsideBody = lines.slice(outsideStart, outsideEnd);
      if (outsideBody.length !== leafThenBody.length) continue;
      if (leafThenBody.some((line, index) => line.trim() !== outsideBody[index].trim())) continue;

      lines.splice(outsideStart, leafThenBody.length);
      const outsideIndent = outsideBody.find((line) => line.trim())?.match(/^\s*/)?.[0].length ?? 0;
      for (const ancestor of [...ancestors].sort((left, right) => right.end - left.end)) {
        const indent = lines[ancestor.start].match(/^\s*/)?.[0] ?? '';
        const body = outsideBody.map((line) => {
          if (!line.trim()) return line;
          return `${indent}  ${line.slice(Math.min(outsideIndent, line.length))}`;
        });
        lines.splice(ancestor.end + 1, 0, `${indent}else {`, ...body, `${indent}}`);
      }
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function resolveTemporaryAssignments(expression, assignments) {
  const values = new Map();
  for (const assignment of assignments) {
    let resolved = assignment.expression;
    for (let pass = 0; pass < assignments.length + 2; pass += 1) {
      const next = resolved.replace(/\btemporary\d+\b/g, (name) => {
        const value = values.get(name);
        return value === undefined ? name : `(${value})`;
      });
      if (next === resolved) break;
      resolved = next;
    }
    values.set(assignment.name, resolved);
  }
  let resolved = expression;
  for (let pass = 0; pass < assignments.length + 2; pass += 1) {
    const next = resolved.replace(/\btemporary\d+\b/g, (name) => {
      const value = values.get(name);
      return value === undefined ? name : `(${value})`;
    });
    if (next === resolved) break;
    resolved = next;
  }
  return resolved;
}

function hasLoopProgress(lines) {
  return lines.some((line) => {
    if (/\+\+|--|\+=|-=|\*=|\/=|\.(?:push|pop|shift|unshift|splice)\s*\(/.test(line)) {
      return true;
    }
    if (/^\s*([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*=\s*\(\1\s*[+-]\s*[^)]+\)\s*;?\s*$/.test(line)) {
      return true;
    }
    const statement = line.trim();
    return /\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*\(/.test(statement);
  });
}

function recoverGuardedLoopsWithConditionAssignments(source) {
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let start = 0; start < lines.length; start += 1) {
      const loop = lines[start].match(
        /^(\s*)while\s*\(\s*(temporary\d+)\s*\)\s*\{\s*$/,
      );
      if (!loop) continue;
      const outerEnd = endOfConditional(lines, start);
      if (outerEnd === null) continue;

      let cursor = start + 1;
      while (cursor < outerEnd && !lines[cursor].trim()) cursor += 1;
      const assignments = [];
      while (cursor < outerEnd) {
        const assignment = lines[cursor].trim().match(
          /^(temporary\d+)\s*=\s*(.+?);?\s*$/,
        );
        if (!assignment) break;
        assignments.push({ name: assignment[1], expression: assignment[2].trim() });
        cursor += 1;
      }
      if (assignments.length < 2) continue;

      const guard = lines[cursor]?.trim().match(
        new RegExp(`^if\\s*\\(\\s*(\\!?)(?:\\(\\s*)?${loop[2]}(?:\\s*\\))?\\s*\\)\\s*\\{$`),
      );
      if (!guard) continue;
      const innerEnd = endOfConditional(lines, cursor);
      if (innerEnd === null || innerEnd >= outerEnd) continue;
      if (lines.slice(innerEnd + 1, outerEnd).some((line) => line.trim())) continue;

      const conditionIndex = assignments.map((item) => item.name).lastIndexOf(loop[2]);
      if (conditionIndex <= 0) continue;
      const conditionAssignment = assignments[conditionIndex];
      if (!/[<>=!&|]/.test(conditionAssignment.expression)) continue;
      const condition = resolveTemporaryAssignments(
        conditionAssignment.expression,
        assignments.slice(0, conditionIndex),
      );
      const body = lines.slice(cursor + 1, innerEnd);
      if (!hasLoopProgress(body)) continue;

      const loopIndent = loop[1];
      const guardIndent = lines[cursor].match(/^\s*/)?.[0] ?? '';
      const dedent = Math.max(0, guardIndent.length - loopIndent.length);
      const recoveredCondition = guard[1] ? `!(${condition})` : condition;
      const recoveredBody = body.map((line) => (
        line.trim() ? line.slice(Math.min(dedent, line.length)) : line
      ));
      lines.splice(
        start,
        outerEnd - start + 1,
        `${loopIndent}while (${recoveredCondition}) {`,
        ...recoveredBody,
        `${loopIndent}}`,
      );
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function recoverGuardedLoopsWithNestedCondition(source) {
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let start = 0; start < lines.length; start += 1) {
      const loop = lines[start].match(
        /^(\s*)while\s*\(\s*(temporary\d+)\s*\)\s*\{\s*$/,
      );
      if (!loop) continue;

      const outerEnd = endOfConditional(lines, start);
      if (outerEnd === null) continue;
      let conditionStart = start + 1;
      while (conditionStart < outerEnd && !lines[conditionStart].trim()) conditionStart += 1;
      const conditionMatch = lines[conditionStart]?.trim().match(
        /^if\s*\((.+)\)\s*\{$/,
      );
      if (!conditionMatch) continue;

      const conditionThenEnd = endOfBlockBeforeElse(lines, conditionStart);
      if (
        conditionThenEnd === null
        || lines[conditionThenEnd + 1]?.trim() === 'else {'
        || lines.slice(conditionThenEnd + 1, outerEnd).some((line) => line.trim())
      ) continue;

      let cursor = conditionStart + 1;
      const assignments = [];
      while (cursor < conditionThenEnd) {
        const assignment = lines[cursor].trim().match(
          /^(temporary\d+)\s*=\s*(.+?);?\s*$/,
        );
        if (!assignment) break;
        assignments.push({ name: assignment[1], expression: assignment[2].trim() });
        cursor += 1;
      }
      if (assignments.length < 2) continue;

      const guard = lines[cursor]?.trim().match(
        new RegExp(`^if\\s*\\(\\s*${loop[2]}\\s*\\)\\s*\\{$`),
      );
      if (!guard) continue;
      const guardEnd = endOfBlockBeforeElse(lines, cursor);
      if (
        guardEnd === null
        || lines[guardEnd + 1]?.trim() === 'else {'
        || lines.slice(guardEnd + 1, conditionThenEnd).some((line) => line.trim())
      ) continue;

      const conditionIndex = assignments.map((item) => item.name).lastIndexOf(loop[2]);
      if (conditionIndex <= 0) continue;
      const resolvedCondition = resolveTemporaryAssignments(
        assignments[conditionIndex].expression,
        assignments.slice(0, conditionIndex),
      );
      if (!/[<>=!&|]/.test(resolvedCondition)) continue;

      const body = lines.slice(cursor + 1, guardEnd);
      if (body.length === 0 || !hasLoopProgress(body)) continue;

      const loopIndent = loop[1];
      const guardIndent = lines[cursor].match(/^\s*/)?.[0] ?? '';
      const dedent = Math.max(0, guardIndent.length - loopIndent.length);
      const recoveredBody = body.map((line) => (
        line.trim() ? line.slice(dedent) : line
      ));
      const condition = `(${conditionMatch[1].trim()}) && (${resolvedCondition})`;
      lines.splice(
        start,
        outerEnd - start + 1,
        `${loopIndent}while (${condition}) {`,
        ...recoveredBody,
        `${loopIndent}}`,
      );
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function removeSyntheticIdentityAssignments(source) {
  return source
    .split('\n')
    .filter((line) => !/^\s*(temporary\d+)\s*=\s*\1\s*;?\s*$/.test(line))
    .join('\n');
}

function removeDeadSyntheticStores(source) {
  // Synthetic registers (`temporaryN`) only exist to carry V8 accumulator or
  // register state between translated lines. A dead store of a plain value
  // reference is scaffolding; literals stay because storing them can document
  // initialization, and calls/yields stay for obvious reasons.
  const pureReference = /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\[[^\]\()]+\])*$/;
  const strippedStrings = (line) => (
    line.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/g, '')
  );
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      const match = lines[index].match(/^(\s*)(temporary\d+)\s*=\s*([^;]+?);?\s*$/);
      if (!match) continue;
      const [, , name, rawExpression] = match;
      const expression = rawExpression.trim();
      if (!pureReference.test(expression) || expression.includes('(')) continue;
      if (/^(?:true|false|null|undefined|NaN|Infinity)$/.test(expression)) continue;

      let consumed = false;
      for (let probe = index + 1; probe < lines.length && !consumed; probe += 1) {
        if (new RegExp(`\\b${name}\\b`).test(strippedStrings(lines[probe]))) consumed = true;
      }
      if (consumed) continue;
      lines.splice(index, 1);
      changed = true;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function recoverGuardedLoopsWithProvenUpdates(source) {
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let start = 0; start < lines.length; start += 1) {
      const shape = guardedLoopShape(lines, start);
      if (!shape) continue;
      const conditionRegisters = new Set(shape.condition.match(/\btemporary\d+\b/g) ?? []);
      for (const target of shape.conditionTargets) conditionRegisters.delete(target);
      if (conditionRegisters.size === 0) continue;
      const body = lines.slice(shape.innerStart + 1, shape.innerEnd);
      const updatesCondition = [...conditionRegisters].some((name) => {
        const assignment = new RegExp(`^\\s*${name}\\s*(?:\\+\\+|--|[+\\-*/%]?=)\\s*`);
        const identity = new RegExp(`^\\s*${name}\\s*=\\s*${name}\\s*;?\\s*$`);
        if (body.some((line) => assignment.test(line) && !identity.test(line))) return true;

        const lengthCondition = new RegExp(`\\b${name}\\.length\\b`).test(shape.condition);
        if (!lengthCondition) return false;
        const lengthMutator = new RegExp(
          `\\b${name}\\.(?:pop|push|shift|unshift|splice)\\s*\\(`,
        );
        return body.some((line) => lengthMutator.test(line));
      });
      if (!updatesCondition) continue;

      const indent = lines[start].match(/^\s*/)?.[0] ?? '';
      const innerIndent = lines[shape.innerStart].match(/^\s*/)?.[0] ?? '';
      const dedent = Math.max(0, innerIndent.length - indent.length);
      for (let index = shape.innerStart + 1; index < shape.innerEnd; index += 1) {
        if (lines[index].trim()) lines[index] = lines[index].slice(dedent);
      }
      const outerCondition = lines[start].trim().match(/^while\s*\((.+)\)\s*\{$/)?.[1].trim();
      const staleOuterCondition = /^(?:temporary\d+|true|false|0|1|null|undefined)$/.test(
        outerCondition ?? '',
      );
      const recoveredCondition = staleOuterCondition
        ? shape.condition
        : `(${outerCondition}) && (${shape.condition})`;
      lines[start] = `${indent}while (${recoveredCondition}) {`;
      lines.splice(shape.innerEnd, 1);
      lines.splice(shape.innerStart, 1);
      if (shape.prefixStart !== shape.innerStart) {
        lines.splice(shape.prefixStart, shape.innerStart - shape.prefixStart);
      }
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function recoverResidualGuardedLoops(source) {
  const lines = source.split('\n');
  for (let pass = 0; pass < 100; pass += 1) {
    let changed = false;
    for (let start = 0; start < lines.length; start += 1) {
      const shape = guardedLoopShape(lines, start);
      if (!shape) continue;
      const body = lines.slice(shape.innerStart + 1, shape.innerEnd);

      const indent = lines[start].match(/^\s*/)?.[0] ?? '';
      const innerIndent = lines[shape.innerStart].match(/^\s*/)?.[0] ?? '';
      const dedent = Math.max(0, innerIndent.length - indent.length);
      const recoveredBody = body.map((line) => (
        line.trim() ? line.slice(Math.min(dedent, line.length)) : line
      ));
      if (body.every((line) => !line.trim())) {
        lines.splice(
          start,
          shape.outerEnd - start + 1,
          `${indent}if (${shape.condition}) {`,
          `${indent}}`,
        );
        changed = true;
        break;
      }
      if (!hasLoopProgress(body)) continue;
      lines.splice(
        start,
        shape.outerEnd - start + 1,
        `${indent}while (${shape.condition}) {`,
        ...recoveredBody,
        `${indent}}`,
      );
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

function lowerResidualConstantLoops(source) {
  return source.split('\n').map((line) => {
    const match = line.match(/^(\s*)while\s*\(\s*([!()\s]*(?:true|false|0|1|null|undefined)[!()\s]*)\s*\)\s*\{$/);
    if (!match) return line;
    const expression = match[2].replace(/[()\s]/g, '');
    const negations = (expression.match(/!/g) ?? []).length;
    const value = expression.replaceAll('!', '');
    const isAlwaysTrue = (value === 'true' || value === '1') === (negations % 2 === 0);
    return isAlwaysTrue ? `${match[1]}for (;;) {` : `${match[1]}if (false) {`;
  }).join('\n');
}

function removeRedundantFunctionBindings(source) {
  const functionNames = new Set(
    [...source.matchAll(/^(?:async\s+)?function(?:\s*\*)?\s+([A-Za-z_$][\w$]*)\s*\(/gm)]
      .map((match) => match[1]),
  );
  if (functionNames.size === 0) return source;

  return source.split('\n').filter((line) => {
    const binding = line.match(/^let\s+([A-Za-z_$][\w$]*)\s*;?\s*$/);
    if (binding && functionNames.has(binding[1])) return false;
    const selfAssignment = line.match(
      /^([A-Za-z_$][\w$]*)\s*=\s*\1\s*;?\s*$/,
    );
    return !(selfAssignment && functionNames.has(selfAssignment[1]));
  }).join('\n');
}

function removeUnreachableStatements(source) {
  const lines = source.split('\n');
  const output = [];
  let deadIndent = null;
  for (const line of lines) {
    const stripped = line.trim();
    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    if (deadIndent !== null && stripped) {
      const isBoundary = indent < deadIndent
        || (indent === deadIndent
          && /^(?:function\b|async\s+function\b)/.test(stripped));
      if (!isBoundary) continue;
      deadIndent = null;
    }
    output.push(line);
    if (/^\s*(?:return\b|throw\b)/.test(line)) deadIndent = indent;
  }
  return output.join('\n');
}

function lowerAsyncIteratorHelpers(source) {
  return source.split('\n').map((line) => replaceHelperCallsOnLine(
    line,
    'CreateAsyncFromSyncIterator',
    (argument) => `(async function* () { yield* ${argument} })()`,
  )).join('\n');
}

function protectAutomaticSemicolonInsertion(source) {
  return source.split('\n').map((line) => {
    const stripped = line.trimStart();
    if (!stripped.startsWith('(') && !stripped.startsWith('[')) return line;
    const indent = line.slice(0, line.length - stripped.length);
    return `${indent};${stripped}`;
  }).join('\n');
}

function simplifyLowLevelExpressions(source) {
  let simplified = source.replace(
    /\bcreate_closure\(([A-Za-z_$][\w$]*)\)/g,
    '$1',
  );
  simplified = simplified.replace(
    /\bcreate_closure\(\s*"<shared_function_info_map>"\s*\)/g,
    'undefined',
  );
  simplified = unwrapCommonJsBootstrap(simplified);

  let lines = simplified.split('\n');
  for (let pass = 0; pass < 20; pass += 1) {
    const before = lines.join('\n');
    lines = removeDuplicatedAccumulatorExpressions(lines);
    lines = inlineAdjacentTemporaries(lines);
    if (lines.join('\n') === before) break;
  }
  return lines.join('\n');
}

function normalizeDerivedSource(source) {
  const lines = source.replaceAll('\r\n', '\n').split('\n');
  const normalized = [];
  for (const line of lines) {
    if (/^\s*\/\/ Bytecode 0x[0-9a-f]+\b/i.test(line)) continue;
    if (/^\s*\/\/ Constant pool:\s*$/.test(line)) continue;
    if (/^\s*\/\/\s+\[\d+\]\s*=/.test(line)) continue;
    if (/^\s*let ACCU = undefined;\s*$/.test(line)) continue;
    if (/^\s*let r\d+(?:, r\d+)*;\s*$/.test(line)) continue;
    normalized.push(
      line
        .replace(/\s*\/\*\s*(?:depth=[^*]*,\s*)?flags=[^*]*\*\//g, '')
        .replace(/[ \t]+$/g, ''),
    );
  }
  const withoutAnnotations = normalized.join('\n').replace(/^\s+|\s+$/g, '');
  let lowered = simplifyLowLevelExpressions(withoutAnnotations);
  lowered = lowerClassDefinitions(lowered);
  lowered = lowerHoleInitializersAndClosureNoops(lowered);
  lowered = materializeContextLocals(lowered);
  lowered = removeResolvedContextScaffolding(lowered);
  lowered = recoverAsyncFunctions(lowered);
  lowered = removeInternalMetadataComments(lowered);
  lowered = unwrapTopLevelScriptBootstrap(lowered);
  lowered = lowerTopLevelScriptScaffolding(lowered);
  lowered = dropResidualScriptContextHelpers(lowered);
  lowered = lowerInternalHelperCalls(lowered);
  lowered = simplifyProvenBoundMethodCalls(lowered);
  lowered = removeUnreferencedOrphanBytecodeFunctions(lowered);
  lowered = legalizeSyntheticTemporaries(lowered);
  lowered = nestCapturedFunctions(lowered);
  lowered = recoverRawLexerLoops(lowered);
  lowered = recoverSentinelIndexLoops(lowered);
  lowered = recoverSharedJoinLeafBlocks(lowered);
  lowered = recoverDuplicatedShortCircuitJoins(lowered);
  lowered = recoverGuardedLoopsWithConditionAssignments(lowered);
  lowered = recoverGuardedLoopsWithNestedCondition(lowered);
  lowered = recoverGuardedLoopsWithProvenUpdates(lowered);
  lowered = recoverResidualGuardedLoops(lowered);
  lowered = removeSyntheticIdentityAssignments(lowered);
  lowered = removeDeadSyntheticStores(lowered);
  lowered = removeRedundantFunctionBindings(lowered);
  lowered = removeUnreachableStatements(lowered);
  lowered = lowerAsyncIteratorHelpers(lowered);
  lowered = recoverDelegatedAsyncGeneratorLoops(lowered, {
    endOfConditional,
    stripBalancedOuterParentheses,
  });
  lowered = lowerResidualConstantLoops(lowered);
  lowered = protectAutomaticSemicolonInsertion(lowered);
  return `${lowered.replace(/^\s+|\s+$/g, '')}\n`;
}

export {
  guardedLoopShape,
  normalizeDerivedSource,
  unresolvedClosureBindings,
};
