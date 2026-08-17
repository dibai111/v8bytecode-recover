# v8blob-to-js

<p align="center">
  <img src="./assets/readme/recovery-pipeline.svg" width="100%" alt="v8blob-to-js 將 V8 cached bytecode 經過 profile 與 snapshot 偵測，還原成 JavaScript，再輸出函式與 graph 分析">
</p>

PowerShell-first 工具，將 V8 `.v8blob`／`.jsc` cached bytecode 還原成可閱讀的 JavaScript 近似源碼，並在同一條 pipeline 完成驗證、函式索引、call graph、tree、報告及可重用 artifact。

[繁體中文](#繁體中文) · [English](#english)

## 繁體中文

### 最短路徑

需求：Node.js 18+。處理 raw bytecode 需要 Python 3；反組譯文字及已驗證 recovery artifact 可不依賴 Python 重跑分析。

```powershell
cd D:\path\to\v8blob-to-js
.\v8blob-to-js.ps1
```

選擇 `1` 即可進入快速恢復：輸入一個檔案或目錄，輸出路徑直接按 Enter 使用 `output`。工具會自動識別 raw、反組譯文字及 `.v8recovery.json`，自動搜尋附近的 `snapshot_blob.bin`、`v8_context_snapshot.bin` 及版本化 `node.exe`（可直接解出 legacy V8 snapshot），並使用內建 profile/backend 預設值。快速模式亦會自動重用已驗證輸出，重跑不會浪費時間處理未改動的檔案。

不需要互動選項時，直接執行 CLI：

```powershell
.\v8blob-to-js.ps1 -Action recover `
  -InputPath .\input -OutputPath .\output
```

目錄會遞迴掃描並保留相對路徑：

```text
input/
├─ app.jsc
├─ nested/source.disassembly.txt
└─ saved.v8recovery.json

output/
├─ app.js
├─ nested/source.js
└─ saved.js
```

### 先看結果，再看細節

成功輸出的 JavaScript 必須通過 syntax check 及 decompiler-residue check；只有在 residue 完全來自缺失 snapshot 的 read-only references 時，工具才會輸出帶有 best-effort 標記的 partial source。其他失敗檔案只會出現在 JSON report，不會被當成有效 source 寫出。分析輸出集中在 `.analysis/`，快速 menu 產生的 `recovery-report.json` 及可恢復的 `recovery-manifest.json` 則放在輸出根目錄。

| 輸出 | 用途 |
| --- | --- |
| `*.js` | 通過品質檢查的近似源碼 |
| `*.disassembly.txt` | 原始或輸入的反組譯內容 |
| `*.functions.json` | 函式索引、nested hierarchy、範圍及參考 |
| `*.callgraph.json` | 函式 call/reference graph |
| `*.tree.json` | 可限制深度的 declarer、call 或 reference tree |
| `*.names.json` | address-derived 名稱 mapping |
| `*.v8recovery.json` | 可在沒有 Python/d8 時重用的已驗證 artifact |
| `recovery-report.json` | 每個輸入的成功、失敗及品質 metrics |
| `recovery-manifest.json` | 輸入 hash、設定指紋、逐檔進度及 resume 狀態 |

### 一條 pipeline，多種證據

這個工具不只把 bytecode 轉成文字，還把恢復結果整理成可以檢查、重跑及程式化消費的資料：

- 自動偵測單檔或混合目錄的輸入格式。
- 自動選擇 matching V8 profile；需要時可指定 patched `d8`。
- 支援 modern startup snapshot 及舊版 V8/Node embedded snapshot，按 cached blob 的 read-only offsets 嚴格對齊。
- 只保留通過 syntax、residue、closure binding 檢查的 source。
- 缺少舊版 snapshot 時，read-only references 會轉成穩定 placeholder 並在 report 標記 `partial`；需要零容忍時使用 `--strict`。
- 大型目錄可用 manifest 逐檔保存進度；輸出通過 syntax/residue 驗證後才可被 resume 重用。
- 以 tokenizer 建立函式索引，支援 nested function、arrow function、function expression 及 lexical scope。
- 由同一索引產生 function files、call graph、reference graph、tree、name mapping。
- 用 PowerShell 提供快速恢復、inspect、doctor、profiles 及 benchmark；CLI 仍適合 CI。

### 分析與 artifact

一次輸出多種分析資料：

```powershell
.\v8blob-to-js.ps1 -Action recover -InputPath .\input `
  -Emit functions,callgraph,tree,names -OutputPath .\output
```

產生可重用 artifact：

```powershell
.\v8blob-to-js.ps1 -Action recover -InputPath .\input `
  -Emit serialized -OutputPath .\artifacts

.\v8blob-to-js.ps1 -Action recover `
  -InputPath .\artifacts\.analysis\sample.v8recovery.json `
  -Emit functions -OutputPath .\replay
```

查看 blob header、profile、snapshot 及 hash：

```powershell
.\v8blob-to-js.ps1 -Action inspect -InputPath .\input\app.jsc
.\v8blob-to-js.ps1 -Action doctor
```

查看完整 CLI 參數：

```powershell
node .\bin\v8blob-to-js.mjs --help
```

`--strict` 會在任何 unresolved read-only reference 存在時保留原本的失敗行為：

```powershell
.\v8blob-to-js.ps1 -Action recover -InputPath .\input -Strict
```

### 大型目錄與中斷恢復

PowerShell menu 的快速恢復已預設開啟 resume。CLI 可明確使用：

```powershell
.\v8blob-to-js.ps1 -Action recover `
  -InputPath .\input -OutputPath .\output -Resume
```

工具會將每個輸入的 SHA-256、大小、設定 fingerprint、輸出 hash 及分析檔案清單寫入 `recovery-manifest.json`。再次執行時只會重用同一輸入、同一設定且仍通過 syntax/residue 檢查的結果；輸入、輸出或分析檔案被改動，便會自動失效並重新恢復。manifest 亦會在每檔完成後更新，適合處理大型目錄或中途停止後繼續。

### Matching d8 與 profiles

當內建 profile 未覆蓋目標 V8 版本，可提供同版本、支援 `loadjsc()` 的 patched `d8`：

```powershell
.\v8blob-to-js.ps1 -Action recover -InputPath .\input\app.jsc `
  -Backend d8 -D8Path D:\path\to\d8.exe
```

```powershell
npm run profiles:list
npm run profiles:validate

python -B engine/v8asm/cached_data/tooling/generate_profiles.py `
  --version V8_VERSION
```

### 模組結構

```text
v8blob-to-js.ps1          PowerShell 入口
powershell/               menu、actions、Node runner
bin/                      CLI、inspect、doctor、profiles、benchmark
src/cli/                  參數解析
src/io/                   輸入發現、artifact、輸出路徑及 recovery manifest
src/pipeline/             批次恢復及分析輸出
src/analysis/             tokenizer、index、graph、tree、function files
src/inspection/           blob header/profile 診斷
src/reporting/            report 及 source summary
src/validation/           syntax/residue 品質門檻
engine/v8asm/             V8 profiles 及 source-recovery engine
test/                     Node 內建測試
```

### 限制

- cached bytecode 受 V8 version、build flags 及 startup snapshot 影響。
- 還原結果是語義近似源碼，不是原始檔案的逐字副本。
- 原始註解、排版及部分 identifier names 不存在於 bytecode 中。
- 控制流及 expressions 可能以等價但不同的結構輸出。
- 發現 unresolved opcode、非 read-only placeholder、invalid syntax 或 V8 residue 時，該檔案會標記為失敗；缺失 snapshot 造成的 read-only residue 會清楚標記為 partial。

### 開發與驗證

專案沒有 runtime npm dependency，使用 Node 內建 test runner：

```powershell
npm run check
npm test
npm run profiles:validate
python -m unittest discover -s engine/v8asm -p 'test_*.py'
```

參考實作：[suleram/View8](https://github.com/suleram/View8) · [xqy2006/jsc2js](https://github.com/xqy2006/jsc2js) · [V8](https://github.com/v8/v8)

本專案使用 MIT License；`engine/v8asm` 保留其原有 MIT 授權聲明。

## English

<details>
<summary>English overview and first commands</summary>

`v8blob-to-js` is a PowerShell-first recovery and analysis pipeline for V8 `.v8blob` and `.jsc` cached bytecode. It emits readable approximate JavaScript only after syntax and decompiler-residue checks pass, then can produce function indexes, call/reference graphs, trees, name maps, reports, and reusable `.v8recovery.json` artifacts.

Node.js 18+ is required. Python 3 is needed for raw bytecode; disassembly text and validated recovery artifacts can be replayed without Python.

```powershell
.\v8blob-to-js.ps1
.\v8blob-to-js.ps1 -Action recover -InputPath .\input -OutputPath .\output
node .\bin\v8blob-to-js.mjs --help
```

The default `auto` format detects raw blobs, disassembly text, and serialized artifacts. Directory input can mix formats while preserving relative paths. If residue is limited to missing read-only snapshot references, the default mode emits a clearly marked best-effort source; use `--strict` to reject it. Use `--resume` to persist and reuse validated batch results through `recovery-manifest.json`. Use `--emit functions`, `--emit callgraph`, `--emit tree`, `--emit names`, or `--emit serialized` for analysis outputs under `.analysis/`.

When a bundled profile does not cover the target V8 build, pass a matching patched `d8` with `-Backend d8 -D8Path ...`. V8 cached data remains version, build-flag, and snapshot sensitive; recovered output is semantic reconstruction rather than the original source.

</details>

## License

MIT. The `engine/v8asm` directory retains its original license notice.
