#!/usr/bin/env bash
set -eu

INSTALL_ROOT="${IMAGE_PPTGEN_INSTALL_ROOT:-$HOME/.codex/image-pptgen}"
RUNTIME_PYTHON="$INSTALL_ROOT/current-venv/bin/python"
RUNTIME_MANAGER="$INSTALL_ROOT/current/app/runtime_manager.py"
RUNTIME_CLI="$INSTALL_ROOT/current-venv/bin/image-pptgen"
HELD_COMMAND="$INSTALL_ROOT/current/macos/image-pptgen-held-command.sh"

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
export IMAGE_PPTGEN_HOST="${IMAGE_PPTGEN_HOST:-127.0.0.1}"
export IMAGE_PPTGEN_PORT="${IMAGE_PPTGEN_PORT:-3130}"
export IMAGE_PPTGEN_BASE_URL="${IMAGE_PPTGEN_BASE_URL:-http://$IMAGE_PPTGEN_HOST:$IMAGE_PPTGEN_PORT}"

if [ ! -x "$RUNTIME_PYTHON" ] || [ ! -f "$RUNTIME_MANAGER" ] || [ ! -x "$RUNTIME_CLI" ]; then
  printf '%s\n' '{"error":"platform_unavailable","message":"Image PPTGen runtime unavailable: active_install_incomplete"}' >&2
  exit 3
fi

should_ensure_ready=1
for argument in "$@"; do
  case "$argument" in
    -h|--help)
      should_ensure_ready=0
      ;;
    --base-url=*)
      argument_base_url="${argument#--base-url=}"
      [ "${argument_base_url%/}" = "http://127.0.0.1:3130" ] || should_ensure_ready=0
      ;;
  esac
done
if [ "$should_ensure_ready" -eq 1 ]; then
  previous_argument=""
  explicit_base_url=""
  for argument in "$@"; do
    if [ "$previous_argument" = "--base-url" ]; then
      explicit_base_url="$argument"
      break
    fi
    previous_argument="$argument"
  done
  if [ -n "$explicit_base_url" ] && [ "${explicit_base_url%/}" != "http://127.0.0.1:3130" ]; then
    should_ensure_ready=0
  fi
fi
if [ "$should_ensure_ready" -eq 1 ] && [ "${IMAGE_PPTGEN_BASE_URL%/}" = "http://127.0.0.1:3130" ]; then
  case "${1:-}" in
    doctor|material|split|status|result|generate-and-follow)
      if [ ! -f "$HELD_COMMAND" ] || [ ! -x "$HELD_COMMAND" ]; then
        printf '%s\n' '{"error":"platform_unavailable","message":"Image PPTGen macOS held command helper is unavailable."}' >&2
        exit 3
      fi
      exec "$HELD_COMMAND" "$@"
      ;;
  esac
  readiness_status=0
  "$RUNTIME_PYTHON" "$RUNTIME_MANAGER" ensure-ready --json >/dev/null || readiness_status=$?
  if [ "$readiness_status" -ne 0 ]; then
    exit "$readiness_status"
  fi
fi
exec "$RUNTIME_CLI" "$@"
