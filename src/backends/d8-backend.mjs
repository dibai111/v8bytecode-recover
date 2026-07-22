import fs from 'node:fs';
import path from 'node:path';

import { runProcess, runPython } from '../runtime/processes.mjs';

function resolveD8Path(requestedPath) {
  const candidate = requestedPath || process.env.V8BLOB_D8 || null;
  if (!candidate) return null;
  return path.resolve(candidate);
}

function assertD8Path(d8Path) {
  if (!d8Path) {
    throw new Error('The d8 backend requires --d8 PATH or V8BLOB_D8');
  }
  if (!fs.existsSync(d8Path)) throw new Error(`d8 executable does not exist: ${d8Path}`);
}

function createD8Backend({ d8Path, engineRoot, python }) {
  const resolvedPath = resolveD8Path(d8Path);
  return {
    id: 'd8',
    d8Path: resolvedPath,
    capabilities: Object.freeze({
      directCachedData: true,
      rawPayload: false,
      snapshots: false,
      matchingRuntime: true,
    }),
    available() {
      return Boolean(resolvedPath && fs.existsSync(resolvedPath));
    },
    disassemble(blobPath) {
      assertD8Path(resolvedPath);
      const expression = `loadjsc(${JSON.stringify(path.resolve(blobPath))})`;
      return {
        backendId: 'd8',
        text: runProcess(resolvedPath, ['-e', expression], {
          cwd: path.dirname(resolvedPath),
        }),
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

export { createD8Backend, resolveD8Path };
