import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  assertUniqueOutputs,
  findInputs,
  outputRelativePath,
  removeLegacyArtifacts,
} from '../io/blob-files.mjs';
import {
  readRecoveryArtifact,
  serializeRecoveryArtifact,
} from '../io/recovery-artifact.mjs';
import { analyzeSource } from '../analysis/source-index.mjs';
import { writeFunctionFiles } from '../analysis/function-files.mjs';
import { buildFunctionTree } from '../analysis/function-tree.mjs';
import { normalizeSourceNames } from '../analysis/name-normalization.mjs';
import {
  normalizeDerivedSource,
  unresolvedClosureBindings,
} from '../recovery/source-transforms.mjs';
import { assertPythonAvailable, syntaxCheck } from '../runtime/processes.mjs';
import { nearbySnapshotCandidates } from '../snapshot/discovery.mjs';
import { describeResidue, qualityMetrics } from '../validation/source-quality.mjs';
import {
  createRecoveryReport,
  sourceSummary,
  writeRecoveryReport,
} from '../reporting/recovery-report.mjs';
import {
  createPartialRecoverySource,
  isReadOnlyOnlyResidue,
} from '../validation/unresolved-values.mjs';
import {
  createRecoveryManifest,
  fileFingerprint,
  isCompatibleRecoveryManifest,
  manifestEntryKey,
  readRecoveryManifest,
  recoveryManifestFileName,
  updateRecoveryManifest,
  writeRecoveryManifest,
} from '../io/recovery-manifest.mjs';

function disassembleWithSnapshots(input, options, backend) {
  const snapshots = options.snapshot
    ? [options.snapshot]
    : options.snapshotSearch !== false
      ? nearbySnapshotCandidates(input.path)
      : [];
  const attempts = [...snapshots, null];
  const errors = [];

  for (const snapshotPath of attempts) {
    try {
      const result = backend.disassemble(input.path, options, snapshotPath);
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

function getDisassembly(input, options, backend, inputFormat) {
  if (inputFormat === 'disassembled') {
    return {
      source: fs.readFileSync(input.path, 'utf8'),
      backendId: 'file',
      snapshotPath: null,
      recoveredSource: null,
    };
  }
  if (inputFormat === 'serialized') {
    const artifact = readRecoveryArtifact(input.path);
    return {
      source: artifact.disassembly ?? artifact.source,
      recoveredSource: artifact.source,
      backendId: 'artifact',
      snapshotPath: artifact.snapshot,
      artifact,
    };
  }
  return disassembleWithSnapshots(input, options, backend);
}

const researchExtensions = Object.freeze({
  disassembly: '.disassembly.txt',
  translated: '.translated.txt',
  cfg: '.cfg.json',
  functions: '.functions.json',
  callgraph: '.callgraph.json',
  tree: '.tree.json',
  names: '.names.json',
  serialized: '.v8recovery.json',
});

function researchOutputPath(outputRoot, relativeOutput, kind) {
  const extension = researchExtensions[kind];
  if (!extension) throw new Error(`Unsupported research output: ${kind}`);
  const base = relativeOutput.replace(/\.(?:c?js|mjs)$/i, '');
  return path.join(outputRoot, '.analysis', `${base}${extension}`);
}

function emitResearchOutputs(
  options,
  backend,
  disassemblyPath,
  relativeOutput,
  source,
  analysis,
  normalization,
  inputMetadata,
  disassemblyMetadata,
) {
  const emittedPaths = [];
  for (const kind of options.emit ?? []) {
    const destination = researchOutputPath(options.output, relativeOutput, kind);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    let content;
    if (kind === 'functions') {
      content = JSON.stringify({
        format: analysis.format,
        sourceBytes: analysis.sourceBytes,
        functionCount: analysis.functionCount,
        functions: analysis.functions,
      }, null, 2);
    } else if (kind === 'callgraph') {
      content = JSON.stringify({
        format: analysis.format,
        sourceBytes: analysis.sourceBytes,
        functionCount: analysis.functionCount,
        callGraph: analysis.callGraph,
      }, null, 2);
    } else if (kind === 'tree') {
      content = JSON.stringify(buildFunctionTree(analysis, {
        root: options.treeRoot ?? 'start',
        mode: options.treeMode ?? 'declarers',
        maxDepth: options.treeDepth,
      }), null, 2);
    } else if (kind === 'names') {
      content = JSON.stringify({
        format: 1,
        normalized: Boolean(options.normalizeNames),
        mappings: normalization.mappings,
      }, null, 2);
    } else if (kind === 'serialized') {
      content = serializeRecoveryArtifact({
        input: inputMetadata,
        backend: disassemblyMetadata.backendId,
        snapshot: disassemblyMetadata.snapshotPath,
        disassembly: fs.readFileSync(disassemblyPath, 'utf8'),
        source,
        analysis,
        normalization: {
          normalized: Boolean(options.normalizeNames),
          mappings: normalization.mappings,
        },
      });
    } else {
      content = backend.emit(disassemblyPath, kind);
    }
    fs.writeFileSync(destination, `${content.trimEnd()}\n`, 'utf8');
    emittedPaths.push(destination);
  }
  return emittedPaths;
}

function outputRelativePathFromAbsolute(outputRoot, filePath) {
  return path.relative(outputRoot, filePath);
}

function generatedFilePath(outputRoot, filePath) {
  return path.isAbsolute(filePath) ? filePath : path.join(outputRoot, filePath);
}

function cachedGeneratedFilesValid(entry, outputRoot, sourcePath, analysisPaths, splitFunctions) {
  if (!Array.isArray(entry.generatedFiles) || entry.generatedFiles.length === 0) return false;
  const expectedPaths = [
    sourcePath,
    ...analysisPaths,
    ...(entry.splitFiles ?? []),
    ...(splitFunctions && entry.splitManifest ? [entry.splitManifest] : []),
  ].map((filePath) => path.resolve(filePath));
  const records = new Map(
    entry.generatedFiles.map((item) => [generatedFilePath(outputRoot, item.path), item]),
  );
  for (const expectedPath of expectedPaths) {
    const record = records.get(expectedPath);
    if (!record || !fs.existsSync(expectedPath)) return false;
    if (record.sha256 && fileFingerprint(expectedPath).sha256 !== record.sha256) return false;
  }
  if (splitFunctions) {
    if (!entry.splitManifest || !fs.existsSync(entry.splitManifest)) return false;
    for (const filePath of entry.splitFiles ?? []) {
      const syntax = syntaxCheck(filePath);
      if (!syntax.ok) return false;
    }
  }
  return true;
}

function generatedFileRecords(outputRoot, sourcePath, analysisPaths, splitResult, splitRoot) {
  const files = [
    { path: path.relative(outputRoot, sourcePath), role: 'source' },
    ...analysisPaths.map((filePath) => ({
      path: path.relative(outputRoot, filePath),
      role: 'analysis',
    })),
    ...(splitResult?.files ?? []).map((file) => ({
      path: path.join(splitRoot, file),
      role: 'split',
    })),
    ...(splitResult?.manifestPath
      ? [{ path: splitResult.manifestPath, role: 'split-manifest' }]
      : []),
  ];
  return files.map((file) => ({
    ...file,
    sha256: fileFingerprint(generatedFilePath(outputRoot, file.path)).sha256,
    sizeBytes: fs.statSync(generatedFilePath(outputRoot, file.path)).size,
  }));
}

function reusableEntry({
  entry,
  input,
  inputFingerprint,
  relativeOutput,
  outputRoot,
  analysisPaths,
  splitFunctions,
  needsSourceAnalysis,
}) {
  if (!entry || !entry.success || entry.inputFormat !== input.format) return null;
  if (entry.key !== manifestEntryKey(input) || entry.output !== relativeOutput) return null;
  if (entry.inputFingerprint?.sha256 !== inputFingerprint.sha256
    || entry.inputFingerprint?.sizeBytes !== inputFingerprint.sizeBytes) return null;

  const sourcePath = path.join(outputRoot, relativeOutput);
  if (!fs.existsSync(sourcePath)) return null;
  if (!cachedGeneratedFilesValid(entry, outputRoot, sourcePath, analysisPaths, splitFunctions)) return null;
  if (entry.outputSha256 && fileFingerprint(sourcePath).sha256 !== entry.outputSha256) return null;

  const syntax = syntaxCheck(sourcePath);
  if (!syntax.ok) return null;
  const source = fs.readFileSync(sourcePath, 'utf8');
  const quality = qualityMetrics(source);
  if (!quality.residueFree) return null;
  let analysis = null;
  try {
    analysis = needsSourceAnalysis ? analyzeSource(source) : null;
  } catch {
    return null;
  }

  const report = {
    ...(entry.report ?? {}),
    success: true,
    resumed: true,
    cached: true,
    elapsedMs: 0,
    inputSha256: inputFingerprint.sha256,
    inputBytes: inputFingerprint.sizeBytes,
    ...sourceSummary(source, quality, analysis),
    functionCount: analysis?.functionCount ?? entry.report?.functionCount ?? null,
    callGraphEdges: analysis?.callGraph.edges.length ?? entry.report?.callGraphEdges ?? null,
    referenceGraphEdges: analysis?.referenceGraph.edges.length ?? entry.report?.referenceGraphEdges ?? null,
  };
  return {
    report,
    entry: {
      ...entry,
      inputFingerprint,
      outputSha256: fileFingerprint(sourcePath).sha256,
      resumed: true,
      report,
    },
  };
}

function recoverSources(options, paths, backend) {
  const inputFormat = options.inputFormat ?? 'auto';
  const payloadOffset = options.payloadOffset ?? null;
  const backendName = options.backend ?? 'auto';
  const emitKinds = options.emit ?? [];
  const needsSourceAnalysis = emitKinds.some((kind) => ['functions', 'callgraph', 'tree', 'names', 'serialized'].includes(kind))
    || Boolean(options.splitFunctions);
  if (!fs.existsSync(options.input)) throw new Error(`Input does not exist: ${options.input}`);
  if (options.snapshot && !fs.existsSync(options.snapshot)) {
    throw new Error(`Snapshot does not exist: ${options.snapshot}`);
  }
  const inputs = findInputs(options.input, inputFormat);
  if (inputs.length === 0) {
    const expected = inputFormat === 'auto'
      ? '.v8blob, .jsc, .disassembly.txt, .disasm.txt, .txt, or .v8recovery.json'
      : inputFormat === 'raw'
      ? '.v8blob or .jsc'
      : inputFormat === 'serialized'
        ? '.v8recovery.json'
        : '.disassembly.txt, .disasm.txt, or .txt';
    throw new Error(`No ${expected} files found under ${options.input}`);
  }
  const hasRawInputs = inputs.some((input) => input.format === 'raw');
  const needsEngine = hasRawInputs || emitKinds.some((kind) => ['translated', 'cfg'].includes(kind));
  if (needsEngine && !fs.existsSync(paths.sourceRecoveryEntryPath)) {
    throw new Error(`Source-recovery engine is missing: ${paths.sourceRecoveryEntryPath}`);
  }
  if (hasRawInputs && payloadOffset !== null && !options.profile) {
    throw new Error('--payload-offset requires --profile');
  }
  if (hasRawInputs && backendName === 'd8' && payloadOffset !== null) {
    throw new Error('The d8 backend does not support --payload-offset');
  }
  if (needsEngine) assertPythonAvailable(options.python);
  assertUniqueOutputs(inputs, inputFormat);
  fs.mkdirSync(options.output, { recursive: true });
  const manifestPath = path.join(options.output, recoveryManifestFileName);
  const previousManifest = options.resume ? readRecoveryManifest(manifestPath) : null;
  const canResume = Boolean(options.resume) && isCompatibleRecoveryManifest(
    previousManifest,
    { inputRoot: options.input, outputRoot: options.output, options },
  );
  removeLegacyArtifacts(options.output, [options.input], { preserveAnalysis: canResume });

  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8bytecode-recover-'));
  const startedAt = new Date();
  const files = [];
  let manifest = options.resume
    ? createRecoveryManifest({
      inputRoot: options.input,
      outputRoot: options.output,
      options,
      startedAt,
    })
    : null;
  const previousEntries = new Map(
    (canResume ? previousManifest.entries : [])
      .map((entry) => [entry.key, entry]),
  );
  const manifestEntries = [];
  let emitted = 0;
  let failed = 0;

  const persistManifest = (status = 'running') => {
    if (!manifest) return;
    manifest = updateRecoveryManifest(manifest, manifestEntries, status);
    writeRecoveryManifest(manifestPath, manifest);
  };

  persistManifest();

  try {
    for (const [index, input] of inputs.entries()) {
      const itemFormat = input.format ?? inputFormat;
      const relativeOutput = outputRelativePath(input.relativePath, itemFormat);
      const sourcePath = path.join(options.output, relativeOutput);
      const disassemblyPath = path.join(temporaryRoot, `${index}.disassembly.txt`);
      const candidatePath = path.join(temporaryRoot, `${index}.candidate.js`);
      const analysisPaths = emitKinds.map(
        (kind) => researchOutputPath(options.output, relativeOutput, kind),
      );
      const fileStartedAt = Date.now();
      let splitResult = null;
      let inputFingerprint = null;
      const fileReport = {
        input: input.relativePath,
        inputFormat: itemFormat,
        output: relativeOutput,
        success: false,
        resumed: false,
      };
      process.stdout.write(`[${index + 1}/${inputs.length}] ${input.relativePath} `);

      try {
        inputFingerprint = fileFingerprint(input.path);
        Object.assign(fileReport, {
          inputSha256: inputFingerprint.sha256,
          inputBytes: inputFingerprint.sizeBytes,
        });
        if (canResume) {
          const cached = reusableEntry({
            entry: previousEntries.get(manifestEntryKey(input)),
            input,
            inputFingerprint,
            relativeOutput,
            outputRoot: options.output,
            analysisPaths,
            splitFunctions: options.splitFunctions,
            needsSourceAnalysis,
          });
          if (cached) {
            emitted += 1;
            Object.assign(fileReport, cached.report);
            files.push(fileReport);
            manifestEntries.push({ ...cached.entry, report: fileReport });
            process.stdout.write(`CACHED -> ${path.join(options.output, relativeOutput)}\n`);
            persistManifest();
            continue;
          }
        }
        const disassembly = getDisassembly(input, options, backend, itemFormat);
        fs.writeFileSync(disassemblyPath, disassembly.source, 'utf8');
        const derived = disassembly.recoveredSource ?? backend.decompile(disassemblyPath, options.level);
        const missingClosures = unresolvedClosureBindings(derived);
        if (missingClosures.length > 0) {
          throw new Error(
            `Recovered closure target has no function declaration: ${missingClosures.join(', ')}`,
          );
        }

        const normalizedSource = normalizeDerivedSource(derived);
        const normalization = options.normalizeNames
          ? normalizeSourceNames(normalizedSource)
          : { source: normalizedSource, mappings: [] };
        let source = normalization.source;
        let partialRecovery = null;
        let quality = qualityMetrics(source);
        if (!quality.residueFree && !options.strict && isReadOnlyOnlyResidue(quality)) {
          const replacement = createPartialRecoverySource(source);
          source = replacement.source;
          partialRecovery = {
            reason: 'unresolved-read-only-references',
            count: replacement.count,
            residueBefore: {
              unresolvedObjects: quality.unresolvedObjects,
              unresolvedHeapValues: quality.unresolvedHeapValues,
            },
            replacements: replacement.replacements,
          };
          quality = qualityMetrics(source);
        }
        fs.writeFileSync(candidatePath, source, 'utf8');
        const syntax = syntaxCheck(candidatePath);
        if (!syntax.ok) {
          throw new Error(`Generated JavaScript failed syntax validation\n${syntax.detail}`);
        }
        if (!quality.residueFree) {
          throw new Error(`Decompiler residue remains: ${describeResidue(quality, source)}`);
        }

        const analysis = needsSourceAnalysis ? analyzeSource(source) : null;
        fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
        fs.copyFileSync(candidatePath, sourcePath);
        emitResearchOutputs(
          options,
          backend,
          disassemblyPath,
          relativeOutput,
          source,
          analysis,
          normalization,
          {
            path: input.relativePath,
            format: itemFormat,
          },
          disassembly,
        );
        if (options.splitFunctions) {
          splitResult = writeFunctionFiles(
            options.splitFunctions,
            relativeOutput,
            source,
            analysis,
            {
              functionNames: options.functionNames,
              includeFunctions: options.includeFunctions,
              excludeFunctions: options.excludeFunctions,
              mode: options.splitMode,
              maxDepth: options.splitDepth,
            },
          );
          for (const file of splitResult.files) {
            const splitPath = path.join(options.splitFunctions, file);
            const splitSyntax = syntaxCheck(splitPath);
            if (!splitSyntax.ok) {
              throw new Error(`Split function failed syntax validation: ${file}\n${splitSyntax.detail}`);
            }
          }
        }
        emitted += 1;
        Object.assign(fileReport, {
          success: true,
          backend: disassembly.backendId,
          snapshot: disassembly.snapshotPath,
          elapsedMs: Date.now() - fileStartedAt,
          ...sourceSummary(source, quality, analysis),
          functionCount: analysis?.functionCount ?? null,
          callGraphEdges: analysis?.callGraph.edges.length ?? null,
          referenceGraphEdges: analysis?.referenceGraph.edges.length ?? null,
          partialRecovery,
          normalizedNames: normalization.mappings,
          splitFunctions: splitResult?.directory ?? null,
          selectedFunctionCount: splitResult?.functionCount ?? null,
          splitMode: splitResult?.selection.mode ?? null,
          splitDepth: splitResult?.selection.maxDepth ?? null,
        });
        const snapshotNote = disassembly.snapshotPath
          ? ` [snapshot: ${path.basename(disassembly.snapshotPath)}]`
          : '';
        const backendNote = disassembly.backendId === 'profile'
          ? ''
          : ` [backend: ${disassembly.backendId}]`;
        const analysisNote = emitKinds.length > 0
          ? ` [analysis: ${emitKinds.join(',')}]`
          : '';
        const partialNote = partialRecovery
          ? ` [best-effort: ${partialRecovery.count} read-only references]`
          : '';
        process.stdout.write(`OK${backendNote}${snapshotNote}${analysisNote}${partialNote} -> ${sourcePath}\n`);
        manifestEntries.push({
          key: manifestEntryKey(input),
          input: input.relativePath,
          inputFormat: itemFormat,
          inputFingerprint,
          output: relativeOutput,
          outputSha256: fileFingerprint(sourcePath).sha256,
          analysisPaths: analysisPaths.map((filePath) => outputRelativePathFromAbsolute(options.output, filePath)),
          splitFunctions: options.splitFunctions ?? null,
          splitManifest: splitResult?.manifestPath ?? null,
          splitFiles: splitResult?.files.map((file) => path.join(options.splitFunctions, file)) ?? [],
          generatedFiles: generatedFileRecords(
            options.output,
            sourcePath,
            analysisPaths,
            splitResult,
            options.splitFunctions,
          ),
          success: true,
          partialRecovery,
          resumed: false,
          report: fileReport,
        });
      } catch (error) {
        failed += 1;
        fileReport.error = String(error.message ?? error);
        fileReport.elapsedMs = Date.now() - fileStartedAt;
        if (fs.existsSync(sourcePath)) fs.rmSync(sourcePath, { force: true });
        for (const analysisPath of analysisPaths) {
          if (fs.existsSync(analysisPath)) fs.rmSync(analysisPath, { force: true });
        }
        if (splitResult) {
          for (const file of splitResult.files) {
            const generatedPath = path.join(options.splitFunctions, file);
            if (fs.existsSync(generatedPath)) fs.rmSync(generatedPath, { force: true });
          }
          if (fs.existsSync(splitResult.manifestPath)) fs.rmSync(splitResult.manifestPath, { force: true });
        }
        process.stdout.write(`FAILED\n  ${String(error.message ?? error).replaceAll('\n', '\n  ')}\n`);
        manifestEntries.push({
          key: manifestEntryKey(input),
          input: input.relativePath,
          inputFormat: itemFormat,
          inputFingerprint,
          output: relativeOutput,
          outputSha256: null,
          analysisPaths: [],
          splitFunctions: options.splitFunctions ?? null,
          splitManifest: null,
          splitFiles: [],
          generatedFiles: [],
          success: false,
          partialRecovery: null,
          resumed: false,
          report: fileReport,
        });
      }
      files.push(fileReport);
      persistManifest();
    }
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }

  const report = createRecoveryReport({ ...options, inputFormat, emit: emitKinds }, files, startedAt);
  if (manifest) persistManifest('complete');
  let reportPath = null;
  if (options.report) {
    reportPath = writeRecoveryReport(options.report, report);
    console.log(`Recovery report: ${reportPath}`);
  }
  console.log(`\nFinished: ${emitted} JavaScript file(s), ${failed} failed conversion(s).`);
  if (failed > 0) process.exitCode = 2;
  return { emitted, failed, reportPath, files };
}

export { recoverSources };
