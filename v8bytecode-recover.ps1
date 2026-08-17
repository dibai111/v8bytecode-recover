[CmdletBinding()]
param(
    [ValidateSet('menu', 'recover', 'inspect', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'names', 'profiles', 'benchmark', 'doctor')]
    [string]$Action = 'menu',

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
    [ValidateSet('disassembly', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'names', 'serialized')]
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

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$callerPath = (Get-Location).Path
Import-Module (Join-Path $projectRoot 'powershell/V8Blob.Menu.psm1') -Force
Import-Module (Join-Path $projectRoot 'powershell/V8Blob.Actions.psm1') -Force

function Resolve-V8CallerPath {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $Value }
    $expanded = [Environment]::ExpandEnvironmentVariables($Value)
    if ([System.IO.Path]::IsPathRooted($expanded)) { return $expanded }
    return [System.IO.Path]::GetFullPath((Join-Path $callerPath $expanded))
}

$InputPath = Resolve-V8CallerPath $InputPath
$OutputPath = Resolve-V8CallerPath $OutputPath
$D8Path = Resolve-V8CallerPath $D8Path
$Snapshot = Resolve-V8CallerPath $Snapshot
$ReportPath = Resolve-V8CallerPath $ReportPath
$SplitFunctionsPath = Resolve-V8CallerPath $SplitFunctionsPath

try {
    if ($Action -eq 'menu' -and -not $InputPath) {
        $exitCode = Start-V8BlobMenu -ProjectRoot $projectRoot
    }
    else {
        if ($Action -eq 'menu') { $Action = 'recover' }
        $parameters = @{
            Action = $Action
            ProjectRoot = $projectRoot
            InputPath = $InputPath
            OutputPath = $OutputPath
            InputFormat = $InputFormat
            Backend = $Backend
            D8Path = $D8Path
            Profile = $Profile
            Snapshot = $Snapshot
            RuntimeVariant = $RuntimeVariant
            PayloadOffset = $PayloadOffset
            Level = $Level
            Emit = $Emit
            ReportPath = $ReportPath
            SplitFunctionsPath = $SplitFunctionsPath
            FunctionName = $FunctionName
            IncludeFunction = $IncludeFunction
            ExcludeFunction = $ExcludeFunction
            SplitMode = $SplitMode
            SplitDepth = $SplitDepth
            TreeRoot = $TreeRoot
            TreeMode = $TreeMode
            TreeDepth = $TreeDepth
            NormalizeNames = $NormalizeNames
            Strict = $Strict
            Resume = $Resume
            Python = $Python
            NoSnapshotSearch = $NoSnapshotSearch
            ProfileCommand = $ProfileCommand
            BenchmarkBackends = $BenchmarkBackends
        }
        $exitCode = Invoke-V8Action @parameters
    }
}
catch {
    Write-Error $_.Exception.Message
    $exitCode = 1
}

exit ([int]$exitCode)
