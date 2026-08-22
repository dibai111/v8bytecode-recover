import path from 'node:path';

import { argumentValue, commaSeparatedValues } from './argument-values.mjs';

function usage() {
  console.log(`V8 cached bytecode to JavaScript

Usage:
  node bin/v8bytecode-recover.mjs <blob-or-directory> [options]

Options:
  -o, --output <dir>       Output directory (default: output)
       --input-format <name> auto, raw, disassembled, or serialized (default: auto)
      --backend <name>     auto, profile, or d8 (default: auto)
      --d8 <path>          Matching patched d8 with loadjsc() support
      --d8-dir <dir>       Search this directory before PATH for patched d8
      --no-d8-download     Never auto-download a matching d8 release build
      --profile <ver>      Override automatic V8 profile detection
      --profile-dir <dir>  Exact profile pack directory containing index.json
      --snapshot <file>    Matching snapshot_blob.bin for read-only values
      --no-snapshot-search Disable nearby snapshot auto-discovery
      --runtime-variant <name>
                           Override the V8 runtime-ID table variant
      --embedder <name>    unknown, node, electron, chromium, or custom
      --payload-offset <n> Read a raw serializer payload at this offset
      --level <1-4>        Decompiler level (default: 4)
      --emit <kind>        Add disassembly, translated, cfg, functions, callgraph, tree, inline, names, or serialized output
      --split-functions DIR
                            Write each recovered function and an index manifest
      --function <name>    Select a function for split output (repeatable; --func alias)
      --include-function <pattern>
                            Include split functions matching a name or * wildcard
      --exclude-function <pattern>
                            Exclude split functions matching a name or * wildcard
      --split-mode <name>  declarers, calls, or references (default: declarers)
      --split-depth <n>    Maximum split traversal depth (default: unlimited)
      --tree <name>        Root function for tree output (default: start)
      --tree-mode <name>   declarers, calls, or references (default: declarers)
      --tree-depth <n>     Maximum tree depth (default: unlimited)
      --scope <name>       Limit analysis to a lexical function scope
      --show-all           Keep unresolved and all relation edges in research output
      --inline-depth <n>   Expand call edges to this depth in inline output
      --inline-branch-limit <n>
                           Limit inline children per function (default: unlimited)
      --normalize-names    Rename address-derived bytecode functions deterministically
      --research           Preserve non-clean candidates under .research/
      --strict             Reject output when only read-only references remain unresolved
      --resume             Reuse validated outputs from recovery-manifest.json and persist progress
      --report <file>      Write a JSON recovery report
      --python <exe>       Python executable (default: python)
  -h, --help               Show this help

Input may be one .v8blob/.jsc/.disassembly.txt/.txt/.v8recovery.json file or a
directory. With the default auto format, directories may contain a mixture of
these inputs. The tool writes only JavaScript files that pass syntax and
decompiler-residue checks. Serialized recovery artifacts can be reprocessed
without invoking Python or d8. Analysis files are written only when --emit is used.`);
}

function parseNonNegativeInteger(value, option) {
  if (!value) throw new Error(`${option} requires a number`);
  const parsed = /^0x/i.test(value) ? Number.parseInt(value.slice(2), 16) : Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new Error(`${option} must be a non-negative integer`);
  }
  return parsed;
}

function parseArguments(argv, defaultOutputPath) {
  const options = {
    input: null,
    output: defaultOutputPath,
    inputFormat: 'auto',
    profile: null,
    backend: 'auto',
    d8Path: null,
    d8Directory: null,
    noD8Download: false,
    snapshot: null,
    profileDirectory: null,
    snapshotSearch: true,
    runtimeVariant: null,
    embedder: 'unknown',
    payloadOffset: null,
    level: 4,
    emit: [],
    splitFunctions: null,
    functionNames: [],
    includeFunctions: [],
    excludeFunctions: [],
    splitMode: 'declarers',
    splitDepth: null,
    treeRoot: null,
    treeMode: 'declarers',
    treeDepth: null,
    scope: null,
    showAll: false,
    inlineDepth: null,
    inlineBranchLimit: null,
    normalizeNames: false,
    research: false,
    strict: false,
    resume: false,
    report: null,
    python: 'python',
    help: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '-h' || argument === '--help') options.help = true;
    else if (argument === '-o' || argument === '--output') {
      options.output = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--input-format') {
      options.inputFormat = argumentValue(argv, index, argument);
      index += 1;
      if (!['auto', 'raw', 'disassembled', 'serialized'].includes(options.inputFormat)) {
        throw new Error('--input-format must be auto, raw, disassembled, or serialized');
      }
    } else if (argument === '--backend') {
      options.backend = argumentValue(argv, index, argument);
      index += 1;
      if (!['auto', 'profile', 'd8'].includes(options.backend)) {
        throw new Error('--backend must be auto, profile, or d8');
      }
    } else if (argument === '--d8') {
      options.d8Path = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--d8-dir') {
      options.d8Directory = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--no-d8-download') {
      options.noD8Download = true;
    } else if (argument === '--profile') {
      options.profile = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--profile-dir') {
      options.profileDirectory = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--snapshot') {
      options.snapshot = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--no-snapshot-search') {
      options.snapshotSearch = false;
    } else if (argument === '--runtime-variant') {
      options.runtimeVariant = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--embedder') {
      options.embedder = argumentValue(argv, index, argument);
      index += 1;
      if (!['unknown', 'node', 'electron', 'chromium', 'custom'].includes(options.embedder)) {
        throw new Error('--embedder must be unknown, node, electron, chromium, or custom');
      }
    } else if (argument === '--payload-offset') {
      options.payloadOffset = parseNonNegativeInteger(
        argumentValue(argv, index, argument),
        argument,
      );
      index += 1;
    } else if (argument === '--level') {
      options.level = Number(argumentValue(argv, index, argument));
      index += 1;
      if (!Number.isInteger(options.level) || options.level < 1 || options.level > 4) {
        throw new Error('--level must be an integer from 1 to 4');
      }
    } else if (argument === '--emit') {
      const kinds = commaSeparatedValues(argumentValue(argv, index, argument), argument);
      index += 1;
      for (const kind of kinds) {
        if (!['disassembly', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'inline', 'names', 'serialized'].includes(kind)) {
          throw new Error('--emit must be disassembly, translated, cfg, functions, callgraph, tree, inline, names, or serialized');
        }
        if (!options.emit.includes(kind)) options.emit.push(kind);
      }
    } else if (argument === '--split-functions') {
      options.splitFunctions = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--function' || argument === '--func') {
      const functionName = argumentValue(argv, index, argument);
      index += 1;
      options.functionNames.push(functionName);
    } else if (argument === '--include-function') {
      const pattern = argumentValue(argv, index, argument);
      index += 1;
      options.includeFunctions.push(pattern);
    } else if (argument === '--exclude-function') {
      const pattern = argumentValue(argv, index, argument);
      index += 1;
      options.excludeFunctions.push(pattern);
    } else if (argument === '--split-mode') {
      options.splitMode = argumentValue(argv, index, argument);
      index += 1;
      if (!['declarers', 'calls', 'references'].includes(options.splitMode)) {
        throw new Error('--split-mode must be declarers, calls, or references');
      }
    } else if (argument === '--split-depth') {
      options.splitDepth = parseNonNegativeInteger(
        argumentValue(argv, index, argument),
        argument,
      );
      index += 1;
    } else if (argument === '--tree') {
      options.treeRoot = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--tree-mode') {
      options.treeMode = argumentValue(argv, index, argument);
      index += 1;
      if (!['declarers', 'calls', 'references'].includes(options.treeMode)) {
        throw new Error('--tree-mode must be declarers, calls, or references');
      }
    } else if (argument === '--tree-depth') {
      options.treeDepth = parseNonNegativeInteger(
        argumentValue(argv, index, argument),
        argument,
      );
      index += 1;
    } else if (argument === '--scope') {
      options.scope = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--show-all') {
      options.showAll = true;
    } else if (argument === '--inline-depth') {
      options.inlineDepth = parseNonNegativeInteger(
        argumentValue(argv, index, argument),
        argument,
      );
      index += 1;
      if (!options.emit.includes('inline')) options.emit.push('inline');
    } else if (argument === '--inline-branch-limit') {
      options.inlineBranchLimit = parseNonNegativeInteger(
        argumentValue(argv, index, argument),
        argument,
      );
      index += 1;
      if (!options.emit.includes('inline')) options.emit.push('inline');
    } else if (argument === '--normalize-names') {
      options.normalizeNames = true;
    } else if (argument === '--research') {
      options.research = true;
    } else if (argument === '--strict') {
      options.strict = true;
    } else if (argument === '--resume') {
      options.resume = true;
    } else if (argument === '--report') {
      options.report = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument === '--python') {
      options.python = argumentValue(argv, index, argument);
      index += 1;
    } else if (argument.startsWith('-')) {
      throw new Error(`Unknown option: ${argument}`);
    } else if (!options.input) options.input = argument;
    else throw new Error(`Unexpected argument: ${argument}`);
  }

  if (!options.input && !options.help) {
    throw new Error('Pass an input file or directory; use --input-format auto, raw, disassembled, or serialized input');
  }
  options.input = options.input ? path.resolve(options.input) : null;
  options.output = path.resolve(options.output);
  options.snapshot = options.snapshot ? path.resolve(options.snapshot) : null;
  options.d8Path = options.d8Path ? path.resolve(options.d8Path) : null;
  options.d8Directory = options.d8Directory ? path.resolve(options.d8Directory) : null;
  options.profileDirectory = options.profileDirectory
    ? path.resolve(options.profileDirectory)
    : null;
  options.splitFunctions = options.splitFunctions ? path.resolve(options.splitFunctions) : null;
  options.report = options.report ? path.resolve(options.report) : null;
  return options;
}

export { parseArguments, usage };
