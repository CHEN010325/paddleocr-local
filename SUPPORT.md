# Support

## 提交问题前

1. 更新到最新 Release 或 `main`。
2. 运行对应平台的预检或诊断。
3. 阅读 [QUICKSTART 常见问题](QUICKSTART.md#常见问题) 和[硬件兼容表](docs/compatibility.md)。
4. 搜索已有 Issues。

Windows：

```powershell
.\windows-one-click.bat -DryRun
$baseEnv = (Resolve-Path .\env.docker).Path
$runtimeEnv = & .\scripts\prepare-runtime-env.ps1 -BaseEnvFile $baseEnv
docker compose --env-file $baseEnv --env-file $runtimeEnv --profile "*" ps
docker compose --env-file $baseEnv --env-file $runtimeEnv --profile "*" logs --tail=200
```

macOS：

```bash
make doctor
./macos-one-click.command --dry-run
```

Linux：

```bash
./scripts/doctor.sh
runtime_env="$(bash ./scripts/prepare-runtime-env.sh env.docker)"
docker compose --env-file env.docker --env-file "$runtime_env" --profile "*" ps
docker compose --env-file env.docker --env-file "$runtime_env" --profile "*" logs --tail=200
```

helper 会复用 Git 已忽略的 `tmp/pandocr-runtime.env`，不会把 controller token 写入 `env.docker`。即使只查看状态或日志，也保留第二个 `--env-file`，避免复制命令用于后续重建时意外换密钥。

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
