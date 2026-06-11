.PHONY: help build deploy up down restart logs test clean mac-one-click mac-setup mac-setup-mlx mac-up mac-up-mlx mac-down mac-test mac-test-mlx mac-logs

# 默认目标
help:
	@echo "PandOCR - 可用命令:"
	@echo ""
	@echo "  make build      - 构建 NVIDIA Docker 镜像"
	@echo "  make deploy     - 部署 NVIDIA Docker 服务"
	@echo "  make up         - 启动 NVIDIA Docker 服务"
	@echo "  make down       - 停止 NVIDIA Docker 服务"
	@echo "  make restart    - 重启 NVIDIA Docker 服务"
	@echo "  make logs       - 查看 NVIDIA Docker 实时日志"
	@echo "  make test       - 测试 NVIDIA Docker 服务连接"
	@echo "  make clean      - 清理 Docker 资源"
	@echo "  make mac-one-click - Apple Silicon 一键部署并打开 WebUI"
	@echo "  make mac-setup  - 安装 Apple Silicon 本地环境"
	@echo "  make mac-setup-mlx - 安装 Apple Silicon MLX-VLM 提速环境"
	@echo "  make mac-up     - 启动 Apple Silicon 本地服务"
	@echo "  make mac-up-mlx - 启动 Apple Silicon MLX-VLM 提速服务"
	@echo "  make mac-down   - 停止 Apple Silicon 本地服务"
	@echo "  make mac-test   - 测试 Apple Silicon 本地服务"
	@echo "  make mac-test-mlx - 测试 Apple Silicon MLX-VLM 服务"
	@echo "  make mac-logs   - 查看 Apple Silicon 本地日志"
	@echo ""

# 构建镜像
build:
	@echo "🔨 构建 Docker 镜像..."
	docker compose --env-file env.txt pull
	docker compose --env-file env.txt build pandocr-web

# 部署（构建 + 启动）
deploy: build up
	@echo "🎉 部署完成！"
	@echo "访问地址: http://localhost:8000"

# 启动服务
up:
	@echo "▶️  启动服务..."
	docker compose --env-file env.txt up -d
	@echo "⏳ 等待服务就绪..."
	@sleep 5
	@make test

# 停止服务
down:
	@echo "🛑 停止服务..."
	docker compose down

# 重启服务
restart:
	@echo "🔄 重启服务..."
	docker compose restart

# 查看日志
logs:
	docker compose logs -f

# 只查看前端日志
logs-web:
	docker compose logs -f pandocr-web

# 只查看 API 日志
logs-api:
	docker compose logs -f paddleocr-vl-api

# 测试连接
test:
	@echo "🔍 测试服务连接..."
	@bash test-connection.sh

# 清理资源
clean:
	@echo "🧹 清理所有资源..."
	docker compose down -v
	docker system prune -f

# 查看服务状态
status:
	docker compose ps

# 进入前端容器
shell-web:
	docker compose exec pandocr-web /bin/bash

# 进入 API 容器
shell-api:
	docker compose exec paddleocr-vl-api /bin/bash

# Apple Silicon 本地部署
mac-one-click:
	bash scripts/macos-one-click.sh

mac-setup:
	bash scripts/setup-macos.sh

mac-setup-mlx:
	INSTALL_MLX_VLM=1 bash scripts/setup-macos.sh

mac-up:
	bash scripts/start-macos.sh

mac-up-mlx:
	PANDOCR_MACOS_BACKEND=mlx bash scripts/start-macos.sh

mac-down:
	bash scripts/stop-macos.sh

mac-test:
	bash scripts/test-macos.sh

mac-test-mlx:
	PANDOCR_MACOS_BACKEND=mlx bash scripts/test-macos.sh

mac-logs:
	tail -f logs/paddlex.log logs/pandocr-web.log logs/mlx-vlm.log
