FROM python:3.10-slim

WORKDIR /app

# The web container only serves FastAPI, converts Office files to PDF, and
# proxies requests to the official PaddleOCR-VL containers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libreoffice-core \
    libreoffice-impress \
    libreoffice-writer \
    libreoffice-common \
    default-jre \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY unlimited_ocr_adapter.py .
COPY ovisocr2_adapter.py .
COPY Dockerfile.ocr Dockerfile.unlimited-ocr Dockerfile.unlimited-ocr-sglang ./
COPY Dockerfile.ovisocr2 ./
COPY static/ ./static/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["python", "server.py"]
