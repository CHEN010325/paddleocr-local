# CPU 部署

无需 NVIDIA 显卡、CUDA 或 NVIDIA Container Toolkit。CPU 入口使用 PP-OCRv6
文字识别，支持图片和 PDF、任务历史以及现有导出；不等同于 VL 模型的表格结构、
公式和复杂版面理解。Office 输入需要另行配置 Office 转换服务。

## Docker（Windows / Linux x86-64）

安装并启动 Docker（Windows 使用 Linux containers），在项目根目录运行：

```sh
docker compose -f docker-compose.cpu.yml up -d --build --wait --wait-timeout 1200
```

打开 http://127.0.0.1:8000 。首次启动下载模型，需要联网；模型缓存在独立卷中。
这个文件是独立部署文件，不要和 GPU Compose 文件合并。没有 GPU 设备声明，
不挂载 Docker socket，不启动模型控制器。识别服务健康后才启动 WebUI。
端口被占用时设置 `PANDOCR_CPU_PORT=8001`。Web 默认仅绑定本机。

```sh
docker compose -f docker-compose.cpu.yml logs --tail 100 ocr
docker compose -f docker-compose.cpu.yml down
```

`down` 保留模型和任务卷。每批 1 页开始测试，大尺寸图片和预处理会增加耗时。
建议先用小文档测量速度与内存，再决定批量大小。

## 独立 Python 环境

使用 Python 3.12，在独立虚拟环境安装模型服务，避免影响其他项目：

```sh
python -m venv .venv-cpu
# Windows: .venv-cpu\Scripts\activate
# Linux: source .venv-cpu/bin/activate
python -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install "paddlex[ocr,serving]==3.7.1"
paddlex --serve --pipeline pipeline_config_ocr_cpu.yaml --device cpu --host 127.0.0.1 --port 8082
```

另开终端，在已安装 `requirements.txt` 的 WebUI 环境中启动：

```powershell
$env:PANDOCR_MODEL_CONTROL = 'none'
$env:PANDOCR_MODEL_CATALOG = 'pp-ocrv6'
$env:PADDLE_OCR_SERVICE_URL = 'http://127.0.0.1:8082/ocr'
python server.py
```

Linux 使用同名 `export NAME=value` 环境变量。

CPU 专用配置显式使用 `run_mode: paddle`，避开 Paddle 3.3.0 的 oneDNN/PIR
算子转换故障。不要只设 `FLAGS_use_mkldnn=0`：推理引擎配置仍可能启用 oneDNN。
背景见 [Paddle 上游问题 #77340](https://github.com/PaddlePaddle/Paddle/issues/77340)。

发布验证范围和结果见 [Issue 验收记录](issue-validation.md)。
