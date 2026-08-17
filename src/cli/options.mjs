import path from 'node:path';

function usage() {
  console.log(`V8 cached bytecode to JavaScript

Usage:
  node bin/v8blob-to-js.mjs <blob-or-directory> [options]

Options:
  -o, --output <dir>       Output directory (default: output)
       --input-format <name> auto, raw, disassembled, or serialized (default: auto)
      --backend <name>     auto, profile, or d8 (default: auto)
      --d8 <path>          Matching patched d8 with loadjsc() support
      --profile <ver>      Override automatic V8 profile detection
      --snapshot <file>    Matching snapshot_blob.bin for read-only values
      --no-snapshot-search Disable nearby snapshot auto-discovery
      --runtime-variant <name>
                           Override the V8 runtime-ID table variant
      --payload-offset <n> Read a raw serializer payload at this offset
      --level <1-4>        Decompiler level (default: 4)
       --emit <kind>        Add disassembly, translated, cfg, functions, callgraph, tree, names, or serialized output
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
      --normalize-names    Rename address-derived bytecode functions deterministically
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
    snapshot: null,
    snapshotSearch: true,
    runtimeVariant: null,
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
    normalizeNames: false,
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
      options.output = argv[++index];
      if (!options.output) throw new Error(`${argument} requires a directory`);
    } else if (argument === '--input-format') {
      options.inputFormat = argv[++index];
      if (!['auto', 'raw', 'disassembled', 'serialized'].includes(options.inputFormat)) {
        throw new Error('--input-format must be auto, raw, disassembled, or serialized');
      }
    } else if (argument === '--backend') {
      options.backend = argv[++index];
      if (!['auto', 'profile', 'd8'].includes(options.backend)) {
        throw new Error('--backend must be auto, profile, or d8');
      }
    } else if (argument === '--d8') {
      options.d8Path = argv[++index];
      if (!options.d8Path) throw new Error('--d8 requires an executable path');
    } else if (argument === '--profile') {
      options.profile = argv[++index];
      if (!options.profile) throw new Error('--profile requires a V8 version');
    } else if (argument === '--snapshot') {
      options.snapshot = argv[++index];
      if (!options.snapshot) throw new Error('--snapshot requires a file');
    } else if (argument === '--no-snapshot-search') {
      options.snapshotSearch = false;
    } else if (argument === '--runtime-variant') {
      options.runtimeVariant = argv[++index];
      if (!options.runtimeVariant) throw new Error('--runtime-variant requires a name');
    } else if (argument === '--payload-offset') {
      options.payloadOffset = parseNonNegativeInteger(argv[++index], '--payload-offset');
    } else if (argument === '--level') {
      options.level = Number(argv[++index]);
      if (!Number.isInteger(options.level) || options.level < 1 || options.level > 4) {
        throw new Error('--level must be an integer from 1 to 4');
      }
    } else if (argument === '--emit') {
      const kind = argv[++index];
      if (!['disassembly', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'names', 'serialized'].includes(kind)) {
        throw new Error('--emit must be disassembly, translated, cfg, functions, callgraph, tree, names, or serialized');
      }
      if (!options.emit.includes(kind)) options.emit.push(kind);
    } else if (argument === '--split-functions') {
      options.splitFunctions = argv[++index];
      if (!options.splitFunctions) throw new Error('--split-functions requires a directory');
    } else if (argument === '--function' || argument === '--func') {
      const functionName = argv[++index];
      if (!functionName) throw new Error(`${argument} requires a function name`);
      options.functionNames.push(functionName);
    } else if (argument === '--include-function') {
      const pattern = argv[++index];
      if (!pattern) throw new Error('--include-function requires a name or pattern');
      options.includeFunctions.push(pattern);
    } else if (argument === '--exclude-function') {
      const pattern = argv[++index];
      if (!pattern) throw new Error('--exclude-function requires a name or pattern');
      options.excludeFunctions.push(pattern);
    } else if (argument === '--split-mode') {
      options.splitMode = argv[++index];
      if (!['declarers', 'calls', 'references'].includes(options.splitMode)) {
        throw new Error('--split-mode must be declarers, calls, or references');
      }
    } else if (argument === '--split-depth') {
      options.splitDepth = parseNonNegativeInteger(argv[++index], '--split-depth');
    } else if (argument === '--tree') {
      options.treeRoot = argv[++index];
      if (!options.treeRoot) throw new Error('--tree requires a function name');
    } else if (argument === '--tree-mode') {
      options.treeMode = argv[++index];
      if (!['declarers', 'calls', 'references'].includes(options.treeMode)) {
        throw new Error('--tree-mode must be declarers, calls, or references');
      }
    } else if (argument === '--tree-depth') {
      options.treeDepth = parseNonNegativeInteger(argv[++index], '--tree-depth');
    } else if (argument === '--normalize-names') {
      options.normalizeNames = true;
    } else if (argument === '--strict') {
      options.strict = true;
    } else if (argument === '--resume') {
      options.resume = true;
    } else if (argument === '--report') {
      options.report = argv[++index];
      if (!options.report) throw new Error('--report requires a JSON file path');
    } else if (argument === '--python') {
      options.python = argv[++index];
      if (!options.python) throw new Error('--python requires an executable name or path');
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
  options.splitFunctions = options.splitFunctions ? path.resolve(options.splitFunctions) : null;
  options.report = options.report ? path.resolve(options.report) : null;
  return options;
}

export { parseArguments, usage };
