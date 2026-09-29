import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { sourceRecoveryEntryPath } from '../src/cli/paths.mjs';
import { recoverSources } from '../src/recovery/pipeline.mjs';

test('emits source analysis and split functions for disassembled input', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-pipeline-test-'));
  const input = path.join(root, 'input');
  const output = path.join(root, 'output');
  const split = path.join(root, 'split');
  const reportPath = path.join(root, 'report.json');
  fs.mkdirSync(input);
  fs.writeFileSync(
    path.join(input, 'sample.disassembly.txt'),
    '# disassembler V8 14.7.57\n# embedder_compatibility=exact-runtime-variant runtime_variant=legacy flags_status=known\nplaceholder\n',
    'utf8',
  );

  const backend = {
    disassemble() {
      throw new Error('raw disassembly should not be called');
    },
    decompile() {
      return 'function outer() { return inner(); }\nfunction inner() { return 1; }\n';
    },
    emit() {
      throw new Error('source analysis should not use backend.emit');
    },
  };

  try {
    const result = recoverSources({
      input,
      output,
      inputFormat: 'disassembled',
      backend: 'profile',
      profile: null,
      d8Path: null,
      snapshot: null,
      snapshotSearch: true,
      runtimeVariant: null,
      payloadOffset: null,
      level: 4,
      emit: ['functions', 'callgraph'],
      splitFunctions: split,
      report: reportPath,
      python: 'python',
    }, { sourceRecoveryEntryPath }, backend);

    assert.equal(result.emitted, 1);
    assert.equal(result.failed, 0);
    assert.equal(fs.existsSync(path.join(output, 'sample.js')), true);
    assert.equal(fs.existsSync(path.join(output, '.analysis', 'sample.functions.json')), true);
    assert.equal(fs.existsSync(path.join(output, '.analysis', 'sample.callgraph.json')), true);
    assert.equal(fs.existsSync(path.join(split, 'sample', 'index.json')), true);
    const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
    assert.equal(report.counts.succeeded, 1);
    assert.equal(report.files[0].profileVersion, '14.7.57');
    assert.equal(report.files[0].compatibilityStatus, 'exact-runtime-variant');
    assert.equal(report.files[0].detectedRuntimeVariant, 'legacy');
  } finally {
    process.exitCode = undefined;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('rejects an empty recovery instead of publishing it as JavaScript', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-empty-pipeline-test-'));
  const input = path.join(root, 'input');
  const output = path.join(root, 'output');
  fs.mkdirSync(input);
  fs.writeFileSync(path.join(input, 'sample.disassembly.txt'), 'placeholder\n', 'utf8');

  try {
    const result = recoverSources({
      input,
      output,
      inputFormat: 'disassembled',
      backend: 'profile',
      emit: [],
      splitFunctions: null,
      strict: false,
      report: null,
      python: 'missing-python',
    }, { sourceRecoveryEntryPath }, {
      decompile() {
        return '';
      },
    });

    assert.equal(result.emitted, 0);
    assert.equal(result.failed, 1);
    assert.match(result.files[0].error, /produced empty source/);
    assert.equal(fs.existsSync(path.join(output, 'sample.js')), false);
  } finally {
    process.exitCode = undefined;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('reuses a serialized recovery artifact without invoking the backend', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-artifact-pipeline-test-'));
  const input = path.join(root, 'input');
  const output = path.join(root, 'output');
  const replayOutput = path.join(root, 'replay');
  fs.mkdirSync(input);
  fs.writeFileSync(path.join(input, 'sample.disassembly.txt'), 'placeholder\n', 'utf8');

  const source = 'function outer() { return inner(); }\nfunction inner() { return 1; }\n';
  const backend = {
    disassemble() {
      throw new Error('disassembly should not be called for text input');
    },
    decompile() {
      return source;
    },
    emit() {
      throw new Error('serialized output should not use backend.emit');
    },
  };

  try {
    const first = recoverSources({
      input,
      output,
      inputFormat: 'disassembled',
      backend: 'profile',
      profile: null,
      d8Path: null,
      snapshot: null,
      snapshotSearch: true,
      runtimeVariant: null,
      payloadOffset: null,
      level: 4,
      emit: ['serialized'],
      splitFunctions: null,
      report: null,
      python: 'python',
    }, { sourceRecoveryEntryPath }, backend);
    assert.equal(first.failed, 0);
    const artifactPath = path.join(output, '.analysis', 'sample.v8recovery.json');
    assert.equal(fs.existsSync(artifactPath), true);

    const replayBackend = {
      decompile() {
        throw new Error('serialized replay must not decompile');
      },
      emit() {
        throw new Error('serialized replay must not emit through a backend');
      },
    };
    const replay = recoverSources({
      input: artifactPath,
      output: replayOutput,
      inputFormat: 'auto',
      backend: 'auto',
      profile: null,
      d8Path: null,
      snapshot: null,
      snapshotSearch: true,
      runtimeVariant: null,
      payloadOffset: null,
      level: 4,
      emit: ['functions'],
      splitFunctions: null,
      report: null,
      python: 'missing-python',
    }, { sourceRecoveryEntryPath: path.join(root, 'missing-engine.py') }, replayBackend);
    assert.equal(replay.failed, 0);
    assert.equal(fs.readFileSync(path.join(replayOutput, 'sample.js'), 'utf8'), source);
    assert.equal(fs.existsSync(path.join(replayOutput, '.analysis', 'sample.functions.json')), true);
  } finally {
    process.exitCode = undefined;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('emits marked best-effort source when only read-only values are unresolved', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-partial-pipeline-test-'));
  const input = path.join(root, 'input');
  const output = path.join(root, 'output');
  const strictOutput = path.join(root, 'strict-output');
  const reportPath = path.join(root, 'report.json');
  fs.mkdirSync(input);
  fs.writeFileSync(path.join(input, 'sample.disassembly.txt'), 'placeholder\n', 'utf8');

  const backend = {
    decompile() {
      return 'const value = "<read_only_0,41160>";\n';
    },
  };
  const options = {
    input,
    inputFormat: 'disassembled',
    backend: 'profile',
    profile: null,
    d8Path: null,
    snapshot: null,
    snapshotSearch: true,
    runtimeVariant: null,
    payloadOffset: null,
    level: 4,
    emit: [],
    splitFunctions: null,
    report: reportPath,
    python: 'missing-python',
    strict: false,
  };

  try {
    const result = recoverSources({ ...options, output }, { sourceRecoveryEntryPath: path.join(root, 'missing-engine.py') }, backend);
    const recovered = fs.readFileSync(path.join(output, 'sample.js'), 'utf8');
    assert.equal(result.failed, 0);
    assert.match(recovered, /best-effort recovery/);
    assert.match(recovered, /__v8_unresolved_read_only_0_41160__/);
    assert.equal(JSON.parse(fs.readFileSync(reportPath, 'utf8')).counts.partial, 1);

    const strict = recoverSources({ ...options, output: strictOutput, strict: true, report: null }, { sourceRecoveryEntryPath: path.join(root, 'missing-engine.py') }, backend);
    assert.equal(strict.emitted, 0);
    assert.equal(strict.failed, 1);
    assert.equal(fs.existsSync(path.join(strictOutput, 'sample.js')), false);
  } finally {
    process.exitCode = undefined;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('resumes validated outputs and invalidates the cache after input or output changes', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-resume-pipeline-test-'));
  const input = path.join(root, 'input');
  const output = path.join(root, 'output');
  fs.mkdirSync(input);
  const inputFile = path.join(input, 'sample.disassembly.txt');
  fs.writeFileSync(inputFile, 'placeholder\n', 'utf8');
  const baseOptions = {
    input,
    output,
    inputFormat: 'disassembled',
    backend: 'profile',
    profile: null,
    d8Path: null,
    snapshot: null,
    snapshotSearch: true,
    runtimeVariant: null,
    payloadOffset: null,
    level: 4,
    emit: ['functions'],
    splitFunctions: null,
    report: null,
    python: 'missing-python',
    strict: false,
    resume: true,
  };
  let calls = 0;
  const backend = {
    decompile() {
      calls += 1;
      return 'function sample() { return 1; }\n';
    },
  };

  try {
    const first = recoverSources(baseOptions, {
      sourceRecoveryEntryPath: path.join(root, 'missing-engine.py'),
    }, backend);
    assert.equal(first.failed, 0);
    assert.equal(calls, 1);
    assert.equal(fs.existsSync(path.join(output, 'recovery-manifest.json')), true);

    const resumed = recoverSources(baseOptions, {
      sourceRecoveryEntryPath: path.join(root, 'missing-engine.py'),
    }, {
      decompile() {
        throw new Error('resume should not call the backend');
      },
    });
    assert.equal(resumed.failed, 0);
    assert.equal(resumed.files[0].resumed, true);
    assert.equal(JSON.parse(fs.readFileSync(path.join(output, 'recovery-manifest.json'), 'utf8')).counts.resumed, 1);

    fs.writeFileSync(path.join(output, '.analysis', 'sample.functions.json'), 'tampered', 'utf8');
    const repairedAnalysis = recoverSources(baseOptions, {
      sourceRecoveryEntryPath: path.join(root, 'missing-engine.py'),
    }, backend);
    assert.equal(repairedAnalysis.failed, 0);
    assert.equal(calls, 2);
    assert.equal(repairedAnalysis.files[0].resumed, false);

    fs.writeFileSync(inputFile, 'changed\n', 'utf8');
    const changed = recoverSources(baseOptions, {
      sourceRecoveryEntryPath: path.join(root, 'missing-engine.py'),
    }, backend);
    assert.equal(changed.failed, 0);
    assert.equal(calls, 3);
    assert.equal(changed.files[0].resumed, false);

    fs.writeFileSync(path.join(output, 'sample.js'), 'not valid javascript', 'utf8');
    const repaired = recoverSources(baseOptions, {
      sourceRecoveryEntryPath: path.join(root, 'missing-engine.py'),
    }, backend);
    assert.equal(repaired.failed, 0);
    assert.equal(calls, 4);
    assert.match(fs.readFileSync(path.join(output, 'sample.js'), 'utf8'), /return 1/);
  } finally {
    process.exitCode = undefined;
    fs.rmSync(root, { recursive: true, force: true });
  }
});
