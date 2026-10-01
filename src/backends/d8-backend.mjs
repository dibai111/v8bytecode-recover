import fs from 'node:fs';
import path from 'node:path';

import { runProcess, runPython } from './processes.mjs';
import { discoverD8 } from './d8-discovery.mjs';
import { createDisassemblyResult } from './protocol.mjs';

function createD8Backend({ d8Path, d8Directory, engineRoot, python, projectRoot }) {
  const discovery = discoverD8({
    requestedPath: d8Path,
    d8Directory,
    projectRoot: projectRoot ?? process.cwd(),
  });
  const resolvedPath = discovery.path;
  return {
    id: 'd8',
    d8Path: resolvedPath,
    discovery,
    capabilities: Object.freeze({
      directCachedData: true,
      rawPayload: false,
      snapshots: false,
      matchingRuntime: true,
    }),
    available() {
      return discovery.available;
    },
    disassemble(blobPath) {
      if (!discovery.available || !resolvedPath) {
        throw new Error(
          `No patched d8 with loadjsc() was found. ${discovery.error ?? 'Configure --d8 or V8BYTECODE_D8.'}`,
        );
      }
      const expression = `loadjsc(${JSON.stringify(path.resolve(blobPath))})`;
      return createDisassemblyResult({
        backend: 'd8',
        d8Path: resolvedPath,
        d8Version: discovery.version,
        text: runProcess(resolvedPath, ['-e', expression], {
          cwd: path.dirname(resolvedPath),
        }),
      });
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

export { createD8Backend };
