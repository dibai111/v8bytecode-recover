import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';

import { findBlobs, outputRelativePath } from '../io/blob-files.mjs';
import { qualityMetrics } from '../validation/source-quality.mjs';

function sha256(source) {
  return crypto.createHash('sha256').update(source).digest('hex');
}

function runCli(entryPath, input, output, backend, options) {
  const args = [entryPath, input, '--output', output, '--backend', backend];
  if (options.profile) args.push('--profile', options.profile);
  if (options.d8Path) args.push('--d8', options.d8Path);
  if (!options.snapshotSearch) args.push('--no-snapshot-search');
  const started = performance.now();
  const run = spawnSync(process.execPath, args, {
    encoding: 'utf8',
    timeout: options.timeout,
    maxBuffer: 512 * 1024 * 1024,
    windowsHide: true,
  });
  return {
    elapsedMs: Math.round((performance.now() - started) * 100) / 100,
    exitCode: run.status,
    stderr: run.stderr.trim(),
    stdout: run.stdout.trim(),
  };
}

function collectResults(input, output) {
  return findBlobs(input).map((blob) => {
    const relativeOutput = outputRelativePath(blob.relativePath);
    const sourcePath = path.join(output, relativeOutput);
    if (!fs.existsSync(sourcePath)) {
      return { input: blob.relativePath, output: relativeOutput, success: false };
    }
    const source = fs.readFileSync(sourcePath, 'utf8');
    const metrics = qualityMetrics(source);
    return {
      input: blob.relativePath,
      output: relativeOutput,
      success: true,
      bytes: Buffer.byteLength(source),
      functions: (source.match(/\bfunction\b/g) ?? []).length,
      sha256: sha256(source),
      residueFree: metrics.residueFree,
      metrics,
    };
  });
}

function compareBackends(results) {
  if (results.length < 2) return [];
  const [reference, ...others] = results;
  const referenceFiles = new Map(reference.files.map((file) => [file.input, file]));
  return others.map((candidate) => {
    let shared = 0;
    let identical = 0;
    const differences = [];
    for (const file of candidate.files) {
      const expected = referenceFiles.get(file.input);
      if (!expected?.success || !file.success) continue;
      shared += 1;
      if (expected.sha256 === file.sha256) identical += 1;
      else differences.push({
        input: file.input,
        referenceHash: expected.sha256,
        candidateHash: file.sha256,
        referenceFunctions: expected.functions,
        candidateFunctions: file.functions,
      });
    }
    return {
      reference: reference.backend,
      candidate: candidate.backend,
      shared,
      identical,
      differences,
    };
  });
}

function benchmark(entryPath, input, backends, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8bytecode-benchmark-'));
  const results = [];
  try {
    for (const backend of backends) {
      const output = path.join(root, backend);
      const execution = runCli(entryPath, input, output, backend, {
        d8Path: options.d8Path ?? null,
        profile: options.profile ?? null,
        snapshotSearch: options.snapshotSearch !== false,
        timeout: options.timeout ?? 600_000,
      });
      const files = collectResults(input, output);
      results.push({
        backend,
        ...execution,
        succeeded: files.filter((file) => file.success).length,
        failed: files.filter((file) => !file.success).length,
        residueFree: files.filter((file) => file.success && file.residueFree).length,
        files,
      });
    }
    return {
      format: 1,
      generatedAt: new Date().toISOString(),
      input: path.resolve(input),
      blobCount: findBlobs(input).length,
      results,
      comparisons: compareBackends(results),
    };
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

export { benchmark };
