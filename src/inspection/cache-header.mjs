const FALLBACK_LAYOUTS = Object.freeze({
  legacy: {
    name: 'legacy', baseSize: 24, versionOffset: 4, sourceOffset: 8, flagsOffset: 12,
    payloadOffset: 16, checksumOffsets: [20],
  },
  'read-only-checksum': {
    name: 'read-only-checksum', baseSize: 28, versionOffset: 4, sourceOffset: 8,
    flagsOffset: 12, payloadOffset: 20, checksumOffsets: [24], roSnapshotOffset: 16,
  },
  'reservation-checksum': {
    name: 'reservation-checksum', baseSize: 28, versionOffset: 4, sourceOffset: 8,
    flagsOffset: 12, payloadOffset: 20, checksumOffsets: [24], reservationCountOffset: 16,
  },
  'reservation-checksum-pair': {
    name: 'reservation-checksum-pair', baseSize: 32, versionOffset: 4, sourceOffset: 8,
    flagsOffset: 12, payloadOffset: 20, checksumOffsets: [24, 28],
    reservationCountOffset: 16,
  },
  'legacy-code-stub': {
    name: 'legacy-code-stub', baseSize: 40, versionOffset: 4, sourceOffset: 8,
    flagsOffset: 16, payloadOffset: 28, checksumOffsets: [32, 36], cpuFeaturesOffset: 12,
    reservationCountOffset: 20, codeStubKeyCountOffset: 24,
  },
  'legacy-code-stub-extra': {
    name: 'legacy-code-stub-extra', baseSize: 44, versionOffset: 8, sourceOffset: 12,
    flagsOffset: 20, payloadOffset: 32, checksumOffsets: [36, 40], cpuFeaturesOffset: 16,
    reservationCountOffset: 28, codeStubKeyCountOffset: 24, externalReferenceCountOffset: 4,
  },
});

function readUInt32(data, offset) {
  return Number.isInteger(offset) && offset >= 0 && data.length >= offset + 4
    ? data.readUInt32LE(offset)
    : null;
}

function align(value, size) {
  return Math.ceil(value / size) * size;
}

function isZeroPadding(data, start, end) {
  return data.subarray(start, end).every((value) => value === 0);
}

function inferredHeaderFormat(profile) {
  if (profile.headerFormat) return profile.headerFormat;
  if (profile.hasRoSnapshotChecksum) return 'read-only-checksum';
  const [major, minor] = profile.version.split('.').map(Number);
  if (major >= 9) return 'legacy';
  if (major >= 8) return 'reservation-checksum';
  if (major > 7 || (major === 7 && minor >= 4)) return 'reservation-checksum-pair';
  if (major >= 6) return 'legacy-code-stub-extra';
  return 'legacy-code-stub';
}

function profileHeaderLayout(profile) {
  const raw = profile.cacheHeaderLayout;
  if (!raw || typeof raw !== 'object') {
    return FALLBACK_LAYOUTS[inferredHeaderFormat(profile)] ?? null;
  }
  const layout = {
    name: raw.name ?? profile.headerFormat ?? 'unknown',
    baseSize: raw.base_size,
    versionOffset: raw.version_offset,
    sourceOffset: raw.source_offset,
    flagsOffset: raw.flags_offset,
    payloadOffset: raw.payload_offset,
    checksumOffsets: raw.checksum_offsets,
    cpuFeaturesOffset: raw.cpu_features_offset,
    roSnapshotOffset: raw.ro_snapshot_offset,
    reservationCountOffset: raw.reservation_count_offset,
    codeStubKeyCountOffset: raw.code_stub_key_count_offset,
    externalReferenceCountOffset: raw.external_reference_count_offset,
  };
  if (!Number.isInteger(layout.baseSize)
    || !Number.isInteger(layout.versionOffset)
    || !Number.isInteger(layout.sourceOffset)
    || !Number.isInteger(layout.flagsOffset)
    || !Number.isInteger(layout.payloadOffset)
    || !Array.isArray(layout.checksumOffsets)
    || layout.checksumOffsets.length === 0
    || layout.checksumOffsets.some((value) => !Number.isInteger(value))) {
    return null;
  }
  const fieldOffsets = [
    layout.versionOffset,
    layout.sourceOffset,
    layout.flagsOffset,
    layout.payloadOffset,
    ...layout.checksumOffsets,
    layout.cpuFeaturesOffset,
    layout.roSnapshotOffset,
    layout.reservationCountOffset,
    layout.codeStubKeyCountOffset,
    layout.externalReferenceCountOffset,
  ].filter((value) => value !== null && value !== undefined);
  if (layout.baseSize < 4 || fieldOffsets.some((offset) => (
    offset < 0 || offset % 4 !== 0 || offset + 4 > layout.baseSize
  ))) {
    return null;
  }
  return layout;
}

function findProfileForData(data, profiles) {
  const candidates = rankVersionHashCandidates(data, profiles)
    .filter((candidate) => candidate.headerValid && candidate.expectedOffset)
    .sort((left, right) => right.score - left.score);
  if (candidates.length === 0) return null;
  const best = candidates[0];
  const tied = candidates.filter((candidate) => candidate.score === best.score);
  return tied.length === 1 ? profiles.profiles.find((profile) => profile.version === best.version) : null;
}

function findVersionHashCandidates(data, profiles) {
  const offsets = new Set(
    profiles.profiles
      .map((profile) => profileHeaderLayout(profile)?.versionOffset)
      .filter((offset) => Number.isInteger(offset)),
  );
  for (let offset = 0; offset + 4 <= Math.min(data.length, 256); offset += 4) {
    const value = readUInt32(data, offset);
    if (profiles.profiles.some((profile) => profile.versionHash === value)) offsets.add(offset);
  }
  return [...offsets].sort((left, right) => left - right).flatMap((offset) => {
    const value = readUInt32(data, offset);
    if (value === null) return [];
    const matches = profiles.profiles
      .filter((profile) => {
        const layout = profileHeaderLayout(profile);
        return layout?.versionOffset === offset && profile.versionHash === value;
      })
      .map((profile) => profile.version);
    return [{ offset, value, matches }];
  });
}

function rankVersionHashCandidates(data, profiles, maxScanBytes = 256) {
  const candidates = [];
  const limit = Math.min(data.length - 4, maxScanBytes);
  for (let offset = 0; offset <= limit; offset += 4) {
    const value = readUInt32(data, offset);
    if (value === null) continue;
    for (const profile of profiles.profiles) {
      if (profile.versionHash !== value) continue;
      const layout = profileHeaderLayout(profile);
      if (!layout) continue;
      let headerValid = false;
      let headerError = null;
      if (layout.versionOffset === offset) {
        try {
          const header = readCachedDataHeader(data, profile);
          headerValid = header.valid === true;
          headerError = header.valid ? null : header.error ?? 'header validation failed';
        } catch (error) {
          headerError = error.message;
        }
      }
      const expectedOffset = layout.versionOffset === offset;
      const score = (headerValid ? 100 : 0) + (expectedOffset ? 40 : 10);
      candidates.push({
        version: profile.version,
        versionHash: value,
        offset,
        expectedOffset,
        headerValid,
        headerFormat: layout.name,
        headerError,
        score,
        confidence: headerValid ? 'exact' : expectedOffset ? 'version-only' : 'weak',
      });
    }
  }
  return candidates.sort((left, right) => right.score - left.score || left.offset - right.offset);
}

function readCachedDataHeader(data, profile) {
  const layout = profileHeaderLayout(profile);
  if (!layout) return { valid: false, error: 'profile has no supported header layout' };
  if (data.length < layout.baseSize) {
    return { valid: false, error: `${layout.name} header is shorter than ${layout.baseSize} bytes` };
  }

  const reservationCount = readUInt32(data, layout.reservationCountOffset) ?? 0;
  const codeStubKeyCount = readUInt32(data, layout.codeStubKeyCountOffset) ?? 0;
  const metadataSize = (reservationCount + codeStubKeyCount) * 4;
  const metadataEnd = layout.baseSize + metadataSize;
  if (reservationCount > 1_000_000 || codeStubKeyCount > 1_000_000
    || metadataEnd > data.length) {
    return { valid: false, error: `${layout.name} metadata counts exceed file bounds` };
  }
  const payloadLength = readUInt32(data, layout.payloadOffset);
  if (payloadLength === null) {
    return { valid: false, error: `${layout.name} payload length is outside the header` };
  }
  const payloadStart = data.length - payloadLength;
  if (payloadStart < metadataEnd || payloadStart > data.length) {
    return {
      valid: false,
      error: `${layout.name} payload is outside the file bounds`,
    };
  }
  const possibleStarts = new Set([align(metadataEnd, 4), align(metadataEnd, 8)]);
  if (!possibleStarts.has(payloadStart)) {
    return {
      valid: false,
      error: `${layout.name} payload starts at ${payloadStart}; expected ${[...possibleStarts].join(' or ')}`,
    };
  }
  if (!isZeroPadding(data, metadataEnd, payloadStart)) {
    return { valid: false, error: `${layout.name} header padding is not zero` };
  }
  const versionHash = readUInt32(data, layout.versionOffset);
  if (versionHash !== profile.versionHash) {
    return {
      valid: false,
      error: `header version hash 0x${versionHash?.toString(16)} does not match profile ${profile.version}`,
    };
  }
  const checksumValues = layout.checksumOffsets.map((offset) => readUInt32(data, offset));
  return {
    valid: true,
    name: layout.name,
    format: layout.name,
    size: payloadStart,
    headerSize: payloadStart,
    baseSize: layout.baseSize,
    magic: readUInt32(data, 0),
    versionHash,
    sourceHash: readUInt32(data, layout.sourceOffset),
    flagsHash: readUInt32(data, layout.flagsOffset),
    cpuFeatures: readUInt32(data, layout.cpuFeaturesOffset),
    externalReferenceCount: readUInt32(data, layout.externalReferenceCountOffset),
    snapshotChecksum: readUInt32(data, layout.roSnapshotOffset),
    payloadLength,
    checksum: checksumValues[0],
    checksumPartB: checksumValues[1] ?? null,
    reservationCount,
    codeStubKeyCount,
    reservations: Array.from({ length: reservationCount }, (_, index) => (
      readUInt32(data, layout.baseSize + index * 4)
    )),
  };
}

function runtimeVariantMapping(profile, flagsHash) {
  const mapping = profile.runtimeVariantByFlagsHash ?? {};
  const key = `0x${flagsHash.toString(16).padStart(8, '0')}`;
  return mapping[key] ?? mapping[String(flagsHash)] ?? null;
}

function hasDistinctRuntimeVariants(profile) {
  const values = Object.values(profile.runtimeVariants ?? {});
  const fingerprints = new Set(values.map((names) => JSON.stringify(names)));
  return fingerprints.size > 1;
}

function assessEmbedderCompatibility(
  header,
  profile,
  runtimeVariant = null,
  verifiedSnapshotChecksum = undefined,
) {
  if (!profile) {
    return {
      status: 'unknown-version',
      version: null,
      versionHash: header?.versionHash ?? null,
      sourceHash: header?.sourceHash ?? null,
      flagsHash: header?.flagsHash ?? null,
      cpuFeatures: header?.cpuFeatures ?? null,
      snapshotChecksum: header?.snapshotChecksum ?? null,
      runtimeVariant: null,
      runtimeVariantSource: null,
      flagsStatus: 'unknown',
      snapshotStatus: 'unknown',
      warnings: ['no exact V8 profile matched the version hash'],
    };
  }
  if (!header?.valid) {
    return {
      status: 'unsupported-header',
      version: profile.version,
      versionHash: profile.versionHash,
      sourceHash: null,
      flagsHash: null,
      cpuFeatures: null,
      snapshotChecksum: null,
      runtimeVariant: runtimeVariant ?? profile.runtimeDefaultVariant,
      runtimeVariantSource: runtimeVariant ? 'override' : 'profile-default',
      flagsStatus: 'unknown',
      snapshotStatus: 'unknown',
      warnings: ['profile matched, but the cached-data header layout did not validate'],
    };
  }
  const mapped = runtimeVariantMapping(profile, header.flagsHash);
  const rawPayload = header.rawPayload === true;
  const status = rawPayload
    ? 'version-only'
    : mapped
      ? 'exact-runtime-variant'
      : hasDistinctRuntimeVariants(profile)
        ? 'unknown-flags'
        : 'version-only';
  const selected = runtimeVariant ?? mapped ?? profile.runtimeDefaultVariant;
  const snapshotStatus = header.snapshotChecksum === null
    ? 'not-applicable'
    : verifiedSnapshotChecksum === undefined
      ? 'required'
      : verifiedSnapshotChecksum === header.snapshotChecksum
        ? 'matched'
        : 'mismatch';
  const compatibilityStatus = snapshotStatus === 'mismatch'
    ? 'snapshot-mismatch'
    : snapshotStatus === 'required'
      ? 'snapshot-unverified'
      : status;
  const warnings = [];
  if (flagsStatus(profile, rawPayload, mapped) === 'unknown') {
    warnings.push('flags hash is not mapped to a unique runtime table; default variant used');
  }
  if (snapshotStatus === 'required') {
    warnings.push('cached data contains a read-only snapshot checksum; snapshot not verified');
  } else if (snapshotStatus === 'mismatch') {
    warnings.push('provided startup snapshot checksum does not match cached data');
  }
  return {
    status: compatibilityStatus,
    version: profile.version,
    versionHash: header.versionHash,
    sourceHash: header.sourceHash,
    flagsHash: header.flagsHash,
    cpuFeatures: header.cpuFeatures,
    externalReferenceCount: header.externalReferenceCount,
    snapshotChecksum: header.snapshotChecksum,
    runtimeVariant: selected,
    runtimeVariantSource: runtimeVariant ? 'override' : mapped ? 'flags-hash' : 'profile-default',
    flagsStatus: flagsStatus(profile, rawPayload, mapped),
    snapshotStatus,
    warnings,
  };
}

function flagsStatus(profile, rawPayload, mapped) {
  return rawPayload
    ? 'not-present'
    : mapped
      ? 'known'
      : hasDistinctRuntimeVariants(profile)
        ? 'unknown'
        : 'not-discriminating';
}

export {
  assessEmbedderCompatibility,
  findVersionHashCandidates,
  rankVersionHashCandidates,
  findProfileForData,
  profileHeaderLayout,
  readCachedDataHeader,
};
