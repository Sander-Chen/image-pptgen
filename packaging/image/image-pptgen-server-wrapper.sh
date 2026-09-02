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
export PPTGEN_CODEX_INHERIT_USER_CONFIG="${PPTGEN_CODEX_INHERIT_USER_CONFIG:-1}"
exec "$DATA_HOME/image-pptgen/current-venv/bin/python" \
  "$DATA_HOME/image-pptgen/current/app/image-launcher.py" "$@"
