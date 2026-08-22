$entryPoint = Join-Path $PSScriptRoot 'v8bytecode-recover.ps1'
& $entryPoint -Language 'zh-CN' @args
exit ([int]$LASTEXITCODE)
