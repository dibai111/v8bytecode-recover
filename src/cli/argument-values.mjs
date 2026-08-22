function argumentValue(argv, index, option, { allowLeadingDash = false } = {}) {
  const value = argv[index + 1];
  if (value === undefined || value.length === 0
    || !allowLeadingDash && value.startsWith('-')) {
    throw new Error(`${option} requires a value`);
  }
  return value;
}

function commaSeparatedValues(value, option) {
  const values = value.split(',').map((item) => item.trim()).filter(Boolean);
  if (values.length === 0) throw new Error(`${option} requires at least one value`);
  return values;
}

export { argumentValue, commaSeparatedValues };
