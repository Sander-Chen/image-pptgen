#!/usr/bin/env bash
# Keep the Image service inside the current Codex Desktop command lifetime.
#
# macOS Desktop's non-TUN proxy path can reject a Codex child launched from an
# orphan/launchd process.  This helper deliberately keeps the installed
# runtime, launcher, and CLI under the current shell parent until the command
# has finished.  It is an internal packaging surface; the public CLI contract
# remains owned by image-pptgen.
set -Eeuo pipefail
umask 077

platform_error() {
  printf '%s\n' "{\"error\":\"platform_unavailable\",\"message\":\"$1\"}" >&2
  exit 3
}

protocol_error() {
  printf '%s\n' "{\"error\":\"platform_error\",\"message\":\"$1\"}" >&2
  exit 4
}

INSTALL_ROOT="${IMAGE_PPTGEN_INSTALL_ROOT:-$HOME/.codex/image-pptgen}"
case "$INSTALL_ROOT" in
  */image-pptgen) PLATFORM_HOME="${INSTALL_ROOT%/image-pptgen}" ;;
  *) platform_error "Image PPTGen runtime unavailable: install_root_namespace_invalid" ;;
esac

RUNTIME_PYTHON="$INSTALL_ROOT/current-venv/bin/python"
RUNTIME_CLI="$INSTALL_ROOT/current-venv/bin/image-pptgen"
RUNTIME_MANAGER="$INSTALL_ROOT/current/app/runtime_manager.py"
IMAGE_LAUNCHER="$INSTALL_ROOT/current/app/image-launcher.py"
RELEASE_IDENTITY="$INSTALL_ROOT/current/app/release-identity.json"

[ -x "$RUNTIME_PYTHON" ] || platform_error "Image PPTGen runtime unavailable: active_python_missing"
[ -x "$RUNTIME_CLI" ] || platform_error "Image PPTGen runtime unavailable: active_cli_missing"
[ -f "$RUNTIME_MANAGER" ] || platform_error "Image PPTGen runtime unavailable: runtime_manager_missing"
[ -f "$IMAGE_LAUNCHER" ] || platform_error "Image PPTGen runtime unavailable: launcher_missing"
[ -f "$RELEASE_IDENTITY" ] || platform_error "Image PPTGen runtime unavailable: release_identity_missing"

export XDG_DATA_HOME="$PLATFORM_HOME"
export XDG_CONFIG_HOME="$PLATFORM_HOME"
export IMAGE_PPTGEN_DATA_ROOT="$INSTALL_ROOT"
export IMAGE_PPTGEN_PYTHON="$RUNTIME_PYTHON"
export IMAGE_PPTGEN_HOST="${IMAGE_PPTGEN_HOST:-127.0.0.1}"
export IMAGE_PPTGEN_PORT="${IMAGE_PPTGEN_PORT:-3130}"
export IMAGE_PPTGEN_BASE_URL="${IMAGE_PPTGEN_BASE_URL:-http://$IMAGE_PPTGEN_HOST:$IMAGE_PPTGEN_PORT}"
export PPTGEN_CODEX_INHERIT_USER_CONFIG="${PPTGEN_CODEX_INHERIT_USER_CONFIG:-1}"

case "$IMAGE_PPTGEN_HOST" in
  127.0.0.1) ;;
  *) platform_error "Image PPTGen held runtime may bind only to 127.0.0.1" ;;
esac
[ "$IMAGE_PPTGEN_PORT" = "3130" ] || platform_error "Image PPTGen held runtime listens only on port 3130"
[ "$IMAGE_PPTGEN_BASE_URL" = "http://127.0.0.1:3130" ] || platform_error "Image PPTGen held runtime requires the exact loopback base URL"

# Preserve the user's external proxy while making all loopback checks bypass it.
append_loopback() {
  local value="$1"
  case ",$value," in
    *,127.0.0.1,*) ;;
    *) value="${value:+$value,}127.0.0.1" ;;
  esac
  case ",$value," in
    *,localhost,*) ;;
    *) value="${value:+$value,}localhost" ;;
  esac
  case ",$value," in
    *,::1,*) ;;
    *) value="${value:+$value,}::1" ;;
  esac
  printf '%s' "$value"
}
export NO_PROXY="$(append_loopback "${NO_PROXY:-}")"
export no_proxy="$(append_loopback "${no_proxy:-${NO_PROXY:-}}")"

STATE_ROOT="$INSTALL_ROOT/state/runtime-manager"
LOCK_ROOT="$STATE_ROOT/held-command.lock"
if ! mkdir -p "$STATE_ROOT" 2>/dev/null; then
  platform_error "Image PPTGen runtime state is not writable; grant file access to the Image PPTGen install root"
fi
if ! mkdir "$LOCK_ROOT" 2>/dev/null; then
  if [ -d "$LOCK_ROOT" ]; then
    platform_error "Image PPTGen held runtime is already in use"
  fi
  platform_error "Image PPTGen runtime state is not writable; grant file access to the Image PPTGen install root"
fi

TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/image-pptgen-held.XXXXXX")" || {
  rmdir "$LOCK_ROOT" 2>/dev/null || true
  platform_error "Image PPTGen held runtime could not create its temporary directory"
}
SERVER_PID=""

cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [ -n "$SERVER_PID" ]; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    if kill -0 "$SERVER_PID" 2>/dev/null; then
      kill -KILL "$SERVER_PID" 2>/dev/null || true
    fi
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  # TEMP_ROOT is an exact mktemp directory created by this invocation.  Never
  # use the install root or a caller-provided path as a cleanup target.
  rm -rf -- "$TEMP_ROOT"
  rmdir "$LOCK_ROOT" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Stop only the installation-owned managed listener.  The manager returns
# success with stopped=false when no owned listener exists, and fails closed
# when ownership is unknown or mismatched.
if ! "$RUNTIME_PYTHON" "$RUNTIME_MANAGER" stop --json >"$TEMP_ROOT/manager.out" 2>"$TEMP_ROOT/manager.err"; then
  cat "$TEMP_ROOT/manager.err" >&2 || true
  platform_error "Image PPTGen managed service could not be stopped safely"
fi
if ! "$RUNTIME_PYTHON" - "$TEMP_ROOT/manager.out" <<'PY'
import json
import sys
from pathlib import Path

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if len(lines) != 1 or not lines[0].strip():
    raise SystemExit("runtime-manager stop receipt must contain exactly one JSON line")
try:
    receipt = json.loads(lines[0])
except json.JSONDecodeError as exc:
    raise SystemExit(f"runtime-manager stop receipt is not valid JSON: {exc}")
if not isinstance(receipt, dict) or receipt.get("ok") is not True:
    raise SystemExit("runtime-manager stop receipt does not prove success")
stopped = receipt.get("stopped")
if not isinstance(stopped, bool):
    raise SystemExit("runtime-manager stop receipt has an invalid stopped value")
if stopped:
    pid = receipt.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise SystemExit("runtime-manager stop receipt has an invalid owned pid")
elif "pid" in receipt:
    raise SystemExit("runtime-manager stop receipt unexpectedly contains a pid")
PY
then
  cat "$TEMP_ROOT/manager.err" >&2 || true
  platform_error "Image PPTGen managed service stop receipt is invalid"
fi

"$RUNTIME_PYTHON" "$IMAGE_LAUNCHER" \
  --host "$IMAGE_PPTGEN_HOST" \
  --port "$IMAGE_PPTGEN_PORT" \
  >"$TEMP_ROOT/server.log" 2>&1 &
SERVER_PID=$!

# Use the installed Runtime's stdlib urllib with an empty ProxyHandler.  Do
# not let macOS's system proxy route a loopback readiness request.  The health
# payload must match the immutable release identity and the fixed relative
# artifact roots before any public CLI request is sent.
if ! "$RUNTIME_PYTHON" - "$IMAGE_PPTGEN_BASE_URL" "$RELEASE_IDENTITY" "$SERVER_PID" <<'PY'
import json
import os
import signal
import sys
import time
from pathlib import Path
from urllib import request

base_url, release_path, server_pid = sys.argv[1:]
try:
    release = json.loads(Path(release_path).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"release identity is unavailable: {exc}")
if not isinstance(release, dict):
    raise SystemExit("release identity is invalid")
required = (
    "build_id",
    "version",
    "source_commit",
    "skill_sha256",
    "runtime_content_sha256",
)
if any(not isinstance(release.get(field), str) or not release[field].strip() for field in required):
    raise SystemExit("release identity is incomplete")

opener = request.build_opener(request.ProxyHandler({}))
url = base_url.rstrip("/") + "/api/runtime-identity"
deadline = time.monotonic() + 30.0
last_error = "not reachable"
while time.monotonic() < deadline:
    try:
        os.kill(int(server_pid), 0)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"held Image service exited before readiness: {exc}")
    try:
        req = request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with opener.open(req, timeout=1.0) as response:
            observed = json.loads(response.read().decode("utf-8"))
        if not isinstance(observed, dict):
            raise ValueError("health endpoint returned a non-object")
        expected = {
            "base_url": base_url,
            "build_id": release["build_id"],
            "product": "image-pptgen",
            "service": "image-pptgen-server",
            "surface": "public_image_3_0",
            "version": release["version"],
            "source_commit": release["source_commit"],
            "skill_sha256": release["skill_sha256"],
            "runtime_content_sha256": release["runtime_content_sha256"],
        }
        for field, value in expected.items():
            if observed.get(field) != value:
                raise ValueError(f"runtime identity mismatch: {field}")
        if not isinstance(observed.get("instance_id"), str) or not observed["instance_id"].strip():
            raise ValueError("runtime identity is missing instance_id")
        if observed.get("data_root") != "image-pptgen/state/data":
            raise ValueError("runtime identity data_root mismatch")
        if observed.get("artifacts_root") != "image-pptgen/state/data/artifacts":
            raise ValueError("runtime identity artifacts_root mismatch")
        raise SystemExit(0)
    except Exception as exc:  # bounded retry until the listener is ready
        last_error = str(exc)
    time.sleep(0.25)
raise SystemExit(f"held Image service did not become ready: {last_error}")
PY
then
  cat "$TEMP_ROOT/server.log" >&2 || true
  platform_error "Image PPTGen held runtime did not become ready"
fi

run_generate_and_follow() {
  local deck_id=""
  local saw_deck_id=0
  local saw_jsonl=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --deck-id)
        [ "$#" -ge 2 ] || protocol_error "generate-and-follow requires --deck-id"
        [ "$saw_deck_id" -eq 0 ] || protocol_error "generate-and-follow accepts --deck-id exactly once"
        deck_id="$2"
        saw_deck_id=1
        shift 2
        ;;
      --jsonl)
        [ "$saw_jsonl" -eq 0 ] || protocol_error "generate-and-follow accepts --jsonl at most once"
        saw_jsonl=1
        shift
        ;;
      *) protocol_error "generate-and-follow received an unsupported argument" ;;
    esac
  done
  case "$deck_id" in
    ''|*[!0-9]*) protocol_error "generate-and-follow requires a positive integer deck id" ;;
  esac
  [ "$deck_id" -gt 0 ] || protocol_error "generate-and-follow requires a positive integer deck id"
  [ "$saw_jsonl" -eq 1 ] || protocol_error "generate-and-follow requires --jsonl exactly once"

  local generation_output="$TEMP_ROOT/generation.json"
  local generation_status=0
  "$RUNTIME_CLI" generate --deck-id "$deck_id" --json >"$generation_output" || generation_status=$?
  if [ "$generation_status" -ne 0 ]; then
    cat "$generation_output" >&2 || true
    return "$generation_status"
  fi

  local run_id
  if ! run_id="$($RUNTIME_PYTHON - "$generation_output" <<'PY'
import json
import sys
from pathlib import Path

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if len(lines) != 1 or not lines[0].strip():
    raise SystemExit("generation response must contain exactly one JSON line")
try:
    payload = json.loads(lines[0])
except json.JSONDecodeError as exc:
    raise SystemExit(f"generation response is not valid JSON: {exc}")
run_ids = payload.get("run_ids") if isinstance(payload, dict) else None
if not isinstance(run_ids, list) or len(run_ids) != 1:
    raise SystemExit("generation response must contain exactly one run_ids item")
run_id = run_ids[0]
if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
    raise SystemExit("generation response run_ids[0] must be a positive integer")
print(run_id)
PY
)"; then
    protocol_error "generation response did not bind exactly one positive run id"
  fi
  case "$run_id" in
    ''|*[!0-9]*) protocol_error "generation response run id is invalid" ;;
  esac
  [ "$run_id" -gt 0 ] || protocol_error "generation response run id is invalid"

  # The generation response is the first line of this dispatcher-only
  # composition.  Follow is executed exactly once while this helper still
  # owns the server child.
  IFS= read -r generation_line < "$generation_output"
  printf '%s\n' "$generation_line"
  "$RUNTIME_CLI" status --run-id "$run_id" --follow --jsonl
}

if [ "${1:-}" = "generate-and-follow" ]; then
  shift
  run_generate_and_follow "$@"
else
  "$RUNTIME_CLI" "$@"
fi
