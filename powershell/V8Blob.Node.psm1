Set-StrictMode -Version Latest

function Invoke-V8NodeCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [Parameter(Mandatory = $true)]
        [string]$EntryPoint,

        [string[]]$Arguments = @()
    )

    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        throw 'Node.js was not found. Install Node.js 18 or newer.'
    }

    $scriptPath = Join-Path $ProjectRoot $EntryPoint
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Tool entry point was not found: $scriptPath"
    }

    Push-Location $ProjectRoot
    try {
        & $node.Source $scriptPath @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $commandExitCode = $LASTEXITCODE
        if ($null -eq $commandExitCode) {
            return 0
        }
        return [int]$commandExitCode
    }
    finally {
        Pop-Location
    }
}

Export-ModuleMember -Function Invoke-V8NodeCommand
