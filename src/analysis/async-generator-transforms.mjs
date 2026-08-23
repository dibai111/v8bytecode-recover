function isDelegationControlLine(line) {
  const statement = line.trim();
  if (!statement || statement === '}' || statement === 'else {') return true;
  return /^(?:r\d+|temporary\d+)\s*=\s*(?:(?:r\d+|temporary\d+)(?:\.[A-Za-z_$][\w$]*)?|-?\d+|undefined)\s*;?$/.test(
    statement,
  );
}

function recoverDelegatedAsyncGeneratorLoops(source, structure) {
  const { endOfConditional, stripBalancedOuterParentheses } = structure;
  const lines = source.split('\n');
  for (let pass = 0; pass < 50; pass += 1) {
    let changed = false;
    for (let start = 0; start < lines.length; start += 1) {
      const setup = lines[start].match(
        /^(\s*)((?:r\d+|temporary\d+))\s*=\s*(.+)\[Symbol_asyncIterator\]\s*;?\s*$/,
      );
      if (!setup) continue;

      const [, indent, resultName, rawDelegate] = setup;
      const delegated = stripBalancedOuterParentheses(rawDelegate);
      let loopStart = -1;
      let loopEnd = -1;
      let modeName = null;

      for (let probe = start + 1; probe < Math.min(lines.length, start + 45); probe += 1) {
        const loop = lines[probe].match(/^(\s*)while\s*\(.+\)\s*\{\s*$/);
        if (!loop || loop[1] !== indent) continue;
        const end = endOfConditional(lines, probe);
        if (end === null) continue;

        for (let index = probe + 1; index < end - 1; index += 1) {
          const yielded = lines[index].trim().match(
            /^(?:r\d+|temporary\d+)\s*=\s*yield\s+(.+)\s*;?$/,
          );
          if (!yielded) continue;
          const step = stripBalancedOuterParentheses(yielded[1]).match(
            /^((?:r\d+|temporary\d+))\.value$/,
          );
          const firstMode = lines[index + 1]?.trim().match(
            /^(?:r\d+|temporary\d+)\s*=\s*GeneratorGetResumeMode\(([^)]+)\)\s*;?$/,
          );
          const secondMode = lines[index + 2]?.trim().match(
            /^((?:r\d+|temporary\d+))\s*=\s*GeneratorGetResumeMode\(([^)]+)\)\s*;?$/,
          );
          if (
            !step
            || !firstMode
            || !secondMode
            || firstMode[1].trim() !== secondMode[2].trim()
            || !lines.slice(probe + 1, index).some((line) => (
              new RegExp(`\\b${step[1]}\\.done\\b`).test(line)
            ))
          ) continue;
          loopStart = probe;
          loopEnd = end;
          modeName = secondMode[1];
          break;
        }
        if (loopStart >= 0) break;
      }
      if (loopStart < 0) continue;

      const setupRegion = lines.slice(start, loopStart).join('\n');
      if (!setupRegion.includes('.call(') || !setupRegion.includes('Symbol_iterator')) continue;

      let modeStart = loopEnd + 1;
      while (modeStart < Math.min(lines.length, loopEnd + 12)) {
        const modeGuard = lines[modeStart].trim().match(
          new RegExp(`^if\\s*\\(\\s*${modeName}\\s*===\\s*1\\s*\\)\\s*\\{$`),
        );
        if (modeGuard) break;
        if (!isDelegationControlLine(lines[modeStart])) break;
        modeStart += 1;
      }
      if (modeStart >= lines.length || modeStart >= loopEnd + 12) continue;
      const modeEnd = endOfConditional(lines, modeStart);
      if (modeEnd === null) continue;
      if (
        !lines.slice(loopEnd + 1, modeStart).every(isDelegationControlLine)
        || !lines.slice(modeStart + 1, modeEnd + 1).every(isDelegationControlLine)
      ) continue;

      lines.splice(
        start,
        modeEnd - start + 1,
        `${indent}${resultName} = yield* ${delegated}`,
      );
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return lines.join('\n');
}

export { recoverDelegatedAsyncGeneratorLoops };
