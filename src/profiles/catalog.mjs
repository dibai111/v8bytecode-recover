import fs from 'node:fs';
import path from 'node:path';

const catalogCache = new Map();

function loadProfileCatalog(engineRoot) {
  const cacheKey = path.resolve(engineRoot);
  if (catalogCache.has(cacheKey)) return catalogCache.get(cacheKey);

  const directory = path.join(engineRoot, 'cached_data', 'profiles');
  const indexPath = path.join(directory, 'index.json');
  const index = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
  const profiles = index.versions.map((version) => {
    const profile = JSON.parse(
      fs.readFileSync(path.join(directory, `${version}.json`), 'utf8'),
    );
    return {
      version: profile.version,
      versionHash: profile.version_hash,
    };
  });
  const catalog = { format: index.format, profiles };
  catalogCache.set(cacheKey, catalog);
  return catalog;
}

function findProfileByHash(catalog, versionHash) {
  return catalog.profiles.find((profile) => profile.versionHash === versionHash) ?? null;
}

export { findProfileByHash, loadProfileCatalog };
