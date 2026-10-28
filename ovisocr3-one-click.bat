@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows-one-click.ps1" -Model ovisocr3 -SkipClean -ReuseManagedGpu %*
set EXIT_CODE=%ERRORLEVEL%
if not "%PANDOCR_NO_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
