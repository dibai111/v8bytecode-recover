import fs from 'node:fs';

import { runPython } from './processes.mjs';

function cachedDataArguments(blobPath, options, snapshotPath) {
  const args = ['-m', 'cached_data', blobPath];
  if (options.profile) args.push('--version', options.profile);
  if (options.profileDirectory) args.push('--profile-dir', options.profileDirectory);
  if (snapshotPath) args.push('--snapshot-blob', snapshotPath);
  if (options.runtimeVariant) args.push('--runtime-variant', options.runtimeVariant);
  if (options.payloadOffset !== null) args.push('--payload-offset', String(options.payloadOffset));
  return args;
}

function createProfileBackend({ engineRoot, python }) {
  return {
    id: 'profile',
    capabilities: Object.freeze({
      directCachedData: true,
      rawPayload: true,
      snapshots: true,
    }),
    disassemble(blobPath, options, snapshotPath) {
      return {
        backendId: 'profile',
        text: runPython(
        python,
        cachedDataArguments(blobPath, options, snapshotPath),
        engineRoot,
        ),
      };
    },
    decompile(disassemblyPath, level) {
      return runPython(
        python,
        ['-m', 'source_recovery', disassemblyPath, '--level', String(level)],
        engineRoot,
      );
    },
    emit(disassemblyPath, kind) {
      if (kind === 'disassembly') return fs.readFileSync(disassemblyPath, 'utf8');
      if (kind === 'translated') {
        return runPython(
          python,
          ['-m', 'source_recovery', disassemblyPath, '--level', '1'],
          engineRoot,
        );
      }
      if (kind === 'cfg') {
        return runPython(
          python,
          ['-m', 'source_recovery.research', disassemblyPath, '--emit', 'cfg'],
          engineRoot,
        );
      }
      throw new Error(`Unsupported research output: ${kind}`);
    },
  };
}

export { createProfileBackend };
