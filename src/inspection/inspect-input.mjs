import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { detectInputFormat, findInputs } from '../io/blob-files.mjs';
import { readRecoveryArtifact } from '../io/recovery-artifact.mjs';
import { findProfileByHash, loadProfileCatalog } from '../profiles/catalog.mjs';
import { nearbySnapshotCandidates } from '../snapshot/discovery.mjs';

function readUInt32(data, offset) {
  return data.length >= offset + 4 ? data.readUInt32LE(offset) : null;
}

function isZeroPadding(data, start, end) {
  return data.subarray(start, end).every((value) => value === 0);
}

function detectHeader(data) {
  const candidates = [];
  for (const layout of [
    {
      name: 'legacy',
      minimumSize: 24,
      snapshotOffset: null,
      lengthOffset: 16,
      checksumOffset: 20,
    },
    {
      name: 'read-only-checksum',
      minimumSize: 28,
      snapshotOffset: 16,
      lengthOffset: 20,
      checksumOffset: 24,
    },
  ]) {
    if (data.length < layout.minimumSize) continue;
    const payloadLength = readUInt32(data, layout.lengthOffset);
    const headerSize = payloadLength === null ? null : data.length - payloadLength;
    if (
      headerSize !== null
      && headerSize >= layout.minimumSize
      && headerSize % 4 === 0
      && isZeroPadding(data, layout.minimumSize, headerSize)
    ) {
      candidates.push({
        name: layout.name,
        size: headerSize,
        payloadLength,
        checksum: readUInt32(data, layout.checksumOffset),
        snapshotChecksum: layout.snapshotOffset === null
          ? null
          : readUInt32(data, layout.snapshotOffset),
      });
    }
  }
  return candidates.length === 1 ? candidates[0] : null;
}

function inspectFile(filePath, engineRoot, inputFormat = 'auto') {
  const data = fs.readFileSync(filePath);
  const format = inputFormat === 'auto' ? detectInputFormat(filePath) : inputFormat;
  const versionHash = readUInt32(data, 4);
  const raw = format === 'raw';
  const catalog = raw ? loadProfileCatalog(engineRoot) : null;
  const profile = raw && versionHash !== null
    ? findProfileByHash(catalog, versionHash)
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
  return {
    path: path.resolve(filePath),
    name: path.basename(filePath),
    extension: path.extname(filePath).toLowerCase(),
    format,
    sizeBytes: data.length,
    sha256: crypto.createHash('sha256').update(data).digest('hex'),
    magic: raw ? readUInt32(data, 0) : null,
    versionHash: raw ? versionHash : null,
    sourceHash: raw ? readUInt32(data, 8) : null,
    flagsHash: raw ? readUInt32(data, 12) : null,
    profileVersion: profile?.version ?? null,
    profileKnown: Boolean(profile),
    header: raw ? detectHeader(data) : null,
    snapshotCandidates: raw ? nearbySnapshotCandidates(filePath) : [],
    textLines: format === 'disassembled' ? data.toString('utf8').split(/\r?\n/).length - 1 : null,
    artifact,
  };
}

function inspectInput(inputPath, engineRoot, inputFormat = 'auto') {
  const inputs = findInputs(inputPath, inputFormat);
  if (inputs.length === 0) {
    throw new Error(`No supported input files found under ${inputPath}`);
  }
  return inputs.map((input) => inspectFile(input.path, engineRoot, input.format ?? inputFormat));
}

function hex(value) {
  return value === null ? 'unknown' : `0x${value.toString(16).padStart(8, '0')}`;
}

function renderInspection(items) {
  const lines = ['V8 Blob 診斷結果', ''];
  for (const [index, item] of items.entries()) {
    if (index > 0) lines.push('');
    lines.push(`[${index + 1}] ${item.path}`);
    lines.push(`  格式: ${item.format ?? 'unknown'}`);
    lines.push(`  大小: ${item.sizeBytes.toLocaleString()} bytes`);
    lines.push(`  SHA-256: ${item.sha256}`);
    if (item.format === 'raw') {
      lines.push(`  Magic: ${hex(item.magic)}`);
      lines.push(`  V8 版本 hash: ${hex(item.versionHash)}`);
      lines.push(`  推測 profile: ${item.profileVersion ?? '未找到，請指定 --profile 或 matching d8'}`);
    }
    if (item.format === 'raw') {
      if (item.header) {
        lines.push(`  Header: ${item.header.name}, ${item.header.size} bytes`);
        lines.push(`  Payload: ${item.header.payloadLength.toLocaleString()} bytes`);
      } else {
        lines.push('  Header: 無法以內建格式唯一辨識');
      }
    } else if (item.format === 'disassembled') {
      lines.push(`  反組譯行數: ${item.textLines.toLocaleString()}`);
    } else if (item.format === 'serialized') {
      lines.push(`  Artifact: ${item.artifact?.valid ? '有效' : `無效 (${item.artifact?.error ?? 'unknown error'})`}`);
      if (item.artifact?.valid) {
        lines.push(`  Source bytes: ${item.artifact.sourceBytes.toLocaleString()}`);
        lines.push(`  Functions: ${item.artifact.functionCount ?? '未索引'}`);
      }
    }
    if (item.format === 'raw') {
      lines.push(
        `  Snapshot: ${item.snapshotCandidates.length > 0
          ? item.snapshotCandidates.join(', ')
          : '附近沒有 matching snapshot'}`,
      );
    }
  }
  return `${lines.join('\n')}\n`;
}

export { detectHeader, inspectFile, inspectInput, renderInspection };
