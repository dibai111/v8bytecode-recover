import { tokenize } from './source-index.mjs';

const addressFunctionPattern = /^bytecode_[0-9a-f]+$/i;

function normalizeSourceNames(source) {
  const tokens = tokenize(source);
  const mapping = new Map();
  for (const token of tokens) {
    if (token.type !== 'identifier' || !addressFunctionPattern.test(token.value)) continue;
    if (!mapping.has(token.value)) {
      mapping.set(token.value, `function_${String(mapping.size + 1).padStart(3, '0')}`);
    }
  }
  if (mapping.size === 0) return { source, mappings: [] };

  const replacements = [];
  for (const [index, token] of tokens.entries()) {
    if (!mapping.has(token.value)) continue;
    const previous = tokens[index - 1]?.value;
    if (previous !== '.' && previous !== '?.') replacements.push(token);
  }
  const parts = [];
  let offset = 0;
  for (const token of replacements) {
    parts.push(source.slice(offset, token.start), mapping.get(token.value));
    offset = token.end;
  }
  parts.push(source.slice(offset));
  return {
    source: parts.join(''),
    mappings: [...mapping.entries()].map(([original, normalized]) => ({ original, normalized })),
  };
}

export { normalizeSourceNames };
