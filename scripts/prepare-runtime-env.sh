#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BASE_ENV_FILE="${1:-${PROJECT_DIR}/env.txt}"
RUNTIME_ENV_FILE="${2:-${PROJECT_DIR}/tmp/pandocr-runtime.env}"
TOKEN_KEY="PANDOCR_MODEL_CONTROLLER_TOKEN"

case "${BASE_ENV_FILE}" in
    /*) ;;
    *) BASE_ENV_FILE="${PWD}/${BASE_ENV_FILE}" ;;
esac
case "${RUNTIME_ENV_FILE}" in
    /*) ;;
    *) RUNTIME_ENV_FILE="${PWD}/${RUNTIME_ENV_FILE}" ;;
esac

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

validate_token() {
    local token="$1"
    local source="$2"
    local normalized="${token#"${token%%[![:space:]]*}"}"
    normalized="${normalized%"${normalized##*[![:space:]]}"}"
    [ -n "${normalized}" ] || fail "${source} contains an empty ${TOKEN_KEY}."
    case "${normalized}" in
        pandocr-internal-controller-v1|change-this-to-a-random-long-value|请替换为随机长值)
            fail "${source} contains an unsafe placeholder ${TOKEN_KEY}."
            ;;
    esac
    [ "${token}" = "${normalized}" ] \
        || fail "${source} contains leading or trailing whitespace in ${TOKEN_KEY}."
    [ "${#normalized}" -ge 32 ] \
        && [ "${#normalized}" -le 256 ] \
        && [[ "${normalized}" =~ ^[A-Za-z0-9._~-]+$ ]] \
        || fail "${source} must use 32-256 URL-safe ASCII characters for ${TOKEN_KEY}."
    case "${token}" in
        *$'\n'*|*$'\r'*) fail "${source} contains an invalid multi-line ${TOKEN_KEY}." ;;
    esac
}

read_runtime_token() {
    local line
    local token=''
    local matches=0
    while IFS= read -r line || [ -n "${line}" ]; do
        line="${line%$'\r'}"
        case "${line}" in
            "${TOKEN_KEY}="*)
                matches=$((matches + 1))
                token="${line#*=}"
                ;;
        esac
    done < "${RUNTIME_ENV_FILE}"
    [ "${matches}" -eq 1 ] || fail "${RUNTIME_ENV_FILE} must contain exactly one ${TOKEN_KEY} entry."
    validate_token "${token}" "${RUNTIME_ENV_FILE}"
    printf '%s' "${token}"
}

[ -f "${BASE_ENV_FILE}" ] || fail "base env file not found: ${BASE_ENV_FILE}"

runtime_token=''
if [ -e "${RUNTIME_ENV_FILE}" ]; then
    [ -f "${RUNTIME_ENV_FILE}" ] && [ ! -L "${RUNTIME_ENV_FILE}" ] \
        || fail "runtime env path must be a regular, non-symlink file: ${RUNTIME_ENV_FILE}"
    runtime_token="$(read_runtime_token)"
fi

if [ "${PANDOCR_MODEL_CONTROLLER_TOKEN+x}" = x ]; then
    validate_token "${PANDOCR_MODEL_CONTROLLER_TOKEN}" 'process environment'
    if [ -n "${runtime_token}" ] && [ "${PANDOCR_MODEL_CONTROLLER_TOKEN}" != "${runtime_token}" ]; then
        fail "process ${TOKEN_KEY} differs from the persisted runtime token; unset it or restore the persisted value."
    fi
fi

if [ -z "${runtime_token}" ]; then
    if [ "${PANDOCR_MODEL_CONTROLLER_TOKEN+x}" = x ]; then
        runtime_token="${PANDOCR_MODEL_CONTROLLER_TOKEN}"
    else
        if command -v python3 >/dev/null 2>&1 \
            && runtime_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null)" \
            && [ -n "${runtime_token}" ]; then
            :
        elif command -v python >/dev/null 2>&1 \
            && runtime_token="$(python -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null)" \
            && [ -n "${runtime_token}" ]; then
            :
        elif command -v openssl >/dev/null 2>&1 \
            && runtime_token="$(openssl rand -hex 32 2>/dev/null)" \
            && [ -n "${runtime_token}" ]; then
            :
        else
            fail 'cannot generate a controller token; install python3, python, or openssl.'
        fi
    fi
    # A native Windows Python invoked from Git Bash may leave its CR after
    # command substitution strips the trailing LF.
    runtime_token="${runtime_token//$'\r'/}"
    validate_token "${runtime_token}" 'generated token'

    runtime_dir="$(dirname -- "${RUNTIME_ENV_FILE}")"
    mkdir -p -- "${runtime_dir}"
    umask 077
    temp_file="$(mktemp "${RUNTIME_ENV_FILE}.tmp.XXXXXX")"
    cleanup_temp() {
        rm -f -- "${temp_file}"
    }
    trap cleanup_temp EXIT HUP INT TERM
    printf '%s=%s\n' "${TOKEN_KEY}" "${runtime_token}" > "${temp_file}"
    chmod 600 "${temp_file}"
    mv -f -- "${temp_file}" "${RUNTIME_ENV_FILE}"
    trap - EXIT HUP INT TERM
else
    chmod 600 "${RUNTIME_ENV_FILE}" 2>/dev/null || true
fi

printf '%s\n' "${RUNTIME_ENV_FILE}"
