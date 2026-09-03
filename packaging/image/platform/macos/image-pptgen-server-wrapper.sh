#!/usr/bin/env bash
set -eu

INSTALL_ROOT="${IMAGE_PPTGEN_INSTALL_ROOT:-$HOME/.codex/image-pptgen}"
RUNTIME_PYTHON="$INSTALL_ROOT/current-venv/bin/python"
IMAGE_LAUNCHER="$INSTALL_ROOT/current/app/image-launcher.py"

case "$INSTALL_ROOT" in
  */image-pptgen) PLATFORM_HOME="${INSTALL_ROOT%/image-pptgen}" ;;
  *)
    printf '%s\n' '{"error":"platform_unavailable","message":"Image PPTGen runtime unavailable: install_root_namespace_invalid"}' >&2
    exit 3
    ;;
esac
export XDG_DATA_HOME="$PLATFORM_HOME"
export XDG_CONFIG_HOME="$PLATFORM_HOME"
export IMAGE_PPTGEN_DATA_ROOT="$INSTALL_ROOT"
export IMAGE_PPTGEN_PYTHON="$RUNTIME_PYTHON"
export PPTGEN_CODEX_INHERIT_USER_CONFIG="${PPTGEN_CODEX_INHERIT_USER_CONFIG:-1}"

if [ ! -x "$RUNTIME_PYTHON" ] || [ ! -f "$IMAGE_LAUNCHER" ]; then
  printf '%s\n' '{"error":"platform_unavailable","message":"Image PPTGen runtime unavailable: active_install_incomplete"}' >&2
  exit 3
fi
exec "$RUNTIME_PYTHON" "$IMAGE_LAUNCHER" "$@"
