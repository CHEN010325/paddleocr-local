@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\unlimited-ocr2-one-click.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%PANDOCR_NO_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
