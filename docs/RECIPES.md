# Command recipes

Examples are kept as command recipes rather than checked-in binary blobs.
This keeps the repository small and avoids redistributing proprietary cached
bytecode.

## Recover a directory

```powershell
.\v8bytecode-recover-zh-TW.ps1 -Action recover `
  -InputPath .\input `
  -OutputPath .\output
```

Use `v8bytecode-recover-zh-CN.ps1` for simplified Chinese prompts, or add
`-Language zh-CN` when calling the main script.

## Inspect before recovery

```powershell
.\v8bytecode-recover.ps1 -Action inspect -InputPath .\input\app.jsc
```

## Generate an external profile pack

```powershell
node .\bin\v8bytecode-profiles.mjs generate --version 15.3.79
```

See [`PROFILE_GENERATION.md`](./PROFILE_GENERATION.md) for local V8 checkouts
and bundled profile maintenance.

## Matching d8 runtime slot

Put a patched `d8.exe` (Windows) or `d8` (Linux/macOS) in `runtime/d8/`. The
tool probes it for `loadjsc()` and selects it automatically when the profile
backend cannot safely decode a blob.

The binary is intentionally not bundled because V8 d8 builds are large,
platform-specific, and must match the producer's V8 tag. You can also set
`V8BYTECODE_D8` or pass `--d8` when the runtime lives elsewhere. When no local
d8 exists, recovery can auto-download the exact matching release build (see the
README's auto-download section); `runtime/` is git-ignored.
