"""Command-line and watch-folder client for a running PaddleOCR Local server."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import sys
import time
from pathlib import Path, PurePosixPath
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

    @staticmethod
    def model_is_exclusively_ready(runtime: dict[str, Any], model_id: str) -> bool:
        models = runtime.get("models") if isinstance(runtime.get("models"), dict) else {}
        status = models.get(model_id) if isinstance(models.get(model_id), dict) else {}
        if not status.get("ready") or runtime.get("exclusivityViolation") is True:
            return False

        for key in ("runningModelIds", "readyModelIds"):
            model_ids = runtime.get(key)
            if isinstance(model_ids, list) and set(model_ids) != {model_id}:
                return False

        active_model_id = runtime.get("activeModelId")
        if active_model_id and active_model_id != model_id:
            return False
        return True

    def ensure_model(self, model_id: str, *, deploy: bool = False, wait_seconds: int = 3600) -> dict[str, Any]:
        runtime = self.runtime()
        status = runtime.get("models", {}).get(model_id, {})
        if self.model_is_exclusively_ready(runtime, model_id):
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
            if self.model_is_exclusively_ready(runtime, model_id):
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
    top_level = result.get("markdown")
    if isinstance(top_level, str) and top_level.strip():
        return top_level.strip()
    if isinstance(top_level, dict):
        text = top_level.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

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


def result_images(value: Any) -> dict[str, str]:
    collected: dict[str, str] = {}
    if isinstance(value, dict):
        images = value.get("images")
        if isinstance(images, dict):
            for name, payload in images.items():
                if isinstance(name, str) and isinstance(payload, str):
                    collected.setdefault(name, payload)
        for key, nested in value.items():
            if key != "images":
                for name, payload in result_images(nested).items():
                    collected.setdefault(name, payload)
    elif isinstance(value, list):
        for item in value:
            for name, payload in result_images(item).items():
                collected.setdefault(name, payload)
    return collected


def safe_image_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    raw_parts = normalized.split("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in raw_parts)
    ):
        raise ValueError(f"Unsafe image path in OCR response: {name}")
    return path


def decode_image_payload(payload: str) -> bytes:
    encoded = payload.split(",", 1)[1] if payload.startswith("data:") and "," in payload else payload
    try:
        return base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("Invalid base64 image payload in OCR response") from err


def write_result_images(output_dir: Path, stem: str, result: dict[str, Any], markdown: str) -> tuple[str, list[str]]:
    resources: list[str] = []
    for name, payload in result_images(result).items():
        relative = safe_image_path(name)
        resource_root = output_dir / f"{stem}.assets"
        destination = resource_root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(decode_image_payload(payload))
        reference = PurePosixPath(f"{stem}.assets", *relative.parts).as_posix()
        markdown = markdown.replace(f"]({name})", f"]({reference})")
        markdown = markdown.replace(f'src="{name}"', f'src="{reference}"')
        markdown = markdown.replace(f"src='{name}'", f"src='{reference}'")
        resources.append(reference)
    return markdown, resources


def unique_output_stem(path: Path, model_id: str, used_stems: set[str]) -> str:
    stem = output_stem(path, model_id)
    if stem not in used_stems:
        used_stems.add(stem)
        return stem
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
    candidate = f"{stem}-{digest}"
    counter = 2
    while candidate in used_stems:
        candidate = f"{stem}-{digest}-{counter}"
        counter += 1
    used_stems.add(candidate)
    return candidate


def write_result(
    output_dir: Path,
    path: Path,
    model_id: str,
    result: dict[str, Any],
    elapsed: float,
    *,
    stem: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or output_stem(path, model_id)
    markdown = result_markdown(result)
    markdown, resources = write_result_images(output_dir, stem, result, markdown)
    (output_dir / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    return {
        "status": "success",
        "file": str(path),
        "model": model_id,
        "elapsedSeconds": round(elapsed, 3),
        "outputCharacters": len(markdown),
        "markdown": f"{stem}.md",
        "json": f"{stem}.json",
        "resources": resources,
    }


def parse_files(
    client: PandOCRClient,
    paths: list[Path],
    model_id: str,
    output_dir: Path,
    deploy: bool,
    *,
    used_stems: set[str] | None = None,
    stems_by_path: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    summaries = []
    used_stems = used_stems if used_stems is not None else set()
    stems_by_path = stems_by_path if stems_by_path is not None else {}
    for path in paths:
        started = time.monotonic()
        result = client.parse(path, model_id, deploy=deploy)
        path_key = str(path.resolve())
        stem = stems_by_path.get(path_key)
        if stem is None:
            stem = unique_output_stem(path, model_id, used_stems)
            stems_by_path[path_key] = stem
        summary = write_result(output_dir, path, model_id, result, time.monotonic() - started, stem=stem)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
    return summaries


def compare_file(client: PandOCRClient, path: Path, model_ids: list[str], output_dir: Path, deploy: bool) -> list[dict[str, Any]]:
    summaries = []
    for model_id in model_ids:
        started = time.monotonic()
        try:
            result = client.parse(path, model_id, deploy=deploy)
            summary = write_result(output_dir, path, model_id, result, time.monotonic() - started)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as err:
            summary = {
                "status": "error",
                "file": str(path),
                "model": model_id,
                "elapsedSeconds": round(time.monotonic() - started, 3),
                "outputCharacters": 0,
                "markdown": None,
                "json": None,
                "resources": [],
                "error": str(err),
            }
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
    report = [
        "# PaddleOCR Local comparison",
        "",
        f"- Source: {path.name}",
        f"- Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
        "| Model | Status | Parse time | Output characters | Markdown | JSON |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in summaries:
        markdown_link = f"[{item['markdown']}]({item['markdown']})" if item["markdown"] else "-"
        json_link = f"[{item['json']}]({item['json']})" if item["json"] else "-"
        report.append(
            f"| {item['model']} | {item['status']} | {item['elapsedSeconds']}s | {item['outputCharacters']} | "
            f"{markdown_link} | {json_link} |"
        )
        if item.get("error"):
            report.extend(["", f"> {item['model']}: {item['error']}"])
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
    used_stems: set[str] = set()
    stems_by_path: dict[str, str] = {}
    while True:
        for path in discover_files(folder, recursive):
            stat = path.stat()
            key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
            if key in processed:
                continue
            try:
                parse_files(
                    client,
                    [path],
                    model_id,
                    output_dir,
                    deploy,
                    used_stems=used_stems,
                    stems_by_path=stems_by_path,
                )
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
            if len(set(model_ids)) != len(model_ids):
                raise ValueError("compare model ids must be unique")
            summaries = compare_file(client, args.path, model_ids, args.output, args.deploy)
            if any(item["status"] == "error" for item in summaries):
                return 1
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
