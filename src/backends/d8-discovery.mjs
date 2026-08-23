import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const D8_NAMES = process.platform === 'win32'
  ? ['d8.exe', 'd8']
  : ['d8', 'd8.exe'];
const D8_ENVIRONMENT_KEYS = Object.freeze([
  'V8BYTECODE_D8',
  'V8BLOB_D8',
]);

function unique(values) {
  return [...new Set(values.filter(Boolean).map((value) => path.resolve(value)))];
}

function pathLike(value) {
  return path.isAbsolute(value)
    || value.includes('/')
    || value.includes('\\')
    || value.startsWith('.');
}

function resolveOnPath(command, env = process.env) {
  const resolver = process.platform === 'win32' ? 'where.exe' : 'which';
  const result = spawnSync(resolver, [command], {
    encoding: 'utf8',
    windowsHide: true,
    env,
  });
  if (result.error || result.status !== 0) return [];
  return (result.stdout ?? '')
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter((value) => value && fs.existsSync(value));
}

function directoryD8Candidates(directory) {
  if (!directory || !fs.existsSync(directory)) return [];
  let entries;
  try {
    entries = fs.readdirSync(directory, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((entry) => /^d8(?:[._-].+)?(?:\.exe)?$/i.test(entry.name))
    .filter((entry) => !/\.(?:dll|json|log|md|pdb|txt)$/i.test(entry.name))
    .filter((entry) => entry.isFile())
    .map((entry) => path.join(directory, entry.name))
    .sort((left, right) => left.localeCompare(right));
}

function candidateD8Paths({
  requestedPath = null,
  d8Directory = null,
  projectRoot = process.cwd(),
  env = process.env,
} = {}) {
  const explicit = requestedPath
    ? [requestedPath]
    : D8_ENVIRONMENT_KEYS.flatMap((key) => env[key] ? [env[key]] : []);
  const values = [];
  for (const value of explicit) {
    if (pathLike(value)) values.push(path.resolve(value));
    else values.push(...resolveOnPath(value, env));
  }
  if (requestedPath || explicit.length > 0) return unique(values);

  const roots = [
    d8Directory,
    path.join(projectRoot, 'runtime', 'd8'),
    path.join(projectRoot, 'tools', 'd8'),
    path.join(projectRoot, '.runtime', 'd8'),
    projectRoot,
  ].filter(Boolean);
  for (const root of roots) {
    for (const name of D8_NAMES) values.push(path.join(root, name));
    values.push(...directoryD8Candidates(root));
  }
  for (const name of D8_NAMES) values.push(...resolveOnPath(name, env));
  return unique(values);
}

function firstOutput(result) {
  return (result.stdout || result.stderr || '')
    .trim()
    .split(/\r?\n/)
    .find(Boolean)
    ?? '';
}

function probeD8(executable, { timeout = 10_000 } = {}) {
  const resolvedPath = path.resolve(executable);
  if (!fs.existsSync(resolvedPath)) {
    return {
      path: resolvedPath,
      available: false,
      loadjsc: false,
      version: null,
      error: 'file does not exist',
    };
  }
  const versionResult = spawnSync(resolvedPath, ['--version'], {
    cwd: path.dirname(resolvedPath),
    encoding: 'utf8',
    timeout,
    windowsHide: true,
  });
  const loadjscResult = spawnSync(resolvedPath, ['-e', 'print(typeof loadjsc)'], {
    cwd: path.dirname(resolvedPath),
    encoding: 'utf8',
    timeout,
    windowsHide: true,
  });
  const loadjscOutput = firstOutput(loadjscResult);
  const loadjsc = !loadjscResult.error
    && loadjscResult.status === 0
    && /^(?:function|true)$/i.test(loadjscOutput);
  const version = firstOutput(versionResult) || null;
  return {
    path: resolvedPath,
    available: loadjsc,
    loadjsc,
    version,
    error: loadjsc
      ? null
      : loadjscResult.error?.message
        ?? (fs.existsSync(resolvedPath)
          ? 'd8 does not expose patched loadjsc()'
          : 'file does not exist'),
  };
}

function discoverD8(options = {}) {
  const candidates = candidateD8Paths(options);
  const probes = candidates.map((candidate) => probeD8(candidate, options));
  const selected = probes.find((probe) => probe.available) ?? null;
  return {
    available: Boolean(selected),
    path: selected?.path ?? null,
    version: selected?.version ?? null,
    loadjsc: selected?.loadjsc ?? false,
    source: selected
      ? options.requestedPath
        ? 'explicit'
        : options.d8Directory
          ? 'directory'
          : D8_ENVIRONMENT_KEYS.some((key) => (options.env ?? process.env)[key])
            ? 'environment'
            : 'auto'
      : null,
    error: selected ? null : probes.find((probe) => probe.error)?.error ?? 'd8 was not found',
    candidates: probes,
  };
}

export {
  candidateD8Paths,
  discoverD8,
  probeD8,
};
