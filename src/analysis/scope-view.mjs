function findScope(analysis, requestedScope) {
  if (!requestedScope) return null;
  return analysis.functions.find((item) => (
    item.id === requestedScope || item.name === requestedScope
  )) ?? null;
}

function descendants(analysis, rootId) {
  const byParent = new Map();
  for (const item of analysis.functions) {
    const children = byParent.get(item.parentId) ?? [];
    children.push(item);
    byParent.set(item.parentId, children);
  }
  const ids = new Set();
  const queue = [rootId];
  while (queue.length > 0) {
    const id = queue.shift();
    if (ids.has(id)) continue;
    ids.add(id);
    for (const child of byParent.get(id) ?? []) queue.push(child.id);
  }
  return ids;
}

function restrictAnalysisToScope(analysis, requestedScope) {
  const root = findScope(analysis, requestedScope);
  if (!requestedScope) return { analysis, scope: null };
  if (!root) throw new Error(`Scope was not found: ${requestedScope}`);
  const ids = descendants(analysis, root.id);
  const functions = analysis.functions.filter((item) => ids.has(item.id));
  const filterGraph = (graph) => ({
    ...graph,
    nodes: graph.nodes.filter((node) => ids.has(node.id)),
    edges: graph.edges.filter((edge) => ids.has(edge.from) && (!edge.to || ids.has(edge.to))),
  });
  return {
    scope: { id: root.id, name: root.name, functionCount: functions.length },
    analysis: {
      ...analysis,
      functionCount: functions.length,
      functions,
      callGraph: filterGraph(analysis.callGraph),
      referenceGraph: filterGraph(analysis.referenceGraph),
    },
  };
}

export { restrictAnalysisToScope };
