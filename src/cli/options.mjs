import path from 'node:path';

function usage() {
  console.log(`V8 cached bytecode to JavaScript

Usage:
  node bin/v8blob-to-js.mjs <blob-or-directory> [options]

Options:
  -o, --output <dir>       Output directory (default: output)
      --backend <name>     auto, profile, or d8 (default: auto)
      --d8 <path>          Matching patched d8 with loadjsc() support
      --profile <ver>      Override automatic V8 profile detection
      --snapshot <file>    Matching snapshot_blob.bin for read-only values
      --no-snapshot-search Disable nearby snapshot auto-discovery
      --runtime-variant <name>
                           Override the V8 runtime-ID table variant
      --payload-offset <n> Read a raw serializer payload at this offset
      --level <1-4>        Decompiler level (default: 4)
      --emit <kind>        Add opt-in disassembly, translated, or cfg output
      --python <cmd>       Python executable (default: python)
  -h, --help               Show this help

Input may be one .v8blob/.jsc file or a directory. Directories are scanned
recursively. The tool writes only JavaScript files that pass syntax and
decompiler-residue checks. Analysis files are written only when --emit is used.`);
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
    profile: null,
    backend: 'auto',
    d8Path: null,
    snapshot: null,
    snapshotSearch: true,
    runtimeVariant: null,
    payloadOffset: null,
    level: 4,
    emit: [],
    python: 'python',
    help: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '-h' || argument === '--help') options.help = true;
    else if (argument === '-o' || argument === '--output') {
      options.output = argv[++index];
      if (!options.output) throw new Error(`${argument} requires a directory`);
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
      if (!['disassembly', 'translated', 'cfg'].includes(kind)) {
        throw new Error('--emit must be disassembly, translated, or cfg');
      }
      if (!options.emit.includes(kind)) options.emit.push(kind);
    } else if (argument === '--python') {
      options.python = argv[++index];
      if (!options.python) throw new Error('--python requires an executable name or path');
    } else if (argument.startsWith('-')) {
      throw new Error(`Unknown option: ${argument}`);
    } else if (!options.input) options.input = argument;
    else throw new Error(`Unexpected argument: ${argument}`);
  }

  if (!options.input && !options.help) {
    throw new Error('Pass a .v8blob/.jsc file or a directory containing those files');
  }
  options.input = options.input ? path.resolve(options.input) : null;
  options.output = path.resolve(options.output);
  options.snapshot = options.snapshot ? path.resolve(options.snapshot) : null;
  options.d8Path = options.d8Path ? path.resolve(options.d8Path) : null;
  return options;
}

export { parseArguments, usage };
