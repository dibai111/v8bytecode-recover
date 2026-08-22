# Bundled V8 Profiles

This directory contains reviewed profiles generated from exact V8 source tags.
The decoder uses `index.json` to map cached-data version hashes to profile
files. A nearby version is never selected as a substitute.

The JSON files are generated artifacts. Update them through
`docs/PROFILE_GENERATION.md`, then run `npm run profiles:validate` before
committing.

Current coverage spans V8 5.1 through 15.3 and includes representative stable
tags for legacy C++ layouts, hybrid Torque/C++ layouts, and modern Torque
layouts. The catalog is intentionally versioned by exact tag, not by a broad
major-version claim.
