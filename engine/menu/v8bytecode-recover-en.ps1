$entryPoint = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'v8bytecode-recover.ps1'
& $entryPoint -Language 'en' @args
exit ([int]$LASTEXITCODE)
