import fs from 'node:fs';
import { spawnSync } from 'node:child_process';

function assertPythonAvailable(python) {
  const run = spawnSync(python, ['--version'], { encoding: 'utf8', timeout: 10_000 });
  if (run.error || run.status !== 0) throw new Error('Python preflight failed');
}

function runProcess(command, args, options = {}) {
  const run = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    encoding: 'utf8',
    timeout: options.timeout ?? 300_000,
    maxBuffer: options.maxBuffer ?? 512 * 1024 * 1024,
    windowsHide: true,
  });
  if (run.error || run.status !== 0) {
    const detail = [run.stdout, run.stderr].filter(Boolean).join('\n').trim();
    throw run.error ?? new Error(
      `${command} exited with ${run.status}${detail ? `\n${detail}` : ''}`,
    );
  }
  return run.stdout;
}

function runPython(python, args, engineRoot, timeout = 300_000) {
  return runProcess(python, args, {
    cwd: engineRoot,
    env: {
      ...process.env,
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONIOENCODING: 'utf-8',
    },
    timeout,
  });
}

function syntaxCheck(sourcePath) {
  const run = spawnSync(process.execPath, ['--check', sourcePath], {
    encoding: 'utf8',
    timeout: 30_000,
  });
  if (run.status === 0) return { ok: true, detail: null };
  let detail = (run.stderr || run.stdout).trim();
  const lineNumber = Number(detail.match(/\.js:(\d+)/)?.[1]);
  if (Number.isSafeInteger(lineNumber) && lineNumber > 0) {
    const lines = fs.readFileSync(sourcePath, 'utf8').split('\n');
    const start = Math.max(0, lineNumber - 21);
    const end = Math.min(lines.length, lineNumber + 20);
    const width = String(end).length;
    const context = lines.slice(start, end).map((line, offset) => {
      const current = start + offset + 1;
      const marker = current === lineNumber ? '>' : ' ';
      return `${marker} ${String(current).padStart(width)} | ${line}`;
    }).join('\n');
    detail += `\n\nGenerated context:\n${context}`;
  }
  return { ok: false, detail };
}

export { assertPythonAvailable, runProcess, runPython, syntaxCheck };
