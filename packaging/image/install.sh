#!/usr/bin/env bash
set -eu

VERSION="__VERSION__"
DEFAULT_DIST_BASE_URL="https://image-pptgen-dist.pages.dev"
DIST_BASE_URL="${1:-${IMAGE_PPTGEN_DIST_BASE_URL:-$DEFAULT_DIST_BASE_URL}}"

fail() { printf 'Image PPTGen install stopped: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "$2"; }
check_writable_root() {
  mkdir -p "$1" || fail "Cannot create writable user directory: $1"
  probe="$1/.image-pptgen-write-probe-$$"
  : > "$probe" || fail "User directory is not writable: $1"
  rm -f "$probe"
}
check_dependencies() {
  [ "$(uname -s)" = "Linux" ] || fail "Only Linux is supported."
  [ "$(uname -m)" = "x86_64" ] || fail "Only Linux x86_64 is supported."
  require_command python3 "Install Python 3.11+ and python3-venv first."
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' \
    || fail "Python 3.11 or newer is required."
  python3 -m venv --help >/dev/null 2>&1 \
    || fail "Python venv is unavailable. On Ubuntu install python3-venv."
  require_command codex "Install the Codex CLI first."
  codex login status >/dev/null 2>&1 || fail "Run codex login before installing."
  require_command fc-list \
    "Install fontconfig and a CJK font. On Ubuntu: sudo apt install fontconfig fonts-noto-cjk."
  [ -n "$(fc-list :lang=zh family 2>/dev/null | head -n 1)" ] \
    || fail "Install a CJK font. On Ubuntu: sudo apt install fonts-noto-cjk."
  require_command sha256sum "Install coreutils first."
  command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
    || fail "Install curl or wget first."
  case "$DIST_BASE_URL" in https://*) ;; *) fail "Use an HTTPS distribution URL." ;; esac
  check_writable_root "${XDG_DATA_HOME:-$HOME/.local/share}"
  check_writable_root "${XDG_CONFIG_HOME:-$HOME/.config}"
  check_writable_root "$HOME/.local/bin"
  check_writable_root "$HOME/.agents/skills"
}
download_file() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1" -o "$2"
  else
    wget -q -O "$2" "$1"
  fi
}
validate_archive() {
  python3 - "$1" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as handle:
    names = set()
    for member in handle.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\x00" in member.name
            or member.name in names
            or not (member.isfile() or member.isdir())
        ):
            raise SystemExit(f"unsafe archive member: {member.name}")
        names.add(member.name)
PY
}

check_dependencies

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/image-pptgen-install.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT HUP INT TERM
MANIFEST_PATH="$WORK_DIR/manifest.json"
download_file "$DIST_BASE_URL/releases/$VERSION/manifest.json" "$MANIFEST_PATH"
manifest_field() {
  python3 - "$MANIFEST_PATH" "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("version") != "__VERSION__" or data.get("platform") != "linux-x86_64":
    raise SystemExit("unexpected Image release manifest")
print(data["archive"][sys.argv[2]])
PY
}
ARCHIVE_NAME="$(manifest_field name)"
EXPECTED_SHA="$(manifest_field sha256)"
[ "$ARCHIVE_NAME" = "image-pptgen-$VERSION-linux-x86_64.tar.gz" ] \
  || fail "Manifest archive name is not allowed."
ARCHIVE_PATH="$WORK_DIR/$ARCHIVE_NAME"
download_file "$DIST_BASE_URL/releases/$VERSION/$ARCHIVE_NAME" "$ARCHIVE_PATH"
[ "$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')" = "$EXPECTED_SHA" ] \
  || fail "Archive SHA-256 mismatch."
validate_archive "$ARCHIVE_PATH" || fail "Archive safety validation failed."

EXTRACT_DIR="$WORK_DIR/extract"
mkdir -p "$EXTRACT_DIR"
python3 - "$ARCHIVE_PATH" "$EXTRACT_DIR" <<'PY'
import pathlib, sys, tarfile
destination = pathlib.Path(sys.argv[2]).resolve()
with tarfile.open(sys.argv[1], "r:gz") as handle:
    for member in handle.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsafe archive member: {member.name}")
        target = (destination / pathlib.Path(*path.parts)).resolve()
        target.relative_to(destination)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = handle.extractfile(member)
        if source is None:
            raise SystemExit(f"archive member cannot be read: {member.name}")
        target.write_bytes(source.read())
        target.chmod(member.mode & 0o777 or 0o644)
PY
STAGED_RELEASE="$EXTRACT_DIR/image-pptgen-$VERSION"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
INSTALL_ROOT="$DATA_HOME/image-pptgen"
mkdir -p "$INSTALL_ROOT/releases" "$INSTALL_ROOT/venvs" \
  "$INSTALL_ROOT/state/data/artifacts" "$CONFIG_HOME/image-pptgen"
INSTALL_ID="$VERSION-$(printf '%s' "$EXPECTED_SHA" | cut -c1-12)"
RELEASE_TARGET="$INSTALL_ROOT/releases/$INSTALL_ID"
VENV_TARGET="$INSTALL_ROOT/venvs/$INSTALL_ID"
if [ -f "$RELEASE_TARGET/app/public_server.py" ]; then
  rm -rf "$STAGED_RELEASE"
else
  rm -rf "$RELEASE_TARGET"
  mv "$STAGED_RELEASE" "$RELEASE_TARGET"
fi
if ! "$VENV_TARGET/bin/image-pptgen" --help >/dev/null 2>&1; then
  rm -rf "$VENV_TARGET"
  if ! (
    python3 -m venv "$VENV_TARGET"
    "$VENV_TARGET/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
    "$VENV_TARGET/bin/python" -m pip install --disable-pip-version-check -q \
      -r "$RELEASE_TARGET/app/requirements.txt"
    "$VENV_TARGET/bin/python" -m pip install --disable-pip-version-check -q \
      "$RELEASE_TARGET/app/packages/pptgen_toolkit"
    "$VENV_TARGET/bin/python" -c 'import flask, PIL, flask_cors, requests, waitress, pptgen_toolkit'
    "$VENV_TARGET/bin/image-pptgen" --help >/dev/null
  ); then
    rm -rf "$VENV_TARGET"
    fail "Python environment installation failed; the previous Image installation remains active."
  fi
fi
ln -sfn "$RELEASE_TARGET" "$INSTALL_ROOT/current"
ln -sfn "$VENV_TARGET" "$INSTALL_ROOT/current-venv"

SKILL_SOURCE="$INSTALL_ROOT/current/app/skills/generate-image-presentation"
SKILL_TARGET="$HOME/.agents/skills/generate-image-presentation"
SKILL_STAGE="$HOME/.agents/skills/.generate-image-presentation.new.$$"
cp -R "$SKILL_SOURCE" "$SKILL_STAGE"
if [ -e "$SKILL_TARGET" ] && ! diff -qr "$SKILL_TARGET" "$SKILL_STAGE" >/dev/null 2>&1; then
  mkdir -p "$INSTALL_ROOT/backups"
  mv "$SKILL_TARGET" "$INSTALL_ROOT/backups/generate-image-presentation.before-$VERSION-$$"
fi
rm -rf "$SKILL_TARGET"
mv "$SKILL_STAGE" "$SKILL_TARGET"

ENV_FILE="$CONFIG_HOME/image-pptgen/env"
if [ ! -e "$ENV_FILE" ]; then
  printf 'PPTGEN_PUBLIC_DATA_DIR=%s\nPPT_DB_PATH=%s\nPPT_ARTIFACTS_DIR=%s\nPPTGEN_HISTORICAL_DATA_DIR=%s\nPPTGEN_HOST=127.0.0.1\nPPTGEN_PORT=3130\nIMAGE_PPTGEN_HOST=127.0.0.1\nIMAGE_PPTGEN_PORT=3130\nIMAGE_PPTGEN_BASE_URL=http://127.0.0.1:3130\n' \
    "$INSTALL_ROOT/state/data" "$INSTALL_ROOT/state/data/ppt.db" \
    "$INSTALL_ROOT/state/data/artifacts" "$INSTALL_ROOT/state/data/historical-data" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
cp "$INSTALL_ROOT/current/app/image-pptgen-wrapper.sh" "$HOME/.local/bin/image-pptgen"
cp "$INSTALL_ROOT/current/app/image-pptgen-server-wrapper.sh" "$HOME/.local/bin/image-pptgen-server"
chmod 755 "$HOME/.local/bin/image-pptgen" "$HOME/.local/bin/image-pptgen-server"
"$HOME/.local/bin/image-pptgen" --help >/dev/null

# Make the freshly installed release user-operable immediately.  The same
# private manager is used by the wrapper for subsequent requests; this call
# only waits for its identity health probe and never sends a product request.
READY_JSON="$WORK_DIR/runtime-ready.json"
READY_ERROR="$WORK_DIR/runtime-ready.error"
if ! "$VENV_TARGET/bin/python" "$INSTALL_ROOT/current/app/runtime_manager.py" \
  ensure-ready --json >"$READY_JSON" 2>"$READY_ERROR"; then
  cat "$READY_ERROR" >&2 || true
  fail "Image service could not become ready; the previous installation remains active."
fi
python3 - "$READY_JSON" "$INSTALL_ROOT/current/app/release-identity.json" "$VERSION" <<'PY' \
  || fail "Image service readiness identity is invalid."
import json
import sys

ready_path, release_path, expected_version = sys.argv[1:]
with open(ready_path, encoding="utf-8") as handle:
    ready = json.load(handle)
with open(release_path, encoding="utf-8") as handle:
    release = json.load(handle)
if (
    ready.get("ok") is not True
    or ready.get("base_url") != "http://127.0.0.1:3130"
    or ready.get("version") != expected_version
    or ready.get("version") != release.get("version")
    or ready.get("build_id") != release.get("build_id")
    or not isinstance(ready.get("instance_id"), str)
    or not ready["instance_id"].strip()
):
    raise SystemExit("runtime health identity does not match the installed release")
PY
printf '\nImage PPTGen %s installed and ready.\n' "$VERSION"
printf 'Start in a fresh Codex task, paste your source material, and invoke $generate-image-presentation.\n'
printf 'Review every proposed content page, request revisions if needed, then explicitly confirm once.\n'
printf '\nDiagnostics and startup:\n'
printf '  export PATH="%s:$PATH"\n' "$HOME/.local/bin"
printf '  image-pptgen-server   # advanced foreground diagnostics (optional)\n'
printf '  image-pptgen doctor --json\n'
printf '  Service address: use the Image doctor-reported base_url (default http://127.0.0.1:3130).\n'
