function positionalArgument(argumentsList, valueOptions = ['--profile-dir']) {
  const options = new Set(valueOptions);
  for (let index = 0; index < argumentsList.length; index += 1) {
    const argument = argumentsList[index];
    if (options.has(argument)) {
      index += 1;
      continue;
    }
    if (!argument.startsWith('-')) return argument;
  }
  return null;
}

export { positionalArgument };
