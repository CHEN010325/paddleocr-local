# CLI 与 Watch Folder

CLI 连接到已经启动的 PaddleOCR Local Web 服务，不会绕过服务端安全限制。

## 查看环境

```bash
python pandocr_cli.py doctor
```

设置了 API Token 时：

```bash
PANDOCR_API_TOKEN=your-token python pandocr_cli.py doctor
```

## 解析文件

```bash
python pandocr_cli.py parse invoice.pdf --model pp-ocrv6 --output output/cli
```

一次解析多个文件：

```bash
python pandocr_cli.py parse docs/a.pdf docs/b.png --model ovisocr2
```

如果模型尚未部署，可以显式允许部署：

```bash
python pandocr_cli.py parse invoice.pdf --model ovisocr2 --deploy
```

每个文件输出 `.md` 和原始 `.json`。

## 多模型对比

```bash
python pandocr_cli.py compare paper.pdf \
  --models paddleocr-vl-1.6,ovisocr2,hpd-parsing \
  --output output/compare
```

CLI 会顺序切换模型，适合单 GPU，并生成 `paper.comparison.md` 与 `paper.comparison.json`。

## 目录监听

处理目录中现有文件后持续等待新文件：

```bash
python pandocr_cli.py watch incoming --model pp-ocrv6 --output parsed
```

递归扫描：

```bash
python pandocr_cli.py watch incoming --recursive
```

只扫描一次，适合定时任务：

```bash
python pandocr_cli.py watch incoming --once
```

可使用 `PANDOCR_URL` 指向局域网服务。公网或局域网部署必须启用 API Token，并通过反向代理配置 TLS 和额外访问控制。
