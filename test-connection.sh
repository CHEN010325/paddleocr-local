#!/bin/bash

# PaddleOCR Local 连接测试脚本
set -u

ENV_FILE="${PANDOCR_ENV_FILE:-${1:-env.txt}}"
RUNTIME_ENV="${PANDOCR_RUNTIME_ENV_FILE:-tmp/pandocr-runtime.env}"

echo "🔍 测试 PaddleOCR Local 服务连接..."
echo "   Env file: $ENV_FILE"
echo ""

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ 环境文件不存在: $ENV_FILE"
    exit 1
fi

RUNTIME_ENV="$(bash ./scripts/prepare-runtime-env.sh "$ENV_FILE" "$RUNTIME_ENV")" || exit 1
unset PANDOCR_MODEL_CONTROLLER_TOKEN
COMPOSE=(docker compose --env-file "$ENV_FILE" --env-file "$RUNTIME_ENV" --profile "*")

# 测试前端服务
echo "1️⃣  测试前端服务 (localhost:8000)..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ | grep -q "200"; then
    echo "   ✅ 前端服务正常"
else
    echo "   ❌ 前端服务异常"
fi

# 测试模型运行时
echo "2️⃣  测试 WebUI 模型运行时..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/model-runtime | grep -q "200"; then
    echo "   ✅ 模型运行时接口正常"
else
    echo "   ❌ 模型运行时接口异常"
fi

# 测试 PaddleOCR-VL API
echo "3️⃣  测试 PaddleOCR-VL API (localhost:8081)..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/health | grep -q "200"; then
    echo "   ✅ PaddleOCR-VL API 正常"
else
    echo "   ⚠️  PaddleOCR-VL API 未就绪（如果当前活跃模型是 PP-OCRv6，这是正常的）"
fi

# 测试 PP-OCRv6 API
echo "4️⃣  测试 PP-OCRv6 API (localhost:8082)..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/health | grep -q "200"; then
    echo "   ✅ PP-OCRv6 API 正常"
else
    echo "   ⚠️  PP-OCRv6 API 未就绪（如果当前活跃模型是 PaddleOCR-VL，这是正常的）"
fi

# 测试网络连接
echo "5️⃣  测试 Docker 内部网络..."
if "${COMPOSE[@]}" exec -T pandocr-web curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/model-runtime | grep -q "200"; then
    echo "   ✅ 内部网络正常"
else
    echo "   ⚠️  内部网络可能未就绪"
fi

# 测试 GPU 可用性
echo "6️⃣  测试 GPU 可用性..."
if "${COMPOSE[@]}" run --rm --no-deps paddleocr-vlm-server nvidia-smi &> /dev/null; then
    echo "   ✅ GPU 可用"
else
    echo "   ⚠️  Docker GPU 当前不可用"
fi

echo ""
echo "📊 服务状态总览:"
"${COMPOSE[@]}" ps -a

echo ""
echo "💡 提示:"
echo "   - 如果服务异常，查看日志: docker compose --env-file $ENV_FILE --env-file $RUNTIME_ENV --profile \"*\" logs -f"
echo "   - 如果正在启动中，等待 3-5 分钟后重试"
