import { buildFunctionTree, treeModes } from './function-tree.mjs';

const defaultSplitMode = 'declarers';

function splitPatterns(values) {
  return (values ?? [])
    .flatMap((value) => String(value).split(','))
    .map((value) => value.trim())
    .filter(Boolean);
}

function wildcardRegExp(pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped.replaceAll('*', '.*').replaceAll('?', '.')}$`);
}

function matchesFunctionPattern(item, pattern) {
  if (item.id === pattern || item.name === pattern) return true;
  if (!pattern.includes('*') && !pattern.includes('?')) return false;
  return wildcardRegExp(pattern).test(item.id) || wildcardRegExp(pattern).test(item.name);
}

function matchesAnyPattern(item, patterns) {
  return patterns.some((pattern) => matchesFunctionPattern(item, pattern));
}

function matchingFunctions(functions, patterns) {
  return functions.filter((item) => matchesAnyPattern(item, patterns));
}

function normalizeMaxDepth(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value === Number.POSITIVE_INFINITY) return null;
  if (!Number.isInteger(value) || value < 0) {
    throw new Error('Function split depth must be a non-negative integer');
  }
  return value;
}

function relationRoots(analysis) {
  return analysis.functions.filter((item) => item.parentId === null);
}

function collectTreeIds(analysis, roots, mode, maxDepth) {
  const ids = new Set();
  for (const root of roots) {
    const tree = buildFunctionTree(analysis, {
      root: root.id,
      mode,
      maxDepth: maxDepth ?? Number.POSITIVE_INFINITY,
    });
    for (const node of tree.nodes) ids.add(node.id);
  }
  return ids;
}

function selectFunctions(analysis, options = {}) {
  const patterns = splitPatterns(options.functionNames ?? options.functions);
  const include = splitPatterns(options.include ?? options.includeFunctions);
  const exclude = splitPatterns(options.exclude ?? options.excludeFunctions);
  const mode = options.mode ?? defaultSplitMode;
  if (!treeModes.has(mode)) throw new Error(`Unsupported function split mode: ${mode}`);
  const maxDepth = normalizeMaxDepth(options.maxDepth ?? options.splitDepth);
  const explicitMatches = patterns.length > 0
    ? matchingFunctions(analysis.functions, patterns)
    : [];
  if (patterns.length > 0 && explicitMatches.length === 0) {
    throw new Error(`No function matched: ${patterns.join(', ')}`);
  }

  const expand = options.expand ?? (
    mode !== defaultSplitMode || maxDepth !== null
  );
  let selectedIds;
  if (expand) {
    const roots = explicitMatches.length > 0 ? explicitMatches : relationRoots(analysis);
    selectedIds = collectTreeIds(analysis, roots, mode, maxDepth);
  } else if (explicitMatches.length > 0) {
    selectedIds = new Set(explicitMatches.map((item) => item.id));
  } else {
    selectedIds = new Set(analysis.functions.map((item) => item.id));
  }

  const selected = analysis.functions.filter((item) => {
    if (!selectedIds.has(item.id)) return false;
    if (include.length > 0 && !matchesAnyPattern(item, include)) return false;
    if (exclude.length > 0 && matchesAnyPattern(item, exclude)) return false;
    return true;
  });
  return {
    functions: selected,
    ids: selected.map((item) => item.id),
    patterns,
    include,
    exclude,
    mode,
    maxDepth,
    expanded: expand,
    roots: explicitMatches.length > 0
      ? explicitMatches.map((item) => item.id)
      : relationRoots(analysis).map((item) => item.id),
  };
}

export {
  selectFunctions,
};
