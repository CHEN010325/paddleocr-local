# Release 流程

当前 Windows/Linux 默认产品线包含六个模型：PaddleOCR-VL 1.6、PP-OCRv6、Unlimited-OCR-2、OvisOCR2、HPD-Parsing 和 NaviDC-OCR。Unlimited-OCR-2 已替换第一代，支持 Transformers 和官方专用 SGLang 后端。发布前分别核验两条路线；原生 macOS MPS 尚未完成第二代验收。

## 发布前

本轮问题验收见 [Issue 验证记录](docs/issue-validation.md)。CPU 新环境验收命令：

```sh
docker compose -f docker-compose.cpu.yml up -d --build --wait --wait-timeout 1200
python scripts/smoke-cpu.py
```

必须确认 CPU smoke 和三平台 CI 实际通过后再发布。仅添加工作流不等于已经验证对应平台。

1. 更新 `CHANGELOG.md`。
2. 确认 README 中模型数量、硬件要求和命令一致。
3. 确认 `README.md`、`README.en.md`、`QUICKSTART.md` 和 `docs/compatibility.md` 的支持矩阵一致。
4. 执行：

```bash
make check
runtime_env="$(bash ./scripts/prepare-runtime-env.sh env.docker)"
unset PANDOCR_MODEL_CONTROLLER_TOKEN
docker compose --env-file env.docker --env-file "$runtime_env" --profile "*" config --quiet
docker compose -f docker-compose.yml -f docker-compose.release.yml --env-file env.docker --env-file "$runtime_env" --profile "*" config --quiet
```

5. 在 Draft PR 中确认 CI、界面截图和至少一份真实硬件报告。
6. 合并到 `main` 后创建签名或普通 tag：

```bash
git tag -a v0.2.0 -m "PaddleOCR Local v0.2.0"
git push origin v0.2.0
```

Tag 会触发 Release 工作流：

- 构建并发布 Web/控制器镜像
- 构建并发布 Office 转换器镜像
- 构建并发布 HPD-Parsing 适配器镜像
- 构建并发布 NaviDC-OCR 适配器镜像
- 根据提交自动生成 GitHub Release Notes

## 使用预构建支持镜像

```bash
export PANDOCR_IMAGE_TAG=v0.2.0
runtime_env="$(bash ./scripts/prepare-runtime-env.sh env.docker)"
unset PANDOCR_MODEL_CONTROLLER_TOKEN

docker compose \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --env-file env.docker \
  --env-file "$runtime_env" \
  --profile paddleocr-vl \
  up -d --no-start \
  pandocr-controller pandocr-office-converter pandocr-web \
  paddleocr-vlm-server paddleocr-vl-api

docker compose \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --env-file env.docker \
  --env-file "$runtime_env" \
  --profile paddleocr-vl \
  start pandocr-controller pandocr-office-converter pandocr-web
```

`tmp/pandocr-runtime.env` 已被 Git 忽略。每个新 shell 都重新调用 helper：它会复用持久 token，而不是为单次 Compose 调用生成新值；空值、占位值或与持久值不同的环境变量会直接中止。不要把随机密钥写入 `env.docker` 或其他 tracked 文件。

模型推理服务仍按所选模型使用官方镜像或本地模型适配镜像。预构建支持镜像不会打包第三方模型权重。示例只启用 `paddleocr-vl` profile；如需其他模型，应切换到对应 profile，仍由控制器确保任意时刻只有一个逻辑模型运行。
