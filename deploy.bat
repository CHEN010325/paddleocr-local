@echo off
REM Deploy PaddleOCR Local with Docker Compose (Windows)

echo Deploying PaddleOCR Local...

if not exist "env.txt" (
    echo env.txt does not exist. Run build.bat or create the env file first.
    pause
    exit /b 1
)

for /f %%T in ('powershell -NoProfile -Command "[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()"') do set "PANDOCR_MODEL_CONTROLLER_TOKEN=%%T"

docker compose --env-file env.txt up -d --no-start
docker compose --env-file env.txt stop pandocr-web pandocr-controller pandocr-office-converter paddleocr-vl-api paddleocr-vlm-server paddleocr-ocr-api >nul 2>&1
docker compose --env-file env.txt start pandocr-controller pandocr-office-converter pandocr-web

echo Waiting for services...
timeout /t 5 /nobreak >nul

echo.
echo Service status:
docker compose --env-file env.txt ps

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
    echo paddleocr-vl-api ^(8081^) standby or starting
)

curl -f http://localhost:8082/health >nul 2>&1
if not errorlevel 1 (
    echo paddleocr-ocr-api ^(8082^) OK
) else (
    echo paddleocr-ocr-api ^(8082^) standby or starting
)

echo.
echo Done.
echo WebUI: http://localhost:8000
echo VL API:  http://localhost:8081
echo OCR API: http://localhost:8082
echo Default model is started by pandocr-controller. Other models stay stopped until selected in the UI.
echo.
echo Useful commands:
echo   docker compose --env-file env.txt logs -f
echo   docker compose --env-file env.txt logs -f pandocr-web
echo   docker compose --env-file env.txt restart pandocr-web
echo   docker compose --env-file env.txt down
echo.
pause
