import fs from 'node:fs';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { spawnSync } from 'node:child_process';

import { findBlobs, outputRelativePath } from '../io/blob-files.mjs';

function replaceTemplate(value, replacements) {
  return value.replace(/\{(input|disassembly|output|root)\}/g, (_, key) => (
    replacements[key]
  ));
}

function validateAdapter(adapter) {
  if (!adapter || adapter.format !== 1 || !adapter.id) {
    throw new Error('external benchmark adapter must have format 1 and an id');
  }
  for (const stage of ['disassemble', 'recover']) {
    if (!adapter[stage]?.command || !Array.isArray(adapter[stage].args)) {
      throw new Error(`external benchmark adapter ${adapter.id} needs ${stage}.command and ${stage}.args`);
    }
  }
  return adapter;
}

function runStage(stage, replacements, cwd, timeout) {
  const command = replaceTemplate(stage.command, replacements);
  const args = stage.args.map((value) => replaceTemplate(String(value), replacements));
  const run = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    timeout,
    maxBuffer: 512 * 1024 * 1024,
    windowsHide: true,
  });
  if (run.error || run.status !== 0) {
    const detail = [run.stdout, run.stderr].filter(Boolean).join('\n').trim();
    throw run.error ?? new Error(`${command} exited with ${run.status}${detail ? `\n${detail}` : ''}`);
  }
  return { stdout: run.stdout ?? '', stderr: run.stderr ?? '' };
}

function runExternalAdapter(input, outputRoot, adapter, options = {}) {
  validateAdapter(adapter);
  const root = path.resolve(options.root ?? outputRoot);
  const cwd = adapter.cwd ? path.resolve(root, adapter.cwd) : root;
  const timeout = options.timeout ?? 600_000;
  const files = [];
  const started = performance.now();

  for (const blob of findBlobs(input)) {
    const relativeOutput = outputRelativePath(blob.relativePath);
    const outputPath = path.join(outputRoot, relativeOutput);
    const disassemblyPath = path.join(outputRoot, '.external', `${relativeOutput}.disassembly.txt`);
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.mkdirSync(path.dirname(disassemblyPath), { recursive: true });
    const replacements = {
      input: path.resolve(blob.path),
      disassembly: disassemblyPath,
      output: outputPath,
      root,
    };
    const fileStarted = performance.now();
    try {
      const disassembly = runStage(adapter.disassemble, replacements, cwd, timeout);
      fs.writeFileSync(disassemblyPath, disassembly.stdout, 'utf8');
      runStage(adapter.recover, replacements, cwd, timeout);
      files.push({
        input: blob.relativePath,
        output: relativeOutput,
        success: fs.existsSync(outputPath),
        elapsedMs: Math.round((performance.now() - fileStarted) * 100) / 100,
        stderr: disassembly.stderr.trim(),
      });
    } catch (error) {
      files.push({
        input: blob.relativePath,
        output: relativeOutput,
        success: false,
        elapsedMs: Math.round((performance.now() - fileStarted) * 100) / 100,
        error: error.message,
      });
    }
  }

  return {
    backend: adapter.id,
    elapsedMs: Math.round((performance.now() - started) * 100) / 100,
    exitCode: files.every((file) => file.success) ? 0 : 2,
    stdout: '',
    stderr: '',
    files,
  };
}

export { replaceTemplate, runExternalAdapter, validateAdapter };
