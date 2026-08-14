#!/usr/bin/env python3

from __future__ import annotations

import importlib
import importlib.metadata
import re
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = ROOT_DIR / "requirements-macos-mlx.txt"
PIN_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*==\s*([^\s#]+)")


def required_versions() -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        match = PIN_PATTERN.match(line)
        if match:
            pins.append(match.groups())
    return pins


def fail(message: str) -> None:
    print(f"MLX runtime check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_runtime() -> str:
    if sys.version_info[:2] not in {(3, 12), (3, 13)}:
        fail(
            f"Python 3.12 or 3.13 is required, but this environment uses "
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}."
        )

    installed_versions: list[tuple[str, str]] = []
    for package, expected in required_versions():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            fail(f"{package} is not installed.")
        if installed != expected:
            fail(f"{package}=={expected} is required, but {installed} is installed.")
        installed_versions.append((package, installed))

    for module_name in ("mlx_lm", "mlx_vlm"):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            fail(f"cannot import {module_name}: {exc.__class__.__name__}: {exc}")

    return ", ".join(f"{package}={version}" for package, version in installed_versions)


def main() -> None:
    versions = check_runtime()
    print(f"MLX runtime check passed ({versions})")


if __name__ == "__main__":
    main()
