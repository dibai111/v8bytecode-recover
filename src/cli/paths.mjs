import path from 'node:path';
import { fileURLToPath } from 'node:url';

const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const applicationRoot = sourceRoot;
const engineRoot = path.join(applicationRoot, 'engine', 'v8asm');
const sourceRecoveryEntryPath = path.join(engineRoot, 'source_recovery', '__main__.py');
const defaultOutputPath = path.resolve(process.cwd(), 'output');

export {
  applicationRoot,
  defaultOutputPath,
  engineRoot,
  sourceRecoveryEntryPath,
};
