#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const engineRoot = path.join(root, 'engine', 'v8asm');
const command = process.argv[2] ?? 'validate';
if (!['validate', 'list'].includes(command)) {
  throw new Error('Usage: v8bytecode-profiles [validate|list]');
}
const run = spawnSync('python', ['-B', '-m', 'cached_data.profile_cli', command], {
  cwd: engineRoot,
  encoding: 'utf8',
  stdio: 'inherit',
  windowsHide: true,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' },
});
if (run.error) throw run.error;
process.exitCode = run.status;
