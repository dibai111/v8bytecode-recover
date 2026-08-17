Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'V8Blob.Actions.psm1') -Force

function ConvertTo-V8MenuPath {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $cleanValue = $Value.Trim().Trim([char]34)
    $expanded = [Environment]::ExpandEnvironmentVariables($cleanValue)
    if ([System.IO.Path]::IsPathRooted($expanded)) { return [System.IO.Path]::GetFullPath($expanded) }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $expanded))
}

function Read-V8InputPath {
    param([string]$Prompt = 'Input file or directory path')

    while ($true) {
        $candidate = ConvertTo-V8MenuPath (Read-Host $Prompt)
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        Write-Host 'Path does not exist. Try again.' -ForegroundColor Yellow
    }
}

function Read-V8OutputPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $defaultPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'output'))
    $candidate = ConvertTo-V8MenuPath (Read-Host "Output directory [Enter for $defaultPath]")
    if (-not $candidate) { return $defaultPath }
    return $candidate
}

function Read-V8Choice {
    param(
        [string]$Prompt,
        [string[]]$Choices,
        [string]$Default
    )

    while ($true) {
        $value = (Read-Host "$Prompt [$($Choices -join '/'), default $Default]").Trim().ToLowerInvariant()
        if (-not $value) { return $Default }
        if ($Choices -contains $value) { return $value }
        Write-Host "Choose one of: $($Choices -join ', ')" -ForegroundColor Yellow
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
        Write-Host 'Operation completed.' -ForegroundColor Green
    }
    else {
        Write-Host "Operation finished with exit code: $code" -ForegroundColor Yellow
    }
    return $code
}

function Start-V8BlobMenu {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    while ($true) {
        Write-Host ''
        Write-Host '========================================' -ForegroundColor Cyan
        Write-Host '        v8blob-to-js PowerShell tool' -ForegroundColor Cyan
        Write-Host '========================================' -ForegroundColor Cyan
        Write-Host '1. Quick recover (auto-detect, sensible defaults)'
        Write-Host '2. Inspect blob, V8 profile, and snapshot'
        Write-Host '3. Export one analysis output'
        Write-Host '4. List or validate bundled V8 profiles'
        Write-Host '5. Compare backends with benchmark'
        Write-Host '6. Check runtime environment'
        Write-Host '0. Exit'
        $choice = (Read-Host 'Select a feature').Trim()

        try {
            switch ($choice) {
                '1' { [void](Invoke-V8MenuConversion -ProjectRoot $ProjectRoot) }
                '2' {
                    $inputPath = Read-V8InputPath
                    [void](Invoke-V8Action -Action inspect -ProjectRoot $ProjectRoot -InputPath $inputPath)
                }
                '3' {
                    $kind = Read-V8Choice 'Analysis output' @('disassembly', 'translated', 'cfg', 'functions', 'callgraph', 'tree', 'names', 'serialized') 'disassembly'
                    [void](Invoke-V8MenuConversion -ProjectRoot $ProjectRoot -Emit @($kind))
                }
                '4' {
                    $command = Read-V8Choice 'Profile action' @('list', 'validate') 'list'
                    [void](Invoke-V8Action -Action profiles -ProjectRoot $ProjectRoot -ProfileCommand $command)
                }
                '5' {
                    $inputPath = Read-V8InputPath
                    $backends = Read-Host 'Backends (profile or profile,d8) [default profile]'
                    if (-not $backends) { $backends = 'profile' }
                    [void](Invoke-V8Action -Action benchmark -ProjectRoot $ProjectRoot -InputPath $inputPath -BenchmarkBackends $backends)
                }
                '6' { [void](Invoke-V8Action -Action doctor -ProjectRoot $ProjectRoot) }
                '0' { return 0 }
                default { Write-Host 'Invalid selection.' -ForegroundColor Yellow }
            }
        }
        catch {
            Write-Host $_.Exception.Message -ForegroundColor Red
        }

        [void](Read-Host 'Press Enter to return to the main menu')
    }
}

Export-ModuleMember -Function Start-V8BlobMenu
