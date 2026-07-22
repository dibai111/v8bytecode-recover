# v8blob-to-js

[繁體中文](#繁體中文) | [English](#english)

---

# 繁體中文

## 項目簡介

`v8blob-to-js` 用於將 V8 產生的 `.v8blob`／`.jsc` cached bytecode
還原成可讀的 JavaScript 近似源碼。

主要功能：

- 直接處理單一 blob 或整個目錄。
- 自動識別內置 V8 profile。
- 支援 matching startup snapshot。
- 可選用 matching patched `d8` 作後端。
- 只輸出通過 JavaScript syntax 和 residue validation 的結果。
- 預設只產生 `.js`，不產生分析 report。

## 快速開始

### 1. 安裝環境

- Node.js 18 或更新版本
- Python 3

```powershell
cd D:\path\to\v8blob-to-js
npm install
```

### 2. 還原一個 blob

```powershell
node .\bin\v8blob-to-js.mjs .\input\index.js.v8blob
```

或者在 Windows 使用：

```powershell
.\v8blob-to-js.cmd .\input\index.js.v8blob
```

結果預設寫入 `output`：

```text
output/
└─ index.js
```

### 3. 還原整個目錄

```powershell
node .\bin\v8blob-to-js.mjs .\input -o .\recovered
```

工具會遞迴尋找 `.v8blob` 和 `.jsc`，並保留原有目錄結構。

## 常用選項

```text
-o, --output DIR        指定輸出目錄
    --profile VERSION   手動指定 V8 profile
    --snapshot FILE     指定 matching snapshot_blob.bin
    --backend NAME      auto、profile 或 d8
    --d8 FILE           指定 matching patched d8
    --emit KIND         額外輸出 disassembly、translated 或 cfg
    --level 1-4         選擇反編譯層級，預設 4
```

查看全部選項：

```powershell
node .\bin\v8blob-to-js.mjs --help
```

## V8 Profiles

列出或驗證內置 profiles：

```powershell
npm run profiles:list
npm run profiles:validate
```

為新的正式 V8 tag 生成 profile：

```powershell
python -B engine/v8asm/cached_data/tooling/generate_profiles.py `
  --version V8_VERSION
```

生成結果會直接寫入：

```text
engine/v8asm/cached_data/profiles/
```

省略 `--version` 會重新生成全部內置 profiles。系統沒有 C preprocessor 時，
先安裝 generation-only dependency：

```powershell
python -m pip install pcpp
```

## 使用 matching d8

如 profile backend 未支援目標版本，可指定相同 V8 版本並包含 `loadjsc()` 的
patched `d8`：

```powershell
node .\bin\v8blob-to-js.mjs input.jsc `
  --backend d8 `
  --d8 D:\path\to\d8.exe
```

## 說明

- V8 cached bytecode 格式與 V8 版本、build flags 和 snapshot 有關。
- 還原結果是語義近似源碼，不是原始檔案的逐字副本。
- 原始註解、排版和部分 identifier names 不存在於 bytecode 中。
- 控制流和 expressions 可能以等價但不同的結構輸出。
- 發現未解析 opcode、placeholder、invalid syntax 或 V8 residue 時，該檔案會標記為失敗。

## 參考與致謝

- [suleram/View8](https://github.com/suleram/View8)
- [xqy2006/jsc2js](https://github.com/xqy2006/jsc2js)
- [V8](https://github.com/v8/v8)

本項目使用 MIT License。`engine/v8asm` 保留其原有 MIT 授權聲明。

---

# English

[繁體中文](#繁體中文) | [English](#english)

## Overview

`v8blob-to-js` recovers readable, approximate JavaScript source from V8
`.v8blob` and `.jsc` cached bytecode.

Main features:

- Process one blob or a complete directory.
- Automatically detect bundled V8 profiles.
- Use a matching startup snapshot when available.
- Optionally use a matching patched `d8` backend.
- Write JavaScript only after syntax and residue validation pass.
- Produce only `.js` files by default, with no analysis reports.

## Quick Start

### 1. Requirements

- Node.js 18 or newer
- Python 3

```powershell
cd D:\path\to\v8blob-to-js
npm install
```

### 2. Recover one blob

```powershell
node .\bin\v8blob-to-js.mjs .\input\index.js.v8blob
```

On Windows, the launcher can also be used:

```powershell
.\v8blob-to-js.cmd .\input\index.js.v8blob
```

The default output is:

```text
output/
└─ index.js
```

### 3. Recover a directory

```powershell
node .\bin\v8blob-to-js.mjs .\input -o .\recovered
```

The directory is scanned recursively for `.v8blob` and `.jsc` files while
preserving its relative structure.

## Common Options

```text
-o, --output DIR        Set the output directory
    --profile VERSION   Override V8 profile detection
    --snapshot FILE     Use a matching snapshot_blob.bin
    --backend NAME      Use auto, profile, or d8
    --d8 FILE           Use a matching patched d8
    --emit KIND         Add disassembly, translated, or cfg output
    --level 1-4         Select the decompiler level; default: 4
```

Show every option:

```powershell
node .\bin\v8blob-to-js.mjs --help
```

## V8 Profiles

List or validate bundled profiles:

```powershell
npm run profiles:list
npm run profiles:validate
```

Generate a profile from an official V8 tag:

```powershell
python -B engine/v8asm/cached_data/tooling/generate_profiles.py `
  --version V8_VERSION
```

Profiles are written directly to:

```text
engine/v8asm/cached_data/profiles/
```

Run the generator without `--version` to rebuild every bundled profile. If no
system C preprocessor is available, install the generation-only dependency:

```powershell
python -m pip install pcpp
```

## Matching d8 Backend

When the profile backend does not cover the target, provide a patched `d8`
from the same V8 version with `loadjsc()` support:

```powershell
node .\bin\v8blob-to-js.mjs input.jsc `
  --backend d8 `
  --d8 D:\path\to\d8.exe
```

## Notes

- V8 cached bytecode depends on the V8 version, build flags, and snapshot.
- Recovered JavaScript is a semantic approximation, not a byte-for-byte copy of the original source.
- Original comments, formatting, and some identifier names are absent from bytecode.
- Control flow and expressions may be emitted using equivalent but different structures.
- Files containing unresolved opcodes, placeholders, invalid syntax, or V8 residue are reported as failed.

## References and Acknowledgments

- [suleram/View8](https://github.com/suleram/View8)
- [xqy2006/jsc2js](https://github.com/xqy2006/jsc2js)
- [V8](https://github.com/v8/v8)

This project is released under the MIT License. `engine/v8asm` retains its
original MIT license notice.
