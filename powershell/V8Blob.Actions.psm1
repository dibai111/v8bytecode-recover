Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'V8Blob.Node.psm1') -Force

function Add-V8Argument {
    param(
        [Parameter(Mandatory = $true)]
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
        [ValidateSet('recover', 'inspect', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'names', 'profiles', 'benchmark', 'doctor')]
        [string]$Action,

        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [string]$InputPath,
        [string]$OutputPath,
        [ValidateSet('auto', 'raw', 'disassembled', 'serialized')]
        [string]$InputFormat = 'auto',
        [ValidateSet('auto', 'profile', 'd8')]
        [string]$Backend = 'auto',
        [string]$D8Path,
        [string]$Profile,
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
        [switch]$NormalizeNames,
        [switch]$Strict,
        [switch]$Resume,
        [string]$Python = 'python',
        [switch]$NoSnapshotSearch,
        [ValidateSet('list', 'validate')]
        [string]$ProfileCommand = 'list',
        [string]$BenchmarkBackends = 'profile'
    )

    $nodeArguments = [System.Collections.Generic.List[string]]::new()
    $entryPoint = $null

    switch ($Action) {
        'recover' { $entryPoint = 'bin/v8blob-to-js.mjs' }
        'disassemble' { $entryPoint = 'bin/v8blob-to-js.mjs'; $Emit = @('disassembly') }
        'translated' { $entryPoint = 'bin/v8blob-to-js.mjs'; $Emit = @('translated') }
        'cfg' { $entryPoint = 'bin/v8blob-to-js.mjs'; $Emit = @('cfg') }
        'functions' { $entryPoint = 'bin/v8blob-to-js.mjs'; $Emit = @('functions') }
        'callgraph' { $entryPoint = 'bin/v8blob-to-js.mjs'; $Emit = @('callgraph') }
        'tree' { $entryPoint = 'bin/v8blob-to-js.mjs'; $Emit = @('tree') }
        'names' { $entryPoint = 'bin/v8blob-to-js.mjs'; $Emit = @('names') }
        'inspect' { $entryPoint = 'bin/v8blob-inspect.mjs' }
        'profiles' { $entryPoint = 'bin/v8blob-profiles.mjs'; [void]$nodeArguments.Add($ProfileCommand) }
        'benchmark' { $entryPoint = 'bin/v8blob-benchmark.mjs' }
        'doctor' { $entryPoint = 'bin/v8blob-doctor.mjs' }
    }

    if ($Action -in @('recover', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'names', 'inspect', 'benchmark') -and [string]::IsNullOrWhiteSpace($InputPath)) {
        throw "Action '$Action' requires an input file or directory."
    }

    if ($Action -in @('recover', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'names')) {
        [void]$nodeArguments.Add($InputPath)
        if ($OutputPath) { Add-V8Argument $nodeArguments '--output' $OutputPath }
        if ($InputFormat -ne 'auto') { Add-V8Argument $nodeArguments '--input-format' $InputFormat }
        if ($Backend -ne 'auto') { Add-V8Argument $nodeArguments '--backend' $Backend }
        if ($D8Path) { Add-V8Argument $nodeArguments '--d8' $D8Path }
        if ($Profile) { Add-V8Argument $nodeArguments '--profile' $Profile }
        if ($Snapshot) { Add-V8Argument $nodeArguments '--snapshot' $Snapshot }
        if ($NoSnapshotSearch) { [void]$nodeArguments.Add('--no-snapshot-search') }
        if ($RuntimeVariant) { Add-V8Argument $nodeArguments '--runtime-variant' $RuntimeVariant }
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
        if ($NormalizeNames) { [void]$nodeArguments.Add('--normalize-names') }
        if ($Strict) { [void]$nodeArguments.Add('--strict') }
        if ($Resume) { [void]$nodeArguments.Add('--resume') }
        if ($Python -ne 'python') { Add-V8Argument $nodeArguments '--python' $Python }
    }
    elseif ($Action -eq 'inspect') {
        [void]$nodeArguments.Add($InputPath)
    }
    elseif ($Action -eq 'benchmark') {
        [void]$nodeArguments.Add($InputPath)
        Add-V8Argument $nodeArguments '--backends' $BenchmarkBackends
        if ($D8Path) { Add-V8Argument $nodeArguments '--d8' $D8Path }
        if ($Profile) { Add-V8Argument $nodeArguments '--profile' $Profile }
        if ($OutputPath) { Add-V8Argument $nodeArguments '--output' $OutputPath }
    }
    elseif ($Action -eq 'doctor') {
        if ($Python -ne 'python') { Add-V8Argument $nodeArguments '--python' $Python }
        if ($D8Path) { Add-V8Argument $nodeArguments '--d8' $D8Path }
    }

    return Invoke-V8NodeCommand -ProjectRoot $ProjectRoot -EntryPoint $entryPoint -Arguments $nodeArguments.ToArray()
}

Export-ModuleMember -Function Invoke-V8Action
