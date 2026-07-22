import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  assertUniqueOutputs,
  findBlobs,
  outputRelativePath,
  removeLegacyArtifacts,
} from '../io/blob-files.mjs';
import {
  normalizeDerivedSource,
  unresolvedClosureBindings,
} from '../recovery/source-transforms.mjs';
import { assertPythonAvailable, syntaxCheck } from '../runtime/processes.mjs';
import { nearbySnapshotCandidates } from '../snapshot/discovery.mjs';
import { describeResidue, qualityMetrics } from '../validation/source-quality.mjs';

function disassembleWithSnapshots(blob, options, backend) {
  const snapshots = options.snapshot
    ? [options.snapshot]
    : options.snapshotSearch
      ? nearbySnapshotCandidates(blob.path)
      : [];
  const attempts = [...snapshots, null];
  const errors = [];

  for (const snapshotPath of attempts) {
    try {
      const result = backend.disassemble(blob.path, options, snapshotPath);
      return {
        source: result.text,
        backendId: result.backendId,
        snapshotPath,
      };
    } catch (error) {
      errors.push({ snapshotPath, error });
      if (options.snapshot) break;
    }
  }

  const finalError = errors.at(-1)?.error ?? new Error('Disassembly failed');
  if (snapshots.length > 0 && !options.snapshot) {
    const tried = snapshots.map((item) => `  - ${item}`).join('\n');
    finalError.message += `\nAuto-discovered snapshot candidates tried:\n${tried}`;
  }
  throw finalError;
}

const researchExtensions = Object.freeze({
  disassembly: '.disassembly.txt',
  translated: '.translated.txt',
  cfg: '.cfg.json',
});

function researchOutputPath(outputRoot, relativeOutput, kind) {
  const extension = researchExtensions[kind];
  const base = relativeOutput.replace(/\.(?:c?js|mjs)$/i, '');
  return path.join(outputRoot, '.analysis', `${base}${extension}`);
}

function emitResearchOutputs(options, backend, disassemblyPath, relativeOutput) {
  const emittedPaths = [];
  for (const kind of options.emit) {
    const destination = researchOutputPath(options.output, relativeOutput, kind);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.writeFileSync(destination, backend.emit(disassemblyPath, kind), 'utf8');
    emittedPaths.push(destination);
  }
  return emittedPaths;
}

function recoverSources(options, paths, backend) {
  if (!fs.existsSync(options.input)) throw new Error(`Input does not exist: ${options.input}`);
  if (!fs.existsSync(paths.sourceRecoveryEntryPath)) {
    throw new Error(`Source-recovery engine is missing: ${paths.sourceRecoveryEntryPath}`);
  }
  if (options.snapshot && !fs.existsSync(options.snapshot)) {
    throw new Error(`Snapshot does not exist: ${options.snapshot}`);
  }
  if (options.payloadOffset !== null && !options.profile) {
    throw new Error('--payload-offset requires --profile');
  }
  if (options.backend === 'd8' && options.payloadOffset !== null) {
    throw new Error('The d8 backend does not support --payload-offset');
  }

  assertPythonAvailable(options.python);
  const blobs = findBlobs(options.input);
  if (blobs.length === 0) throw new Error(`No .v8blob or .jsc files found under ${options.input}`);
  assertUniqueOutputs(blobs);
  fs.mkdirSync(options.output, { recursive: true });
  removeLegacyArtifacts(options.output);

  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-to-js-'));
  let emitted = 0;
  let failed = 0;

  try {
    for (const [index, blob] of blobs.entries()) {
      const relativeOutput = outputRelativePath(blob.relativePath);
      const sourcePath = path.join(options.output, relativeOutput);
      const disassemblyPath = path.join(temporaryRoot, `${index}.disassembly.txt`);
      const candidatePath = path.join(temporaryRoot, `${index}.candidate.js`);
      const analysisPaths = options.emit.map(
        (kind) => researchOutputPath(options.output, relativeOutput, kind),
      );
      process.stdout.write(`[${index + 1}/${blobs.length}] ${blob.relativePath} `);

      try {
        const disassembly = disassembleWithSnapshots(blob, options, backend);
        fs.writeFileSync(disassemblyPath, disassembly.source, 'utf8');
        const derived = backend.decompile(disassemblyPath, options.level);
        const missingClosures = unresolvedClosureBindings(derived);
        if (missingClosures.length > 0) {
          throw new Error(
            `Recovered closure target has no function declaration: ${missingClosures.join(', ')}`,
          );
        }

        const source = normalizeDerivedSource(derived);
        fs.writeFileSync(candidatePath, source, 'utf8');
        const syntax = syntaxCheck(candidatePath);
        if (!syntax.ok) {
          throw new Error(`Generated JavaScript failed syntax validation\n${syntax.detail}`);
        }
        const quality = qualityMetrics(source);
        if (!quality.residueFree) {
          throw new Error(`Decompiler residue remains: ${describeResidue(quality, source)}`);
        }

        fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
        fs.copyFileSync(candidatePath, sourcePath);
        emitResearchOutputs(options, backend, disassemblyPath, relativeOutput);
        emitted += 1;
        const snapshotNote = disassembly.snapshotPath
          ? ` [snapshot: ${path.basename(disassembly.snapshotPath)}]`
          : '';
        const backendNote = disassembly.backendId === 'profile'
          ? ''
          : ` [backend: ${disassembly.backendId}]`;
        const analysisNote = options.emit.length > 0
          ? ` [analysis: ${options.emit.join(',')}]`
          : '';
        process.stdout.write(`OK${backendNote}${snapshotNote}${analysisNote} -> ${sourcePath}\n`);
      } catch (error) {
        failed += 1;
        if (fs.existsSync(sourcePath)) fs.rmSync(sourcePath, { force: true });
        for (const analysisPath of analysisPaths) {
          if (fs.existsSync(analysisPath)) fs.rmSync(analysisPath, { force: true });
        }
        process.stdout.write(`FAILED\n  ${String(error.message ?? error).replaceAll('\n', '\n  ')}\n`);
      }
    }
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }

  console.log(`\nFinished: ${emitted} JavaScript file(s), ${failed} failed conversion(s).`);
  if (failed > 0) process.exitCode = 2;
  return { emitted, failed };
}

export { recoverSources, researchOutputPath };
