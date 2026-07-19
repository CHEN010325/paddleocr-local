#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/macos-python.sh
source "$ROOT_DIR/scripts/macos-python.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

make_fake_python() {
  local path="$1"
  local version="$2"
  mkdir -p "$(dirname "$path")"
  {
    # Use an absolute shell so tests can fully isolate PATH from the CI runner.
    printf '%s\n' '#!/bin/bash'
    printf 'if [[ "${1:-}" == "-c" ]]; then printf "%%s\\n" "%s"; exit 0; fi\n' "$version"
    printf '%s\n' 'exit 99'
  } > "$path"
  chmod +x "$path"
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [[ "$actual" != "$expected" ]]; then
    printf "FAIL: %s\nexpected: %s\nactual:   %s\n" "$label" "$expected" "$actual" >&2
    exit 1
  fi
  printf "PASS: %s\n" "$label"
}

case_default_314_with_313() {
  local fake_bin="$TMP_ROOT/default-314-with-313"
  local selected
  make_fake_python "$fake_bin/python3" "3.14"
  make_fake_python "$fake_bin/python3.13" "3.13"
  selected="$(PATH="$fake_bin:/usr/bin:/bin" PYTHON_BIN= find_supported_macos_python)"
  assert_eq "$fake_bin/python3.13" "$selected" "Python 3.13 wins when default python3 is 3.14"
}

case_supported_existing_venv() {
  local fake_bin="$TMP_ROOT/supported-existing-venv"
  local venv_python="$TMP_ROOT/existing/.venv-macos/bin/python"
  local selected
  make_fake_python "$fake_bin/python3.13" "3.13"
  make_fake_python "$venv_python" "3.12"
  selected="$(PATH="$fake_bin:/usr/bin:/bin" PYTHON_BIN= find_supported_macos_python "$venv_python")"
  assert_eq "$venv_python" "$selected" "supported existing virtual environment is reused"
}

case_unsupported_existing_venv() {
  local fake_bin="$TMP_ROOT/unsupported-existing-venv"
  local venv_python="$TMP_ROOT/old/.venv-macos/bin/python"
  local selected
  make_fake_python "$fake_bin/python3.13" "3.13"
  make_fake_python "$venv_python" "3.14"
  selected="$(PATH="$fake_bin:/usr/bin:/bin" PYTHON_BIN= find_supported_macos_python "$venv_python")"
  assert_eq "$fake_bin/python3.13" "$selected" "unsupported virtual environment is replaced by Python 3.13"
}

case_secondary_venv_fallback() {
  local primary_python="$TMP_ROOT/secondary-fallback/.venv-macos/bin/python"
  local secondary_python="$TMP_ROOT/secondary-fallback/.venv-unlimited-ocr-macos/bin/python"
  local selected
  make_fake_python "$primary_python" "3.14"
  make_fake_python "$secondary_python" "3.13"
  selected="$(PATH="/usr/bin:/bin" PYTHON_BIN= find_supported_macos_python "$primary_python" "$secondary_python")"
  assert_eq "$secondary_python" "$selected" "compatible secondary virtual environment can repair the primary environment"
}

case_explicit_supported_python() {
  local fake_bin="$TMP_ROOT/explicit-supported"
  local selected
  make_fake_python "$fake_bin/custom-python" "3.12"
  selected="$(PATH="/usr/bin:/bin" PYTHON_BIN="$fake_bin/custom-python" find_supported_macos_python)"
  assert_eq "$fake_bin/custom-python" "$selected" "supported PYTHON_BIN override is honored"
}

case_explicit_unsupported_python() {
  local fake_bin="$TMP_ROOT/explicit-unsupported"
  make_fake_python "$fake_bin/custom-python" "3.14"
  if PATH="/usr/bin:/bin" PYTHON_BIN="$fake_bin/custom-python" find_supported_macos_python >/dev/null 2>&1; then
    echo "FAIL: unsupported PYTHON_BIN must be rejected" >&2
    exit 1
  fi
  echo "PASS: unsupported PYTHON_BIN is rejected"
}

case_no_supported_python() {
  local fake_bin="$TMP_ROOT/no-supported"
  make_fake_python "$fake_bin/python3" "3.14"
  if PATH="$fake_bin" PYTHON_BIN= find_supported_macos_python >/dev/null 2>&1; then
    echo "FAIL: discovery must fail when only Python 3.14 exists" >&2
    exit 1
  fi
  echo "PASS: discovery fails when only Python 3.14 exists"
}

case_generic_python_fallback() {
  local fake_bin="$TMP_ROOT/generic-python-fallback"
  local selected
  make_fake_python "$fake_bin/python3" "3.14"
  make_fake_python "$fake_bin/python" "3.12"
  selected="$(PATH="$fake_bin" PYTHON_BIN= find_supported_macos_python)"
  assert_eq "$fake_bin/python" "$selected" "compatible generic python is used when python3 is unsupported"
}

case_homebrew_keg_python() {
  local fake_bin="$TMP_ROOT/homebrew-keg/bin"
  local keg_prefix="$TMP_ROOT/homebrew-keg/opt/python@3.13"
  local selected
  make_fake_python "$fake_bin/python3" "3.14"
  make_fake_python "$keg_prefix/bin/python3.13" "3.13"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf 'if [[ "${1:-}" == "--prefix" && "${2:-}" == "python@3.13" ]]; then printf "%%s\\n" "%s"; exit 0; fi\n' "$keg_prefix"
    printf '%s\n' 'exit 1'
  } > "$fake_bin/brew"
  chmod +x "$fake_bin/brew"
  selected="$(PATH="$fake_bin:/usr/bin:/bin" PYTHON_BIN= find_supported_macos_python)"
  assert_eq "$keg_prefix/bin/python3.13" "$selected" "Homebrew keg-only Python 3.13 is discovered"
}

case_default_314_with_313
case_supported_existing_venv
case_unsupported_existing_venv
case_secondary_venv_fallback
case_explicit_supported_python
case_explicit_unsupported_python
case_no_supported_python
case_generic_python_fallback
case_homebrew_keg_python
