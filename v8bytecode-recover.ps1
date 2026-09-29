[CmdletBinding()]
param(
    [ValidateSet('zh-TW', 'zh-CN', 'en')]
    [string]$Language = 'zh-TW',

    [ValidateSet('menu', 'recover', 'inspect', 'disassemble', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'inline', 'names', 'profiles', 'benchmark', 'doctor')]
    [string]$Action = 'menu',

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
    [ValidateSet('disassembly', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'inline', 'names', 'serialized')]
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

# The central entry point lives in the repository root.
$projectRoot = $PSScriptRoot
$scriptDir = Join-Path $PSScriptRoot 'powershell'
$callerPath = (Get-Location).Path
Import-Module (Join-Path $scriptDir 'V8BytecodeRecover.Localization.psm1') -Force -ErrorAction Stop
Set-V8Language -Language $Language
Import-Module (Join-Path $scriptDir 'V8BytecodeRecover.Menu.psm1') -Force
Import-Module (Join-Path $scriptDir 'V8BytecodeRecover.Actions.psm1') -Force

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
$D8Directory = Resolve-V8CallerPath $D8Directory
$ProfileDirectory = Resolve-V8CallerPath $ProfileDirectory
$ProfileOutputDirectory = Resolve-V8CallerPath $ProfileOutputDirectory
$ProfileCacheDirectory = Resolve-V8CallerPath $ProfileCacheDirectory
$V8Repository = Resolve-V8CallerPath $V8Repository
$ProfileVersionsFile = Resolve-V8CallerPath $ProfileVersionsFile
$ProfileReportPath = Resolve-V8CallerPath $ProfileReportPath
$ProfileDiscoveryOutput = Resolve-V8CallerPath $ProfileDiscoveryOutput
$CorpusManifest = Resolve-V8CallerPath $CorpusManifest
$Snapshot = Resolve-V8CallerPath $Snapshot
$ReportPath = Resolve-V8CallerPath $ReportPath
$SplitFunctionsPath = Resolve-V8CallerPath $SplitFunctionsPath

try {
    if ($Action -eq 'menu' -and -not $InputPath) {
        $exitCode = Start-RecoverMenu -ProjectRoot $projectRoot -Language $Language
    }
    else {
        if ($Action -eq 'menu') { $Action = 'recover' }
        $parameters = @{
            Action = $Action
            ProjectRoot = $projectRoot
            Language = $Language
            InputPath = $InputPath
            OutputPath = $OutputPath
            InputFormat = $InputFormat
            Backend = $Backend
            D8Path = $D8Path
            D8Directory = $D8Directory
            Profile = $Profile
            ProfileDirectory = $ProfileDirectory
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
            Scope = $Scope
            ShowAll = $ShowAll
            InlineDepth = $InlineDepth
            InlineBranchLimit = $InlineBranchLimit
            NormalizeNames = $NormalizeNames
            Research = $Research
            Strict = $Strict
            Resume = $Resume
            Python = $Python
            NoSnapshotSearch = $NoSnapshotSearch
            ProfileCommand = $ProfileCommand
            ProfileVersion = $ProfileVersion
            ProfileOutputDirectory = $ProfileOutputDirectory
            ProfileCacheDirectory = $ProfileCacheDirectory
            ProfileSource = $ProfileSource
            V8Repository = $V8Repository
            ProfileVersionsFile = $ProfileVersionsFile
            ProfileReportPath = $ProfileReportPath
            ProfileDiscoveryOutput = $ProfileDiscoveryOutput
            MergeExisting = $MergeExisting
            BenchmarkBackends = $BenchmarkBackends
            BenchmarkAdapter = $BenchmarkAdapter
            CorpusManifest = $CorpusManifest
            FailOnRegression = $FailOnRegression
            Embedder = $Embedder
        }
        $exitCode = Invoke-V8Action @parameters
    }
}
catch {
    Write-Error $_.Exception.Message
    $exitCode = 1
}

exit ([int]$exitCode)
