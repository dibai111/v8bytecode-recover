import fs from 'node:fs';
import path from 'node:path';

function loadCorpusManifest(filePath) {
  const resolved = path.resolve(filePath);
  let value;
  try {
    value = JSON.parse(fs.readFileSync(resolved, 'utf8'));
  } catch (error) {
    throw new Error(`Unable to read corpus manifest ${resolved}: ${error.message}`);
  }
  if (!value || value.format !== 1 || !Array.isArray(value.cases)) {
    throw new Error(`Corpus manifest ${resolved} must have format 1 and a cases array`);
  }
  value.cases.forEach((item, index) => {
    if (!item || typeof item.path !== 'string' || !item.path) {
      throw new Error(`Corpus case ${index} must contain a path`);
    }
    if (item.backend !== undefined && typeof item.backend !== 'string') {
      throw new Error(`Corpus case ${item.path} backend must be a string`);
    }
    for (const field of ['profile', 'embedder', 'compatibilityStatus']) {
      if (item[field] !== undefined && typeof item[field] !== 'string') {
        throw new Error(`Corpus case ${item.path} ${field} must be a string`);
      }
    }
    for (const field of ['minFunctions', 'maxFunctions', 'minBytes', 'maxBytes']) {
      if (item[field] !== undefined
        && (!Number.isInteger(item[field]) || item[field] < 0)) {
        throw new Error(`Corpus case ${item.path} ${field} must be a non-negative integer`);
      }
    }
    if (item.residueFree !== undefined && typeof item.residueFree !== 'boolean') {
      throw new Error(`Corpus case ${item.path} residueFree must be a boolean`);
    }
  });
  return { ...value, path: resolved };
}

function evaluateCase(file, expected) {
  const checks = [];
  const expectSuccess = expected.success !== false;
  checks.push({ name: 'success', expected: expectSuccess, actual: file?.success === true });
  if (Number.isInteger(expected.minFunctions)) {
    checks.push({
      name: 'minFunctions',
      expected: expected.minFunctions,
      actual: file?.functions ?? 0,
      pass: (file?.functions ?? 0) >= expected.minFunctions,
    });
  }
  if (Number.isInteger(expected.maxFunctions)) {
    checks.push({
      name: 'maxFunctions',
      expected: expected.maxFunctions,
      actual: file?.functions ?? 0,
      pass: (file?.functions ?? 0) <= expected.maxFunctions,
    });
  }
  for (const [name, actual, expectedValue] of [
    ['backend', file?.backend ?? null, expected.backend],
    ['profile', file?.profileVersion ?? null, expected.profile],
    ['embedder', file?.embedder ?? null, expected.embedder],
    ['compatibilityStatus', file?.compatibilityStatus ?? null, expected.compatibilityStatus],
  ]) {
    if (expectedValue !== undefined) {
      checks.push({
        name,
        expected: expectedValue,
        actual,
        pass: actual === expectedValue,
      });
    }
  }
  if (Number.isInteger(expected.minBytes)) {
    checks.push({
      name: 'minBytes',
      expected: expected.minBytes,
      actual: file?.bytes ?? 0,
      pass: (file?.bytes ?? 0) >= expected.minBytes,
    });
  }
  if (Number.isInteger(expected.maxBytes)) {
    checks.push({
      name: 'maxBytes',
      expected: expected.maxBytes,
      actual: file?.bytes ?? 0,
      pass: (file?.bytes ?? 0) <= expected.maxBytes,
    });
  }
  if (expected.residueFree === true) {
    checks.push({
      name: 'residueFree',
      expected: true,
      actual: file?.residueFree === true,
    });
  }
  for (const check of checks) {
    if (check.pass === undefined) check.pass = check.actual === check.expected;
  }
  return {
    id: expected.id ?? expected.path,
    input: expected.path,
    backend: expected.backend ?? null,
    pass: checks.every((check) => check.pass),
    checks,
    actual: file ?? null,
  };
}

function evaluateCorpus(report, manifest) {
  const results = new Map();
  for (const result of report.results ?? []) {
    for (const file of result.files ?? []) {
      results.set(`${result.backend}\u0000${file.input}`, {
        ...file,
        backend: result.backend,
      });
    }
  }
  const cases = manifest.cases.map((expected) => {
    const backend = expected.backend ?? report.results?.[0]?.backend;
    return evaluateCase(results.get(`${backend}\u0000${expected.path}`), expected);
  });
  return {
    format: 1,
    manifest: manifest.path ?? null,
    passed: cases.every((item) => item.pass),
    total: cases.length,
    passedCases: cases.filter((item) => item.pass).length,
    failedCases: cases.filter((item) => !item.pass).length,
    cases,
  };
}

export { evaluateCorpus, loadCorpusManifest };
