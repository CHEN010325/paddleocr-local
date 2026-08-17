@echo off
REM PaddleOCR Local 连接测试脚本 (Windows)
setlocal

if "%PANDOCR_ENV_FILE%"=="" (
    set "ENV_FILE=env.txt"
) else (
    set "ENV_FILE=%PANDOCR_ENV_FILE%"
)
if not "%~1"=="" set "ENV_FILE=%~1"
if "%PANDOCR_RUNTIME_ENV_FILE%"=="" (
    set "RUNTIME_ENV=%~dp0tmp\pandocr-runtime.env"
) else (
    set "RUNTIME_ENV=%PANDOCR_RUNTIME_ENV_FILE%"
)

echo 🔍 测试 PaddleOCR Local 服务连接...
echo    Env file: %ENV_FILE%
echo.

if not exist "%ENV_FILE%" (
    echo ❌ 环境文件不存在: %ENV_FILE%
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prepare-runtime-env.ps1" -BaseEnvFile "%ENV_FILE%" -RuntimeEnvFile "%RUNTIME_ENV%" >nul
if errorlevel 1 (
    echo ❌ 无法准备持久 controller token
    exit /b 1
)
set "PANDOCR_MODEL_CONTROLLER_TOKEN="
set "COMPOSE=docker compose --env-file "%ENV_FILE%" --env-file "%RUNTIME_ENV%" --profile "*""

REM 测试前端服务
echo 1️⃣  测试前端服务 (localhost:8000)...
curl -s -o nul -w "%%{http_code}" http://localhost:8000/ | findstr "200" >nul
if not errorlevel 1 (
    echo    ✅ 前端服务正常
) else (
    echo    ❌ 前端服务异常
)

REM 测试模型运行时
echo 2️⃣  测试 WebUI 模型运行时...
curl -s -o nul -w "%%{http_code}" http://localhost:8000/api/model-runtime | findstr "200" >nul
if not errorlevel 1 (
    echo    ✅ 模型运行时接口正常
) else (
    echo    ❌ 模型运行时接口异常
)

REM 测试 PaddleOCR-VL API
echo 3️⃣  测试 PaddleOCR-VL API (localhost:8081)...
curl -s -o nul -w "%%{http_code}" http://localhost:8081/health | findstr "200" >nul
if not errorlevel 1 (
    echo    ✅ PaddleOCR-VL API 正常
) else (
    echo    ⚠️  PaddleOCR-VL API 未就绪（如果当前活跃模型是 PP-OCRv6，这是正常的）
)

REM 测试 PP-OCRv6 API
echo 4️⃣  测试 PP-OCRv6 API (localhost:8082)...
curl -s -o nul -w "%%{http_code}" http://localhost:8082/health | findstr "200" >nul
if not errorlevel 1 (
    echo    ✅ PP-OCRv6 API 正常
) else (
    echo    ⚠️  PP-OCRv6 API 未就绪（如果当前活跃模型是 PaddleOCR-VL，这是正常的）
)

REM 测试网络连接
echo 5️⃣  测试 Docker 内部网络...
%COMPOSE% exec -T pandocr-web curl -s -o /dev/null -w "%%{http_code}" http://localhost:8000/api/model-runtime 2>nul | findstr "200" >nul
if not errorlevel 1 (
    echo    ✅ 内部网络正常
) else (
    echo    ⚠️  内部网络可能未就绪
)

REM 测试 GPU 可用性
echo 6️⃣  测试 GPU 可用性...
%COMPOSE% run --rm --no-deps paddleocr-vlm-server nvidia-smi >nul 2>&1
if not errorlevel 1 (
    echo    ✅ GPU 可用
) else (
    echo    ⚠️  Docker GPU 当前不可用
)

echo.
echo 📊 服务状态总览:
%COMPOSE% ps -a

echo.
echo 💡 提示:
echo    - 如果服务异常，查看日志: docker compose --env-file "%ENV_FILE%" --env-file "%RUNTIME_ENV%" --profile "*" logs -f
echo    - 如果正在启动中，等待 3-5 分钟后重试
echo.
pause
