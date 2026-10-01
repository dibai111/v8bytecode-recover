$entryPoint = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'v8bytecode-recover.ps1'
& $entryPoint -Language 'zh-TW' @args
exit ([int]$LASTEXITCODE)
