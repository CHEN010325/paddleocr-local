"""Command-line and watch-folder client for a running PaddleOCR Local server."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import httpx


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".tiff",
    ".tif",
    ".gif",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
}
OFFICE_EXTENSIONS = {".ppt", ".pptx", ".doc", ".docx"}


class PandOCRClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 3600) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def models(self) -> dict[str, Any]:
        response = self.client.get("/api/models")
        response.raise_for_status()
        return response.json()

    def runtime(self) -> dict[str, Any]:
        response = self.client.get("/api/model-runtime")
        response.raise_for_status()
        return response.json()

    def model_map(self) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in self.models().get("data", []) if isinstance(item, dict) and item.get("id")}

    def ensure_model(self, model_id: str, *, deploy: bool = False, wait_seconds: int = 3600) -> dict[str, Any]:
        runtime = self.runtime()
        status = runtime.get("models", {}).get(model_id, {})
        if status.get("ready"):
            return runtime
        endpoint = "/api/model-runtime/deploy" if status.get("state") == "missing" else "/api/model-runtime/switch"
        if status.get("state") == "missing" and not deploy:
            raise RuntimeError(f"{model_id} is not deployed; rerun with --deploy or use the WebUI installer")
        response = self.client.post(endpoint, json={"modelId": model_id})
        response.raise_for_status()
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            runtime = self.runtime()
            status = runtime.get("models", {}).get(model_id, {})
            if status.get("ready"):
                return runtime
            operation = runtime.get("operation") or {}
            if operation.get("targetModelId") == model_id and operation.get("state") == "error":
                raise RuntimeError(operation.get("message") or f"Failed to start {model_id}")
            time.sleep(2.5)
        raise TimeoutError(f"Timed out waiting for {model_id}")

    def convert_office(self, path: Path) -> tuple[str, bytes, str]:
        with path.open("rb") as stream:
            response = self.client.post(
                "/api/convert/to-pdf",
                files={"file": (path.name, stream, "application/octet-stream")},
            )
        response.raise_for_status()
        return f"{path.stem}.pdf", response.content, "application/pdf"

    def parse(self, path: Path, model_id: str, *, deploy: bool = False) -> dict[str, Any]:
        model = self.model_map().get(model_id)
        if not model:
            raise ValueError(f"Unknown model: {model_id}")
        self.ensure_model(model_id, deploy=deploy)
        filename = path.name
        content_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
        content = path.read_bytes()
        if path.suffix.lower() in OFFICE_EXTENSIONS:
            filename, content, content_type = self.convert_office(path)
        file_type = 0 if filename.lower().endswith(".pdf") else 1
        fields = {
            "fileType": str(file_type),
            "useLayoutDetection": "true",
            "useChartRecognition": "false",
            "useDocUnwarping": "false",
            "useDocOrientationClassify": "false",
            "useSealRecognition": "true",
            "formatBlockContent": "true",
            "showFormulaNumber": "true",
            "markdownIgnoreLabels": json.dumps(["number", "footnote", "header", "header_image", "footer", "footer_image", "aside_text"]),
            "modelId": model_id,
        }
        response = self.client.post(
            "/api/parse",
            data=fields,
            files={"file": (filename, content, content_type)},
        )
        response.raise_for_status()
        return response.json()


def markdown_fragments(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        markdown = value.get("markdown")
        if isinstance(markdown, str) and markdown.strip():
            yield markdown.strip()
        elif isinstance(markdown, dict) and isinstance(markdown.get("text"), str) and markdown["text"].strip():
            yield markdown["text"].strip()
        for key, nested in value.items():
            if key != "markdown":
                yield from markdown_fragments(nested)
    elif isinstance(value, list):
        for item in value:
            yield from markdown_fragments(item)


def result_markdown(result: dict[str, Any]) -> str:
    seen: set[str] = set()
    fragments = []
    for fragment in markdown_fragments(result):
        if fragment not in seen:
            seen.add(fragment)
            fragments.append(fragment)
    return "\n\n".join(fragments)


def output_stem(path: Path, model_id: str) -> str:
    safe_model = "".join(char if char.isalnum() or char in "-_" else "-" for char in model_id)
    return f"{path.stem}.{safe_model}"


def write_result(output_dir: Path, path: Path, model_id: str, result: dict[str, Any], elapsed: float) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(path, model_id)
    markdown = result_markdown(result)
    (output_dir / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    return {
        "file": str(path),
        "model": model_id,
        "elapsedSeconds": round(elapsed, 3),
        "outputCharacters": len(markdown),
        "markdown": f"{stem}.md",
        "json": f"{stem}.json",
    }


def parse_files(client: PandOCRClient, paths: list[Path], model_id: str, output_dir: Path, deploy: bool) -> list[dict[str, Any]]:
    summaries = []
    for path in paths:
        started = time.monotonic()
        result = client.parse(path, model_id, deploy=deploy)
        summary = write_result(output_dir, path, model_id, result, time.monotonic() - started)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
    return summaries


def compare_file(client: PandOCRClient, path: Path, model_ids: list[str], output_dir: Path, deploy: bool) -> list[dict[str, Any]]:
    summaries = []
    for model_id in model_ids:
        summaries.extend(parse_files(client, [path], model_id, output_dir, deploy))
    report = [
        "# PaddleOCR Local comparison",
        "",
        f"- Source: {path.name}",
        f"- Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
        "| Model | Parse time | Output characters | Markdown | JSON |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for item in summaries:
        report.append(
            f"| {item['model']} | {item['elapsedSeconds']}s | {item['outputCharacters']} | "
            f"[{item['markdown']}]({item['markdown']}) | [{item['json']}]({item['json']}) |"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{path.stem}.comparison.md").write_text("\n".join(report), encoding="utf-8")
    (output_dir / f"{path.stem}.comparison.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summaries


def discover_files(folder: Path, recursive: bool) -> list[Path]:
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def watch_folder(
    client: PandOCRClient,
    folder: Path,
    model_id: str,
    output_dir: Path,
    deploy: bool,
    recursive: bool,
    once: bool,
    interval: float,
) -> None:
    processed: set[tuple[str, int, int]] = set()
    while True:
        for path in discover_files(folder, recursive):
            stat = path.stat()
            key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
            if key in processed:
                continue
            try:
                parse_files(client, [path], model_id, output_dir, deploy)
                processed.add(key)
            except Exception as err:  # keep watching after one bad file
                print(f"Failed: {path}: {err}", file=sys.stderr)
        if once:
            return
        time.sleep(max(interval, 0.5))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pandocr", description="PaddleOCR Local CLI")
    parser.add_argument("--url", default=os.getenv("PANDOCR_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.getenv("PANDOCR_API_TOKEN"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Print server, model, and runtime information")

    parse_parser = subparsers.add_parser("parse", help="Parse one or more files")
    parse_parser.add_argument("paths", nargs="+", type=Path)
    parse_parser.add_argument("--model", default="paddleocr-vl-1.6")
    parse_parser.add_argument("--output", type=Path, default=Path("output/cli"))
    parse_parser.add_argument("--deploy", action="store_true")

    compare_parser = subparsers.add_parser("compare", help="Run one file through multiple models")
    compare_parser.add_argument("path", type=Path)
    compare_parser.add_argument("--models", required=True, help="Comma-separated model ids")
    compare_parser.add_argument("--output", type=Path, default=Path("output/compare"))
    compare_parser.add_argument("--deploy", action="store_true")

    watch_parser = subparsers.add_parser("watch", help="Parse new or changed files in a folder")
    watch_parser.add_argument("folder", type=Path)
    watch_parser.add_argument("--model", default="pp-ocrv6")
    watch_parser.add_argument("--output", type=Path, default=Path("output/watch"))
    watch_parser.add_argument("--deploy", action="store_true")
    watch_parser.add_argument("--recursive", action="store_true")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--interval", type=float, default=3.0)
    return parser


def validate_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = PandOCRClient(args.url, args.token)
    try:
        if args.command == "doctor":
            print(json.dumps({"models": client.models(), "runtime": client.runtime()}, ensure_ascii=False, indent=2))
        elif args.command == "parse":
            validate_paths(args.paths)
            parse_files(client, args.paths, args.model, args.output, args.deploy)
        elif args.command == "compare":
            validate_paths([args.path])
            model_ids = [item.strip() for item in args.models.split(",") if item.strip()]
            if len(model_ids) < 2:
                raise ValueError("compare requires at least two model ids")
            compare_file(client, args.path, model_ids, args.output, args.deploy)
        elif args.command == "watch":
            if not args.folder.is_dir():
                raise NotADirectoryError(args.folder)
            watch_folder(client, args.folder, args.model, args.output, args.deploy, args.recursive, args.once, args.interval)
        return 0
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as err:
        print(f"pandocr: {err}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
