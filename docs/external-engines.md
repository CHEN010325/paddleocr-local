# External OCR engines / 外部 OCR API

Issue #12: use the WebUI with independently deployed local or remote OCR services.
The existing one-click deployment remains the default when `PANDOCR_ENGINE_CONFIG`
is unset. Setting it selects an **API-only catalog**: no Docker access, GPU probe,
container startup, model switching or controller lease is required. Multiple
services can be available at once. This mode replaces, rather than merges with,
the managed model catalog.

## 快速开始

1. 复制根目录的 `engines.example.json` 为 `engines.local.json`。
2. 修改服务 URL、模型名称，删除不需要的示例引擎。模型必须由用户提前部署。
3. 安装 `requirements.txt`，设置配置文件路径，再启动 Web 服务：

```powershell
python -m pip install -r requirements.txt
$env:PANDOCR_ENGINE_CONFIG = "$PWD/engines.local.json"
python server.py
```

Linux / macOS:

```sh
export PANDOCR_ENGINE_CONFIG="$PWD/engines.local.json"
python server.py
```

打开 `http://127.0.0.1:8000`，在模型下拉框选择服务即可上传图片或 PDF。
API 模式不需要运行一键模型安装脚本。Office 转 PDF 仍使用原有转换服务。
取消设置该变量并重启，即可恢复原有部署模式。

## Configuration

`default` must match an engine `id`; IDs must be unique. Configuration is loaded
at startup; restart the WebUI after edits. Invalid configuration fails startup
instead of silently starting a local model. Keep the file under operator control.

| Field | Meaning |
| --- | --- |
| `id`, `label` | Stable selection ID and display name |
| `protocol` | `openai`, `paddle`, or `unified` |
| `url` | Complete HTTP(S) recognition endpoint, local or remote |
| `model` | Served model name; required for `openai`; optional upstream modelId for `unified` |
| `apiKeyEnv` | Optional environment variable containing the upstream bearer token |
| `healthUrl` | Optional health endpoint; a successful HTTP response means reachable |
| `timeout` | Total recognition deadline in seconds, including all PDF pages; default 300 |
| `prompt`, `maxTokens` | OpenAI vision instruction and output token limit |
| `pdfDpi`, `maxPages`, `maxPixels` | OpenAI PDF rendering limits; defaults 150, 50, 20,000,000 |

For authenticated services, add `"apiKeyEnv": "MY_OCR_API_KEY"` to the engine
and set that environment variable on the WebUI server. Credentials and service
URLs are never returned in the model catalog or stored in browser settings.
`PANDOCR_API_TOKEN` continues to protect the WebUI itself; it is separate from
the upstream token. URLs must not embed username/password credentials.

With no `healthUrl`, the UI displays “API configured”: connectivity is checked
when recognition runs. This is not a claim that a model has been loaded.
An offline health endpoint marks only its own service unavailable.

## Protocols and capabilities

- **openai**: posts vision messages to the exact `url` (usually
  `/v1/chat/completions`) with `model`, `max_tokens`, text and a PNG data URL.
  Suitable for independently deployed vLLM/llama.cpp services that implement
  this contract and run an image-capable model. PDFs are rendered lazily and
  recognized one page at a time, in order. Configure a model-appropriate prompt.
  The service must return `choices[0].message.content` as nonempty text.
  Truncated output is reported as an error. No bounding boxes or figure crops
  are invented; layout-specific UI options are disabled.
- **paddle**: posts base64 `file`, `fileType` (0 PDF, 1 image) and parsing options
  to a Paddle **layout-parsing** endpoint. Expects
  `result.layoutParsingResults[*].markdown.text`. This is not the PP-OCR text
  recognition protocol.
- **unified**: posts base64 `image`, `fileType`, options and optional upstream
  `modelId`; expects `layoutParsingResults[*].markdown.text`. Can connect to
  another instance's `/api/parse`. For a custom adapter exposing a different
  schema, add an adapter rather than assuming wire compatibility.

All adapters produce `{markdown, images, layoutParsingResults}` for the existing
preview, task persistence and export pipeline. Generic vision APIs do not
guarantee the same OCR accuracy or layout information as dedicated OCR pipelines.
`maxPages` and `maxPixels` apply to the local OpenAI renderer; document services
enforce their own PDF limits. The WebUI upload/concurrency limits still apply.

## API and deployment

`GET /api/models` returns the configured catalog and default. Entries advertise
`managed: false`, `capabilities`, and the common `/api/parse` endpoint.
`GET /api/model-runtime` reports independent service readiness without GPU
exclusivity. Deployment/switch endpoints return 409 in this mode.

```sh
curl http://127.0.0.1:8000/api/parse \
  -F modelId=my-vision-model -F file=@document.pdf
```

For Docker, build the existing Dockerfile and run only the WebUI container:

```sh
docker build -t pandocr-webui .
docker run --rm -p 127.0.0.1:8000:8000 \
  -e PANDOCR_HOST=0.0.0.0 \
  -e PANDOCR_ENGINE_CONFIG=/app/engines.json \
  -v "$PWD/engines.local.json:/app/engines.json:ro" \
  -v pandocr-data:/app/data pandocr-webui
```

No Docker socket or GPU mount is needed. A URL using `127.0.0.1` inside a
container refers to that container; use the OCR container's network hostname
or a reachable host address instead. Forward the `apiKeyEnv` variable with `-e`
if needed. Do not point a `unified` engine back at its own WebUI instance.

## Extending the adapter layer

`ocr_engines.py` owns configuration, transport and result normalization, without
importing `server.py` or any model deployment code. An adapter is an async
callable `(client, config, content_bytes, file_type, options) -> document_result`.
To add a protocol, extend the configuration protocol choices and `ADAPTERS`,
then add contract tests. The browser and deployment controller need no new
model-specific branch. Built-in managed adapters remain compatible with their
existing endpoints.
