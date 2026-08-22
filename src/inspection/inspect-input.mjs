import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { detectInputFormat, findInputs } from '../io/blob-files.mjs';
import { readRecoveryArtifact } from '../io/recovery-artifact.mjs';
import { loadProfileCatalog } from '../inspection/profile-catalog.mjs';
import { nearbySnapshotCandidates } from '../runtime/snapshot-discovery.mjs';
import {
  assessEmbedderCompatibility,
  findVersionHashCandidates,
  findProfileForData,
  rankVersionHashCandidates,
  readCachedDataHeader,
} from './cache-header.mjs';
import { assessEmbedderCompatibility as assessEmbedderMatrix } from './embedder-matrix.mjs';

function readUInt32(data, offset) {
  return Number.isInteger(offset) && data.length >= offset + 4
    ? data.readUInt32LE(offset)
    : null;
}

function inspectFile(
  filePath,
  engineRoot,
  inputFormat = 'auto',
  profileDirectory = null,
  embedder = 'unknown',
) {
  const data = fs.readFileSync(filePath);
  const format = inputFormat === 'auto' ? detectInputFormat(filePath) : inputFormat;
  const raw = format === 'raw';
  const catalog = raw ? loadProfileCatalog(engineRoot, profileDirectory) : null;
  const profile = raw ? findProfileForData(data, catalog) : null;
  const header = raw && profile ? readCachedDataHeader(data, profile) : null;
  const versionHashCandidates = raw ? findVersionHashCandidates(data, catalog) : [];
  const rankedVersionCandidates = raw ? rankVersionHashCandidates(data, catalog) : [];
  const versionHash = raw
    ? header?.versionHash ?? profile?.versionHash ?? null
    : null;
  const artifact = format === 'serialized'
    ? (() => {
      try {
        const value = readRecoveryArtifact(filePath);
        return {
          valid: true,
          backend: value.backend,
          snapshot: value.snapshot,
          sourceBytes: Buffer.byteLength(value.source, 'utf8'),
          functionCount: value.analysis?.functionCount ?? null,
        };
      } catch (error) {
        return { valid: false, error: error.message };
      }
    })()
    : null;
  const compatibility = raw ? assessEmbedderCompatibility(header, profile) : null;
  return {
    path: path.resolve(filePath),
    name: path.basename(filePath),
    extension: path.extname(filePath).toLowerCase(),
    format,
    sizeBytes: data.length,
    sha256: crypto.createHash('sha256').update(data).digest('hex'),
    magic: raw ? readUInt32(data, 0) : null,
    versionHash: raw ? versionHash : null,
    versionHashCandidates,
    rankedVersionCandidates,
    sourceHash: raw ? header?.sourceHash ?? null : null,
    flagsHash: raw ? header?.flagsHash ?? null : null,
    profileVersion: profile?.version ?? null,
    profileKnown: Boolean(profile),
    profileSource: profile ? catalog.source : null,
    profileDirectory: raw ? catalog.directory : null,
    header,
    compatibility,
    embedder: raw ? assessEmbedderMatrix(engineRoot, embedder, compatibility) : null,
    snapshotCandidates: raw ? nearbySnapshotCandidates(filePath) : [],
    textLines: format === 'disassembled' ? data.toString('utf8').split(/\r?\n/).length - 1 : null,
    artifact,
  };
}

function inspectInput(
  inputPath,
  engineRoot,
  inputFormat = 'auto',
  profileDirectory = null,
  embedder = 'unknown',
) {
  const inputs = findInputs(inputPath, inputFormat);
  if (inputs.length === 0) {
    throw new Error(`No supported input files found under ${inputPath}`);
  }
  return inputs.map((input) => inspectFile(
    input.path,
    engineRoot,
    input.format ?? inputFormat,
    profileDirectory,
    embedder,
  ));
}

function hex(value) {
  return value == null ? 'unknown' : `0x${value.toString(16).padStart(8, '0')}`;
}

const INSPECTION_LABELS = Object.freeze({
  'zh-TW': {
    title: 'V8 Blob 診斷結果',
    format: '格式',
    size: '大小',
    versionHash: 'V8 版本 hash',
    candidates: 'Header hash 候選',
    bestCandidate: 'Hash 最佳候選',
    profile: '推測 profile',
    profileMissing: '未找到，請指定 --profile 或 matching d8',
    profilePack: 'Profile pack',
    profileMiss: '未命中',
    compatibility: 'Embedder 相容性',
    recommendation: '建議 backend',
    missingEvidence: '缺少 exact evidence',
    headerMissing: '未找到 exact profile，無法安全解析',
    disassemblyLines: '反組譯行數',
    valid: '有效',
    invalid: '無效',
    unindexed: '未索引',
    noSnapshot: '附近沒有 matching snapshot',
  },
  'zh-CN': {
    title: 'V8 Blob 诊断结果',
    format: '格式',
    size: '大小',
    versionHash: 'V8 版本 hash',
    candidates: 'Header hash 候选',
    bestCandidate: 'Hash 最佳候选',
    profile: '推测 profile',
    profileMissing: '未找到，请指定 --profile 或 matching d8',
    profilePack: 'Profile pack',
    profileMiss: '未命中',
    compatibility: 'Embedder 兼容性',
    recommendation: '建议 backend',
    missingEvidence: '缺少 exact evidence',
    headerMissing: '未找到 exact profile，无法安全解析',
    disassemblyLines: '反汇编行数',
    valid: '有效',
    invalid: '无效',
    unindexed: '未索引',
    noSnapshot: '附近没有 matching snapshot',
  },
});

function renderInspection(items, language = 'zh-TW') {
  const labels = INSPECTION_LABELS[language] ?? INSPECTION_LABELS['zh-TW'];
  const lines = [labels.title, ''];
  for (const [index, item] of items.entries()) {
    if (index > 0) lines.push('');
    lines.push(`[${index + 1}] ${item.path}`);
    lines.push(`  ${labels.format}: ${item.format ?? 'unknown'}`);
    lines.push(`  ${labels.size}: ${item.sizeBytes.toLocaleString()} bytes`);
    lines.push(`  SHA-256: ${item.sha256}`);
    if (item.format === 'raw') {
      lines.push(`  Magic: ${hex(item.magic)}`);
      lines.push(`  ${labels.versionHash}: ${hex(item.versionHash)}`);
      if (!item.profileKnown && item.versionHashCandidates.length > 0) {
        const candidates = item.versionHashCandidates.map((candidate) => (
          `offset ${candidate.offset}=${hex(candidate.value)}`
            + (candidate.matches.length > 0 ? ` (${candidate.matches.join(', ')})` : '')
        ));
        lines.push(`  ${labels.candidates}: ${candidates.join('; ')}`);
      }
      if (item.rankedVersionCandidates?.length > 0) {
        const best = item.rankedVersionCandidates[0];
        lines.push(
          `  ${labels.bestCandidate}: V8 ${best.version} @ offset ${best.offset} `
            + `${best.confidence} (score ${best.score})`,
        );
      }
      lines.push(`  ${labels.profile}: ${item.profileVersion ?? labels.profileMissing}`);
      lines.push(`  ${labels.profilePack}: ${item.profileSource ?? labels.profileMiss}${item.profileDirectory ? ` (${item.profileDirectory})` : ''}`);
      lines.push(`  ${labels.compatibility}: ${item.compatibility?.status ?? 'unknown-version'}`);
      if (item.embedder) {
        lines.push(`  Embedder matrix: ${item.embedder.label} / ${item.embedder.confidence}`);
        lines.push(`  ${labels.recommendation}: ${item.embedder.recommendedBackend}`);
        if (item.embedder.missingEvidence?.length > 0) {
          lines.push(`  ${labels.missingEvidence}: ${item.embedder.missingEvidence.join(', ')}`);
        }
      }
      if (item.compatibility?.runtimeVariant) {
        lines.push(`  Runtime variant: ${item.compatibility.runtimeVariant} (${item.compatibility.runtimeVariantSource})`);
      }
    }
    if (item.format === 'raw') {
      if (item.header?.valid) {
        lines.push(`  Header: ${item.header.name}, ${item.header.size} bytes`);
        lines.push(`  Payload: ${item.header.payloadLength.toLocaleString()} bytes`);
        lines.push(`  CPU features: ${hex(item.header.cpuFeatures)}`);
        lines.push(`  Snapshot checksum: ${hex(item.header.snapshotChecksum)}`);
      } else if (item.header?.error) {
        lines.push(`  Header: ${item.header.error}`);
      } else {
        lines.push(`  Header: ${labels.headerMissing}`);
      }
    } else if (item.format === 'disassembled') {
      lines.push(`  ${labels.disassemblyLines}: ${item.textLines.toLocaleString()}`);
    } else if (item.format === 'serialized') {
      lines.push(`  Artifact: ${item.artifact?.valid ? labels.valid : `${labels.invalid} (${item.artifact?.error ?? 'unknown error'})`}`);
      if (item.artifact?.valid) {
        lines.push(`  Source bytes: ${item.artifact.sourceBytes.toLocaleString()}`);
        lines.push(`  Functions: ${item.artifact.functionCount ?? labels.unindexed}`);
      }
    }
    if (item.format === 'raw') {
      lines.push(
        `  Snapshot: ${item.snapshotCandidates.length > 0
          ? item.snapshotCandidates.join(', ')
          : labels.noSnapshot}`,
      );
    }
  }
  return `${lines.join('\n')}\n`;
}

export { inspectFile, inspectInput, renderInspection };
