@echo off
REM Deploy PaddleOCR Local with Docker Compose (Windows)
setlocal

cd /d "%~dp0"

echo Deploying PaddleOCR Local...

set "BASE_ENV=%~dp0env.txt"
set "RUNTIME_ENV=%~dp0tmp\pandocr-runtime.env"

if not exist "%BASE_ENV%" (
    echo env.txt does not exist. Run build.bat or create the env file first.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prepare-runtime-env.ps1" -BaseEnvFile "%BASE_ENV%" -RuntimeEnvFile "%RUNTIME_ENV%" >nul
if errorlevel 1 (
    echo Failed to prepare the persistent controller credential.
    pause
    exit /b 1
)
set "PANDOCR_MODEL_CONTROLLER_TOKEN="
set "COMPOSE=docker compose --env-file "%BASE_ENV%" --env-file "%RUNTIME_ENV%" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr"
set "CORE_SERVICES=pandocr-controller pandocr-office-converter pandocr-web paddleocr-vlm-server paddleocr-vl-api paddleocr-ocr-api navidc-ocr-api"

%COMPOSE% up -d --no-start --force-recreate %CORE_SERVICES%
%COMPOSE% start pandocr-controller pandocr-office-converter pandocr-web

echo Waiting for services...
timeout /t 5 /nobreak >nul

echo.
echo Service status:
%COMPOSE% ps

echo.
echo Health checks:

curl -f http://localhost:8000/ >nul 2>&1
if not errorlevel 1 (
    echo pandocr-web ^(8000^) OK
) else (
    echo pandocr-web ^(8000^) not ready
)

curl -f http://localhost:8081/health >nul 2>&1
if not errorlevel 1 (
    echo paddleocr-vl-api ^(8081^) OK
) else (
    echo paddleocr-vl-api ^(8081^) stopped or starting
)

curl -f http://localhost:8082/health >nul 2>&1
if not errorlevel 1 (
    echo paddleocr-ocr-api ^(8082^) OK
) else (
    echo paddleocr-ocr-api ^(8082^) stopped or starting
)

echo.
echo Done.
echo WebUI: http://localhost:8000
echo VL API:  http://localhost:8081
echo OCR API: http://localhost:8082
echo Only the selected model runs. On a switch, pandocr-controller fully stops the old model and releases GPU memory before starting the new one.
echo.
echo Useful commands:
echo   docker compose --env-file "%BASE_ENV%" --env-file "%RUNTIME_ENV%" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr logs -f
echo   docker compose --env-file "%BASE_ENV%" --env-file "%RUNTIME_ENV%" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr logs -f pandocr-web
echo   docker compose --env-file "%BASE_ENV%" --env-file "%RUNTIME_ENV%" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr restart pandocr-web
echo   docker compose --env-file "%BASE_ENV%" --env-file "%RUNTIME_ENV%" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr down
echo.
pause
