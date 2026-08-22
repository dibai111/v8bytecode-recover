$entryPoint = Join-Path $PSScriptRoot 'v8bytecode-recover.ps1'
& $entryPoint -Language 'zh-TW' @args
exit ([int]$LASTEXITCODE)
