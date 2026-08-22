function resolveRoot(analysis, requestedRoot = 'start') {
  const roots = analysis.functions.filter((item) => item.parentId === null);
  if (requestedRoot === 'start') return roots[0] ?? analysis.functions[0] ?? null;
  return analysis.functions.find((item) => item.id === requestedRoot || item.name === requestedRoot) ?? null;
}

function buildInlineView(analysis, options = {}) {
  const root = resolveRoot(analysis, options.root ?? 'start');
  if (!root) throw new Error(`Inline view root was not found: ${options.root ?? 'start'}`);
  const maxDepth = options.maxDepth ?? Number.POSITIVE_INFINITY;
  const branchLimit = options.branchLimit ?? Number.POSITIVE_INFINITY;
  if (!Number.isInteger(maxDepth) && maxDepth !== Number.POSITIVE_INFINITY) {
    throw new Error('Inline depth must be a non-negative integer');
  }
  if (!Number.isInteger(branchLimit) && branchLimit !== Number.POSITIVE_INFINITY) {
    throw new Error('Inline branch limit must be a non-negative integer');
  }
  if (maxDepth < 0 || branchLimit < 0) {
    throw new Error('Inline depth and branch limit must be non-negative integers');
  }

  const byId = new Map(analysis.functions.map((item) => [item.id, item]));
  const calls = new Map();
  for (const edge of analysis.callGraph.edges) {
    const list = calls.get(edge.from) ?? [];
    list.push(edge);
    calls.set(edge.from, list);
  }
  const nodes = [];
  const edges = [];
  const queue = [{ id: root.id, depth: 0, path: [root.id] }];
  const visited = new Set();

  while (queue.length > 0) {
    const current = queue.shift();
    const item = byId.get(current.id);
    if (!item) continue;
    const nodeKey = `${current.path.join('>')}`;
    if (!visited.has(nodeKey)) {
      visited.add(nodeKey);
      nodes.push({
        id: item.id,
        name: item.name,
        depth: current.depth,
        path: current.path,
        cycle: current.path.length !== new Set(current.path).size,
      });
    }
    if (current.depth >= maxDepth) continue;
    const outgoing = calls.get(current.id) ?? [];
    let branchCount = 0;
    for (const edge of outgoing) {
      if (branchCount >= branchLimit) break;
      if (!edge.to) {
        if (!options.showAll) continue;
        const unresolvedId = `${current.id}:unresolved:${edge.name}`;
        edges.push({
          from: current.id,
          to: unresolvedId,
          name: edge.name,
          resolved: false,
          unresolved: true,
        });
        nodes.push({
          id: unresolvedId,
          name: `<unresolved:${edge.name}>`,
          depth: current.depth + 1,
          path: [...current.path, unresolvedId],
          unresolved: true,
        });
        branchCount += 1;
        continue;
      }
      const cycle = current.path.includes(edge.to);
      edges.push({
        from: current.id,
        to: edge.to,
        name: edge.name,
        resolved: edge.resolved,
        cycle,
      });
      branchCount += 1;
      if (!cycle) queue.push({
        id: edge.to,
        depth: current.depth + 1,
        path: [...current.path, edge.to],
      });
    }
  }

  return {
    format: 1,
    mode: 'inline-call-tree',
    root: root.id,
    maxDepth: Number.isFinite(maxDepth) ? maxDepth : null,
    branchLimit: Number.isFinite(branchLimit) ? branchLimit : null,
    showAll: Boolean(options.showAll),
    nodes,
    edges,
  };
}

export { buildInlineView };
