import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { engineRoot } from '../src/cli/paths.mjs';
import { inspectFile, inspectInput, renderInspection } from '../src/inspection/inspect-input.mjs';
import { assessEmbedderCompatibility } from '../src/inspection/cache-header.mjs';

test('inspects a legacy V8 cached-data header', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-inspect-test-'));
  const filePath = path.join(root, 'sample.jsc');
  try {
    const data = Buffer.alloc(28);
    data.writeUInt32LE(0xdec0ded0, 0);
    data.writeUInt32LE(0xcd991825, 4);
    data.writeUInt32LE(0x11223344, 8);
    data.writeUInt32LE(0x55667788, 12);
    data.writeUInt32LE(4, 16);
    data.writeUInt32LE(0xaabbccdd, 20);
    data.writeUInt32LE(0x01020304, 24);
    fs.writeFileSync(filePath, data);

    const item = inspectFile(filePath, engineRoot, 'auto', null, 'node');
    assert.equal(item.profileVersion, '9.4.146.24');
    assert.equal(item.header.name, 'legacy');
    assert.equal(item.header.payloadLength, 4);
    assert.equal(item.sizeBytes, 28);
    assert.equal(item.embedder.embedder, 'node');
    assert.equal(item.embedder.recommendedBackend, 'matching-d8');
    assert.match(renderInspection([item]), /V8 Blob/);
    assert.match(renderInspection([item], 'zh-CN'), /诊断结果/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('inspects disassembly and recovery artifacts with automatic format detection', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-inspect-mixed-test-'));
  try {
    fs.writeFileSync(path.join(root, 'sample.disassembly.txt'), 'Bytecode\nreturn\n', 'utf8');
    fs.writeFileSync(path.join(root, 'sample.v8recovery.json'), JSON.stringify({
      kind: 'v8bytecode-recover-recovery',
      format: 1,
      source: 'function sample() {}\n',
    }), 'utf8');
    const items = inspectInput(root, engineRoot);
    assert.deepEqual(items.map((item) => item.format), ['disassembled', 'serialized']);
    assert.equal(items[0].textLines, 2);
    assert.equal(items[1].artifact.valid, true);
    assert.match(renderInspection(items), /Artifact: 有效/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('inspects raw blobs against an external exact profile pack', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-external-profile-test-'));
  const profileDirectory = path.join(root, 'profiles');
  const filePath = path.join(root, 'sample.jsc');
  const bundledDirectory = path.join(engineRoot, 'cached_data', 'profiles');
  try {
    fs.mkdirSync(profileDirectory);
    const index = JSON.parse(fs.readFileSync(path.join(bundledDirectory, 'index.json'), 'utf8'));
    index.versions = ['9.4.146.24'];
    fs.writeFileSync(path.join(profileDirectory, 'index.json'), JSON.stringify(index), 'utf8');
    fs.copyFileSync(
      path.join(bundledDirectory, '9.4.146.24.json'),
      path.join(profileDirectory, '9.4.146.24.json'),
    );
    const data = Buffer.alloc(28);
    data.writeUInt32LE(0xdec0ded0, 0);
    data.writeUInt32LE(0xcd991825, 4);
    data.writeUInt32LE(4, 16);
    fs.writeFileSync(filePath, data);

    const item = inspectFile(filePath, engineRoot, 'auto', profileDirectory);
    assert.equal(item.profileVersion, '9.4.146.24');
    assert.equal(item.profileSource, 'external');
    assert.equal(item.profileDirectory, path.resolve(profileDirectory));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('reports a missing external profile file clearly', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-invalid-profile-test-'));
  const profileDirectory = path.join(root, 'profiles');
  try {
    fs.mkdirSync(profileDirectory);
    fs.writeFileSync(path.join(profileDirectory, 'index.json'), JSON.stringify({
      format: 1,
      operand_encoding: {},
      versions: ['14.7.57'],
    }), 'utf8');
    fs.writeFileSync(path.join(root, 'sample.jsc'), Buffer.alloc(28));
    assert.throws(
      () => inspectFile(path.join(root, 'sample.jsc'), engineRoot, 'raw', profileDirectory),
      /ENOENT|no such file/i,
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('reports version-hash candidates for an unknown header layout', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-unknown-header-test-'));
  const filePath = path.join(root, 'unknown.jsc');
  try {
    const data = Buffer.alloc(24);
    data.writeUInt32LE(0xc0de0001, 0);
    data.writeUInt32LE(0x01020304, 4);
    data.writeUInt32LE(0x55667788, 8);
    data.writeUInt32LE(4, 16);
    data.writeUInt32LE(0xaabbccdd, 20);
    fs.writeFileSync(filePath, data);

    const item = inspectFile(filePath, engineRoot, 'raw');
    const candidate = item.versionHashCandidates.find((entry) => entry.offset === 8);
    assert.equal(item.profileKnown, false);
    assert.equal(item.versionHash, null);
    assert.equal(candidate.value, 0x55667788);
    assert.match(renderInspection([item]), /offset 8=0x55667788/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('parses historical cached-data headers from source-derived profile layouts', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-header-layout-test-'));
  const layouts = {
    legacy: {
      base_size: 24, version_offset: 4, source_offset: 8, flags_offset: 12,
      payload_offset: 16, checksum_offsets: [20],
    },
    'read-only-checksum': {
      base_size: 28, version_offset: 4, source_offset: 8, flags_offset: 12,
      payload_offset: 20, checksum_offsets: [24], ro_snapshot_offset: 16,
    },
    'reservation-checksum': {
      base_size: 28, version_offset: 4, source_offset: 8, flags_offset: 12,
      payload_offset: 20, checksum_offsets: [24], reservation_count_offset: 16,
    },
    'reservation-checksum-pair': {
      base_size: 32, version_offset: 4, source_offset: 8, flags_offset: 12,
      payload_offset: 20, checksum_offsets: [24, 28], reservation_count_offset: 16,
    },
    'legacy-code-stub': {
      base_size: 40, version_offset: 4, source_offset: 8, flags_offset: 16,
      payload_offset: 28, checksum_offsets: [32, 36], cpu_features_offset: 12,
      reservation_count_offset: 20, code_stub_key_count_offset: 24,
    },
    'legacy-code-stub-extra': {
      base_size: 44, version_offset: 8, source_offset: 12, flags_offset: 20,
      payload_offset: 32, checksum_offsets: [36, 40], cpu_features_offset: 16,
      reservation_count_offset: 28, code_stub_key_count_offset: 24,
      external_reference_count_offset: 4,
    },
  };
  try {
    const bundled = path.join(engineRoot, 'cached_data', 'profiles', '9.4.146.24.json');
    for (const [index, [format, layout]] of Object.entries(layouts).entries()) {
      const profileDirectory = path.join(root, format);
      fs.mkdirSync(profileDirectory);
      const version = `99.0.${index}`;
      const versionHash = 0x10000001 + index;
      const profile = JSON.parse(fs.readFileSync(bundled, 'utf8'));
      profile.version = version;
      profile.version_hash = versionHash;
      profile.header_format = format;
      profile.has_ro_snapshot_checksum = format === 'read-only-checksum';
      profile.cache_header_layout = { name: format, ...layout };
      fs.writeFileSync(path.join(profileDirectory, 'index.json'), JSON.stringify({
        format: 1,
        operand_encoding: {
          scalable_signed: [], scalable_unsigned: [], fixed_sizes: {},
        },
        versions: [version],
      }));
      fs.writeFileSync(path.join(profileDirectory, `${version}.json`), JSON.stringify(profile));

      const reservationCount = layout.reservation_count_offset ? 1 : 0;
      const stubCount = layout.code_stub_key_count_offset ? 1 : 0;
      const metadataEnd = layout.base_size + ((reservationCount + stubCount) * 4);
      const payloadStart = Math.ceil(metadataEnd / 8) * 8;
      const data = Buffer.alloc(payloadStart + 4);
      data.writeUInt32LE(0xc0de0001, 0);
      data.writeUInt32LE(versionHash, layout.version_offset);
      data.writeUInt32LE(0x11223344, layout.source_offset);
      data.writeUInt32LE(0x55667788, layout.flags_offset);
      if (layout.ro_snapshot_offset !== undefined) data.writeUInt32LE(0x01020304, layout.ro_snapshot_offset);
      if (layout.cpu_features_offset !== undefined) data.writeUInt32LE(0x0a0b0c0d, layout.cpu_features_offset);
      if (layout.external_reference_count_offset !== undefined) data.writeUInt32LE(7, layout.external_reference_count_offset);
      if (layout.reservation_count_offset !== undefined) data.writeUInt32LE(1, layout.reservation_count_offset);
      if (layout.code_stub_key_count_offset !== undefined) data.writeUInt32LE(1, layout.code_stub_key_count_offset);
      data.writeUInt32LE(4, layout.payload_offset);
      layout.checksum_offsets.forEach((offset, checksumIndex) => data.writeUInt32LE(0xaabbccdd + checksumIndex, offset));
      data.writeUInt32LE(0xdeadbeef, payloadStart);
      const filePath = path.join(root, `${format}.jsc`);
      fs.writeFileSync(filePath, data);

      const item = inspectFile(filePath, engineRoot, 'raw', profileDirectory);
      assert.equal(item.profileVersion, version);
      assert.equal(item.header.name, format);
      assert.equal(item.header.valid, true);
      assert.equal(item.header.payloadLength, 4);
      assert.equal(item.header.externalReferenceCount, layout.external_reference_count_offset === undefined ? null : 7);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('distinguishes exact runtime flags from unknown embedder flags', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8blob-compatibility-test-'));
  try {
    const sourceProfile = JSON.parse(fs.readFileSync(
      path.join(engineRoot, 'cached_data', 'profiles', '13.2.152.41.json'),
      'utf8',
    ));
    for (const [name, mapping, expected] of [
      ['exact', { '0x55667788': 'legacy' }, 'exact-runtime-variant'],
      ['unknown', {}, 'unknown-flags'],
    ]) {
      const profileDirectory = path.join(root, name);
      fs.mkdirSync(profileDirectory);
      const version = `99.1.${name === 'exact' ? 1 : 2}`;
      const versionHash = name === 'exact' ? 0x20000001 : 0x20000002;
      const profile = {
        ...sourceProfile,
        version,
        version_hash: versionHash,
        header_format: 'legacy',
        cache_header_layout: {
          name: 'legacy', base_size: 24, version_offset: 4, source_offset: 8,
          flags_offset: 12, payload_offset: 16, checksum_offsets: [20],
        },
        runtime_variant_by_flags_hash: mapping,
      };
      fs.writeFileSync(path.join(profileDirectory, 'index.json'), JSON.stringify({
        format: 1,
        operand_encoding: { scalable_signed: [], scalable_unsigned: [], fixed_sizes: {} },
        versions: [version],
      }));
      fs.writeFileSync(path.join(profileDirectory, `${version}.json`), JSON.stringify(profile));
      const data = Buffer.alloc(28);
      data.writeUInt32LE(0xc0de0001, 0);
      data.writeUInt32LE(versionHash, 4);
      data.writeUInt32LE(0x11223344, 8);
      data.writeUInt32LE(0x55667788, 12);
      data.writeUInt32LE(4, 16);
      data.writeUInt32LE(0xaabbccdd, 20);
      const filePath = path.join(root, `${name}.jsc`);
      fs.writeFileSync(filePath, data);
      assert.equal(inspectFile(filePath, engineRoot, 'raw', profileDirectory).compatibility.status, expected);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('does not call an unverified read-only snapshot exact embedder compatibility', () => {
  const profile = {
    version: '99.2.1',
    versionHash: 0x40000001,
    runtimeDefaultVariant: 'legacy',
    runtimeVariants: { legacy: ['A'] },
    runtimeVariantByFlagsHash: {},
  };
  const header = {
    valid: true,
    versionHash: profile.versionHash,
    sourceHash: 1,
    flagsHash: 2,
    snapshotChecksum: 0x01020304,
    rawPayload: false,
  };

  assert.equal(assessEmbedderCompatibility(header, profile).status, 'snapshot-unverified');
  assert.equal(
    assessEmbedderCompatibility(header, profile, null, 0xffffffff).status,
    'snapshot-mismatch',
  );
  assert.equal(
    assessEmbedderCompatibility(header, profile, null, 0x01020304).status,
    'version-only',
  );
});
