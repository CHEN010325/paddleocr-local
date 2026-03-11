# 使用官方 Python 3.10 镜像作为基础镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 1. 安装系统依赖 (关键修改)
# 1. 安装系统依赖
# PaddleOCR 和 OpenCV 需要 libgl1, libgomp1, libglib2.0 等库
# LibreOffice 用于 PPT/PPTX 转 PDF (需要 default-jre)
# 中文字体 (fonts-noto-cjk) 用于正确渲染中文文档
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libreoffice-core \
    libreoffice-impress \
    libreoffice-writer \
    libreoffice-common \
    default-jre \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 2. 优先安装适配 Blackwell 架构 (CUDA 12.x) 的 PaddlePaddle (关键修改)
# 使用官方 Nightly 源，覆盖默认的 pip 源
RUN pip install --pre paddlepaddle-gpu \
    -i https://www.paddlepaddle.org.cn/packages/nightly/cu129/ \
    --no-cache-dir

# 3. 安装 PaddleX 和 OCR 插件 (关键修改)
# 你的 requirements.txt 里似乎漏掉了这些核心库
RUN pip install "paddlex[ocr]" --no-cache-dir

# 复制依赖文件
COPY requirements.txt .

# 4. 安装其他 Web 依赖 (fastapi, uvicorn 等)
# 使用国内镜像源加速 (可选，根据网络情况)
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY server.py .
COPY static/ ./static/

# 创建 OCR 图片存储目录
RUN mkdir -p static/ocr_images

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# 启动应用
CMD ["python", "server.py"]
