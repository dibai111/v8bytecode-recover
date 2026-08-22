# V8 Profile Generation

Profiles describe the V8 source layout needed to decode cached bytecode. Each
profile is generated from one exact upstream V8 tag; it is not inferred from a
nearby version number.

## Generate one profile

From the repository root:

```powershell
node .\bin\v8bytecode-profiles.mjs generate --version 15.3.79
```

The generated pack is written to `output/profiles/15.3.79/` and contains:

- `15.3.79.json`: the generated profile;
- `index.json`: the pack index;
- `profile-generation-report.json`: source layout, hash and failure details.

Use the generated pack explicitly during recovery:

```powershell
.\v8bytecode-recover.ps1 -Action recover `
  -InputPath .\input\app.jsc `
  -OutputPath .\output `
  -Profile 15.3.79 `
  -ProfileDirectory .\output\profiles\15.3.79
```

The PowerShell menu exposes the same generator under **Profiles**. For a
non-interactive equivalent, use `-Action profiles` and
`-ProfileCommand generate`.

## Generate from a local checkout

Avoid network access by pointing the generator at a local V8 repository:

```powershell
node .\bin\v8bytecode-profiles.mjs generate `
  --version 15.3.79 `
  --source git `
  --v8-repo D:\src\v8
```

The checkout must contain the exact requested tag. The generator reads the
source using `git show`, so the working tree does not need to be checked out
at that tag.

## Maintain the bundled catalog

The bundled catalog is under `engine/v8asm/cached_data/profiles/`. Its
`index.json` is the single source of truth for the bundled version list. To add
a verified profile, generate it into that directory with `--merge-existing`:

```powershell
python -B engine\v8asm\cached_data\tooling\generate_profiles.py `
  --version 15.3.79 `
  --output-dir engine\v8asm\cached_data\profiles `
  --merge-existing
```

Only commit a profile when its generation report says `status: success`, then
run:

```powershell
npm run profiles:validate
python -B -m unittest discover -s engine/v8asm -p 'test_*.py'
```

Do not hand-edit generated JSON files or `index.json`. Parser and source-path
changes belong in `tooling/generate_profiles.py` or
`tooling/source_layout.py`, with a focused test in `engine/v8asm/cached_data/`.

To discover upstream coverage instead of maintaining a handwritten version
list:

```powershell
node .\bin\v8bytecode-profiles.mjs discover `
  --minimum 5.1.0 `
  --maximum 15.3.79 `
  --output .\temp\v8-versions.txt `
  --report .\temp\v8-versions-report.json

python -B engine\v8asm\cached_data\tooling\generate_profiles.py `
  --versions-file .\temp\v8-versions.txt `
  --output-dir .\temp\profile-expansion `
  --report .\temp\profile-expansion-report.json
```

Use `--all` with the discover command when every semantic tag is required.
Generation is intentionally separate from discovery so unsupported source
layouts are reported and never silently added to the bundled pack.

## Source cache and generated output

The default GitHub provider caches raw V8 files under
`Path.home()/.cache/v8bytecode-recover` (override it with `--cache-dir`). That
cache is an input cache and is not part of the repository. Generated packs
under `output/` are also disposable. Bundled profiles are the reviewed,
committed copies used by the default recovery workflow.

## Unknown blob versions

A cached-data header contains a V8 version hash, but the hash is not a reliable
reverse mapping to every possible V8 source tag. If no bundled profile matches,
identify the exact V8 tag from the producer or build metadata, generate that
profile, and inspect the report. If the source layout is unsupported, use a
matching patched `d8` backend or add a versioned adapter rather than silently
using the nearest profile.
