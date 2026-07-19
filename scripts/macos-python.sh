#!/usr/bin/env bash

# Shared Python discovery for the macOS installers. Keep this file compatible
# with the Bash 3.2 that ships with macOS.

python_minor_version() {
  "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null
}

is_supported_macos_python() {
  local version
  version="$(python_minor_version "$1")" || return 1
  case "$version" in
    3.12|3.13) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_python_candidate() {
  local candidate="$1"
  if [[ "$candidate" == */* ]]; then
    [[ -x "$candidate" ]] || return 1
    printf "%s\n" "$candidate"
    return
  fi
  command -v "$candidate" 2>/dev/null
}

find_supported_macos_python() {
  local preferred_venv_python
  local candidate resolved brew_prefix

  if [[ -n "${PYTHON_BIN:-}" ]]; then
    resolved="$(resolve_python_candidate "$PYTHON_BIN")" || {
      echo "Configured PYTHON_BIN was not found or is not executable: $PYTHON_BIN" >&2
      return 1
    }
    if ! is_supported_macos_python "$resolved"; then
      echo "Configured PYTHON_BIN must use Python 3.12 or 3.13; found $(python_minor_version "$resolved" || echo unknown) at $resolved." >&2
      return 1
    fi
    printf "%s\n" "$resolved"
    return
  fi

  for preferred_venv_python in "$@"; do
    if [[ -n "$preferred_venv_python" && -x "$preferred_venv_python" ]] && is_supported_macos_python "$preferred_venv_python"; then
      printf "%s\n" "$preferred_venv_python"
      return
    fi
  done

  for candidate in python3.13 python3.12; do
    resolved="$(resolve_python_candidate "$candidate")" || continue
    if is_supported_macos_python "$resolved"; then
      printf "%s\n" "$resolved"
      return
    fi
  done

  if command -v brew >/dev/null 2>&1; then
    for candidate in python@3.13 python@3.12; do
      brew_prefix="$(brew --prefix "$candidate" 2>/dev/null)" || continue
      resolved="$brew_prefix/bin/${candidate/@/}"
      if [[ -x "$resolved" ]] && is_supported_macos_python "$resolved"; then
        printf "%s\n" "$resolved"
        return
      fi
    done
  fi

  for candidate in python3 python; do
    resolved="$(resolve_python_candidate "$candidate")" || continue
    if is_supported_macos_python "$resolved"; then
      printf "%s\n" "$resolved"
      return
    fi
  done

  return 1
}

install_supported_macos_python() {
  if ! command -v brew >/dev/null 2>&1; then
    return 1
  fi

  echo "No compatible Python was found. Installing Python 3.13 with Homebrew..."
  brew install python@3.13
}

select_supported_macos_python() {
  local preferred_venv_python="${1:-}"
  local selected

  if selected="$(find_supported_macos_python "$preferred_venv_python")"; then
    PYTHON_BIN="$selected"
    export PYTHON_BIN
    return
  fi

  echo "Python 3.12 or 3.13 is required. Python 3.14 is not supported by the current PaddlePaddle macOS package." >&2
  echo "Install Python 3.13, or set PYTHON_BIN to a compatible interpreter." >&2
  return 1
}
