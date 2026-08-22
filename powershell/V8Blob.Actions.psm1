Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'V8Blob.Node.psm1') -Force

function Add-V8Argument {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    [void]$Arguments.Add($Name)
    [void]$Arguments.Add($Value)
}

function Invoke-V8Action {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('recover', 'inspect', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'inline', 'names', 'profiles', 'benchmark', 'doctor')]
        [string]$Action,

        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [ValidateSet('zh-TW', 'zh-CN')]
        [string]$Language = 'zh-TW',

        [string]$InputPath,
        [string]$OutputPath,
        [ValidateSet('auto', 'raw', 'disassembled', 'serialized')]
        [string]$InputFormat = 'auto',
        [ValidateSet('auto', 'profile', 'd8')]
        [string]$Backend = 'auto',
        [string]$D8Path,
        [string]$D8Directory,
        [string]$Profile,
        [string]$ProfileDirectory,
        [string]$Snapshot,
        [string]$RuntimeVariant,
        [int]$PayloadOffset = -1,
        [ValidateRange(1, 4)]
        [int]$Level = 4,
        [string[]]$Emit = @(),
        [string]$ReportPath,
        [string]$SplitFunctionsPath,
        [string[]]$FunctionName = @(),
        [string[]]$IncludeFunction = @(),
        [string[]]$ExcludeFunction = @(),
        [ValidateSet('declarers', 'calls', 'references')]
        [string]$SplitMode = 'declarers',
        [int]$SplitDepth = -1,
        [string]$TreeRoot,
        [ValidateSet('declarers', 'calls', 'references')]
        [string]$TreeMode = 'declarers',
        [int]$TreeDepth = -1,
        [string]$Scope,
        [switch]$ShowAll,
        [int]$InlineDepth = -1,
        [int]$InlineBranchLimit = -1,
        [switch]$NormalizeNames,
        [switch]$Research,
        [switch]$Strict,
        [switch]$Resume,
        [string]$Python = 'python',
        [switch]$NoSnapshotSearch,
        [ValidateSet('list', 'validate', 'identify', 'coverage', 'generate', 'discover')]
        [string]$ProfileCommand = 'list',
        [string[]]$ProfileVersion = @(),
        [string]$ProfileOutputDirectory,
        [string]$ProfileCacheDirectory,
        [ValidateSet('github', 'git')]
        [string]$ProfileSource = 'github',
        [string]$V8Repository,
        [string]$ProfileVersionsFile,
        [string]$ProfileReportPath,
        [string]$ProfileDiscoveryOutput,
        [switch]$MergeExisting,
        [string]$BenchmarkBackends = 'profile',
        [string[]]$BenchmarkAdapter = @(),
        [string]$CorpusManifest,
        [switch]$FailOnRegression,
        [ValidateSet('unknown', 'node', 'electron', 'chromium', 'custom')]
        [string]$Embedder = 'unknown'
    )

    $nodeArguments = [System.Collections.Generic.List[string]]::new()
    $entryPoint = $null

    switch ($Action) {
        'recover' { $entryPoint = 'bin/v8bytecode-recover.mjs' }
        'disassemble' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('disassembly') }
        'translated' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('translated') }
        'cfg' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('cfg') }
        'functions' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('functions') }
        'callgraph' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('callgraph') }
        'tree' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('tree') }
        'inline' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('inline') }
        'names' { $entryPoint = 'bin/v8bytecode-recover.mjs'; $Emit = @('names') }
        'inspect' { $entryPoint = 'bin/v8bytecode-inspect.mjs' }
        'profiles' {
            $entryPoint = 'bin/v8bytecode-profiles.mjs'
            [void]$nodeArguments.Add($ProfileCommand)
            if ($ProfileCommand -eq 'identify') {
                if ([string]::IsNullOrWhiteSpace($InputPath)) { throw 'Profile identify requires an input file.' }
                [void]$nodeArguments.Add($InputPath)
                Add-V8Argument $nodeArguments '--language' $Language
            }
            elseif ($ProfileCommand -eq 'coverage') {
                if ([string]::IsNullOrWhiteSpace($InputPath)) { throw 'Profile coverage requires a corpus directory.' }
                Add-V8Argument $nodeArguments '--corpus' $InputPath
            }
            elseif ($ProfileCommand -eq 'generate') {
                foreach ($version in $ProfileVersion) { Add-V8Argument $nodeArguments '--version' $version }
                if ($ProfileOutputDirectory) { Add-V8Argument $nodeArguments '--output-dir' $ProfileOutputDirectory }
                if ($ProfileCacheDirectory) { Add-V8Argument $nodeArguments '--cache-dir' $ProfileCacheDirectory }
                if ($ProfileSource -ne 'github') { Add-V8Argument $nodeArguments '--source' $ProfileSource }
                if ($V8Repository) { Add-V8Argument $nodeArguments '--v8-repo' $V8Repository }
                if ($ProfileVersionsFile) { Add-V8Argument $nodeArguments '--versions-file' $ProfileVersionsFile }
                if ($ProfileReportPath) { Add-V8Argument $nodeArguments '--report' $ProfileReportPath }
                if ($MergeExisting) { [void]$nodeArguments.Add('--merge-existing') }
            }
            elseif ($ProfileCommand -eq 'discover') {
                if ($ProfileDiscoveryOutput) { Add-V8Argument $nodeArguments '--output' $ProfileDiscoveryOutput }
                if ($ProfileReportPath) { Add-V8Argument $nodeArguments '--report' $ProfileReportPath }
            }
            elseif ($ProfileDirectory) {
                Add-V8Argument $nodeArguments '--profile-dir' $ProfileDirectory
            }
        }
        'benchmark' { $entryPoint = 'bin/v8bytecode-benchmark.mjs' }
        'doctor' { $entryPoint = 'bin/v8bytecode-doctor.mjs' }
    }

    if ($Action -in @('recover', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'inline', 'names', 'inspect', 'benchmark') -and [string]::IsNullOrWhiteSpace($InputPath)) {
        throw "Action '$Action' requires an input file or directory."
    }

    if ($Action -in @('recover', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'inline', 'names')) {
        [void]$nodeArguments.Add($InputPath)
        if ($OutputPath) { Add-V8Argument $nodeArguments '--output' $OutputPath }
        if ($InputFormat -ne 'auto') { Add-V8Argument $nodeArguments '--input-format' $InputFormat }
        if ($Backend -ne 'auto') { Add-V8Argument $nodeArguments '--backend' $Backend }
        if ($D8Path) { Add-V8Argument $nodeArguments '--d8' $D8Path }
        if ($D8Directory) { Add-V8Argument $nodeArguments '--d8-dir' $D8Directory }
        if ($Profile) { Add-V8Argument $nodeArguments '--profile' $Profile }
        if ($ProfileDirectory) { Add-V8Argument $nodeArguments '--profile-dir' $ProfileDirectory }
        if ($Snapshot) { Add-V8Argument $nodeArguments '--snapshot' $Snapshot }
        if ($NoSnapshotSearch) { [void]$nodeArguments.Add('--no-snapshot-search') }
        if ($RuntimeVariant) { Add-V8Argument $nodeArguments '--runtime-variant' $RuntimeVariant }
        if ($Embedder -ne 'unknown') { Add-V8Argument $nodeArguments '--embedder' $Embedder }
        if ($PayloadOffset -ge 0) { Add-V8Argument $nodeArguments '--payload-offset' ([string]$PayloadOffset) }
        if ($Level -ne 4) { Add-V8Argument $nodeArguments '--level' ([string]$Level) }
        foreach ($kind in $Emit) { Add-V8Argument $nodeArguments '--emit' $kind }
        if ($ReportPath) { Add-V8Argument $nodeArguments '--report' $ReportPath }
        if ($SplitFunctionsPath) { Add-V8Argument $nodeArguments '--split-functions' $SplitFunctionsPath }
        foreach ($name in $FunctionName) { Add-V8Argument $nodeArguments '--function' $name }
        foreach ($pattern in $IncludeFunction) { Add-V8Argument $nodeArguments '--include-function' $pattern }
        foreach ($pattern in $ExcludeFunction) { Add-V8Argument $nodeArguments '--exclude-function' $pattern }
        if ($SplitMode -ne 'declarers') { Add-V8Argument $nodeArguments '--split-mode' $SplitMode }
        if ($SplitDepth -ge 0) { Add-V8Argument $nodeArguments '--split-depth' ([string]$SplitDepth) }
        if ($TreeRoot) { Add-V8Argument $nodeArguments '--tree' $TreeRoot }
        if ($TreeMode -ne 'declarers') { Add-V8Argument $nodeArguments '--tree-mode' $TreeMode }
        if ($TreeDepth -ge 0) { Add-V8Argument $nodeArguments '--tree-depth' ([string]$TreeDepth) }
        if ($Scope) { Add-V8Argument $nodeArguments '--scope' $Scope }
        if ($ShowAll) { [void]$nodeArguments.Add('--show-all') }
        if ($InlineDepth -ge 0) { Add-V8Argument $nodeArguments '--inline-depth' ([string]$InlineDepth) }
        if ($InlineBranchLimit -ge 0) { Add-V8Argument $nodeArguments '--inline-branch-limit' ([string]$InlineBranchLimit) }
        if ($NormalizeNames) { [void]$nodeArguments.Add('--normalize-names') }
        if ($Research) { [void]$nodeArguments.Add('--research') }
        if ($Strict) { [void]$nodeArguments.Add('--strict') }
        if ($Resume) { [void]$nodeArguments.Add('--resume') }
        if ($Python -ne 'python') { Add-V8Argument $nodeArguments '--python' $Python }
    }
    elseif ($Action -eq 'inspect') {
        [void]$nodeArguments.Add($InputPath)
        Add-V8Argument $nodeArguments '--language' $Language
        if ($ProfileDirectory) { Add-V8Argument $nodeArguments '--profile-dir' $ProfileDirectory }
        if ($Embedder -ne 'unknown') { Add-V8Argument $nodeArguments '--embedder' $Embedder }
    }
    elseif ($Action -eq 'benchmark') {
        [void]$nodeArguments.Add($InputPath)
        Add-V8Argument $nodeArguments '--backends' $BenchmarkBackends
        foreach ($adapter in $BenchmarkAdapter) { Add-V8Argument $nodeArguments '--adapter' $adapter }
        if ($CorpusManifest) { Add-V8Argument $nodeArguments '--corpus-manifest' $CorpusManifest }
        if ($FailOnRegression) { [void]$nodeArguments.Add('--fail-on-regression') }
        if ($D8Path) { Add-V8Argument $nodeArguments '--d8' $D8Path }
        if ($D8Directory) { Add-V8Argument $nodeArguments '--d8-dir' $D8Directory }
        if ($Profile) { Add-V8Argument $nodeArguments '--profile' $Profile }
        if ($ProfileDirectory) { Add-V8Argument $nodeArguments '--profile-dir' $ProfileDirectory }
        if ($OutputPath) { Add-V8Argument $nodeArguments '--output' $OutputPath }
    }
    elseif ($Action -eq 'doctor') {
        Add-V8Argument $nodeArguments '--language' $Language
        if ($Python -ne 'python') { Add-V8Argument $nodeArguments '--python' $Python }
        if ($D8Path) { Add-V8Argument $nodeArguments '--d8' $D8Path }
        if ($D8Directory) { Add-V8Argument $nodeArguments '--d8-dir' $D8Directory }
        if ($ProfileDirectory) { Add-V8Argument $nodeArguments '--profile-dir' $ProfileDirectory }
    }

    return Invoke-V8NodeCommand -ProjectRoot $ProjectRoot -EntryPoint $entryPoint -Arguments $nodeArguments.ToArray()
}

Export-ModuleMember -Function Invoke-V8Action
