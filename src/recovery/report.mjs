import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

function sha256(source) {
  return crypto.createHash('sha256').update(source).digest('hex');
}

function sourceSummary(source, metrics, analysis = null) {
  return {
    bytes: Buffer.byteLength(source, 'utf8'),
    lines: source.split('\n').length - 1,
    functions: analysis?.functionCount ?? (source.match(/\bfunction\b/g) ?? []).length,
    sha256: sha256(source),
    residueFree: metrics.residueFree,
    metrics,
  };
}

function createRecoveryReport(options, files, startedAt, finishedAt = new Date()) {
  const succeeded = files.filter((file) => file.success).length;
  const partial = files.filter((file) => file.success && file.partialRecovery).length;
  return {
    format: 1,
    generatedAt: finishedAt.toISOString(),
    elapsedMs: Math.round(finishedAt.getTime() - startedAt.getTime()),
    input: options.input,
    inputFormat: options.inputFormat ?? 'raw',
    output: options.output,
    settings: {
      backend: options.backend,
      d8Path: options.d8Path ?? null,
      d8Directory: options.d8Directory ?? null,
      profile: options.profile,
      profileDirectory: options.profileDirectory ?? null,
      level: options.level,
      emit: options.emit,
      splitFunctions: options.splitFunctions ?? null,
      functionNames: options.functionNames ?? [],
      includeFunctions: options.includeFunctions ?? [],
      excludeFunctions: options.excludeFunctions ?? [],
      splitMode: options.splitMode ?? 'declarers',
      splitDepth: options.splitDepth ?? null,
      treeRoot: options.treeRoot ?? null,
      treeMode: options.treeMode ?? 'declarers',
      treeDepth: options.treeDepth ?? null,
      normalizeNames: Boolean(options.normalizeNames),
      strict: Boolean(options.strict),
      resume: Boolean(options.resume),
      snapshotSearch: options.snapshotSearch,
      runtimeVariant: options.runtimeVariant,
      embedder: options.embedder ?? 'unknown',
      scope: options.scope ?? null,
      showAll: Boolean(options.showAll),
      inlineDepth: options.inlineDepth ?? null,
      inlineBranchLimit: options.inlineBranchLimit ?? null,
      research: Boolean(options.research),
    },
    counts: {
      total: files.length,
      succeeded,
      partial,
      resumed: files.filter((file) => file.success && file.resumed).length,
      failed: files.length - succeeded,
    },
    files,
  };
}

function writeRecoveryReport(reportPath, report) {
  const destination = path.resolve(reportPath);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  return destination;
}

export { createRecoveryReport, sourceSummary, writeRecoveryReport };
