function createDisassemblyResult({
  backend,
  text,
  profileVersion = null,
  compatibilityStatus = null,
  runtimeVariant = null,
  flagsStatus = null,
  snapshot = null,
  d8Path = null,
  d8Version = null,
  diagnostics = [],
}) {
  if (typeof backend !== 'string' || !backend) throw new Error('backend result requires a backend name');
  if (typeof text !== 'string') throw new Error('backend result requires text');
  return Object.freeze({
    backend,
    text,
    profileVersion,
    compatibilityStatus,
    runtimeVariant,
    flagsStatus,
    snapshot,
    d8Path,
    d8Version,
    diagnostics: [...diagnostics],
  });
}

function normalizeDisassemblyResult(result, fallbackBackend) {
  return createDisassemblyResult({
    backend: result.backend ?? result.backendId ?? fallbackBackend,
    text: result.text,
    profileVersion: result.profileVersion ?? null,
    compatibilityStatus: result.compatibilityStatus ?? null,
    runtimeVariant: result.runtimeVariant ?? null,
    flagsStatus: result.flagsStatus ?? null,
    snapshot: result.snapshot ?? null,
    d8Path: result.d8Path ?? null,
    d8Version: result.d8Version ?? null,
    diagnostics: result.diagnostics ?? [],
  });
}

export { createDisassemblyResult, normalizeDisassemblyResult };
