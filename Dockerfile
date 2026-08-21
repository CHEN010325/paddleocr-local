FROM python:3.10-slim@sha256:a45c323edaa44976ef63b9a85e0d3bd7bbf31676029dccfbc119f88a65311852

WORKDIR /app

# The image serves the restricted Web/API process and the isolated model
# controller. Office conversion lives in its own image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY controller.py .
COPY unlimited_ocr_adapter.py .
COPY ovisocr2_adapter.py .
COPY hpd_parsing_adapter.py .
COPY navidc_ocr_adapter.py .
COPY Dockerfile.ocr Dockerfile.unlimited-ocr Dockerfile.unlimited-ocr-sglang ./
COPY Dockerfile.ovisocr2 ./
COPY Dockerfile.hpd-parsing-adapter ./
COPY Dockerfile.navidc-ocr ./
COPY start-hpd-parsing.sh ./
COPY static/ ./static/

# Embed non-secret build provenance so comparison reports can identify the
# exact application image. Runtime environment variables may override these.
ARG PANDOCR_APP_VERSION=0.2.0
ARG PANDOCR_GIT_COMMIT=
ENV PANDOCR_APP_VERSION=${PANDOCR_APP_VERSION} \
    PANDOCR_GIT_COMMIT=${PANDOCR_GIT_COMMIT}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["python", "server.py"]
