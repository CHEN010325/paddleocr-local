# Release 流程

## 发布前

1. 更新 `CHANGELOG.md`。
2. 确认 README 中模型数量、硬件要求和命令一致。
3. 执行：

```bash
make check
docker compose --env-file env.docker config --quiet
docker compose -f docker-compose.yml -f docker-compose.release.yml --env-file env.docker config --quiet
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
PANDOCR_IMAGE_TAG=v0.1.0 docker compose \
  -f docker-compose.yml \
  -f docker-compose.release.yml \
  --env-file env.docker \
  up -d
```

模型推理服务仍按所选模型使用官方镜像或本地模型适配镜像。预构建支持镜像不会打包第三方模型权重。
