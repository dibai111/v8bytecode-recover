# Cross-tool benchmarks

The benchmark runner uses the same input directory and the same output quality
metrics for the built-in profile backend, a matching patched `d8`, and an
external two-stage adapter such as `jsc2js`.

Do not commit proprietary `.jsc` or `.v8blob` files. Keep a local corpus with
one representative sample per V8 major/minor, embedder and header format, then
run:

```powershell
node .\bin\v8bytecode-benchmark.mjs D:\corpus\v8 `
  --backends profile,d8 `
  --d8 D:\tools\d8-windows.exe `
  --adapter .\docs\benchmarks\jsc2js.adapter.json `
  --output .\output\benchmarks\v8.json
```

For a repeatable gate, copy `corpus.example.json`, adjust the private corpus
paths, and add minimum expectations:

```powershell
node .\bin\v8bytecode-benchmark.mjs D:\corpus\v8 `
  --backends profile,d8 `
  --d8-dir .\runtime\d8 `
  --corpus-manifest .\docs\benchmarks\corpus.example.json `
  --fail-on-regression `
  --output .\output\benchmarks\v8.json
```

The same corpus can be checked before source recovery so missing profile
coverage is separated from decompiler quality:

```powershell
node .\bin\v8bytecode-profiles.mjs coverage --corpus D:\corpus\v8 --json
```

The Node.js test suite creates a small real `.jsc` file with Node.js 22.18.0 and
recovers it with the bundled V8 12.4.254.21 profile and the matching Node
executable as its snapshot source. The generated input lives in a temporary
directory and is deleted after the smoke run, so no proprietary or checked-in
bytecode is needed.

The adapter runs two commands per blob. `{input}` is the original blob,
`{disassembly}` is the temporary text listing, `{output}` is the recovered
JavaScript path, and `{root}` is the benchmark temporary root. Commands are
executed directly with argument arrays, so no `cmd.exe` or shell quoting is
needed.

## jsc2js adapter shape

Create a local adapter file and adjust paths for the checkout:

```json
{
  "format": 1,
  "id": "jsc2js",
  "cwd": "D:/tools/jsc2js",
  "disassemble": {
    "command": "D:/tools/jsc2js/d8-windows.exe",
    "args": ["-e", "loadjsc('{input}')"]
  },
  "recover": {
    "command": "python",
    "args": ["View8/view8.py", "--disassembled", "{disassembly}", "{output}"]
  }
}
```

The report compares success rate, residue-free output, source size, function
count, detected profile, embedder compatibility, SHA-256 and elapsed time.
Manifest cases can additionally set `profile`, `embedder`,
`compatibilityStatus`, `minFunctions`, `maxFunctions`, `minBytes`,
`maxBytes`, and `residueFree`. Hash equality is not treated as semantic
correctness; it only identifies byte-for-byte identical output. Function
counts come from the source index and include arrow functions.

