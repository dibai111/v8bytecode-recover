import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';

import { findBlobs, outputRelativePath } from '../recovery/blob-files.mjs';
import { analyzeSource } from './source-index.mjs';
import { syntaxCheck } from '../backends/processes.mjs';
import { qualityMetrics } from './source-quality.mjs';
import { runExternalAdapter } from './benchmark-adapter.mjs';
import { evaluateCorpus } from './benchmark-corpus.mjs';

function sha256(source) {
  return crypto.createHash('sha256').update(source).digest('hex');
}

function runCli(entryPath, input, output, backend, options) {
  const reportPath = path.join(output, 'recovery-report.json');
  const args = [
    entryPath,
    input,
    '--output', output,
    '--backend', backend,
    '--report', reportPath,
  ];
  if (options.profile) args.push('--profile', options.profile);
  if (options.profileDirectory) args.push('--profile-dir', options.profileDirectory);
  if (options.d8Path) args.push('--d8', options.d8Path);
  if (options.d8Directory) args.push('--d8-dir', options.d8Directory);
  if (options.embedder && options.embedder !== 'unknown') args.push('--embedder', options.embedder);
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

function functionCount(source) {
  try {
    return analyzeSource(source).functionCount;
  } catch {
    return (source.match(/\bfunction\b/g) ?? []).length;
  }
}

function loadRecoveryReport(output) {
  const reportPath = path.join(output, 'recovery-report.json');
  try {
    const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
    return new Map((report.files ?? []).map((file) => [file.input, file]));
  } catch {
    return new Map();
  }
}

function collectResults(input, output) {
  const recoveryFiles = loadRecoveryReport(output);
  return findBlobs(input).map((blob) => {
    const relativeOutput = outputRelativePath(blob.relativePath);
    const sourcePath = path.join(output, relativeOutput);
    if (!fs.existsSync(sourcePath)) {
      return { input: blob.relativePath, output: relativeOutput, success: false };
    }
    const source = fs.readFileSync(sourcePath, 'utf8');
    const syntax = syntaxCheck(sourcePath);
    const metrics = qualityMetrics(source);
    const recovery = recoveryFiles.get(blob.relativePath) ?? {};
    return {
      input: blob.relativePath,
      output: relativeOutput,
      success: syntax.ok,
      syntaxOk: syntax.ok,
      syntaxError: syntax.ok ? null : syntax.detail,
      backend: recovery.backend ?? null,
      profileVersion: recovery.profileVersion ?? null,
      compatibilityStatus: recovery.compatibilityStatus ?? null,
      embedder: recovery.embedder ?? null,
      bytes: Buffer.byteLength(source),
      functions: functionCount(source),
      sha256: sha256(source),
      residueFree: metrics.residueFree,
      metrics,
    };
  });
}

function enrichExternalResults(execution, input, output) {
  const files = execution.files.map((file) => {
    if (!file.success) return file;
    const sourcePath = path.join(output, file.output);
    if (!fs.existsSync(sourcePath)) return { ...file, success: false };
    const source = fs.readFileSync(sourcePath, 'utf8');
    const syntax = syntaxCheck(sourcePath);
    const metrics = qualityMetrics(source);
    return {
      ...file,
      backend: execution.backend,
      success: syntax.ok,
      syntaxOk: syntax.ok,
      syntaxError: syntax.ok ? null : syntax.detail,
      bytes: Buffer.byteLength(source),
      functions: functionCount(source),
      sha256: sha256(source),
      residueFree: metrics.residueFree,
      metrics,
    };
  });
  return {
    ...execution,
    succeeded: files.filter((file) => file.success).length,
    failed: files.filter((file) => !file.success).length,
    residueFree: files.filter((file) => file.success && file.residueFree).length,
    files,
  };
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
        d8Directory: options.d8Directory ?? null,
        profile: options.profile ?? null,
        profileDirectory: options.profileDirectory ?? null,
        embedder: options.embedder ?? 'unknown',
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
    for (const adapter of options.adapters ?? []) {
      const output = path.join(root, adapter.id.replace(/[^a-z0-9._-]/gi, '_'));
      const execution = runExternalAdapter(input, output, adapter, {
        root,
        timeout: options.timeout ?? 600_000,
      });
      results.push(enrichExternalResults(execution, input, output));
    }
    const report = {
      format: 1,
      generatedAt: new Date().toISOString(),
      input: path.resolve(input),
      blobCount: findBlobs(input).length,
      results,
      comparisons: compareBackends(results),
    };
    if (options.corpusManifest) report.regression = evaluateCorpus(report, options.corpusManifest);
    return report;
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

export { benchmark, functionCount };
