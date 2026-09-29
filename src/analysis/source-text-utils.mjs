function matchingParenthesis(text, openIndex) {
  let depth = 0;
  let quote = null;
  for (let index = openIndex; index < text.length; index += 1) {
    const char = text[index];
    if (quote !== null) {
      if (char === '\\') index += 1;
      else if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char;
      continue;
    }
    if (char === '(') depth += 1;
    else if (char === ')') {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

function stripBalancedOuterParentheses(expression) {
  let result = expression.trim();
  while (result.startsWith('(') && matchingParenthesis(result, 0) === result.length - 1) {
    result = result.slice(1, -1).trim();
  }
  return result;
}

export { matchingParenthesis, stripBalancedOuterParentheses };
