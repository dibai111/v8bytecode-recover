# v8bytecode-recover

<p align="center">
  <img src="./assets/readme/recovery-pipeline.svg" width="100%" alt="v8bytecode-recover 將 V8 cached bytecode 自動偵測、解碼、還原成可驗證的 JavaScript，並輸出分析資料">
</p>

<p align="center"><strong>PowerShell-first V8 cached bytecode recovery</strong><br>由 .v8blob / .jsc 到可檢查的 JavaScript 近似源碼</p>

<p align="center">
  <a href="#繁體中文">繁體中文</a> ·
  <a href="#english">English</a>
</p>

## 繁體中文

### 它解決甚麼問題

V8 cached bytecode 通常不能直接閱讀，而且還原結果會受 V8 版本、build flags 及 startup snapshot 影響。v8bytecode-recover 將解碼、snapshot/profile 偵測、source recovery、品質檢查及分析輸出放在同一條 pipeline，讓你由一個檔案或一個目錄開始，不需要手動拼接多個工具。

本專案參考了 [xqy2006/jsc2js](https://github.com/xqy2006/jsc2js) 及 [suleram/View8](https://github.com/suleram/View8) 的 bytecode source-recovery 方向，並以 PowerShell-first 方式整理完整操作流程。

### 30 秒開始

需求：

- Windows PowerShell
- Node.js 18+
- Python 3：只在處理 raw .v8blob / .jsc 時需要
- 不需要 npm runtime dependency

最簡單的互動模式：

~~~powershell
cd D:\path\to\v8bytecode-recover
.\v8bytecode-recover.ps1
~~~

選擇 **Quick recover**，輸入檔案或目錄，其他設定使用自動預設值。

需要在 script、CI 或批次流程使用時：

~~~powershell
.\v8bytecode-recover.ps1 -Action recover -InputPath .\input -OutputPath .\output
~~~

也可以直接使用 Node CLI：

~~~powershell
node .\bin\v8bytecode-recover.mjs .\input -o .\output
~~~

成功的輸出只會在通過 JavaScript syntax check 及 decompiler-residue check 後寫入：

~~~text
input/
└─ app.jsc

output/
├─ app.js
├─ recovery-report.json
└─ recovery-manifest.json
~~~

工具會清楚區分 **成功**、**partial** 及 **failed**，並保留可檢查的 evidence、report 或 serialized artifact。

### Pipeline

1. 偵測輸入格式：.v8blob、.jsc、disassembly text 或 .v8recovery.json
2. 自動選擇 V8 profile，並搜尋 matching startup snapshot 或附近的 versioned Node runtime
3. 解碼 bytecode，還原控制流、函式、作用域及常見 JavaScript 結構
4. 執行 syntax、residue 及 closure-binding 檢查
5. 輸出 JavaScript、報告、manifest 及可選的函式分析資料

### 輸入與輸出

| 類型 | 支援 |
| --- | --- |
| Raw input | .v8blob、.jsc |
| Text input | .disassembly.txt、.disasm.txt、.txt |
| Replay input | .v8recovery.json |
| Source output | 通過品質檢查的 *.js |
| Analysis | *.functions.json、*.callgraph.json、*.tree.json、*.names.json |
| Evidence | *.disassembly.txt、*.translated.txt、*.cfg.json |
| Run state | recovery-report.json、recovery-manifest.json |

### 常用命令

查看 blob header、V8 profile、snapshot 候選及 hash：

~~~powershell
.\v8bytecode-recover.ps1 -Action inspect -InputPath .\input\app.jsc
~~~

嚴格模式：任何 unresolved read-only reference 都不輸出 JavaScript：

~~~powershell
.\v8bytecode-recover.ps1 -Action recover -InputPath .\input -OutputPath .\output -Strict
~~~

輸出函式索引及 graph：

~~~powershell
.\v8bytecode-recover.ps1 -Action recover -InputPath .\input -OutputPath .\output -Emit functions,callgraph,tree,names
~~~

產生可重用 artifact，之後可在沒有 Python 或 d8 的情況下重播：

~~~powershell
.\v8bytecode-recover.ps1 -Action recover -InputPath .\input -OutputPath .\artifacts -Emit serialized
.\v8bytecode-recover.ps1 -Action recover -InputPath .\artifacts\.analysis\sample.v8recovery.json -OutputPath .\replay -Emit functions
~~~

大型目錄可使用 resume；輸入、設定或輸出被改動時，舊結果會自動失效：

~~~powershell
.\v8bytecode-recover.ps1 -Action recover -InputPath .\input -OutputPath .\output -Resume
~~~

### Matching d8 與 profiles

內建 profile 優先處理 raw bytecode。當目標 V8 build 未被內建 profile 覆蓋，才提供同版本、支援 loadjsc() 的 patched d8：

~~~powershell
.\v8bytecode-recover.ps1 -Action recover -InputPath .\input\app.jsc -Backend d8 -D8Path D:\path\to\d8.exe
~~~

查看或驗證內建 profiles：

~~~powershell
npm run profiles:list
npm run profiles:validate
~~~

### 限制

- V8 cached bytecode 是 version、build flags 及 startup snapshot 敏感格式；匹配版本仍然重要。
- 輸出是語義近似源碼，不是原始檔案的逐字副本。
- 原始註解、排版及部分 identifier names 不存在於 bytecode 中。
- 控制流及 expressions 可能以等價但不同的形式輸出。
- 缺少 read-only snapshot 時，預設可輸出有明確標記的 partial source；使用 -Strict 可改為零容忍。
- 非 read-only residue、未知 opcode、invalid syntax 或 closure binding 問題會令該檔案失敗，不會冒充成功輸出。

### 專案結構

~~~text
v8bytecode-recover.ps1    PowerShell 入口及簡化 menu
powershell/               PowerShell actions、menu、Node runner
bin/                      recover、inspect、doctor、profiles、benchmark CLI
src/                      輸入、pipeline、analysis、validation 及 reporting
engine/v8asm/             V8 profiles、cached-data decoder、source recovery
assets/readme/            README pipeline visual
test/                     Node 內建測試
~~~

### 開發與驗證

本專案沒有 runtime npm dependency：

~~~powershell
npm run check
npm test
npm run profiles:validate
python -B -m unittest discover -s engine/v8asm -p 'test_*.py'
~~~

MIT License。engine/v8asm 保留其原有 MIT 授權聲明。

## English

v8bytecode-recover is a PowerShell-first recovery pipeline for V8 cached bytecode. It turns .v8blob, .jsc, disassembly text, or validated recovery artifacts into readable approximate JavaScript, then applies syntax and decompiler-residue gates before writing source files.

The project is informed by [xqy2006/jsc2js](https://github.com/xqy2006/jsc2js) and [suleram/View8](https://github.com/suleram/View8), and focuses on a clear PowerShell-first recovery workflow.

### Quick start

Requirements:

- Windows PowerShell
- Node.js 18+
- Python 3 for raw bytecode
- No runtime npm dependency

~~~powershell
.\v8bytecode-recover.ps1
~~~

For automation:

~~~powershell
.\v8bytecode-recover.ps1 -Action recover -InputPath .\input -OutputPath .\output
node .\bin\v8bytecode-recover.mjs .\input -o .\output
~~~

The default workflow auto-detects raw blobs, disassembly text, and .v8recovery.json artifacts. It can discover matching profiles and nearby snapshots, process mixed directories, preserve relative paths, and resume validated results through recovery-manifest.json.

### What makes it useful

- Built-in profile backend for supported V8 cached-data formats.
- Optional matching patched d8 backend for builds outside the bundled profiles.
- Snapshot discovery for modern startup snapshots and legacy embedded Node snapshots.
- Quality gates that separate success, partial recovery, and failure.
- Reusable serialized artifacts for replay without Python or d8.
- Function indexes, nested hierarchy, call/reference graphs, trees, and deterministic name maps.
- PowerShell menu for quick use and CLI entry points for CI or scripts.

### Important limitations

V8 cached bytecode depends on its V8 version, build flags, and startup snapshot. Recovered output is a semantic reconstruction, not a byte-for-byte copy of the original source. Names, comments, formatting, optimized expressions, and some control-flow structure may not be recoverable. Use --strict when partial output is not acceptable.

### Development

~~~powershell
npm run check
npm test
npm run profiles:validate
python -B -m unittest discover -s engine/v8asm -p 'test_*.py'
~~~

## References

- [xqy2006/jsc2js](https://github.com/xqy2006/jsc2js) — reference JSC-to-JavaScript workflow.
- [suleram/View8](https://github.com/suleram/View8) — related bytecode source-recovery project.
- [V8](https://github.com/v8/v8) — upstream engine and bytecode implementation.
- [v8bytecode-recover](https://github.com/dibai111/v8bytecode-recover) — this project.

## License

MIT. The engine/v8asm directory retains its original license notice.
