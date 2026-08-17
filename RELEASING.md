# Release 流程

## 发布前

1. 更新 `CHANGELOG.md`。
2. 确认 README 中模型数量、硬件要求和命令一致。
3. 执行：

```bash
make check
runtime_env="$(bash ./scripts/prepare-runtime-env.sh env.docker)"
unset PANDOCR_MODEL_CONTROLLER_TOKEN
docker compose --env-file env.docker --env-file "$runtime_env" --profile "*" config --quiet
docker compose -f docker-compose.yml -f docker-compose.release.yml --env-file env.docker --env-file "$runtime_env" --profile "*" config --quiet
```

4. 在 Draft PR 中确认 CI 和界面截图。
5. 合并到 `main` 后创建签名或普通 tag：

```bash
git tag -a v0.1.0 -m "PaddleOCR Local v0.1.0"
git push origin v0.1.0
```

Tag 会触发 Release 工作流：

- 构建并发布 Web/控制器镜像
- 构建并发布 Office 转换器镜像
- 构建并发布 HPD-Parsing 适配器镜像
- 根据提交自动生成 GitHub Release Notes

## 使用预构建支持镜像

```bash
export PANDOCR_IMAGE_TAG=v0.1.0
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
