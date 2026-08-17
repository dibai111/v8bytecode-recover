const readOnlyReferencePattern = /<read_only_(\d+),(\d+)>/g;
const readOnlyResidueFields = new Set(['unresolvedObjects', 'unresolvedHeapValues']);

const partialRecoveryBanner = '// v8blob-to-js: best-effort recovery; unresolved read-only V8 references use stable placeholders.';

function isReadOnlyOnlyResidue(metrics) {
  const hasReadOnlyResidue = [...readOnlyResidueFields]
    .some((field) => metrics[field] > 0);
  if (!hasReadOnlyResidue) return false;
  return Object.entries(metrics).every(([field, value]) => (
    field === 'residueFree'
    || readOnlyResidueFields.has(field)
    || value === 0
  ));
}

function replaceUnresolvedReadOnlyReferences(source) {
  const counts = new Map();
  const replacedSource = source.replace(
    readOnlyReferencePattern,
    (_match, page, offset) => {
      const token = `read_only_${page}_${offset}`;
      const replacement = `__v8_unresolved_${token}__`;
      const current = counts.get(token) ?? { token, replacement, count: 0 };
      current.count += 1;
      counts.set(token, current);
      return replacement;
    },
  );

  return {
    source: replacedSource,
    replacements: [...counts.values()],
    count: [...counts.values()].reduce((total, item) => total + item.count, 0),
  };
}

function createPartialRecoverySource(source) {
  const replacement = replaceUnresolvedReadOnlyReferences(source);
  const declarations = replacement.replacements.map(
    ({ replacement: name }) => `const ${name} = undefined;`,
  );
  return {
    ...replacement,
    source: [partialRecoveryBanner, ...declarations, replacement.source].join('\n'),
  };
}

export {
  createPartialRecoverySource,
  isReadOnlyOnlyResidue,
  partialRecoveryBanner,
  replaceUnresolvedReadOnlyReferences,
};
