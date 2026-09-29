import { stripBalancedOuterParentheses } from './source-text-utils.mjs';

function recoverAsyncFunctions(source) {
  let lines = source.split('\n');
  const asyncFunctionStarts = new Set();
  for (let index = 0; index < lines.length; index += 1) {
    if (!/\bAsyncFunctionEnter\(/.test(lines[index])) continue;
    for (let header = index; header >= 0; header -= 1) {
      if (/^\s*(?:async\s+)?function(?:\s*\*)?\s+[A-Za-z_$][\w$]*\s*\(/.test(lines[header])) {
        asyncFunctionStarts.add(header);
        break;
      }
    }
  }
  for (const index of asyncFunctionStarts) {
    lines[index] = lines[index].replace(/^(\s*)function\b/, '$1async function');
  }

  const recovered = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\s*(?:r\d+|temporary\d+)\s*=\s*AsyncFunctionEnter\([^)]*\)\s*$/.test(line)) {
      continue;
    }
    const awaitCall = line.match(
      /^(\s*)AsyncFunctionAwait(?:Caught|Uncaught)\(([^,]+),\s*(.+)\)\s*$/,
    );
    if (awaitCall) {
      const suspend = lines[index + 1]?.trim() ?? '';
      const resume = lines[index + 2]?.trim().match(
        /^((?:r\d+|temporary\d+))\s*=\s*ResumeGenerator\(([^)]+)\)$/,
      );
      const guard = lines[index + 3]?.trim() ?? '';
      const thrown = lines[index + 4]?.trim() ?? '';
      const close = lines[index + 5]?.trim() ?? '';
      const awaitedGenerator = awaitCall[2].trim();
      const guardMentionsGenerator = guard.includes(`GeneratorGetResumeMode(${awaitedGenerator})`);
      if (
        /^\/\/ SuspendGenerator\b/.test(suspend)
        && resume
        && resume[2].trim() === awaitedGenerator
        && /^if \(.+\) \{$/.test(guard)
        && guardMentionsGenerator
        && thrown === `throw ${resume[1]}`
        && close === '}'
      ) {
        const expression = stripBalancedOuterParentheses(awaitCall[3]);
        recovered.push(`${awaitCall[1]}${resume[1]} = await ${expression}`);
        index += 5;
        continue;
      }

      // Some V8 versions place a context/register save between the suspend
      // marker and ResumeGenerator. Keep those setup statements and lower
      // only the resume protocol to a native await expression.
      let resumeIndex = index + 2;
      let extendedResume = null;
      while (resumeIndex < Math.min(lines.length, index + 8)) {
        extendedResume = lines[resumeIndex]?.trim().match(
          /^((?:r\d+|temporary\d+))\s*=\s*ResumeGenerator\(([^)]+)\)$/,
        );
        if (extendedResume) break;
        resumeIndex += 1;
      }
      const extendedGuard = lines[resumeIndex + 1]?.trim() ?? '';
      const extendedThrown = lines[resumeIndex + 2]?.trim() ?? '';
      const extendedClose = lines[resumeIndex + 3]?.trim() ?? '';
      const bridge = lines.slice(index + 1, resumeIndex);
      const usesResume = lines
        .slice(resumeIndex + 4, Math.min(lines.length, resumeIndex + 8))
        .some((nextLine) => new RegExp(`\\b${extendedResume?.[1] ?? 'never'}\\b`).test(nextLine));
      if (
        extendedResume
        && bridge.some((bridgeLine) => /^\s*\/\/ SuspendGenerator\b/.test(bridgeLine))
        && extendedResume[2].trim() === awaitedGenerator
        && /^if \(.+\) \{$/.test(extendedGuard)
        && extendedGuard.includes(`GeneratorGetResumeMode(${awaitedGenerator})`)
        && extendedThrown === `throw ${extendedResume[1]}`
        && extendedClose === '}'
        && usesResume
      ) {
        const expression = stripBalancedOuterParentheses(awaitCall[3]);
        // Bridge lines run before the suspension, but V8 computes the await
        // argument first and then reuses its source register for a pre-suspend
        // save (`r14 = Promise.resolve(r12); ...; r12 = r1`). Moving a bridge
        // store whose target register appears inside the awaited expression
        // AFTER the await keeps both reads on their intended values.
        const keptBridge = bridge.filter(
          (bridgeLine) => !/^\s*\/\/ SuspendGenerator\b/.test(bridgeLine),
        );
        const deferredBridge = keptBridge.filter((bridgeLine) => {
          const store = bridgeLine.trim().match(/^((?:r\d+|temporary\d+))\s*=\s*([^;]+?)\s*;?\s*$/);
          if (!store) return false;
          const token = new RegExp(`\\b${store[1]}\\b`);
          return token.test(expression);
        });
        const leadingBridge = keptBridge.filter((line) => !deferredBridge.includes(line));
        recovered.push(...leadingBridge);
        recovered.push(`${awaitCall[1]}${extendedResume[1]} = await ${expression}`);
        recovered.push(...deferredBridge);
        index = resumeIndex + 3;
        continue;
      }
    }
    recovered.push(line);
  }

  return recovered.join('\n')
    .replace(/\breturn\s+AsyncFunctionResolve\([^,]+,\s*([^\n]+)\)/g, 'return $1')
    .replace(/\breturn\s+AsyncFunctionReject\([^,]+,\s*([^\n]+)\)/g, 'throw $1');
}

export { recoverAsyncFunctions };
