Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'V8BytecodeRecover.Actions.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'V8BytecodeRecover.Localization.psm1') -Force -ErrorAction Stop

function ConvertTo-V8MenuPath {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $cleanValue = $Value.Trim().Trim([char]34)
    $expanded = [Environment]::ExpandEnvironmentVariables($cleanValue)
    if ([System.IO.Path]::IsPathRooted($expanded)) { return [System.IO.Path]::GetFullPath($expanded) }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $expanded))
}

function Read-V8InputPath {
    param([string]$Prompt = (Get-V8Text 'InputPath'))

    while ($true) {
        $candidate = ConvertTo-V8MenuPath (Read-Host $Prompt)
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        Write-Host (Get-V8Text 'PathMissing') -ForegroundColor Yellow
    }
}

function Read-V8OutputPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $defaultPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'output'))
    $candidate = ConvertTo-V8MenuPath (Read-Host (Get-V8Text 'OutputPath' $defaultPath))
    if (-not $candidate) { return $defaultPath }
    return $candidate
}

function Read-V8Choice {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,
        [string[]]$Choices,
        [Parameter(Mandatory = $true)]
        [string]$Default
    )

    while ($true) {
        $hint = Get-V8Text 'ChoiceHint' $Prompt ($Choices -join '/') $Default
        $value = (Read-Host $hint).Trim().ToLowerInvariant()
        if (-not $value) { return $Default }
        if ($Choices -contains $value) { return $value }
        Write-Host (Get-V8Text 'ChooseOne' ($Choices -join ', ')) -ForegroundColor Yellow
    }
}

function Read-V8ConversionSettings {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [string[]]$Emit = @()
    )

    $inputPath = Read-V8InputPath
    $outputPath = Read-V8OutputPath -ProjectRoot $ProjectRoot

    return @{
        InputPath = $inputPath
        InputFormat = 'auto'
        OutputPath = $outputPath
        Backend = 'auto'
        D8Path = $null
        Profile = $null
        ProfileDirectory = $null
        Snapshot = $null
        Level = 4
        Emit = $Emit
        ReportPath = Join-Path $outputPath 'recovery-report.json'
        SplitFunctionsPath = $null
        FunctionName = @()
        IncludeFunction = @()
        ExcludeFunction = @()
        SplitMode = 'declarers'
        SplitDepth = -1
        TreeRoot = $null
        TreeMode = 'declarers'
        TreeDepth = -1
        NormalizeNames = $false
        Resume = $true
    }
}

function Invoke-V8MenuConversion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [string[]]$Emit = @()
    )

    $settings = Read-V8ConversionSettings -ProjectRoot $ProjectRoot -Emit $Emit
    $code = Invoke-V8Action -Action recover -ProjectRoot $ProjectRoot @settings
    if ($code -eq 0) {
        Write-Host (Get-V8Text 'Completed') -ForegroundColor Green
    }
    else {
        Write-Host (Get-V8Text 'Finished' $code) -ForegroundColor Yellow
    }
    return $code
}

function Start-RecoverMenu {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [ValidateSet('zh-TW', 'zh-CN', 'en')]
        [string]$Language = 'zh-TW'
    )

    Set-V8Language -Language $Language

    while ($true) {
        Write-Host ''
        Write-Host '========================================' -ForegroundColor Cyan
        Write-Host (Get-V8Text 'Title') -ForegroundColor Cyan
        Write-Host '========================================' -ForegroundColor Cyan
        Write-Host "1. $(Get-V8Text 'QuickRecover')"
        Write-Host "2. $(Get-V8Text 'Inspect')"
        Write-Host "3. $(Get-V8Text 'ExportAnalysis')"
        Write-Host "4. $(Get-V8Text 'Profiles')"
        Write-Host "5. $(Get-V8Text 'Benchmark')"
        Write-Host "6. $(Get-V8Text 'Doctor')"
        Write-Host "0. $(Get-V8Text 'Exit')"
        $choice = (Read-Host (Get-V8Text 'SelectFeature')).Trim()

        try {
            switch ($choice) {
                '1' { [void](Invoke-V8MenuConversion -ProjectRoot $ProjectRoot) }
                '2' {
                    $inputPath = Read-V8InputPath
                    [void](Invoke-V8Action -Action inspect -ProjectRoot $ProjectRoot -Language $Language -InputPath $inputPath)
                }
                '3' {
                    $kind = Read-V8Choice (Get-V8Text 'AnalysisOutput') @('disassembly', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'inline', 'names', 'serialized') 'disassembly'
                    [void](Invoke-V8MenuConversion -ProjectRoot $ProjectRoot -Emit @($kind))
                }
                '4' {
                    $command = Read-V8Choice (Get-V8Text 'ProfileAction') @('list', 'validate', 'generate', 'discover') 'list'
                    if ($command -eq 'generate') {
                        $version = (Read-Host (Get-V8Text 'ProfileVersion')).Trim()
                        $versions = if ($version) { @($version) } else { @() }
                        $suffix = if ($version) { $version } else { 'generated' }
                        $defaultProfileOutput = Join-Path $ProjectRoot (Join-Path 'output/profiles' $suffix)
                        $profileOutput = ConvertTo-V8MenuPath (Read-Host (Get-V8Text 'ProfileOutput' $defaultProfileOutput))
                        if (-not $profileOutput) { $profileOutput = $defaultProfileOutput }
                        [void](Invoke-V8Action -Action profiles -ProjectRoot $ProjectRoot -ProfileCommand generate -ProfileVersion $versions -ProfileOutputDirectory $profileOutput)
                    }
                    else {
                        [void](Invoke-V8Action -Action profiles -ProjectRoot $ProjectRoot -ProfileCommand $command)
                    }
                }
                '5' {
                    $inputPath = Read-V8InputPath
                    $backends = Read-Host (Get-V8Text 'Backends')
                    if (-not $backends) { $backends = 'profile' }
                    [void](Invoke-V8Action -Action benchmark -ProjectRoot $ProjectRoot -InputPath $inputPath -BenchmarkBackends $backends)
                }
                '6' { [void](Invoke-V8Action -Action doctor -ProjectRoot $ProjectRoot -Language $Language) }
                '0' { return 0 }
                default { Write-Host (Get-V8Text 'InvalidSelection') -ForegroundColor Yellow }
            }
        }
        catch {
            Write-Host $_.Exception.Message -ForegroundColor Red
        }

        [void](Read-Host (Get-V8Text 'PressEnter'))
    }
}

Export-ModuleMember -Function Start-RecoverMenu
