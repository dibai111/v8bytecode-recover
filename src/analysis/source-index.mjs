const callKeywords = new Set([
  'catch',
  'do',
  'for',
  'function',
  'if',
  'switch',
  'while',
  'with',
]);

const referenceKeywords = new Set([
  'as', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger',
  'default', 'delete', 'do', 'else', 'export', 'extends', 'false', 'finally', 'for',
  'from', 'function', 'if', 'import', 'in', 'instanceof', 'let', 'new', 'null',
  'of', 'return', 'static', 'super', 'switch', 'this', 'throw', 'true', 'try',
  'typeof', 'var', 'void', 'while', 'with', 'yield',
]);

function isIdentifierStart(code) {
  return code === 36 || code === 95 || code >= 65 && code <= 90 || code >= 97 && code <= 122;
}

function isIdentifierPart(code) {
  return isIdentifierStart(code) || code >= 48 && code <= 57;
}

function skipQuoted(source, start, quote) {
  let index = start + 1;
  while (index < source.length) {
    if (source.charCodeAt(index) === 92) {
      index += 2;
      continue;
    }
    if (source[index] === quote) return index + 1;
    index += 1;
  }
  return source.length;
}

function skipLineComment(source, start) {
  const lineEnd = source.indexOf('\n', start + 2);
  return lineEnd < 0 ? source.length : lineEnd;
}

function skipBlockComment(source, start) {
  const end = source.indexOf('*/', start + 2);
  return end < 0 ? source.length : end + 2;
}

function canStartRegularExpression(previous) {
  return previous === undefined
    || ['!', '(', '[', '{', ',', ':', ';', '=', '=>', '?', 'return', 'case'].includes(previous);
}

function skipRegularExpression(source, start) {
  let index = start + 1;
  let inClass = false;
  while (index < source.length) {
    const character = source[index];
    if (character === '\\') {
      index += 2;
      continue;
    }
    if (character === '[') inClass = true;
    else if (character === ']') inClass = false;
    else if (character === '/' && !inClass) {
      index += 1;
      while (/[A-Za-z]/.test(source[index] ?? '')) index += 1;
      return index;
    }
    if (character === '\n') return start + 1;
    index += 1;
  }
  return start + 1;
}

function tokenize(source) {
  const tokens = [];
  let index = 0;
  let previous;

  while (index < source.length) {
    const code = source.charCodeAt(index);
    const character = source[index];
    if (/\s/.test(character)) {
      index += 1;
      continue;
    }
    if (character === '/' && source[index + 1] === '/') {
      index = skipLineComment(source, index);
      continue;
    }
    if (character === '/' && source[index + 1] === '*') {
      index = skipBlockComment(source, index);
      continue;
    }
    if (character === '/' && canStartRegularExpression(previous)) {
      const expressionEnd = skipRegularExpression(source, index);
      if (expressionEnd !== index + 1) {
        index = expressionEnd;
        continue;
      }
    }
    if (character === '\'' || character === '"' || character === '`') {
      index = skipQuoted(source, index, character);
      continue;
    }
    if (isIdentifierStart(code)) {
      const start = index;
      index += 1;
      while (index < source.length && isIdentifierPart(source.charCodeAt(index))) index += 1;
      const value = source.slice(start, index);
      tokens.push({ type: 'identifier', value, start, end: index });
      previous = value;
      continue;
    }
    if (code >= 48 && code <= 57) {
      index += 1;
      while (index < source.length && /[\w.]/.test(source[index])) index += 1;
      previous = 'number';
      continue;
    }

    const value = ['===', '!==', '>>>', '**=', '&&=', '||=', '??=', '=>', '?.', '++', '--',
      '&&', '||', '??', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '**', '<<', '>>',
      '...'].find((candidate) => source.startsWith(candidate, index)) ?? character;
    tokens.push({ type: 'punctuation', value, start: index, end: index + value.length });
    previous = value;
    index += value.length;
  }
  return tokens;
}

function matchingPairs(tokens) {
  const pairs = new Map();
  const opening = new Map([['(', ')'], ['[', ']'], ['{', '}']]);
  const closing = new Set([')', ']', '}']);
  const stack = [];
  for (const [index, token] of tokens.entries()) {
    if (opening.has(token.value)) stack.push({ index, close: opening.get(token.value) });
    else if (closing.has(token.value)) {
      const opener = stack.at(-1);
      if (!opener || opener.close !== token.value) continue;
      stack.pop();
      pairs.set(opener.index, index);
      pairs.set(index, opener.index);
    }
  }
  return pairs;
}

function lineStarts(source) {
  const starts = [0];
  for (let index = 0; index < source.length; index += 1) {
    if (source[index] === '\n') starts.push(index + 1);
  }
  return starts;
}

function lineNumber(starts, offset) {
  let low = 0;
  let high = starts.length;
  while (low + 1 < high) {
    const middle = Math.floor((low + high) / 2);
    if (starts[middle] <= offset) low = middle;
    else high = middle;
  }
  return low + 1;
}

function arrowName(tokens, arrowIndex, pairs) {
  let parameterIndex = arrowIndex - 1;
  let parametersStart = parameterIndex;
  let parametersEnd = parameterIndex;
  if (tokens[parameterIndex]?.value === ')') {
    parametersEnd = parameterIndex;
    parametersStart = pairs.get(parameterIndex) ?? parameterIndex;
    parameterIndex = parametersStart - 1;
  } else {
    parameterIndex -= 1;
  }
  if (tokens[parameterIndex]?.value === 'async') parameterIndex -= 1;
  const assignment = tokens[parameterIndex]?.value;
  const nameToken = assignment === '=' || assignment === ':'
    ? tokens[parameterIndex - 1]?.type === 'identifier'
      ? tokens[parameterIndex - 1]
      : null
    : null;
  const asyncStart = tokens[parametersStart - 1]?.value === 'async'
    ? parametersStart - 1
    : null;
  return {
    name: nameToken?.value ?? null,
    nameToken,
    parametersStart,
    parametersEnd,
    asyncStart,
  };
}

function statementStartToken(tokens, index) {
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    if ([';', '{', '}'].includes(tokens[cursor].value)) break;
    if (['const', 'let', 'var'].includes(tokens[cursor].value)) return cursor;
  }
  return index;
}

function arrowExpressionEnd(tokens, start, pairs) {
  let index = start;
  while (index < tokens.length) {
    const token = tokens[index];
    if (token.value === ';') return index;
    if (['(', '[', '{'].includes(token.value) && pairs.has(index)) {
      index = pairs.get(index) + 1;
      continue;
    }
    if ([',', ')', ']', '}'].includes(token.value)) return Math.max(start, index - 1);
    index += 1;
  }
  return Math.max(start, tokens.length - 1);
}

function assignedFunctionName(tokens, functionIndex) {
  const assignment = tokens[functionIndex - 1]?.value;
  if (assignment !== '=' && assignment !== ':') return null;
  return tokens[functionIndex - 2]?.type === 'identifier'
    ? tokens[functionIndex - 2]
    : null;
}

function functionDeclarations(source, tokens, pairs) {
  const declarations = [];

  for (const [index, token] of tokens.entries()) {
    if (token.value !== 'function') continue;
    let cursor = index + 1;
    if (tokens[cursor]?.value === '*') cursor += 1;
    const nameToken = tokens[cursor]?.type === 'identifier' ? tokens[cursor] : null;
    if (nameToken) cursor += 1;
    const parametersStart = tokens[cursor]?.value === '(' ? cursor : -1;
    if (parametersStart < 0) continue;
    const parametersEnd = pairs.get(parametersStart);
    const bodyStart = parametersEnd === undefined ? -1 : parametersEnd + 1;
    if (bodyStart < 0 || tokens[bodyStart]?.value !== '{') continue;
    const bodyEnd = pairs.get(bodyStart);
    if (bodyEnd === undefined) continue;
    const inferredNameToken = nameToken ? null : assignedFunctionName(tokens, index);
    const inferredPropertyName = !nameToken && tokens[index - 1]?.value === ':';
    const startTokenIndex = inferredNameToken
      && !inferredPropertyName
      ? statementStartToken(tokens, tokens.indexOf(inferredNameToken))
      : index;
    const startToken = tokens[startTokenIndex] ?? token;

    declarations.push({
      kind: 'function',
      anonymous: !nameToken && (!inferredNameToken || inferredPropertyName),
      name: nameToken?.value ?? inferredNameToken?.value ?? null,
      start: startToken.start,
      end: tokens[bodyEnd].end,
      bodyStart: tokens[bodyStart].end,
      bodyEnd: tokens[bodyEnd].start,
      parameters: source.slice(tokens[parametersStart].end, tokens[parametersEnd].start).trim(),
      declaration: source.slice(startToken.start, tokens[bodyStart].end),
      bodyTokenStart: bodyStart,
      bodyTokenEnd: bodyEnd,
    });
  }

  for (const [index, token] of tokens.entries()) {
    if (token.value !== '=>') continue;
    const details = arrowName(tokens, index, pairs);
    const bodyStartToken = tokens[index + 1];
    if (!bodyStartToken) continue;
    let bodyTokenStart;
    let bodyTokenEnd;
    let endToken;
    if (bodyStartToken.value === '{') {
      const bodyEnd = pairs.get(index + 1);
      if (bodyEnd === undefined) continue;
      bodyTokenStart = index + 1;
      bodyTokenEnd = bodyEnd;
      endToken = bodyEnd;
    } else {
      bodyTokenStart = index;
      endToken = arrowExpressionEnd(tokens, index + 1, pairs);
      bodyTokenEnd = endToken + 1;
    }
    const startTokenIndex = details.nameToken
      ? statementStartToken(tokens, tokens.indexOf(details.nameToken))
      : details.asyncStart ?? details.parametersStart;
    const startToken = tokens[startTokenIndex] ?? token;
    const parameters = details.parametersStart === details.parametersEnd
      ? tokens[details.parametersStart]?.value === ')'
        ? ''
        : tokens[details.parametersStart]?.value ?? ''
      : source.slice(
        tokens[details.parametersStart].end,
        tokens[details.parametersEnd].start,
      ).trim();
    const bodyStart = bodyStartToken.value === '{'
      ? bodyStartToken.end
      : tokens[index + 1].start;
    const bodyEnd = tokens[endToken].start;
    declarations.push({
      kind: 'arrow',
      anonymous: !details.name,
      name: details.name,
      start: startToken.start,
      end: tokens[endToken].end,
      bodyStart,
      bodyEnd,
      parameters,
      declaration: source.slice(startToken.start, bodyStartToken.end),
      bodyTokenStart,
      bodyTokenEnd,
    });
  }

  declarations.sort((left, right) => left.start - right.start || left.end - right.end);
  const nameCounts = new Map();
  let anonymousCount = 0;
  for (const declaration of declarations) {
    const baseName = declaration.name ?? `anonymous_${anonymousCount += 1}`;
    declaration.name = baseName;
    const count = (nameCounts.get(baseName) ?? 0) + 1;
    nameCounts.set(baseName, count);
    declaration.id = count === 1 ? baseName : `${baseName}#${count}`;
  }
  return declarations;
}

function assignParents(declarations) {
  const ordered = [...declarations].sort((left, right) => (
    left.start - right.start || right.end - left.end
  ));
  for (const declaration of ordered) {
    const parent = ordered
      .filter((candidate) => (
        candidate !== declaration
        && candidate.start < declaration.start
        && candidate.end >= declaration.end
      ))
      .sort((left, right) => (left.end - left.start) - (right.end - right.start))[0];
    declaration.parentId = parent?.id ?? null;
  }
}

function collectCalls(declaration, tokens, declarations) {
  const calls = new Set();
  const nestedRanges = declarations
    .filter((candidate) => (
      candidate !== declaration
      && candidate.start >= declaration.bodyStart
      && candidate.end <= declaration.bodyEnd
    ))
    .map((candidate) => ({ start: candidate.start, end: candidate.end }));
  for (let index = declaration.bodyTokenStart + 1; index < declaration.bodyTokenEnd; index += 1) {
    const token = tokens[index];
    if (nestedRanges.some((range) => token.start >= range.start && token.end <= range.end)) continue;
    if (token?.type !== 'identifier' || tokens[index + 1]?.value !== '(') continue;
    const previous = tokens[index - 1]?.value;
    const previousPrevious = tokens[index - 2]?.value;
    if (
      callKeywords.has(token.value)
      || previous === '.'
      || previous === '?.'
      || previous === 'function'
      || previous === '*' && previousPrevious === 'function'
    ) continue;
    calls.add(token.value);
  }
  return [...calls].sort();
}

function collectReferences(declaration, tokens, declarations) {
  const references = new Set();
  const nestedRanges = declarations
    .filter((candidate) => (
      candidate !== declaration
      && candidate.start >= declaration.bodyStart
      && candidate.end <= declaration.bodyEnd
    ))
    .map((candidate) => ({ start: candidate.start, end: candidate.end }));
  for (let index = declaration.bodyTokenStart + 1; index < declaration.bodyTokenEnd; index += 1) {
    const token = tokens[index];
    if (token?.type !== 'identifier') continue;
    if (nestedRanges.some((range) => token.start >= range.start && token.end <= range.end)) continue;
    const previous = tokens[index - 1]?.value;
    if (referenceKeywords.has(token.value) || previous === '.' || previous === '?.') continue;
    references.add(token.value);
  }
  return [...references].sort();
}

function buildRelationGraph(functions, relation) {
  const byName = new Map();
  const byId = new Map(functions.map((item) => [item.id, item]));
  for (const item of functions) {
    const matches = byName.get(item.name) ?? [];
    matches.push(item);
    byName.set(item.name, matches);
  }
  const nodes = functions.map(({ id, name, parentId, startLine, endLine }) => ({
    id,
    name,
    parentId,
    startLine,
    endLine,
  }));
  const edges = [];
  for (const item of functions) {
    for (const reference of item[relation] ?? []) {
      const candidates = byName.get(reference) ?? [];
      const scopes = new Set();
      let scope = item.id;
      while (scope) {
        scopes.add(scope);
        scope = byId.get(scope)?.parentId ?? null;
      }
      scopes.add(null);
      const visible = candidates.filter((candidate) => scopes.has(candidate.parentId));
      const distanceToScope = (candidate) => {
        let distance = 0;
        let current = item.id;
        while (current && current !== candidate.parentId) {
          current = byId.get(current)?.parentId ?? null;
          distance += 1;
        }
        return distance;
      };
      const nearestDistance = visible.length === 0
        ? Number.POSITIVE_INFINITY
        : Math.min(...visible.map(distanceToScope));
      const nearest = visible.filter((candidate) => distanceToScope(candidate) === nearestDistance);
      const target = nearest.length === 1 ? nearest[0] : null;
      edges.push({
        from: item.id,
        to: target?.id ?? null,
        name: reference,
        resolved: nearest.length === 1,
        ambiguous: nearest.length > 1,
        candidates: nearest.map((candidate) => candidate.id),
      });
    }
  }
  return { nodes, edges };
}

function buildCallGraph(functions) {
  return buildRelationGraph(functions, 'calls');
}

function buildReferenceGraph(functions) {
  return buildRelationGraph(functions, 'references');
}

function analyzeSource(source) {
  const tokens = tokenize(source);
  const pairs = matchingPairs(tokens);
  const starts = lineStarts(source);
  const declarations = functionDeclarations(source, tokens, pairs);
  assignParents(declarations);
  const declaredNames = new Set(declarations.map((item) => item.name));
  const functions = declarations.map((item) => {
    const calls = collectCalls(item, tokens, declarations);
    const references = collectReferences(item, tokens, declarations);
    return {
      id: item.id,
      name: item.name,
      kind: item.kind,
      anonymous: item.anonymous,
      parentId: item.parentId,
      parameters: item.parameters,
      declaration: item.declaration,
      startLine: lineNumber(starts, item.start),
      endLine: lineNumber(starts, item.end),
      startOffset: item.start,
      endOffset: item.end,
      sourceBytes: Buffer.byteLength(source.slice(item.start, item.end), 'utf8'),
      calls,
      references,
      unresolvedCalls: calls.filter((call) => !declaredNames.has(call)),
      unresolvedReferences: references.filter((reference) => !declaredNames.has(reference)),
      start: item.start,
      end: item.end,
      bodyTokenStart: item.bodyTokenStart,
      bodyTokenEnd: item.bodyTokenEnd,
    };
  });
  const publicFunctions = functions.map(({ start, end, bodyTokenStart, bodyTokenEnd, ...item }) => item);
  return {
    format: 1,
    sourceBytes: Buffer.byteLength(source, 'utf8'),
    functionCount: publicFunctions.length,
    functions: publicFunctions,
    callGraph: buildCallGraph(publicFunctions),
    referenceGraph: buildReferenceGraph(publicFunctions),
  };
}

function functionSource(source, item) {
  const value = source.slice(item.startOffset, item.endOffset);
  if (item.kind === 'function' && item.anonymous) return `(${value});\n`;
  return `${value}\n`;
}

export {
  analyzeSource,
  functionSource,
  tokenize,
};
