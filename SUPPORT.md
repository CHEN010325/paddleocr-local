# Support

## 提交问题前

1. 更新到最新 Release 或 `main`。
2. 运行对应平台的预检或诊断。
3. 阅读 [QUICKSTART 常见问题](QUICKSTART.md#常见问题) 和[硬件兼容表](docs/compatibility.md)。
4. 搜索已有 Issues。

Windows：

```powershell
.\windows-one-click.bat -DryRun
docker compose --env-file env.docker ps
docker compose --env-file env.docker logs --tail=200
```

macOS：

```bash
make doctor
./macos-one-click.command --dry-run
```

Linux：

```bash
./scripts/doctor.sh
docker compose --env-file env.docker ps
docker compose --env-file env.docker logs --tail=200
```

## 日志安全

提交前删除：

- API Token、控制器 Token 和私有镜像凭据
- 用户名、内网地址和不希望公开的路径
- 原始文档内容与 OCR 结果
- `data/tasks/`、模型缓存和完整环境文件

建议只附失败前后 100～200 行日志。

## 在哪里提问

- 可复现错误：Bug Report
- 硬件能否运行：Hardware Report
- 新能力建议：Feature Request
- 安全漏洞：按 [SECURITY.md](SECURITY.md) 私下报告，不要公开 Issue
