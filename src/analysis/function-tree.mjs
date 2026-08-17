const treeModes = new Set(['declarers', 'calls', 'references']);

function resolveRoot(analysis, requestedRoot = 'start') {
  const candidates = analysis.functions.filter((item) => item.parentId === null);
  if (requestedRoot === 'start') return candidates[0] ?? analysis.functions[0] ?? null;
  return analysis.functions.find((item) => item.id === requestedRoot || item.name === requestedRoot) ?? null;
}

function relationEdges(analysis, mode) {
  if (mode === 'calls') return analysis.callGraph.edges;
  if (mode === 'references') return analysis.referenceGraph.edges;
  const children = analysis.functions
    .filter((item) => item.parentId !== null)
    .map((item) => ({ from: item.parentId, to: item.id, name: item.name, resolved: true }));
  return children;
}

function buildFunctionTree(analysis, options = {}) {
  const mode = options.mode ?? 'declarers';
  if (!treeModes.has(mode)) throw new Error(`Unsupported function tree mode: ${mode}`);
  const root = resolveRoot(analysis, options.root ?? 'start');
  if (!root) throw new Error(`Function tree root was not found: ${options.root ?? 'start'}`);
  const maxDepth = options.maxDepth ?? Number.POSITIVE_INFINITY;
  if (maxDepth !== Number.POSITIVE_INFINITY && (!Number.isInteger(maxDepth) || maxDepth < 0)) {
    throw new Error('Function tree depth must be a non-negative integer');
  }

  const adjacency = new Map();
  for (const edge of relationEdges(analysis, mode)) {
    if (!edge.to) continue;
    const outgoing = adjacency.get(edge.from) ?? [];
    outgoing.push(edge);
    adjacency.set(edge.from, outgoing);
  }
  const functionById = new Map(analysis.functions.map((item) => [item.id, item]));
  const queue = [{ id: root.id, depth: 0, parentId: null }];
  const visited = new Set();
  const nodes = [];
  const edges = [];
  while (queue.length > 0) {
    const current = queue.shift();
    if (visited.has(current.id)) continue;
    visited.add(current.id);
    const item = functionById.get(current.id);
    if (!item) continue;
    nodes.push({
      id: item.id,
      name: item.name,
      parentId: current.parentId,
      depth: current.depth,
      startLine: item.startLine,
      endLine: item.endLine,
    });
    if (current.depth >= maxDepth) continue;
    for (const edge of adjacency.get(current.id) ?? []) {
      if (visited.has(edge.to)) continue;
      edges.push({
        from: current.id,
        to: edge.to,
        name: edge.name,
        resolved: edge.resolved,
      });
      queue.push({ id: edge.to, depth: current.depth + 1, parentId: current.id });
    }
  }
  return {
    format: 1,
    mode,
    root: root.id,
    maxDepth: Number.isFinite(maxDepth) ? maxDepth : null,
    nodes,
    edges,
  };
}

export { buildFunctionTree, treeModes };
