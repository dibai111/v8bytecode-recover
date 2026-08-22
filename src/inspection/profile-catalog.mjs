import fs from 'node:fs';
import path from 'node:path';

const catalogCache = new Map();

function readJson(filePath, label) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    throw new Error(`unable to read ${label} ${filePath}: ${error.message}`);
  }
}

function loadProfileCatalog(engineRoot, profileDirectory = null) {
  const directory = path.resolve(
    profileDirectory ?? path.join(engineRoot, 'cached_data', 'profiles'),
  );
  const cacheKey = `${directory}\u0000${profileDirectory ? 'external' : 'bundled'}`;
  if (catalogCache.has(cacheKey)) return catalogCache.get(cacheKey);

  const indexPath = path.join(directory, 'index.json');
  const index = readJson(indexPath, 'profile index');
  if (!index || typeof index !== 'object' || index.format !== 1) {
    throw new Error(`profile index format must be 1: ${indexPath}`);
  }
  if (!Array.isArray(index.versions)) {
    throw new Error(`profile index versions must be an array: ${indexPath}`);
  }
  if (index.versions.length === 0) {
    throw new Error(`profile index versions must not be empty: ${indexPath}`);
  }
  if (new Set(index.versions).size !== index.versions.length) {
    throw new Error(`profile index contains duplicate versions: ${indexPath}`);
  }
  const seenHashes = new Map();
  const profiles = index.versions.map((version) => {
    if (!/^\d+(?:\.\d+)*$/.test(version)) {
      throw new Error(`invalid V8 profile version in ${indexPath}: ${version}`);
    }
    const profilePath = path.join(directory, `${version}.json`);
    const profile = readJson(profilePath, `profile ${version}`);
    if (!profile || typeof profile !== 'object' || profile.version !== version) {
      throw new Error(`profile ${version} has an invalid embedded version: ${profilePath}`);
    }
    if (!Number.isInteger(profile.version_hash)
      || profile.version_hash < 0
      || profile.version_hash > 0xffffffff) {
      throw new Error(`profile ${version} has an invalid version hash: ${profilePath}`);
    }
    const previousVersion = seenHashes.get(profile.version_hash);
    if (previousVersion) {
      throw new Error(
        `profile ${version} duplicates version hash from ${previousVersion}: ${profilePath}`,
      );
    }
    seenHashes.set(profile.version_hash, version);
    if (!profile.runtime_variants || typeof profile.runtime_variants !== 'object'
      || Array.isArray(profile.runtime_variants)
      || Object.keys(profile.runtime_variants).length === 0) {
      throw new Error(`profile ${version} has invalid runtime variants: ${profilePath}`);
    }
    const runtimeVariants = profile.runtime_variants;
    const runtimeDefaultVariant = profile.runtime_default_variant ?? 'legacy';
    if (Object.keys(runtimeVariants).length > 0
      && !Object.hasOwn(runtimeVariants, runtimeDefaultVariant)) {
      throw new Error(`profile ${version} has an unknown default runtime variant: ${profilePath}`);
    }
    if (profile.runtime_variant_by_flags_hash !== undefined
      && (!profile.runtime_variant_by_flags_hash
        || typeof profile.runtime_variant_by_flags_hash !== 'object'
        || Array.isArray(profile.runtime_variant_by_flags_hash))) {
      throw new Error(`profile ${version} has invalid runtime flag mappings: ${profilePath}`);
    }
    if (profile.cache_header_layout !== undefined
      && profile.cache_header_layout !== null
      && (typeof profile.cache_header_layout !== 'object'
        || Array.isArray(profile.cache_header_layout))) {
      throw new Error(`profile ${version} has an invalid cache header layout: ${profilePath}`);
    }
    return {
      version: profile.version,
      versionHash: profile.version_hash,
      hasRoSnapshotChecksum: profile.has_ro_snapshot_checksum === true,
      headerFormat: profile.header_format ?? null,
      cacheHeaderLayout: profile.cache_header_layout ?? null,
      runtimeDefaultVariant,
      runtimeVariants,
      runtimeVariantByFlagsHash: profile.runtime_variant_by_flags_hash ?? {},
    };
  });
  const catalog = {
    format: index.format,
    profiles,
    directory,
    source: profileDirectory ? 'external' : 'bundled',
  };
  catalogCache.set(cacheKey, catalog);
  return catalog;
}

function findProfileByHash(catalog, versionHash) {
  return catalog.profiles.find((profile) => profile.versionHash === versionHash) ?? null;
}

export { findProfileByHash, loadProfileCatalog };
