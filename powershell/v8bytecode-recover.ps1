$entryPoint = Join-Path (Split-Path -Parent $PSScriptRoot) 'v8bytecode-recover.ps1'
& $entryPoint @args
exit ([int]$LASTEXITCODE)
