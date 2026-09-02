#!/usr/bin/env bash
set -eu
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
ENV_FILE="$CONFIG_HOME/image-pptgen/env"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi
IMAGE_PPTGEN_HOST="${IMAGE_PPTGEN_HOST:-127.0.0.1}"
IMAGE_PPTGEN_PORT="${IMAGE_PPTGEN_PORT:-3130}"
export IMAGE_PPTGEN_BASE_URL="${IMAGE_PPTGEN_BASE_URL:-http://$IMAGE_PPTGEN_HOST:$IMAGE_PPTGEN_PORT}"

# Readiness is a private preflight for the default local service.  Keep help
# and explicit non-default endpoints completely side-effect free, and pass
# the public CLI argv through unchanged exactly once after readiness succeeds.
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
  runtime_manager="$DATA_HOME/image-pptgen/current/app/runtime_manager.py"
  runtime_python="$DATA_HOME/image-pptgen/current-venv/bin/python"
  if [ ! -x "$runtime_python" ]; then
    runtime_python="$(command -v python3 || true)"
  fi
  if [ ! -f "$runtime_manager" ] || [ -z "$runtime_python" ]; then
    printf '%s\n' \
      '{"error":"platform_unavailable","message":"Image PPTGen runtime unavailable: runtime_manager_missing"}' \
      >&2
    exit 3
  fi
  readiness_status=0
  "$runtime_python" "$runtime_manager" ensure-ready --json >/dev/null || readiness_status=$?
  if [ "$readiness_status" -ne 0 ]; then
    exit "$readiness_status"
  fi
fi
exec "$DATA_HOME/image-pptgen/current-venv/bin/image-pptgen" "$@"
