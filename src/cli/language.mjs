const supportedLanguages = Object.freeze(['zh-TW', 'zh-CN']);

function parseLanguage(value, option = '--language') {
  if (!supportedLanguages.includes(value)) {
    throw new Error(`${option} must be zh-TW or zh-CN`);
  }
  return value;
}

export { parseLanguage, supportedLanguages };
