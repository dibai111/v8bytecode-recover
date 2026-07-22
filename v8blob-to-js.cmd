@echo off
setlocal
pushd "%~dp0"

if "%~1"=="" (
  node "%~dp0bin\v8blob-to-js.mjs" --help
) else (
  node "%~dp0bin\v8blob-to-js.mjs" %*
)

set "EXIT_CODE=%ERRORLEVEL%"
popd
echo.
if "%EXIT_CODE%"=="0" (
  echo JavaScript recovery finished.
) else (
  echo One or more files still contain unresolved bytecode structures.
)
pause
exit /b %EXIT_CODE%
