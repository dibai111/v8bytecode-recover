function createAutoBackend(profileBackend, d8Backend, autoOptions = {}) {
  return {
    id: 'auto',
    capabilities: Object.freeze({
      directCachedData: true,
      rawPayload: true,
      snapshots: true,
      matchingRuntimeFallback: true,
    }),
    disassemble(blobPath, options = {}, snapshotPath) {
      if (autoOptions.preferD8 && !snapshotPath && d8Backend.available()) {
        try {
          return d8Backend.disassemble(blobPath, options, null);
        } catch (d8Error) {
          try {
            return profileBackend.disassemble(blobPath, options, null);
          } catch (profileError) {
            throw new AggregateError(
              [d8Error, profileError],
              `Both matching-d8 and profile backends failed:\nd8: ${d8Error.message}\nprofile: ${profileError.message}`,
            );
          }
        }
      }
      try {
        return profileBackend.disassemble(blobPath, options, snapshotPath);
      } catch (profileError) {
        const hasPayloadOffset = options.payloadOffset !== null
          && options.payloadOffset !== undefined;
        if (snapshotPath || !d8Backend.available() || hasPayloadOffset) {
          throw profileError;
        }
        try {
          return d8Backend.disassemble(blobPath, options, null);
        } catch (d8Error) {
          throw new AggregateError(
            [profileError, d8Error],
            `Both profile and matching-d8 backends failed:\nprofile: ${profileError.message}\nd8: ${d8Error.message}`,
          );
        }
      }
    },
    decompile(disassemblyPath, level) {
      return profileBackend.decompile(disassemblyPath, level);
    },
    emit(disassemblyPath, kind) {
      return profileBackend.emit(disassemblyPath, kind);
    },
  };
}

export { createAutoBackend };
