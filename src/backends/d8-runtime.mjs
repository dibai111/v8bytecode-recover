import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

// Prebuilt patched-d8 releases exist per exact V8 tag. A blob stores only a
// version hash, but V8's Version::Hash is computable from the version string,
// so the blob hash maps directly to the release tag that can load it. Both
// hash-combine orders (V8 era dependent) and 3/4-part versions are covered.
const RELEASE_REPO = 'xqy2006/jsc2js';
const CACHE_ROOT = 'd8';
const VERSION_LIST_URL = `https://raw.githubusercontent.com/${RELEASE_REPO}/main/public/version.json`;
const RELEASE_API_URL = `https://api.github.com/repos/${RELEASE_REPO}/releases/tags`;
const DOWNLOAD_TIMEOUT_MS = 300_000;

const MASK_32 = 0xffffffffn;
const MASK_64 = 0xffffffffffffffffn;
const HASH_MULTIPLIER = 0xc6a4a7935bd1e995n;

function hashUnsigned32(value) {
  let x = BigInt(value) & MASK_32;
  x = (~x + (x << 15n)) & MASK_32;
  x ^= x >> 12n;
  x = (x + (x << 2n)) & MASK_32;
  x ^= x >> 4n;
  x = (x * 2057n) & MASK_32;
  x ^= x >> 16n;
  return x;
}

function hashCombine(seed, value) {
  let combined = (value * HASH_MULTIPLIER) & MASK_64;
  combined ^= combined >> 47n;
  combined = (combined * HASH_MULTIPLIER) & MASK_64;
  return ((seed ^ combined) * HASH_MULTIPLIER) & MASK_64;
}

function computeVersionHash(version, reverseOrder) {
  const parts = version.split('.').map(Number);
  if (parts.length < 3 || parts.length > 4
    || parts.some((value) => !Number.isInteger(value) || value < 0)) {
    return null;
  }
  while (parts.length < 4) parts.push(0);
  const values = parts.map(hashUnsigned32);
  const ordered = reverseOrder ? [...values].reverse() : values;
  let seed = 0n;
  for (const value of ordered) seed = hashCombine(seed, value);
  return Number(seed & MASK_32);
}

function platformAssetName(version) {
  if (process.platform === 'win32') return `d8-${version}-windows.zip`;
  if (process.platform === 'linux') return `d8-${version}-linux.tar.gz`;
  return null;
}

function runtimeCacheRoot() {
  const configured = process.env.V8BYTECODE_CACHE_DIR?.trim();
  if (configured) return path.resolve(configured);
  return process.cwd();
}

function cacheDirectory(cacheRoot, version) {
  return path.join(cacheRoot, CACHE_ROOT, version);
}

function executableName() {
  return process.platform === 'win32' ? 'd8.exe' : 'd8';
}

function cachedExecutablePath(cacheRoot, version) {
  const executable = path.join(cacheDirectory(cacheRoot, version), executableName());
  return fs.existsSync(executable) ? executable : null;
}

function downloadFile(url, destination) {
  if (process.platform === 'win32') {
    const result = spawnSync('powershell.exe', [
      '-NoProfile', '-Command',
      `$ProgressPreference='SilentlyContinue'; `
        + `[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; `
        + 'Invoke-WebRequest -Uri $env:V8BYTECODE_DOWNLOAD_URL '
        + '-OutFile $env:V8BYTECODE_DOWNLOAD_DESTINATION',
    ], {
      encoding: 'utf8',
      timeout: DOWNLOAD_TIMEOUT_MS,
      windowsHide: true,
      env: {
        ...process.env,
        V8BYTECODE_DOWNLOAD_URL: url,
        V8BYTECODE_DOWNLOAD_DESTINATION: destination,
      },
    });
    return result.status === 0 && fs.existsSync(destination)
      && fs.statSync(destination).size > 0;
  }
  const result = spawnSync('curl', ['-fsSL', '-o', destination, url], {
    encoding: 'utf8',
    timeout: DOWNLOAD_TIMEOUT_MS,
  });
  return result.status === 0 && fs.existsSync(destination)
    && fs.statSync(destination).size > 0;
}

function extractArchive(archivePath, destination) {
  let result;
  if (process.platform === 'win32') {
    result = spawnSync('powershell.exe', [
      '-NoProfile', '-Command',
      `$ProgressPreference='SilentlyContinue'; `
        + 'Expand-Archive -LiteralPath $env:V8BYTECODE_ARCHIVE_PATH '
        + '-DestinationPath $env:V8BYTECODE_EXTRACT_DIRECTORY -Force',
    ], {
      encoding: 'utf8',
      timeout: 120_000,
      windowsHide: true,
      env: {
        ...process.env,
        V8BYTECODE_ARCHIVE_PATH: archivePath,
        V8BYTECODE_EXTRACT_DIRECTORY: destination,
      },
    });
  } else {
    result = spawnSync('tar', ['-xzf', archivePath, '-C', destination], {
      encoding: 'utf8',
      timeout: 120_000,
    });
  }
  return result.status === 0;
}

function fetchReleaseAsset(version, assetName) {
  const url = `${RELEASE_API_URL}/${encodeURIComponent(version)}`;
  const result = spawnSync('curl', [
    '-fsSL',
    '-H', 'Accept: application/vnd.github+json',
    '-H', 'User-Agent: v8bytecode-recover',
    url,
  ], {
    encoding: 'utf8',
    timeout: 60_000,
    maxBuffer: 4 * 1024 * 1024,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) return null;
  try {
    const release = JSON.parse(result.stdout);
    const asset = release.assets?.find((item) => item.name === assetName);
    const digest = asset?.digest?.match(/^sha256:([\da-f]{64})$/i)?.[1];
    if (!digest || !Number.isSafeInteger(asset.size) || asset.size <= 0) return null;
    return { sha256: digest.toLowerCase(), size: asset.size };
  } catch {
    return null;
  }
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function installRelease(cacheRoot, version) {
  const assetName = platformAssetName(version);
  if (!assetName) return null;
  const asset = fetchReleaseAsset(version, assetName);
  if (!asset) return null;

  const directory = cacheDirectory(cacheRoot, version);
  fs.mkdirSync(directory, { recursive: true });
  const archivePath = path.join(directory, assetName);
  const url = `https://github.com/${RELEASE_REPO}/releases/download/${version}/${assetName}`;
  try {
    if (!downloadFile(url, archivePath)) return null;
    const downloadedSize = fs.statSync(archivePath).size;
    if (downloadedSize !== asset.size) {
      throw new Error(
        `Downloaded d8 archive has ${downloadedSize} bytes; expected ${asset.size}`,
      );
    }
    const actualDigest = sha256File(archivePath);
    if (actualDigest !== asset.sha256) {
      throw new Error(
        `SHA-256 mismatch for d8 ${version}: expected ${asset.sha256}, received ${actualDigest}`,
      );
    }
    if (!extractArchive(archivePath, directory)) return null;
  } finally {
    try { fs.rmSync(archivePath, { force: true }); } catch { /* ignore */ }
  }
  return cachedExecutablePath(cacheRoot, version);
}

function fetchVersionList() {
  const result = spawnSync('curl', ['-fsSL', VERSION_LIST_URL], {
    encoding: 'utf8',
    timeout: 60_000,
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.status !== 0) return [];
  try {
    const versions = JSON.parse(result.stdout);
    return Array.isArray(versions)
      ? versions.filter((version) => typeof version === 'string')
      : [];
  } catch {
    return [];
  }
}

/**
 * Resolve a raw V8 version hash to the release tag whose computed hash
 * matches, then ensure that patched d8 build is installed locally.
 */
export function ensureMatchingD8({ cacheRoot, versionHash }) {
  if (!Number.isInteger(versionHash)) return null;

  const resolvedCacheRoot = path.resolve(cacheRoot ?? runtimeCacheRoot());
  const memoPath = path.join(resolvedCacheRoot, CACHE_ROOT, `${versionHash}.json`);
  try {
    const memo = JSON.parse(fs.readFileSync(memoPath, 'utf8'));
    if (memo?.version) {
      return cachedExecutablePath(resolvedCacheRoot, memo.version)
        ?? installRelease(resolvedCacheRoot, memo.version);
    }
  } catch { /* no memo yet */ }

  for (const version of fetchVersionList()) {
    const forward = computeVersionHash(version, false);
    const reverse = computeVersionHash(version, true);
    if (versionHash === forward || versionHash === reverse) {
      fs.mkdirSync(path.dirname(memoPath), { recursive: true });
      fs.writeFileSync(
        memoPath,
        `${JSON.stringify({ versionHash, version }, null, 2)}\n`,
      );
      return cachedExecutablePath(resolvedCacheRoot, version)
        ?? installRelease(resolvedCacheRoot, version);
    }
  }
  return null;
}
