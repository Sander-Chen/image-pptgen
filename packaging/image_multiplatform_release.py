#!/usr/bin/env python3
"""Build immutable Public Image payloads for Linux, Windows, and macOS.

Pages receives only the aggregate manifest and small bootstrap text/scripts.
Application payloads and the optional frozen Python runtimes are staged in an
R2-shaped directory and recorded in a deterministic upload ledger.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform as host_platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import Any, Mapping
from urllib.parse import urlparse
import zipfile


TARGET_PLATFORMS = ("linux-x86_64", "macos-arm64", "windows-amd64")
FALLBACK_PLATFORMS = ("macos-arm64", "windows-amd64")
FALLBACK_LOCK_PATH = Path(__file__).resolve().parent / "image" / "fallback" / "fallback-lock.json"
PAGES_FILE_LIMIT = 25 * 1024 * 1024
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_PREFIX_PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ASSET_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}")
_IMAGE_TOOLKIT_PROJECT = "image-pptgen-toolkit"
_IMAGE_TOOLKIT_VERSION = "0.1.0"
_IMAGE_TOOLKIT_WHEEL_NAME = (
    f"{_IMAGE_TOOLKIT_PROJECT.replace('-', '_')}-{_IMAGE_TOOLKIT_VERSION}-py3-none-any.whl"
)


class BuildError(RuntimeError):
    """The requested release cannot be built without violating its contract."""


def _load_legacy_builder():
    path = Path(__file__).resolve().with_name("image_build_release.py")
    spec = importlib.util.spec_from_file_location("image_pptgen_legacy_release_builder", path)
    if spec is None or spec.loader is None:
        raise BuildError(f"legacy Image release builder is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_url(value: str, *, field: str) -> str:
    if not isinstance(value, str) or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise BuildError(f"{field} must not contain whitespace or control characters")
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise BuildError(f"{field} is not a valid URL") from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BuildError(f"{field} must be an absolute HTTP(S) URL without credentials/query/fragment")
    return value.rstrip("/")


def _shell_literal(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_prefix(value: str) -> str:
    prefix = value.strip("/")
    parts = PurePosixPath(prefix).parts
    if not parts or any(part in {"", ".", ".."} or not _PREFIX_PART_RE.fullmatch(part) for part in parts):
        raise BuildError("r2_prefix must be a safe relative object prefix")
    return PurePosixPath(*parts).as_posix()


def select_platform(*, system: str, machine: str, user_platform: str | None = None) -> str:
    """Map only promised OS/architecture pairs; never ask the user to choose."""

    if user_platform is not None:
        raise BuildError("manual platform selection is unsupported; platform is detected automatically")
    key = (system.strip().casefold(), machine.strip().casefold())
    aliases = {
        ("linux", "x86_64"): "linux-x86_64",
        ("linux", "amd64"): "linux-x86_64",
        ("windows", "amd64"): "windows-amd64",
        ("windows", "x86_64"): "windows-amd64",
        ("darwin", "arm64"): "macos-arm64",
        ("darwin", "aarch64"): "macos-arm64",
    }
    try:
        return aliases[key]
    except KeyError as exc:
        raise BuildError(f"unsupported platform/architecture: {system}/{machine}") from exc


def detect_platform() -> str:
    return select_platform(system=host_platform.system(), machine=host_platform.machine())


def _load_fallback_lock() -> dict[str, Any]:
    try:
        value = json.loads(FALLBACK_LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"fallback authority is unavailable: {FALLBACK_LOCK_PATH}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("platforms"), dict):
        raise BuildError("fallback authority is malformed")
    return value


def _asset_spec(platform: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = platform.get(name)
    if not isinstance(value, dict):
        raise BuildError(f"fallback authority is missing {name}")
    filename, size, sha256 = value.get("filename"), value.get("bytes"), value.get("sha256")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not _ASSET_NAME_RE.fullmatch(filename)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
    ):
        raise BuildError(f"fallback authority contains invalid {name} metadata")
    return dict(value)


def _find_asset(root: Path, filename: str) -> Path:
    resolved_root = root.resolve(strict=True)
    candidates = []
    for path in root.rglob("*"):
        if path.name != filename:
            continue
        if path.is_symlink():
            raise BuildError(f"frozen asset must not be a symbolic link: {path}")
        if not path.is_file():
            continue
        candidate = path.resolve(strict=True)
        try:
            candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise BuildError(f"frozen asset escaped fallback_assets_root: {path}") from exc
        candidates.append(candidate)
    candidates.sort()
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    if len(unique) != 1:
        raise BuildError(f"expected exactly one frozen asset {filename!r}; found {unique}")
    return unique[0]


def _verify_asset(root: Path, spec: Mapping[str, Any]) -> Path:
    path = _find_asset(root, str(spec["filename"]))
    observed_size = path.stat().st_size
    observed_sha = _sha256(path)
    if observed_size != spec["bytes"] or observed_sha != spec["sha256"]:
        raise BuildError(
            f"frozen asset identity mismatch for {path.name}: "
            f"size={observed_size} sha256={observed_sha}"
        )
    return path


def _verified_wheels(
    assets_root: Path, platform: Mapping[str, Any]
) -> tuple[Path, list[tuple[str, bytes]]]:
    bundle_spec = _asset_spec(platform, "wheelhouse_bundle")
    bundle = _verify_asset(assets_root, bundle_spec)
    wheel_specs = platform.get("wheels")
    if not isinstance(wheel_specs, list) or not wheel_specs:
        raise BuildError("fallback authority has no wheel inventory")
    expected: dict[str, tuple[int, str]] = {}
    for value in wheel_specs:
        if not isinstance(value, dict):
            raise BuildError("fallback wheel inventory is malformed")
        filename, size, sha256 = value.get("filename"), value.get("bytes"), value.get("sha256")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".whl")
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            raise BuildError("fallback wheel inventory is malformed")
        expected[filename] = (size, sha256)
    observed: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            normalized: dict[str, str] = {}
            for raw_name in names:
                parts = PurePosixPath(raw_name).parts
                if len(parts) == 1:
                    wheel_name = parts[0]
                elif len(parts) == 2 and parts[0] == "wheelhouse":
                    wheel_name = parts[1]
                else:
                    raise BuildError(f"unsafe frozen wheelhouse member: {raw_name}")
                if wheel_name in normalized:
                    raise BuildError(f"duplicate frozen wheelhouse member: {wheel_name}")
                normalized[wheel_name] = raw_name
            if (
                names != sorted(names)
                or len(names) != len(set(names))
                or set(normalized) != set(expected)
            ):
                raise BuildError("frozen wheelhouse members do not match the authority inventory")
            for name in sorted(normalized):
                payload = archive.read(normalized[name])
                size, sha256 = expected[name]
                if len(payload) != size or hashlib.sha256(payload).hexdigest() != sha256:
                    raise BuildError(f"frozen wheel identity mismatch: {name}")
                observed.append((name, payload))
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildError(f"frozen wheelhouse is unreadable: {bundle}") from exc
    return bundle, observed


def _copy_tree(source: Path, target: Path) -> None:
    _assert_regular_tree(source, label="required source directory")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def _assert_regular_tree(root: Path, *, label: str) -> None:
    if root.is_symlink() or not root.is_dir():
        raise BuildError(f"{label} is unavailable or unsafe: {root}")
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BuildError(f"{label} contains an unsafe filesystem entry: {path}")


def _wheel_digest(payload: bytes) -> str:
    """Return the URL-safe hash representation required by Wheel RECORD."""

    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode(
        "ascii"
    ).rstrip("=")


def _build_image_toolkit_wheel(app_root: Path) -> Path:
    """Build the pure-Python toolkit wheel without a runtime build backend.

    The Desktop Runtime is intentionally not expected to contain setuptools.
    Shipping this tiny wheel keeps the source tree and pyproject available for
    provenance while making the user-side install a direct, offline wheel
    install instead of a PEP 517 source build.
    """

    source = app_root / "packages" / "pptgen_toolkit" / "src" / "pptgen_toolkit"
    if not source.is_dir():
        raise BuildError(f"Image toolkit source is unavailable: {source}")
    package_members: list[tuple[str, bytes]] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(source)
        # The toolkit is pure Python.  Keep the wheel portable and
        # reproducible by excluding interpreter caches, bytecode, and any
        # non-source files that happen to be present in a dirty checkout.
        if relative_path.suffix != ".py" or "__pycache__" in relative_path.parts:
            continue
        relative = relative_path.as_posix()
        package_members.append((f"pptgen_toolkit/{relative}", path.read_bytes()))
    if not package_members:
        raise BuildError("Image toolkit source is empty")

    dist_info = f"{_IMAGE_TOOLKIT_PROJECT.replace('-', '_')}-{_IMAGE_TOOLKIT_VERSION}.dist-info"
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {_IMAGE_TOOLKIT_PROJECT}\n"
        f"Version: {_IMAGE_TOOLKIT_VERSION}\n"
        "Requires-Python: >=3.11\n\n"
    ).encode("utf-8")
    wheel_metadata = (
        "Wheel-Version: 1.0\n"
        "Generator: public-image-pptgen-release-builder\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n\n"
    ).encode("utf-8")
    entry_points = (
        "[console_scripts]\n"
        "image-pptgen = pptgen_toolkit.image_cli:main\n"
    ).encode("utf-8")
    metadata_members = [
        (f"{dist_info}/METADATA", metadata),
        (f"{dist_info}/WHEEL", wheel_metadata),
        (f"{dist_info}/entry_points.txt", entry_points),
    ]
    wheel_members = package_members + metadata_members
    record_rows = [
        f"{name},{_wheel_digest(payload)},{len(payload)}"
        for name, payload in sorted(wheel_members)
    ]
    record_rows.append(f"{dist_info}/RECORD,,")
    wheel_members.append(
        (f"{dist_info}/RECORD", ("\n".join(record_rows) + "\n").encode("utf-8"))
    )

    target = app_root / "packages" / "pptgen_toolkit" / "dist" / _IMAGE_TOOLKIT_WHEEL_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in sorted(wheel_members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return target


def _platform_identity(legacy, repo_root: Path, app_root: Path, *, version: str, platform_id: str) -> dict[str, Any]:
    identity = dict(legacy._release_identity(repo_root, app_root, version=version))
    identity.pop("build_id", None)
    identity["platform"] = platform_id
    if platform_id == "windows-amd64":
        identity["data_root"] = "%LOCALAPPDATA%\\ImagePPTGen"
        identity["config_root"] = "%LOCALAPPDATA%\\ImagePPTGen\\state"
    elif platform_id == "macos-arm64":
        identity["data_root"] = "~/.codex/image-pptgen"
        identity["config_root"] = "~/.codex/image-pptgen/state"
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity["build_id"] = hashlib.sha256(canonical).hexdigest()
    return identity


def _populate_platform_bundle(
    *,
    legacy,
    repo_root: Path,
    bundle: Path,
    version: str,
    platform_id: str,
    fallback_lock: Mapping[str, Any],
    fallback_assets_root: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    app_root = bundle / "app"
    legacy._populate_runtime(repo_root, app_root)
    if platform_id in {"windows-amd64", "macos-arm64"}:
        _build_image_toolkit_wheel(app_root)

    fallback_runtime: dict[str, Any] | None = None
    if platform_id == "linux-x86_64":
        linux = bundle / "linux"
        linux.mkdir(parents=True)
        shutil.copy2(repo_root / "packaging" / "image" / "requirements.txt", linux / "requirements.lock")
    else:
        platform_name = "windows" if platform_id == "windows-amd64" else "macos"
        target = bundle / platform_name
        _copy_tree(repo_root / "packaging" / "image" / "platform" / platform_name, target)
        shutil.copy2(FALLBACK_LOCK_PATH, target / "fallback-lock.json")
        platform_authority = fallback_lock["platforms"].get(platform_id)
        if not isinstance(platform_authority, dict):
            raise BuildError(f"fallback authority is missing platform: {platform_id}")
        _bundle, wheels = _verified_wheels(fallback_assets_root, platform_authority)
        wheelhouse = bundle / "wheelhouse"
        wheelhouse.mkdir()
        for name, payload in wheels:
            (wheelhouse / name).write_bytes(payload)
        license_spec = _asset_spec(platform_authority, "license_bundle")
        license_asset = _verify_asset(fallback_assets_root, license_spec)
        licenses = bundle / "licenses"
        licenses.mkdir()
        shutil.copy2(license_asset, licenses / license_asset.name)
        runtime_spec = _asset_spec(platform_authority, "runtime_asset")
        runtime_asset = _verify_asset(fallback_assets_root, runtime_spec)
        fallback_runtime = {
            "freeze_id": fallback_lock.get("freeze_id"),
            "source_path": runtime_asset,
            "name": runtime_asset.name,
            "sha256": runtime_spec["sha256"],
            "size": runtime_spec["bytes"],
        }

    identity = _platform_identity(
        legacy, repo_root, app_root, version=version, platform_id=platform_id
    )
    (app_root / "release-identity.json").write_bytes(_json_bytes(identity))
    _assert_regular_tree(bundle, label="platform bundle")
    findings = legacy._scan_text_files(bundle)
    if findings:
        raise BuildError("secret-like value in platform bundle: " + "; ".join(findings))
    return identity, fallback_runtime


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if info.isdir():
        info.mode = 0o755
    elif info.isfile():
        info.mode = 0o755 if info.mode & stat.S_IXUSR else 0o644
    return info


def _create_tar(source_root: Path, archive_path: Path) -> None:
    _assert_regular_tree(source_root, label="tar source")
    members = [source_root, *sorted(source_root.rglob("*"), key=lambda p: p.relative_to(source_root).as_posix())]
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in members:
                    arcname = source_root.name
                    if path != source_root:
                        arcname += "/" + path.relative_to(source_root).as_posix()
                    archive.add(path, arcname=arcname, recursive=False, filter=_tar_filter)


def _create_zip(source_root: Path, archive_path: Path) -> None:
    _assert_regular_tree(source_root, label="zip source")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in source_root.rglob("*") if path.is_file())
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            name = source_root.name + "/" + path.relative_to(source_root).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = 0o100755 if os.access(path, os.X_OK) else 0o100644
            info.external_attr = mode << 16
            with path.open("rb") as source:
                archive.writestr(info, source.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _regular_file_matches(path: Path, *, expected_size: int, expected_sha256: str) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(mode)
        and path.stat().st_size == expected_size
        and _sha256(path) == expected_sha256
    )


def _publish_stage_immutable(
    stage: Path,
    target: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    try:
        os.link(stage, target)
    except FileExistsError:
        if not _regular_file_matches(
            target,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        ):
            raise BuildError(f"immutable release conflict; refusing to overwrite: {target}")


def _new_stage(target: Path) -> tuple[int, Path]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise BuildError(f"release target parent is unsafe: {target.parent}")
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.new-", dir=target.parent)
    return descriptor, Path(name)


def _write_immutable(source: Path, target: Path) -> None:
    expected_size = source.stat().st_size
    expected_sha256 = _sha256(source)
    if _regular_file_matches(
        target, expected_size=expected_size, expected_sha256=expected_sha256
    ):
        return
    descriptor, stage = _new_stage(target)
    try:
        with source.open("rb") as src, os.fdopen(descriptor, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        _publish_stage_immutable(
            stage,
            target,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
    finally:
        try:
            stage.unlink()
        except FileNotFoundError:
            pass


def _object_metadata(path: Path, *, object_path: str, r2_base_url: str, kind: str, platform_id: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "platform": platform_id,
        "name": path.name,
        "path": object_path,
        "url": f"{r2_base_url}/{object_path}",
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _write_text_immutable(path: Path, payload: bytes) -> None:
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    if _regular_file_matches(
        path, expected_size=len(payload), expected_sha256=expected_sha256
    ):
        return
    descriptor, stage = _new_stage(path)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        _publish_stage_immutable(
            stage,
            path,
            expected_size=len(payload),
            expected_sha256=expected_sha256,
        )
    finally:
        try:
            stage.unlink()
        except FileNotFoundError:
            pass


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _safe_output_target(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise BuildError(f"release output path is unsafe: {relative}")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise BuildError(f"release output root is unsafe: {root}")
    parent = root
    for part in parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise BuildError(f"release output path crosses a symbolic link: {parent}")
        if parent.exists() and not parent.is_dir():
            raise BuildError(f"release output parent is not a directory: {parent}")
        parent.mkdir(exist_ok=True)
    target = parent / parts[-1]
    if target.is_symlink():
        raise BuildError(f"release output target is a symbolic link: {target}")
    return target


def _assert_pages_text_only(pages_root: Path) -> None:
    _assert_regular_tree(pages_root, label="Pages tree")
    for path in pages_root.rglob("*"):
        if not path.is_file():
            continue
        if path.stat().st_size >= PAGES_FILE_LIMIT:
            raise BuildError(f"Pages asset exceeds the 25 MiB limit: {path}")
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BuildError(f"Pages tree contains a non-text asset: {path}") from exc


_SHELL_BOOTSTRAP_TEMPLATE = r'''#!/usr/bin/env bash
set -euo pipefail

# Generated user-scoped bootstrap.  Pages carries only this text and the
# aggregate manifest; the selected immutable application object is downloaded
# from R2 after the manifest has bound its name, size, hash, and URL.
VERSION=__VERSION__
DIST_BASE_URL=__DIST_BASE_URL__
MANIFEST_URL=__MANIFEST_URL__
R2_BASE_URL=__R2_BASE_URL__

# The caller may pass the Python returned by Codex Desktop's
# load_workspace_dependencies or provide CODEX_WORKSPACE_PYTHON.  This is the
# dynamic Attempt 1 candidate.  Known managed-primary roots are deliberately
# discovered only for Attempt 2 below.  Linux keeps its existing PATH-based
# Python behavior; macOS and Windows never promote a system-PATH Python to the
# official Runtime candidate.
EXPLICIT_OFFICIAL_PYTHON=""
if [ "${1:-}" = '--official-python' ]; then
  [ "$#" -ge 2 ] || { printf '%s\n' '--official-python requires a path' >&2; exit 2; }
  EXPLICIT_OFFICIAL_PYTHON="$2"
  shift 2
fi
if [ -n "$EXPLICIT_OFFICIAL_PYTHON" ]; then
  DYNAMIC_OFFICIAL_PYTHON="$EXPLICIT_OFFICIAL_PYTHON"
else
  DYNAMIC_OFFICIAL_PYTHON="${CODEX_WORKSPACE_PYTHON:-}"
fi
PYTHON=""

fail() { printf 'Image PPTGen bootstrap stopped: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "$2"; }

validate_url() {
  "$PYTHON" - "$1" <<'PY'
import sys
from urllib.parse import urlparse
value = sys.argv[1]
if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in value):
    raise SystemExit("URL contains whitespace or control characters")
parsed = urlparse(value)
if (parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment):
    raise SystemExit("URL must be an absolute HTTP(S) URL without credentials/query/fragment")
PY
}

download_file() {
  local url="$1" destination="$2"
  validate_url "$url" || fail "unsafe download URL"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --location --retry 2 -o "$destination" -- "$url" \
      || fail "download failed: $url"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --output-document="$destination" -- "$url" || fail "download failed: $url"
  else
    fail "Install curl or wget first."
  fi
}

json_get() {
  "$PYTHON" - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
for part in sys.argv[2].split("."):
    if not isinstance(value, dict) or part not in value:
        raise SystemExit("missing manifest field: " + sys.argv[2])
    value = value[part]
if isinstance(value, (dict, list)):
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
elif isinstance(value, bool):
    print("true" if value else "false")
elif value is not None:
    print(value)
PY
}

verify_file() {
  "$PYTHON" - "$1" "$2" "$3" <<'PY'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
expected_size, expected_sha = int(sys.argv[2]), sys.argv[3].lower()
observed_size = path.stat().st_size
digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
observed_sha = digest.hexdigest()
if observed_size != expected_size or observed_sha != expected_sha:
    raise SystemExit(f"file identity mismatch: size={observed_size} sha256={observed_sha}")
PY
}

validate_relative_path() {
  "$PYTHON" - "$1" <<'PY'
from pathlib import PurePosixPath
import sys
raw = sys.argv[1]
path = PurePosixPath(raw)
if (not raw or "\\" in raw or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)):
    raise SystemExit("unsafe relative path")
PY
}

# Extract regular members only.  Links, duplicate names, traversal, special
# files, and files outside the temporary destination are rejected before any
# installer sees the payload.
safe_extract_archive() {
  local archive="$1" destination="$2" format="$3" expected_root="$4" require_root="$5"
  "$PYTHON" - "$archive" "$destination" "$format" "$expected_root" "$require_root" <<'PY'
import os, stat, sys, tarfile, zipfile
from pathlib import Path, PurePosixPath
archive_path, destination = Path(sys.argv[1]), Path(sys.argv[2])
archive_format, expected_root, require_root = sys.argv[3], sys.argv[4], sys.argv[5] == "1"
destination.mkdir(parents=True, exist_ok=False)
destination_root = destination.resolve()
seen, top_levels = set(), set()

def safe_name(raw):
    path = PurePosixPath(raw)
    if (not raw or "\x00" in raw or "\\" in raw or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)):
        raise SystemExit("unsafe archive member: " + raw)
    folded = raw.rstrip("/").casefold()
    if not folded or folded in seen:
        raise SystemExit("duplicate archive member: " + raw)
    seen.add(folded); top_levels.add(path.parts[0]); return path

def ensure_target(target):
    try: target.parent.resolve().relative_to(destination_root)
    except ValueError as exc: raise SystemExit("archive member escapes staging") from exc

def write_file(target, source, mode):
    ensure_target(target); target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"): flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, mode or 0o644)
    with os.fdopen(descriptor, "wb") as output:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): output.write(chunk)
        output.flush(); os.fsync(output.fileno())
    target.chmod(mode or 0o644)

if archive_format == "zip":
    try: handle = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc: raise SystemExit("invalid ZIP archive") from exc
    with handle:
        if not handle.infolist(): raise SystemExit("archive is empty")
        for info in handle.infolist():
            path = safe_name(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or (file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise SystemExit("unsafe ZIP member: " + info.filename)
            target = destination.joinpath(*path.parts); ensure_target(target)
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True); target.chmod(mode & 0o777 or 0o755)
            else: write_file(target, handle.open(info, "r"), mode & 0o777)
else:
    try: handle = tarfile.open(archive_path, "r:gz")
    except (OSError, tarfile.TarError) as exc: raise SystemExit("invalid tar.gz archive") from exc
    with handle:
        members = handle.getmembers()
        if not members: raise SystemExit("archive is empty")
        for member in members:
            path = safe_name(member.name)
            if (not (member.isfile() or member.isdir()) or member.issym() or member.islnk()
                    or member.isdev() or member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)):
                raise SystemExit("unsafe tar member: " + member.name)
            target = destination.joinpath(*path.parts); ensure_target(target)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True); target.chmod(member.mode & 0o777 or 0o755)
            else:
                source = handle.extractfile(member)
                if source is None: raise SystemExit("unreadable tar member: " + member.name)
                write_file(target, source, member.mode & 0o777)
if require_root and top_levels != {expected_root}:
    raise SystemExit(f"archive root is not {expected_root!r}: {sorted(top_levels)!r}")
PY
}

macos_native_validate_url() {
  local value="$1" remainder authority
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* && "$value" != *$'\t'* && "$value" != *' '* ]] || return 1
  case "$value" in http://*|https://*) ;; *) return 1 ;; esac
  remainder="${value#*://}"
  authority="${remainder%%/*}"
  [[ -n "$authority" && "$authority" != *'@'* && "$value" != *'?'* && "$value" != *'#'* ]] || return 1
}

macos_native_download_file() {
  local url="$1" destination="$2"
  macos_native_validate_url "$url" || fail "unsafe download URL"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --location --retry 2 -o "$destination" -- "$url" || fail "download failed: $url"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --output-document="$destination" -- "$url" || fail "download failed: $url"
  else
    fail "Install curl or wget first."
  fi
}

macos_native_json_get() {
  local file="$1" key="$2" expected_type="${3:-}"
  require_command plutil "macOS plutil is required for the Python-free bootstrap."
  if [ -n "$expected_type" ]; then
    plutil -extract "$key" raw -expect "$expected_type" -o - "$file" 2>/dev/null
  else
    plutil -extract "$key" raw -o - "$file" 2>/dev/null
  fi
}

macos_native_object_url() {
  local file="$1" object_key="$2" label="$3" path url
  path="$(macos_native_json_get "$file" "$object_key.path" string)" || fail "$label path is missing"
  macos_native_validate_relative_path "$path" || fail "$label path is unsafe"
  url="$(macos_native_json_get "$file" "$object_key.url" string 2>/dev/null || true)"
  if [ -z "$url" ]; then
    url="${R2_BASE_URL%/}/$path"
  fi
  macos_native_validate_url "$url" || fail "$label URL is unsafe"
  printf '%s\n' "$url"
}

macos_native_verify_file() {
  local path="$1" expected_size="$2" expected_sha="$3" observed_size observed_sha
  [[ "$expected_size" =~ ^[1-9][0-9]*$ && "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || return 1
  observed_size="$(stat -f%z "$path" 2>/dev/null || true)"
  if [[ ! "$observed_size" =~ ^[0-9]+$ ]]; then
    observed_size="$(stat -c%s "$path" 2>/dev/null || true)"
  fi
  observed_sha="$(shasum -a 256 "$path" 2>/dev/null | awk '{print tolower($1)}')"
  [[ "$observed_size" = "$expected_size" && "$observed_sha" = "$expected_sha" ]]
}

macos_native_validate_relative_path() {
  local raw="$1" part
  [[ -n "$raw" && "$raw" != *\\* && "$raw" != /* && "$raw" != *$'\n'* && "$raw" != *$'\r'* && "$raw" != *$'\t'* ]] || return 1
  IFS='/' read -r -a _macos_path_parts <<< "$raw"
  for part in "${_macos_path_parts[@]}"; do
    [[ -n "$part" && "$part" != '.' && "$part" != '..' ]] || return 1
  done
}

# This path deliberately needs no Python.  It validates all member names and
# types before the platform archive or frozen Runtime is unpacked, then checks
# the extracted tree again before it can be used.
macos_native_safe_extract_tar_gz() {
  local archive="$1" destination="$2" expected_root="$3" list="$WORK_DIR/tar-members.$RANDOM" normalized="$WORK_DIR/tar-members-normalized.$RANDOM" member detail perms roots
  mkdir "$destination" || return 1
  tar -tzf "$archive" >"$list" 2>/dev/null || return 1
  [[ -s "$list" ]] || return 1
  : >"$normalized"
  while IFS= read -r member || [ -n "$member" ]; do
    member="${member%/}"
    macos_native_validate_relative_path "$member" || return 1
    printf '%s\n' "$member" >>"$normalized"
  done <"$list"
  ! LC_ALL=C tr '[:upper:]' '[:lower:]' <"$normalized" | LC_ALL=C sort | uniq -d | grep -q . || return 1
  roots="$(sed 's#/.*##' "$normalized" | LC_ALL=C sort -u)"
  [[ "$roots" = "$expected_root" ]] || return 1
  while IFS= read -r detail || [ -n "$detail" ]; do
    perms="${detail%% *}"
    case "${perms:0:1}" in -|d) ;; *) return 1 ;; esac
    case "$perms" in *s*|*S*|*t*|*T*) return 1 ;; esac
  done < <(tar -tvzf "$archive" 2>/dev/null) || return 1
  tar -xzf "$archive" -C "$destination" || return 1
  ! find "$destination" -xdev ! -type f ! -type d -print | grep -q .
}

macos_native_safe_extract_zip() {
  local archive="$1" destination="$2" list="$WORK_DIR/zip-members.$RANDOM" normalized="$WORK_DIR/zip-members-normalized.$RANDOM" member detail perms
  require_command zipinfo "macOS zipinfo is required to validate the license bundle."
  require_command unzip "macOS unzip is required to unpack the license bundle."
  mkdir "$destination" || return 1
  zipinfo -1 "$archive" >"$list" 2>/dev/null || return 1
  [[ -s "$list" ]] || return 1
  : >"$normalized"
  while IFS= read -r member || [ -n "$member" ]; do
    member="${member%/}"
    macos_native_validate_relative_path "$member" || return 1
    printf '%s\n' "$member" >>"$normalized"
  done <"$list"
  ! LC_ALL=C tr '[:upper:]' '[:lower:]' <"$normalized" | LC_ALL=C sort | uniq -d | grep -q . || return 1
  while IFS= read -r detail || [ -n "$detail" ]; do
    perms="${detail%% *}"
    case "${perms:0:1}" in ''|A|Z|[0-9]) ;; -|d) ;; *) return 1 ;; esac
  done < <(zipinfo -l "$archive" 2>/dev/null) || return 1
  unzip -qq "$archive" -d "$destination" || return 1
  ! find "$destination" -xdev ! -type f ! -type d -print | grep -q .
}

macos_native_prepare_payload() {
  local schema product release_version name size sha url fallback_name fallback_size fallback_sha fallback_freeze
  macos_native_validate_url "$MANIFEST_URL" || fail "Manifest URL is unsafe."
  MANIFEST_PATH="$WORK_DIR/aggregate-manifest.json"
  macos_native_download_file "$MANIFEST_URL" "$MANIFEST_PATH"
  schema="$(macos_native_json_get "$MANIFEST_PATH" schema_version integer)" || fail "aggregate manifest schema is invalid"
  product="$(macos_native_json_get "$MANIFEST_PATH" product string)" || fail "aggregate manifest product is invalid"
  release_version="$(macos_native_json_get "$MANIFEST_PATH" version string)" || fail "aggregate manifest version is invalid"
  [[ "$schema" = '2' && "$product" = 'image-pptgen' && "$release_version" = "$VERSION" ]] || fail "aggregate manifest schema/product/version mismatch"
  name="$(macos_native_json_get "$MANIFEST_PATH" "platforms.$PLATFORM.archive.name" string)" || fail "platform archive metadata is missing"
  size="$(macos_native_json_get "$MANIFEST_PATH" "platforms.$PLATFORM.archive.size" integer)" || fail "platform archive size is missing"
  sha="$(macos_native_json_get "$MANIFEST_PATH" "platforms.$PLATFORM.archive.sha256" string)" || fail "platform archive SHA-256 is missing"
  url="$(macos_native_object_url "$MANIFEST_PATH" "platforms.$PLATFORM.archive" "platform archive")"
  [[ "$name" = "image-pptgen-$VERSION-$PLATFORM.tar.gz" && "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$ && "$size" =~ ^[1-9][0-9]*$ && "$sha" =~ ^[0-9a-f]{64}$ ]] || fail "platform archive metadata is invalid"
  fallback_name="$(macos_native_json_get "$MANIFEST_PATH" "platforms.$PLATFORM.fallback_runtime.name" string)" || fail "fallback Runtime metadata is missing"
  fallback_size="$(macos_native_json_get "$MANIFEST_PATH" "platforms.$PLATFORM.fallback_runtime.size" integer)" || fail "fallback Runtime metadata is missing"
  fallback_sha="$(macos_native_json_get "$MANIFEST_PATH" "platforms.$PLATFORM.fallback_runtime.sha256" string)" || fail "fallback Runtime metadata is missing"
  fallback_freeze="$(macos_native_json_get "$MANIFEST_PATH" "platforms.$PLATFORM.fallback_runtime.freeze_id" string)" || fail "fallback Runtime metadata is missing"
  FALLBACK_URL="$(macos_native_object_url "$MANIFEST_PATH" "platforms.$PLATFORM.fallback_runtime" "fallback Runtime")"
  [[ "$fallback_name" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$ && "$fallback_size" =~ ^[1-9][0-9]*$ && "$fallback_sha" =~ ^[0-9a-f]{64}$ && -n "$fallback_freeze" ]] || fail "fallback Runtime metadata is invalid"
  ARCHIVE_NAME="$name"; ARCHIVE_SIZE="$size"; ARCHIVE_SHA="$sha"; ARCHIVE_URL="$url"
  FALLBACK_NAME="$fallback_name"; FALLBACK_SIZE="$fallback_size"; FALLBACK_SHA="$fallback_sha"; FALLBACK_FREEZE_ID="$fallback_freeze"
  ARCHIVE_PATH="$WORK_DIR/$ARCHIVE_NAME"
  macos_native_download_file "$ARCHIVE_URL" "$ARCHIVE_PATH"
  macos_native_verify_file "$ARCHIVE_PATH" "$ARCHIVE_SIZE" "$ARCHIVE_SHA" || fail "Platform archive size/SHA-256 verification failed."
  EXTRACT_DIR="$WORK_DIR/application"
  macos_native_safe_extract_tar_gz "$ARCHIVE_PATH" "$EXTRACT_DIR" "image-pptgen-$VERSION" || fail "Platform archive safety validation failed."
  RELEASE_ROOT="$EXTRACT_DIR/image-pptgen-$VERSION"
  MACOS_NATIVE_BOOTSTRAP=1
}

require_command uname "Cannot detect the current platform."
case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Linux:amd64) PLATFORM='linux-x86_64' ;;
  Darwin:arm64|Darwin:aarch64) PLATFORM='macos-arm64' ;;
  *) fail "Unsupported Image PPTGen platform; no platform choice is offered." ;;
esac

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/image-pptgen-bootstrap.XXXXXX")" || fail "Cannot create a private temporary directory."
cleanup() { rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT HUP INT TERM
if [ "$PLATFORM" = 'macos-arm64' ]; then
  # macOS must be able to bind, fetch, verify, and unpack the frozen Runtime
  # even when Desktop has not yet provisioned any Python executable.
  macos_native_prepare_payload
else
  BOOTSTRAP_PYTHON="$DYNAMIC_OFFICIAL_PYTHON"
  if [ -z "$BOOTSTRAP_PYTHON" ]; then
    for managed_candidate in \
      "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
      "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12" \
      "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.11"; do
      if [ -f "$managed_candidate" ] && [ -x "$managed_candidate" ]; then
        BOOTSTRAP_PYTHON="$managed_candidate"
        break
      fi
    done
  fi
  if [ -z "$BOOTSTRAP_PYTHON" ]; then
    BOOTSTRAP_PYTHON="$(command -v python3 2>/dev/null || true)"
  fi
  [ -n "$BOOTSTRAP_PYTHON" ] && [ -x "$BOOTSTRAP_PYTHON" ] || fail "No Python interpreter is available for the manifest bootstrap."
  PYTHON="$BOOTSTRAP_PYTHON"
  validate_url "$MANIFEST_URL" || fail "Manifest URL is unsafe."
  MANIFEST_PATH="$WORK_DIR/aggregate-manifest.json"
  download_file "$MANIFEST_URL" "$MANIFEST_PATH"
  SELECTION_PATH="$WORK_DIR/selection.json"
"$PYTHON" - "$MANIFEST_PATH" "$SELECTION_PATH" "$PLATFORM" "$VERSION" "$R2_BASE_URL" <<'PY'
import json, re, sys
from pathlib import Path
from urllib.parse import urlparse
manifest_path, selection_path, platform_id, version, r2_base = sys.argv[1:]
with open(manifest_path, encoding="utf-8") as handle: root = json.load(handle)
if not isinstance(root, dict) or root.get("schema_version") != 2 or root.get("product") != "image-pptgen" or root.get("version") != version:
    raise SystemExit("aggregate manifest schema/product/version mismatch")
platforms = root.get("platforms")
if not isinstance(platforms, dict) or platform_id not in platforms or not isinstance(platforms[platform_id], dict):
    raise SystemExit("aggregate manifest platform entry is missing")
entry = platforms[platform_id]; archive = entry.get("archive")
if not isinstance(archive, dict): raise SystemExit("platform archive metadata is missing")
name_re, sha_re = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}"), re.compile(r"[0-9a-f]{64}")
expected_name = f"image-pptgen-{version}-{platform_id}" + (".zip" if platform_id == "windows-amd64" else ".tar.gz")
name, size, sha256 = archive.get("name"), archive.get("size"), archive.get("sha256")
if not isinstance(name, str) or not name_re.fullmatch(name) or name != expected_name: raise SystemExit("platform archive name is unsafe")
if not isinstance(size, int) or isinstance(size, bool) or size <= 0: raise SystemExit("platform archive size is invalid")
if not isinstance(sha256, str) or not sha_re.fullmatch(sha256): raise SystemExit("platform archive SHA-256 is invalid")
def safe_url(value, fallback, field):
    raw = value if isinstance(value, str) and value else fallback; parsed = urlparse(raw)
    if (any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in raw) or parsed.scheme not in {"http", "https"}
            or not parsed.netloc or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment): raise SystemExit(field + " URL is unsafe")
    return raw
def object_url(meta, field):
    path = meta.get("path")
    if not isinstance(path, str) or not path or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise SystemExit(field + " path is unsafe")
    return safe_url(meta.get("url"), r2_base.rstrip("/") + "/" + path, field)
selected = {"archive_name": name, "archive_size": size, "archive_sha256": sha256,
            "archive_url": object_url(archive, "archive"), "archive_format": "zip" if platform_id == "windows-amd64" else "tar.gz"}
fallback = entry.get("fallback_runtime")
if fallback is not None:
    if not isinstance(fallback, dict): raise SystemExit("fallback Runtime metadata is invalid")
    f_name, f_size, f_sha, f_freeze = fallback.get("name"), fallback.get("size"), fallback.get("sha256"), fallback.get("freeze_id")
    if (not isinstance(f_name, str) or not name_re.fullmatch(f_name) or not isinstance(f_size, int) or isinstance(f_size, bool)
            or f_size <= 0 or not isinstance(f_sha, str) or not sha_re.fullmatch(f_sha) or not isinstance(f_freeze, str) or not f_freeze):
        raise SystemExit("fallback Runtime metadata is invalid")
    selected.update({"fallback_name": f_name, "fallback_size": f_size, "fallback_sha256": f_sha,
                     "fallback_freeze_id": f_freeze, "fallback_url": object_url(fallback, "fallback Runtime")})
Path(selection_path).write_text(json.dumps(selected, sort_keys=True) + "\n", encoding="utf-8")
PY
ARCHIVE_NAME="$(json_get "$SELECTION_PATH" archive_name)"
ARCHIVE_SIZE="$(json_get "$SELECTION_PATH" archive_size)"
ARCHIVE_SHA="$(json_get "$SELECTION_PATH" archive_sha256)"
ARCHIVE_URL="$(json_get "$SELECTION_PATH" archive_url)"
ARCHIVE_FORMAT="$(json_get "$SELECTION_PATH" archive_format)"
ARCHIVE_PATH="$WORK_DIR/$ARCHIVE_NAME"
download_file "$ARCHIVE_URL" "$ARCHIVE_PATH"
verify_file "$ARCHIVE_PATH" "$ARCHIVE_SIZE" "$ARCHIVE_SHA" || fail "Platform archive size/SHA-256 verification failed."
EXTRACT_DIR="$WORK_DIR/application"
safe_extract_archive "$ARCHIVE_PATH" "$EXTRACT_DIR" "$ARCHIVE_FORMAT" "image-pptgen-$VERSION" 1 || fail "Platform archive safety validation failed."
RELEASE_ROOT="$EXTRACT_DIR/image-pptgen-$VERSION"
fi
'''


_SHELL_BOOTSTRAP_SUFFIX = r'''
if [ "$PLATFORM" = 'macos-arm64' ]; then
  LOCK_PATH="$RELEASE_ROOT/macos/fallback-lock.json"
  [ -f "$LOCK_PATH" ] || fail "macOS fallback lock is missing from the application archive."
  [ "$(macos_native_json_get "$LOCK_PATH" schema_version integer)" = '1' ] || fail "macOS fallback lock schema is unsupported."
  LICENSE_NAME="$(macos_native_json_get "$LOCK_PATH" "platforms.$PLATFORM.license_bundle.filename" string)" || fail "macOS license bundle metadata is missing."
  LICENSE_SIZE="$(macos_native_json_get "$LOCK_PATH" "platforms.$PLATFORM.license_bundle.bytes" integer)" || fail "macOS license bundle metadata is missing."
  LICENSE_SHA="$(macos_native_json_get "$LOCK_PATH" "platforms.$PLATFORM.license_bundle.sha256" string)" || fail "macOS license bundle metadata is missing."
  macos_native_validate_relative_path "$LICENSE_NAME" || fail "macOS license bundle path is unsafe."
  LICENSE_ZIP="$RELEASE_ROOT/licenses/$LICENSE_NAME"
  [ -f "$LICENSE_ZIP" ] || fail "macOS license bundle is missing from the application archive."
  macos_native_verify_file "$LICENSE_ZIP" "$LICENSE_SIZE" "$LICENSE_SHA" || fail "macOS license bundle size/SHA-256 verification failed."
  LICENSE_DIR="$WORK_DIR/licenses"
  macos_native_safe_extract_zip "$LICENSE_ZIP" "$LICENSE_DIR" || fail "macOS license bundle safety validation failed."
fi

# The attempts are deliberately different, observable provisioning methods.
# The first success wins; fallback metadata is not touched until both fail.
discover_known_managed_primary_python() {
  local candidate candidate_path
  local -a candidates=(
    "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python"
    "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin"
  )
  for candidate in "${candidates[@]}"; do
    [ -n "$candidate" ] || continue
    for candidate_path in "$candidate" "$candidate/bin/python3.11" "$candidate/bin/python3" "$candidate/python3.11" "$candidate/python3"; do
      if [ -f "$candidate_path" ] && [ -x "$candidate_path" ]; then
        printf '%s\n' "$candidate_path"
        return 0
      fi
    done
  done
  return 1
}

probe_official() {
  local candidate="$1" approach="$2" probe_dir="$WORK_DIR/probe-$2"
  rm -rf -- "$probe_dir"
  [ -n "$candidate" ] && [ -x "$candidate" ] || return 1
  "$candidate" -I -c 'import os,platform,sys; expected=os.path.realpath(sys.argv[1]); actual=os.path.realpath(sys.executable); raise SystemExit(0 if actual == expected and sys.version_info >= (3,11) and platform.machine().lower() in ("arm64", "aarch64") else 1)' "$candidate" \
    >"$WORK_DIR/$approach.log" 2>&1 || return 1
  if [ "$approach" = 'venv-ensurepip' ]; then
    "$candidate" -I -m venv --copies "$probe_dir" >"$WORK_DIR/$approach.log" 2>&1 \
      && [ -x "$probe_dir/bin/python" ] \
      && "$probe_dir/bin/python" -I -m pip --version >>"$WORK_DIR/$approach.log" 2>&1
  else
    "$candidate" -I -m venv --copies --without-pip "$probe_dir" >"$WORK_DIR/$approach.log" 2>&1 \
      && "$candidate" -I -m pip --version >>"$WORK_DIR/$approach.log" 2>&1 \
      && "$candidate" -I -m pip --python "$probe_dir/bin/python" --version >>"$WORK_DIR/$approach.log" 2>&1
  fi
}

OFFICIAL_PYTHON=""
OFFICIAL_SUCCESS=0
ATTEMPT_ONE='failed'
ATTEMPT_TWO='failed'
if probe_official "$DYNAMIC_OFFICIAL_PYTHON" 'venv-ensurepip'; then
  OFFICIAL_PYTHON="$DYNAMIC_OFFICIAL_PYTHON"
  OFFICIAL_SUCCESS=1
  ATTEMPT_ONE='succeeded'
else
  KNOWN_MANAGED_PRIMARY_PYTHON="$(discover_known_managed_primary_python || true)"
  if probe_official "$KNOWN_MANAGED_PRIMARY_PYTHON" 'venv-host-pip'; then
    OFFICIAL_PYTHON="$KNOWN_MANAGED_PRIMARY_PYTHON"
    OFFICIAL_SUCCESS=1
    ATTEMPT_TWO='succeeded'
  fi
fi
# From this point onward every helper invocation uses the interpreter whose
# official probe succeeded.  If both probes failed, keep the bootstrap
# interpreter until the frozen fallback has been verified and selected.
if [ "$OFFICIAL_SUCCESS" -eq 1 ]; then
  [ -n "$OFFICIAL_PYTHON" ] || fail "Official Runtime probe returned success without an executable."
  PYTHON="$OFFICIAL_PYTHON"
fi

invoke_macos_installer() {
  local python="$1" fallback_root="${2:-}" receipt="${3:-}" output="$WORK_DIR/macos-installer.out" official_python="$1"
  # Codex Desktop's managed Runtime may be sandboxed away from generic
  # Application Support.  Keep the default installation beneath the same
  # user-scoped Codex home that owns the installed Skill; explicit IMAGE_*
  # values remain the only compatibility override.
  local install_root="${IMAGE_PPTGEN_INSTALL_ROOT:-$HOME/.codex/image-pptgen}"
  local bin_home="${IMAGE_PPTGEN_BIN_HOME:-$HOME/.codex/bin}"
  local skill_home="${IMAGE_PPTGEN_SKILL_HOME:-$HOME/.codex/skills}"
  local -a args=(--json --home "$HOME" install
    --manifest "$WORK_DIR/macos-manifest.json"
    --archive "$ARCHIVE_PATH"
    --wheelhouse "$RELEASE_ROOT/wheelhouse"
    --license-dir "$LICENSE_DIR"
    --install-root "$install_root"
    --bin-home "$bin_home"
    --skill-home "$skill_home")
  if [ -n "$fallback_root" ]; then
    official_python="$WORK_DIR/no-official-python"
    args+=(--official-python "$official_python")
    args+=(--fallback-python-dir "$fallback_root" --fallback-freeze-id "$FALLBACK_FREEZE_ID" --fallback-authorization-receipt "$receipt")
  else
    args+=(--official-python "$official_python")
  fi
  local status
  # Keep the installer invocation in a conditional: the generated bootstrap
  # runs with `set -e`, and a bare non-zero command would terminate the shell
  # before we can report and return its original status.
  if "$python" "$RELEASE_ROOT/macos/installer.py" "${args[@]}" >"$output" 2>&1; then
    cat "$output"
    return 0
  else
    status=$?
  fi
  cat "$output" >&2 || true
  return "$status"
}

write_macos_manifest() {
  local interpreter="$1"
  "$interpreter" - "$WORK_DIR/macos-manifest.json" "$VERSION" "$ARCHIVE_NAME" "$ARCHIVE_SHA" "$ARCHIVE_SIZE" <<'PY'
import json, sys
from pathlib import Path
path, version, name, sha256, size = sys.argv[1:]
Path(path).write_text(json.dumps({"schema_version": 1, "version": version, "platform": "macos-arm64", "archive": {"name": name, "sha256": sha256, "size": int(size)}}, sort_keys=True) + "\n", encoding="utf-8")
PY
}

if [ "$PLATFORM" = 'macos-arm64' ]; then
  if [ "$OFFICIAL_SUCCESS" -eq 1 ]; then
    write_macos_manifest "$OFFICIAL_PYTHON"
    if invoke_macos_installer "$OFFICIAL_PYTHON"; then
      exit 0
    else
      installer_status=$?
      printf '%s\n' 'Image PPTGen bootstrap stopped: Official Runtime passed its probe but the platform installer failed; fallback was not downloaded.' >&2
      exit "$installer_status"
    fi
  fi

  if [ "${MACOS_NATIVE_BOOTSTRAP:-0}" = '1' ]; then
    LOCK_FREEZE_ID="$(macos_native_json_get "$LOCK_PATH" freeze_id string)" || fail "macOS fallback lock metadata is missing."
    RUNTIME_NAME="$(macos_native_json_get "$LOCK_PATH" "platforms.$PLATFORM.runtime_asset.filename" string)" || fail "macOS fallback lock metadata is missing."
    RUNTIME_SIZE="$(macos_native_json_get "$LOCK_PATH" "platforms.$PLATFORM.runtime_asset.bytes" integer)" || fail "macOS fallback lock metadata is missing."
    RUNTIME_SHA="$(macos_native_json_get "$LOCK_PATH" "platforms.$PLATFORM.runtime_asset.sha256" string)" || fail "macOS fallback lock metadata is missing."
    RUNTIME_RELATIVE="$(macos_native_json_get "$LOCK_PATH" "platforms.$PLATFORM.python_json.python_exe" string)" || fail "macOS fallback lock metadata is missing."
    macos_native_validate_relative_path "$RUNTIME_RELATIVE" || fail "Fallback Runtime Python path is unsafe."
  else
    FALLBACK_NAME="$(json_get "$SELECTION_PATH" fallback_name)" || fail "Fallback Runtime metadata is missing."
    FALLBACK_SIZE="$(json_get "$SELECTION_PATH" fallback_size)"
    FALLBACK_SHA="$(json_get "$SELECTION_PATH" fallback_sha256)"
    FALLBACK_FREEZE_ID="$(json_get "$SELECTION_PATH" fallback_freeze_id)"
    FALLBACK_URL="$(json_get "$SELECTION_PATH" fallback_url)"
    LOCK_FREEZE_ID="$(json_get "$LOCK_PATH" freeze_id)"
    RUNTIME_NAME="$(json_get "$LOCK_PATH" "platforms.$PLATFORM.runtime_asset.filename")"
    RUNTIME_SIZE="$(json_get "$LOCK_PATH" "platforms.$PLATFORM.runtime_asset.bytes")"
    RUNTIME_SHA="$(json_get "$LOCK_PATH" "platforms.$PLATFORM.runtime_asset.sha256")"
    RUNTIME_RELATIVE="$(json_get "$LOCK_PATH" "platforms.$PLATFORM.python_json.python_exe")"
    validate_relative_path "$RUNTIME_RELATIVE" || fail "Fallback Runtime Python path is unsafe."
  fi
  [ "$FALLBACK_FREEZE_ID" = "$LOCK_FREEZE_ID" ] || fail "Fallback Runtime freeze identity is not bound to the frozen lock."
  [ "$FALLBACK_NAME" = "$RUNTIME_NAME" ] || fail "Fallback Runtime name is not bound to the frozen lock."
  [ "$FALLBACK_SIZE" = "$RUNTIME_SIZE" ] || fail "Fallback Runtime size is not bound to the frozen lock."
  [ "$FALLBACK_SHA" = "$RUNTIME_SHA" ] || fail "Fallback Runtime SHA-256 is not bound to the frozen lock."
  FALLBACK_ARCHIVE="$WORK_DIR/$FALLBACK_NAME"
  if [ "${MACOS_NATIVE_BOOTSTRAP:-0}" = '1' ]; then
    macos_native_download_file "$FALLBACK_URL" "$FALLBACK_ARCHIVE"
    macos_native_verify_file "$FALLBACK_ARCHIVE" "$RUNTIME_SIZE" "$RUNTIME_SHA" || fail "Frozen fallback Runtime size/SHA-256 verification failed."
  else
    download_file "$FALLBACK_URL" "$FALLBACK_ARCHIVE"
    verify_file "$FALLBACK_ARCHIVE" "$RUNTIME_SIZE" "$RUNTIME_SHA" || fail "Frozen fallback Runtime size/SHA-256 verification failed."
  fi
  FALLBACK_ROOT="$WORK_DIR/fallback-runtime"
  if [ "${MACOS_NATIVE_BOOTSTRAP:-0}" = '1' ]; then
    macos_native_safe_extract_tar_gz "$FALLBACK_ARCHIVE" "$FALLBACK_ROOT" python || fail "Frozen fallback Runtime safety validation failed."
  else
    safe_extract_archive "$FALLBACK_ARCHIVE" "$FALLBACK_ROOT" tar.gz python 1 || fail "Frozen fallback Runtime safety validation failed."
  fi
  FALLBACK_PYTHON="$FALLBACK_ROOT/$RUNTIME_RELATIVE"
  [ -f "$FALLBACK_PYTHON" ] && [ -x "$FALLBACK_PYTHON" ] || fail "Frozen fallback Runtime Python executable is missing."
  RECEIPT="$WORK_DIR/fallback-authorization.json"
  PYTHON="$FALLBACK_PYTHON"
  "$PYTHON" - "$RECEIPT" "$FALLBACK_FREEZE_ID" "$RUNTIME_SHA" "$RUNTIME_SIZE" "$FALLBACK_ROOT" "$FALLBACK_PYTHON" "$ATTEMPT_ONE" "$ATTEMPT_TWO" <<'PY'
import json, sys
from pathlib import Path
path, freeze_id, sha256, size, root, python, first, second = sys.argv[1:]
Path(path).write_text(json.dumps({
    "schema_version": 1, "platform": "macos-arm64", "freeze_id": freeze_id, "decision": "fallback_authorized",
    "official_attempts": [{"approach": "venv-ensurepip", "result": first}, {"approach": "venv-host-pip", "result": second}],
    "fallback_runtime": {"archive_sha256": sha256, "archive_bytes": int(size), "extracted_root": str(Path(root).resolve()), "python_path": str(Path(python).resolve())},
}, sort_keys=True) + "\n", encoding="utf-8")
PY
  write_macos_manifest "$PYTHON"
  if invoke_macos_installer "$FALLBACK_PYTHON" "$FALLBACK_ROOT" "$RECEIPT"; then
    exit 0
  else
    installer_status=$?
    printf '%s\n' 'Image PPTGen bootstrap stopped: macOS fallback installer failed.' >&2
    exit "$installer_status"
  fi
fi

# Linux retains the accepted system-Python installer behavior.
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' || fail "Python 3.11 or newer is required."
"$PYTHON" -m venv --help >/dev/null 2>&1 || fail "Python venv is unavailable. On Ubuntu install python3-venv."
require_command codex "Install the Codex CLI first."
codex login status >/dev/null 2>&1 || fail "Run codex login before installing."
require_command fc-list "Install fontconfig and a CJK font. On Ubuntu: sudo apt install fontconfig fonts-noto-cjk."
[ -n "$(fc-list :lang=zh family 2>/dev/null | head -n 1)" ] || fail "Install a CJK font. On Ubuntu: sudo apt install fonts-noto-cjk."
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
INSTALL_ROOT="${IMAGE_PPTGEN_INSTALL_ROOT:-$DATA_HOME/image-pptgen}"
CONFIG_ROOT="${IMAGE_PPTGEN_CONFIG_ROOT:-$CONFIG_HOME/image-pptgen}"
mkdir -p "$INSTALL_ROOT/releases" "$INSTALL_ROOT/venvs" "$INSTALL_ROOT/state/data/artifacts" "$CONFIG_ROOT" || fail "Cannot create writable user directories."
INSTALL_ID="$VERSION-$(printf '%s' "$ARCHIVE_SHA" | cut -c1-12)"
RELEASE_TARGET="$INSTALL_ROOT/releases/$INSTALL_ID"
VENV_TARGET="$INSTALL_ROOT/venvs/$INSTALL_ID"
if [ -f "$RELEASE_TARGET/app/public_server.py" ]; then
  rm -rf -- "$RELEASE_ROOT"
else
  rm -rf -- "$RELEASE_TARGET"
  mv -- "$RELEASE_ROOT" "$RELEASE_TARGET"
fi
if ! "$VENV_TARGET/bin/image-pptgen" --help >/dev/null 2>&1; then
  rm -rf -- "$VENV_TARGET"
  if ! ("$PYTHON" -m venv "$VENV_TARGET"
    "$VENV_TARGET/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
    "$VENV_TARGET/bin/python" -m pip install --disable-pip-version-check -q -r "$RELEASE_TARGET/app/requirements.txt"
    "$VENV_TARGET/bin/python" -m pip install --disable-pip-version-check -q "$RELEASE_TARGET/app/packages/pptgen_toolkit"
    "$VENV_TARGET/bin/python" -c 'import flask, PIL, flask_cors, requests, waitress, pptgen_toolkit'
    "$VENV_TARGET/bin/image-pptgen" --help >/dev/null
  ); then
    rm -rf -- "$VENV_TARGET"
    fail "Python environment installation failed; the previous Image installation remains active."
  fi
fi
ln -sfn "$RELEASE_TARGET" "$INSTALL_ROOT/current"
ln -sfn "$VENV_TARGET" "$INSTALL_ROOT/current-venv"
BIN_HOME="${IMAGE_PPTGEN_BIN_HOME:-$HOME/.local/bin}"
SKILL_HOME="${IMAGE_PPTGEN_SKILL_HOME:-$HOME/.agents/skills}"
mkdir -p "$BIN_HOME" "$SKILL_HOME" || fail "Cannot create writable user command/Skill directories."
SKILL_SOURCE="$INSTALL_ROOT/current/app/skills/generate-image-presentation"
SKILL_TARGET="$SKILL_HOME/generate-image-presentation"
SKILL_STAGE="$SKILL_HOME/.generate-image-presentation.new.$$"
cp -R -- "$SKILL_SOURCE" "$SKILL_STAGE"
if [ -e "$SKILL_TARGET" ] && ! diff -qr "$SKILL_TARGET" "$SKILL_STAGE" >/dev/null 2>&1; then
  mkdir -p "$INSTALL_ROOT/backups"
  mv -- "$SKILL_TARGET" "$INSTALL_ROOT/backups/generate-image-presentation.before-$VERSION-$$"
fi
rm -rf -- "$SKILL_TARGET"; mv -- "$SKILL_STAGE" "$SKILL_TARGET"
ENV_FILE="$CONFIG_ROOT/env"
if [ ! -e "$ENV_FILE" ]; then
  printf 'PPTGEN_PUBLIC_DATA_DIR=%s\nPPT_DB_PATH=%s\nPPT_ARTIFACTS_DIR=%s\nPPTGEN_HISTORICAL_DATA_DIR=%s\nPPTGEN_HOST=127.0.0.1\nPPTGEN_PORT=3130\nIMAGE_PPTGEN_HOST=127.0.0.1\nIMAGE_PPTGEN_PORT=3130\nIMAGE_PPTGEN_BASE_URL=http://127.0.0.1:3130\n' "$INSTALL_ROOT/state/data" "$INSTALL_ROOT/state/data/ppt.db" "$INSTALL_ROOT/state/data/artifacts" "$INSTALL_ROOT/state/data/historical-data" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
cp -- "$INSTALL_ROOT/current/app/image-pptgen-wrapper.sh" "$BIN_HOME/image-pptgen"
cp -- "$INSTALL_ROOT/current/app/image-pptgen-server-wrapper.sh" "$BIN_HOME/image-pptgen-server"
chmod 755 "$BIN_HOME/image-pptgen" "$BIN_HOME/image-pptgen-server"
"$BIN_HOME/image-pptgen" --help >/dev/null
READY_JSON="$WORK_DIR/runtime-ready.json"; READY_ERROR="$WORK_DIR/runtime-ready.error"
if ! "$VENV_TARGET/bin/python" "$INSTALL_ROOT/current/app/runtime_manager.py" ensure-ready --json >"$READY_JSON" 2>"$READY_ERROR"; then
  cat "$READY_ERROR" >&2 || true
  fail "Image service could not become ready; the previous installation remains active."
fi
"$PYTHON" - "$READY_JSON" "$INSTALL_ROOT/current/app/release-identity.json" "$VERSION" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle: ready = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle: release = json.load(handle)
if (ready.get("ok") is not True or ready.get("base_url") != "http://127.0.0.1:3130"
        or ready.get("version") != sys.argv[3] or ready.get("version") != release.get("version")
        or ready.get("build_id") != release.get("build_id") or not isinstance(ready.get("instance_id"), str)
        or not ready["instance_id"].strip()): raise SystemExit("runtime health identity mismatch")
PY
printf '\nImage PPTGen %s installed and ready.\n' "$VERSION"
printf 'Start in a fresh Codex task, paste your source material, and invoke $generate-image-presentation.\n'
printf '  export PATH="%s:$PATH"\n' "$BIN_HOME"
printf '  image-pptgen doctor --json\n'
'''


def _render_shell_bootstrap(*, version: str, dist_base_url: str, manifest_url: str, r2_base_url: str) -> str:
    return (
        (_SHELL_BOOTSTRAP_TEMPLATE + _SHELL_BOOTSTRAP_SUFFIX)
        .replace("__VERSION__", _shell_literal(version))
        .replace("__DIST_BASE_URL__", _shell_literal(dist_base_url))
        .replace("__MANIFEST_URL__", _shell_literal(manifest_url))
        .replace("__R2_BASE_URL__", _shell_literal(r2_base_url))
    )


_POWERSHELL_BOOTSTRAP_TEMPLATE = r'''[CmdletBinding()]
param(
    [string]$OfficialPython
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$Version = __VERSION__
$DistBaseUrl = __DIST_BASE_URL__
$ManifestUrl = __MANIFEST_URL__
$R2BaseUrl = __R2_BASE_URL__
$Platform = 'windows-amd64'

function Stop-Bootstrap([string]$Message) {
    [Console]::Error.WriteLine(('Image PPTGen bootstrap stopped: ' + $Message))
    exit 1
}

function Convert-ToSafeUri([string]$Value, [string]$Field) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw ($Field + ' URL is empty') }
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri)) { throw ($Field + ' URL is invalid') }
    if ($uri.Scheme -notin @('http', 'https') -or [string]::IsNullOrWhiteSpace($uri.Host) -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $Value -match '[\x00-\x20\x7f]') {
        throw ($Field + ' URL is unsafe')
    }
    return $uri.AbsoluteUri
}

function Download-File([string]$Url, [string]$Destination) {
    Invoke-WebRequest -UseBasicParsing -Uri (Convert-ToSafeUri $Url 'download') -OutFile $Destination
}

function Assert-FileIdentity([string]$Path, [long]$ExpectedSize, [string]$ExpectedSha256) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item -or $item.PSIsContainer -or $item.Length -ne $ExpectedSize) { throw 'downloaded file size does not match the manifest' }
    $observed = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($observed -ne $ExpectedSha256.ToLowerInvariant()) { throw 'downloaded file SHA-256 does not match the manifest' }
}

function Assert-SafeMemberName([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name.Contains('\') -or $Name.Contains("`0") -or $Name.StartsWith('/') -or $Name -match '^[A-Za-z]:') { throw ('unsafe archive member: ' + $Name) }
    foreach ($part in $Name.Split('/')) {
        if ([string]::IsNullOrWhiteSpace($part) -or $part -in @('.', '..') -or $part.EndsWith(' ') -or $part.EndsWith('.') -or $part.Contains(':')) { throw ('unsafe archive member: ' + $Name) }
        $stem = $part.TrimEnd(' ', '.').Split('.')[0].ToUpperInvariant()
        if ($stem -in @('CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9')) { throw ('unsafe Windows device member: ' + $Name) }
    }
}

function Get-NativeOutputTail([string]$Value, [int]$MaxChars = 12000) {
    if ($null -eq $Value) { return '' }
    $text = $Value.Trim()
    if ($text.Length -le $MaxChars) { return $text }
    return ('[native output truncated; showing final ' + $MaxChars + ' characters]' + [Environment]::NewLine + $text.Substring($text.Length - $MaxChars))
}

function Redact-NativeOutput([string]$Value) {
    $text = Get-NativeOutputTail $Value
    if ([string]::IsNullOrWhiteSpace($text)) { return '' }
    # Native diagnostics are useful evidence, but their output can echo
    # credentials or user-specific paths. Keep stable error text while
    # redacting those values before it reaches a receipt or final error.
    $text = [regex]::Replace($text, '(?i)(api[_-]?key|access[_-]?token|token|authorization|password|secret|cookie)\s*[:=]\s*[^\s\r\n]+', '[REDACTED]')
    $text = [regex]::Replace($text, '(?i)\bBearer\s+[^\s\r\n]+', 'Bearer [REDACTED]')
    $text = [regex]::Replace($text, '(?im)([A-Z]:\\Users\\|\\\\)[^\r\n]+', '<redacted-path>')
    return $text
}

function Write-Utf8NoBom([string]$Path, [string]$Value) {
    # Windows PowerShell 5.1's `-Encoding UTF8` writes a BOM.  The Python
    # controller intentionally decodes these receipts as strict UTF-8, so
    # write the bootstrap-owned JSON/script bytes explicitly and without one.
    $encoding = [System.Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($Path, $Value, $encoding)
}

function New-OfficialProbeFailure([string]$Stage, [int]$ExitCode, [string]$Message) {
    [pscustomobject]@{
        Stage = $Stage
        FilePath = ''
        ArgumentList = @()
        ExitCode = $ExitCode
        Stdout = ''
        Stderr = $Message
        Exception = ''
    }
}

function New-OfficialAttemptRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Approach,
        [Parameter(Mandatory = $true)][bool]$Succeeded,
        [Parameter(Mandatory = $false)][object]$Probe
    )
    if ($null -eq $Probe) {
        $Probe = New-OfficialProbeFailure ('official-probe:' + $Approach + ':unknown') 1 'official probe produced no result'
    }
    $result = if ($Succeeded) { 'succeeded' } else { 'failed' }
    [ordered]@{
        approach = $Approach
        result = $result
        stage = [string]$Probe.Stage
        exit_code = [int]$Probe.ExitCode
        stderr = Redact-NativeOutput ([string]$Probe.Stderr)
        stdout = Redact-NativeOutput ([string]$Probe.Stdout)
        exception = Redact-NativeOutput ([string]$Probe.Exception)
    }
}

function Format-OfficialAttemptDiagnostics([object[]]$Attempts) {
    $lines = @()
    foreach ($attempt in @($Attempts)) {
        if ($null -eq $attempt) { continue }
        $lines += ('approach: ' + [string]$attempt.approach + '; stage: ' + [string]$attempt.stage + '; exit: ' + [string]$attempt.exit_code)
        foreach ($field in @('stderr', 'stdout', 'exception')) {
            $value = Redact-NativeOutput ([string]$attempt.$field)
            if (-not [string]::IsNullOrWhiteSpace($value)) { $lines += ($field + ': ' + $value) }
        }
    }
    if ($lines.Count -eq 0) { return '' }
    return ('official Runtime attempts:' + [Environment]::NewLine + ($lines -join [Environment]::NewLine))
}

function Invoke-NativeStage {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $false)][string[]]$ArgumentList = @()
    )
    $captureRoot = $script:NativeCaptureRoot
    if ([string]::IsNullOrWhiteSpace($captureRoot)) { $captureRoot = [IO.Path]::GetTempPath() }
    New-Item -ItemType Directory -Path $captureRoot -Force | Out-Null
    $token = [Guid]::NewGuid().ToString('N')
    $stdoutPath = Join-Path $captureRoot ('native-' + $token + '.stdout')
    $stderrPath = Join-Path $captureRoot ('native-' + $token + '.stderr')
    $previousEap = $ErrorActionPreference
    $exitCode = 1
    $exceptionText = ''
    try {
        # Native stderr is data for this seam. Do not let the bootstrap-wide
        # Stop policy turn it into a terminating PowerShell error record. The
        # argument array is splatted so PS5.1 preserves argv boundaries and
        # Unicode paths rather than rebuilding a command-line string.
        $ErrorActionPreference = 'Continue'
        & $FilePath @ArgumentList 1> $stdoutPath 2> $stderrPath
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    } catch {
        $exceptionText = ($_ | Out-String).Trim()
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousEap
    }
    $stdout = if (Test-Path -LiteralPath $stdoutPath -PathType Leaf) { [IO.File]::ReadAllText($stdoutPath) } else { '' }
    $stderr = if (Test-Path -LiteralPath $stderrPath -PathType Leaf) { [IO.File]::ReadAllText($stderrPath) } else { '' }
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Stage = $Stage
        FilePath = $FilePath
        ArgumentList = @($ArgumentList)
        ExitCode = [int]$exitCode
        Stdout = [string]$stdout
        Stderr = [string]$stderr
        Exception = [string]$exceptionText
    }
}

function Format-NativeFailure([object]$Result) {
    $parts = @()
    $stderr = Redact-NativeOutput ([string]$Result.Stderr)
    $stdout = Redact-NativeOutput ([string]$Result.Stdout)
    $exception = Redact-NativeOutput ([string]$Result.Exception)
    if (-not [string]::IsNullOrWhiteSpace($stderr)) { $parts += 'stderr: ' + $stderr }
    if (-not [string]::IsNullOrWhiteSpace($stdout)) { $parts += 'stdout: ' + $stdout }
    if (-not [string]::IsNullOrWhiteSpace($exception)) { $parts += 'exception: ' + $exception }
    $detail = if ($parts.Count) { $parts -join ([Environment]::NewLine) } else { 'no native output captured' }
    return ('native stage ' + [string]$Result.Stage + ' failed (exit ' + [string]$Result.ExitCode + '):' + [Environment]::NewLine + $detail)
}

function Resolve-ManifestObjectUrl([object]$Metadata, [string]$Field) {
    $pathProperty = $Metadata.PSObject.Properties['path']
    if (-not $pathProperty -or -not ($pathProperty.Value -is [string])) { throw ($Field + ' path is unsafe') }
    $path = [string]$pathProperty.Value
    Assert-SafeMemberName $path
    $urlProperty = $Metadata.PSObject.Properties['url']
    if ($urlProperty -and $urlProperty.Value -is [string] -and -not [string]::IsNullOrWhiteSpace([string]$urlProperty.Value)) {
        return Convert-ToSafeUri ([string]$urlProperty.Value) $Field
    }
    return Convert-ToSafeUri ($R2BaseUrl.TrimEnd('/') + '/' + $path) $Field
}

function Expand-SafeZip([string]$Archive, [string]$Destination, [string]$ExpectedRoot, [bool]$RequireRoot) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $destinationFull = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
    $seen = @{}; $roots = @{}
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        if ($zip.Entries.Count -eq 0) { throw 'archive is empty' }
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName; $normalized = $name.TrimEnd('/')
            Assert-SafeMemberName $normalized
            $folded = $normalized.ToLowerInvariant()
            if ($seen.ContainsKey($folded)) { throw ('duplicate archive member: ' + $name) }
            $seen[$folded] = $true; $roots[$normalized.Split('/')[0]] = $true
            $target = [IO.Path]::GetFullPath((Join-Path $Destination ($normalized.Replace('/', '\'))))
            if (-not $target.StartsWith($destinationFull + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'archive member escapes staging' }
            if ($name.EndsWith('/') -or [string]::IsNullOrEmpty($entry.Name)) {
                New-Item -ItemType Directory -Path $target -Force | Out-Null
                continue
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            $stream = $entry.Open()
            try {
                $output = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try { $stream.CopyTo($output); $output.Flush() } finally { $output.Dispose() }
            } finally { $stream.Dispose() }
        }
    } finally { $zip.Dispose() }
    if ($RequireRoot -and ($roots.Count -ne 1 -or -not $roots.ContainsKey($ExpectedRoot))) { throw ('archive root is not ' + $ExpectedRoot) }
}

function Expand-SafeTarGz([string]$Archive, [string]$Destination, [string]$ExpectedRoot) {
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if (-not $tar) { throw 'Windows tar.exe is required to unpack the frozen Runtime' }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $seen = @{}; $roots = @{}
    $listingResult = Invoke-NativeStage -Stage 'fallback-runtime:archive-list' -FilePath $tar.Source -ArgumentList @('-tzf', $Archive)
    if ($listingResult.ExitCode -ne 0) { throw (Format-NativeFailure $listingResult) }
    $listing = if ([string]::IsNullOrEmpty($listingResult.Stdout)) { @() } else { $listingResult.Stdout -split "`r?`n" }
    foreach ($line in $listing) {
        $name = ([string]$line).Trim().TrimEnd('/')
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        Assert-SafeMemberName $name
        $folded = $name.ToLowerInvariant()
        if ($seen.ContainsKey($folded)) { throw ('duplicate frozen Runtime member: ' + $name) }
        $seen[$folded] = $true; $roots[$name.Split('/')[0]] = $true
    }
    $detailsResult = Invoke-NativeStage -Stage 'fallback-runtime:archive-type-list' -FilePath $tar.Source -ArgumentList @('-tvzf', $Archive)
    if ($detailsResult.ExitCode -ne 0) { throw (Format-NativeFailure $detailsResult) }
    $details = if ([string]::IsNullOrEmpty($detailsResult.Stdout)) { @() } else { $detailsResult.Stdout -split "`r?`n" }
    foreach ($line in $details) {
        $text = [string]$line
        if ($text -match '(^|\s)[lshbc][rwx-]{9}\s' -or $text -match '\s->\s') { throw 'frozen Runtime archive contains a link or special member' }
    }
    if ($roots.Count -ne 1 -or -not $roots.ContainsKey($ExpectedRoot)) { throw ('frozen Runtime root is not ' + $ExpectedRoot) }
    $extractResult = Invoke-NativeStage -Stage 'fallback-runtime:archive-extract' -FilePath $tar.Source -ArgumentList @('-xzf', $Archive, '-C', $Destination, '--no-same-owner', '--no-same-permissions')
    if ($extractResult.ExitCode -ne 0) { throw (Format-NativeFailure $extractResult) }
    foreach ($item in Get-ChildItem -LiteralPath $Destination -Recurse -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'frozen Runtime extraction produced a reparse point' }
    }
}

function Test-OfficialApproach([string]$Candidate, [string]$Approach, [string]$ProbeRoot) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        $script:LastOfficialProbe = New-OfficialProbeFailure ('official-probe:' + $Approach + ':candidate') 127 'official Runtime candidate was not discovered'
        return $false
    }
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        $script:LastOfficialProbe = New-OfficialProbeFailure ('official-probe:' + $Approach + ':candidate') 127 'official Runtime candidate is not a file'
        return $false
    }
    # Do not send a nested Python `-c` program through PS5.1 native argv
    # reconstruction.  PS5.1 can remove the inner quotes from a tuple/list
    # literal, turning valid Python into `NameError: amd64 is not defined`.
    # A disposable workRoot script keeps the native argv to two simple paths.
    $identityProbePath = Join-Path $script:NativeCaptureRoot ('official-identity-' + $Approach + '-' + [Guid]::NewGuid().ToString('N') + '.py')
    $identityProbeSource = @'
import os
import platform
import sys

expected = os.path.realpath(sys.argv[1])
actual = os.path.realpath(sys.executable)
is_supported = (
    actual == expected
    and sys.version_info >= (3, 11)
    and platform.machine().lower() in {"amd64", "x86_64"}
)
raise SystemExit(0 if is_supported else 1)
'@
    try {
        Write-Utf8NoBom $identityProbePath ($identityProbeSource.TrimStart() + [Environment]::NewLine)
        $identityResult = Invoke-NativeStage -Stage ('official-probe:' + $Approach + ':identity') -FilePath $Candidate -ArgumentList @('-I', $identityProbePath, $Candidate)
    } finally {
        Remove-Item -LiteralPath $identityProbePath -Force -ErrorAction SilentlyContinue
    }
    $script:LastOfficialProbe = $identityResult
    if ($identityResult.ExitCode -ne 0) { return $false }
    if (Test-Path -LiteralPath $ProbeRoot) { Remove-Item -LiteralPath $ProbeRoot -Recurse -Force }
    if ($Approach -eq 'venv-ensurepip') {
        $venvResult = Invoke-NativeStage -Stage ('official-probe:' + $Approach + ':venv') -FilePath $Candidate -ArgumentList @('-I', '-m', 'venv', '--copies', $ProbeRoot)
        $script:LastOfficialProbe = $venvResult
        if ($venvResult.ExitCode -ne 0) { return $false }
        $probePython = Join-Path $ProbeRoot 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $probePython -PathType Leaf)) {
            $script:LastOfficialProbe = New-OfficialProbeFailure ('official-probe:' + $Approach + ':venv-output') 126 'official probe Python executable was not created'
            return $false
        }
        $pipResult = Invoke-NativeStage -Stage ('official-probe:' + $Approach + ':pip') -FilePath $probePython -ArgumentList @('-I', '-m', 'pip', '--version')
        $script:LastOfficialProbe = $pipResult
        return $pipResult.ExitCode -eq 0
    }
    $venvResult = Invoke-NativeStage -Stage ('official-probe:' + $Approach + ':venv') -FilePath $Candidate -ArgumentList @('-I', '-m', 'venv', '--copies', '--without-pip', $ProbeRoot)
    $script:LastOfficialProbe = $venvResult
    if ($venvResult.ExitCode -ne 0) { return $false }
    $probePython = Join-Path $ProbeRoot 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $probePython -PathType Leaf)) {
        $script:LastOfficialProbe = New-OfficialProbeFailure ('official-probe:' + $Approach + ':venv-output') 126 'official probe Python executable was not created'
        return $false
    }
    # A Desktop-managed base Python may omit pip while its stdlib ensurepip
    # remains usable and offline. Bootstrap pip in this disposable probe only.
    $ensurePipResult = Invoke-NativeStage -Stage ('official-probe:' + $Approach + ':ensurepip') -FilePath $probePython -ArgumentList @('-I', '-m', 'ensurepip')
    $script:LastOfficialProbe = $ensurePipResult
    if ($ensurePipResult.ExitCode -ne 0) { return $false }
    $pipResult = Invoke-NativeStage -Stage ('official-probe:' + $Approach + ':pip') -FilePath $probePython -ArgumentList @('-I', '-m', 'pip', '--version')
    $script:LastOfficialProbe = $pipResult
    return $pipResult.ExitCode -eq 0
}

function Resolve-ManagedPythonCandidate([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    if (Test-Path -LiteralPath $Value -PathType Leaf) { return [IO.Path]::GetFullPath($Value) }
    foreach ($relative in @('python.exe', 'python\python.exe', 'bin\python.exe')) {
        $nested = Join-Path $Value $relative
        if (Test-Path -LiteralPath $nested -PathType Leaf) { return [IO.Path]::GetFullPath($nested) }
    }
    return $null
}

# Attempt 1 is exclusively the value injected by Desktop's
# load_workspace_dependencies (or its explicit equivalent).  It must not
# silently become a known-root discovery when that candidate is broken.
function Find-DynamicOfficialPython {
    foreach ($value in @($OfficialPython, $env:CODEX_WORKSPACE_PYTHON)) {
        $candidate = Resolve-ManagedPythonCandidate $value
        if ($candidate) { return $candidate }
    }
    return $null
}

# Attempt 2 intentionally probes only documented managed-primary layouts.
# Do not recursively enumerate %LOCALAPPDATA%\OpenAI\Codex: that can select a
# stale or user-installed interpreter which Desktop does not own.
function Find-KnownManagedPrimaryPython {
    foreach ($root in @(
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python'),
        (Join-Path $env:LOCALAPPDATA '.cache\codex-runtimes\codex-primary-runtime\dependencies\python'),
        (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\runtimes\codex-primary-runtime\dependencies\python'),
        (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\codex-primary-runtime\dependencies\python')
    )) {
        $candidate = Resolve-ManagedPythonCandidate $root
        if ($candidate) { return $candidate }
    }
    return $null
}

$workRoot = Join-Path ([IO.Path]::GetTempPath()) ('image-pptgen-bootstrap-' + [Guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
    $script:NativeCaptureRoot = $workRoot
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64') { throw 'Unsupported Image PPTGen Windows architecture' }
    Convert-ToSafeUri $ManifestUrl 'manifest' | Out-Null
    $manifestPath = Join-Path $workRoot 'aggregate-manifest.json'
    Download-File $ManifestUrl $manifestPath
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema_version -ne 2 -or $manifest.product -ne 'image-pptgen' -or $manifest.version -ne $Version) { throw 'aggregate manifest schema/product/version mismatch' }
    $entryProperty = $manifest.platforms.PSObject.Properties[$Platform]
    if (-not $entryProperty) { throw 'aggregate manifest has no Windows AMD64 entry' }
    $entry = $entryProperty.Value; $archive = $entry.archive
    $archiveName = [string]$archive.name; $expectedName = 'image-pptgen-' + $Version + '-windows-amd64.zip'
    if ($archiveName -ne $expectedName -or $archiveName -match '[^A-Za-z0-9._+-]') { throw 'platform archive name is unsafe' }
    $archiveSize = [int64]$archive.size; $archiveSha = ([string]$archive.sha256).ToLowerInvariant()
    if ($archiveSize -le 0 -or $archiveSha -notmatch '^[0-9a-f]{64}$') { throw 'platform archive identity is invalid' }
    $archiveUrl = Resolve-ManifestObjectUrl $archive 'archive'
    $archivePath = Join-Path $workRoot $archiveName
    Download-File $archiveUrl $archivePath; Assert-FileIdentity $archivePath $archiveSize $archiveSha
    $extractRoot = Join-Path $workRoot 'application'
    Expand-SafeZip $archivePath $extractRoot ('image-pptgen-' + $Version) $true
    $releaseRoot = Join-Path $extractRoot ('image-pptgen-' + $Version)
    $lockPath = Join-Path $releaseRoot 'windows\fallback-lock.json'
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) { throw 'Windows fallback lock is missing from the application archive' }
    $lock = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $fallback = $entry.fallback_runtime; $lockPlatform = $lock.platforms.PSObject.Properties[$Platform].Value; $runtimeLock = $lockPlatform.runtime_asset
    if ($lock.schema_version -ne 1 -or $lock.freeze_id -ne $fallback.freeze_id) { throw 'Windows fallback lock identity is inconsistent' }
    $runtimeLayout = $lockPlatform.runtime_archive_layout
    $runtimeRoot = [string]$runtimeLayout.member_root
    $runtimeRel = [string]$runtimeLayout.python_exe
    if ([string]::IsNullOrWhiteSpace($runtimeRoot) -or [string]::IsNullOrWhiteSpace($runtimeRel)) { throw 'Windows fallback Runtime archive layout is missing from the frozen lock' }
    Assert-SafeMemberName $runtimeRoot
    Assert-SafeMemberName ($runtimeRel.Replace('\', '/'))
    if (('' + $runtimeRoot + '/' + ($runtimeRel.Replace('\', '/'))) -ne 'python/python.exe') { throw 'Windows fallback Runtime archive layout is not the frozen delivery contract' }

    $official = $null; $officialSuccess = $false; $selectedOfficialApproach = $null
    $script:LastOfficialProbe = $null
    $script:OfficialAttemptDetails = @()
    $dynamicOfficial = Find-DynamicOfficialPython
    if (Test-OfficialApproach $dynamicOfficial 'venv-ensurepip' (Join-Path $workRoot 'probe-ensurepip')) {
        $official = $dynamicOfficial; $officialSuccess = $true
        $selectedOfficialApproach = 'venv-ensurepip'
        $script:OfficialAttemptDetails += (New-OfficialAttemptRecord -Approach 'venv-ensurepip' -Succeeded $true -Probe $script:LastOfficialProbe)
    } else {
        $script:OfficialAttemptDetails += (New-OfficialAttemptRecord -Approach 'venv-ensurepip' -Succeeded $false -Probe $script:LastOfficialProbe)
        $knownManagedOfficial = Find-KnownManagedPrimaryPython
        if (Test-OfficialApproach $knownManagedOfficial 'venv-explicit-ensurepip' (Join-Path $workRoot 'probe-explicit-ensurepip')) {
            $official = $knownManagedOfficial; $officialSuccess = $true
            $selectedOfficialApproach = 'venv-explicit-ensurepip'
            $script:OfficialAttemptDetails += (New-OfficialAttemptRecord -Approach 'venv-explicit-ensurepip' -Succeeded $true -Probe $script:LastOfficialProbe)
        } else {
            $script:OfficialAttemptDetails += (New-OfficialAttemptRecord -Approach 'venv-explicit-ensurepip' -Succeeded $false -Probe $script:LastOfficialProbe)
        }
    }
    $installRoot = if ($env:IMAGE_PPTGEN_INSTALL_ROOT) { $env:IMAGE_PPTGEN_INSTALL_ROOT } else { Join-Path $env:LOCALAPPDATA 'ImagePPTGen' }
    $skillRoot = if ($env:IMAGE_PPTGEN_SKILL_HOME) { $env:IMAGE_PPTGEN_SKILL_HOME } else { Join-Path $env:USERPROFILE '.agents\skills' }
    $platformInstaller = Join-Path $releaseRoot 'windows\install.ps1'
    if (-not (Test-Path -LiteralPath $platformInstaller -PathType Leaf)) { throw 'Windows platform installer is missing' }
    if (-not $officialSuccess) {
        $fallbackName = [string]$fallback.name; $fallbackSize = [int64]$fallback.size; $fallbackSha = ([string]$fallback.sha256).ToLowerInvariant()
        if ($fallbackName -ne [string]$runtimeLock.filename -or $fallbackSize -ne [int64]$runtimeLock.bytes -or $fallbackSha -ne ([string]$runtimeLock.sha256).ToLowerInvariant()) { throw 'fallback Runtime identity is not bound to the frozen lock' }
        $fallbackArchive = Join-Path $workRoot $fallbackName
        $fallbackUrl = Resolve-ManifestObjectUrl $fallback 'fallback Runtime'
        Download-File $fallbackUrl $fallbackArchive; Assert-FileIdentity $fallbackArchive $fallbackSize $fallbackSha
        $fallbackStage = Join-Path $workRoot 'fallback-runtime'; Expand-SafeTarGz $fallbackArchive $fallbackStage $runtimeRoot
        $fallbackRoot = Join-Path $fallbackStage $runtimeRoot
        $fallbackPython = [IO.Path]::GetFullPath((Join-Path $fallbackRoot ($runtimeRel.Replace('/', '\'))))
        if (-not (Test-Path -LiteralPath $fallbackPython -PathType Leaf)) { throw 'frozen fallback Runtime Python executable is missing' }
        $receiptPath = Join-Path $workRoot 'fallback-authorization.json'
        $receiptJson = [ordered]@{ schema_version = 1; platform = 'windows-amd64'; freeze_id = [string]$fallback.freeze_id; decision = 'fallback_authorized'; official_attempts = @($script:OfficialAttemptDetails); fallback_runtime = [ordered]@{ archive_sha256 = $fallbackSha; archive_bytes = $fallbackSize; extracted_root = [IO.Path]::GetFullPath($fallbackRoot); python_path = $fallbackPython } } | ConvertTo-Json -Depth 8
        Write-Utf8NoBom $receiptPath ($receiptJson + [Environment]::NewLine)
        $installerArgs = @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $platformInstaller, '-Action', 'Install')
        if (-not [string]::IsNullOrWhiteSpace($official)) { $installerArgs += @('-OfficialPython', $official) }
        $installerArgs += @('-FallbackPythonRoot', $fallbackRoot, '-FallbackAuthorizationFile', $receiptPath, '-RuntimeSelectionReceipt', $receiptPath, '-PayloadZip', $archivePath, '-PayloadSha256', $archiveSha, '-PayloadSize', [string]$archiveSize, '-Version', $Version, '-InstallRoot', $installRoot, '-SkillRoot', $skillRoot)
        $installerResult = Invoke-NativeStage -Stage 'platform-installer:fallback' -FilePath 'powershell.exe' -ArgumentList $installerArgs
        if ($installerResult.ExitCode -ne 0) { throw ('Windows fallback platform installer failed. ' + (Format-NativeFailure $installerResult)) }
        exit 0
    }
    if ([string]::IsNullOrWhiteSpace($official)) { throw 'official Runtime probe returned success without an executable' }
    if ([string]::IsNullOrWhiteSpace($selectedOfficialApproach)) { throw 'official Runtime probe returned success without an approach' }
    Write-Output ('Image PPTGen bootstrap selected official Runtime approach: ' + $selectedOfficialApproach)
    $receiptPath = Join-Path $workRoot 'official-runtime-selection.json'
    $receiptJson = [ordered]@{ schema_version = 1; platform = 'windows-amd64'; decision = 'official_selected'; selected_approach = $selectedOfficialApproach; official_attempts = @($script:OfficialAttemptDetails) } | ConvertTo-Json -Depth 8
    Write-Utf8NoBom $receiptPath ($receiptJson + [Environment]::NewLine)
    $installerArgs = @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $platformInstaller, '-Action', 'Install', '-OfficialPython', $official, '-RuntimeSelectionReceipt', $receiptPath, '-PayloadZip', $archivePath, '-PayloadSha256', $archiveSha, '-PayloadSize', [string]$archiveSize, '-Version', $Version, '-InstallRoot', $installRoot, '-SkillRoot', $skillRoot)
    $installerResult = Invoke-NativeStage -Stage 'platform-installer:official' -FilePath 'powershell.exe' -ArgumentList $installerArgs
    if ($installerResult.ExitCode -ne 0) { throw ('official Runtime platform installer failed; fallback was not downloaded. ' + (Format-NativeFailure $installerResult)) }
} catch {
    $message = Redact-NativeOutput ([string]$_.Exception.Message)
    $attemptDiagnostics = Format-OfficialAttemptDiagnostics $script:OfficialAttemptDetails
    if (-not [string]::IsNullOrWhiteSpace($attemptDiagnostics)) {
        $message += [Environment]::NewLine + $attemptDiagnostics
    }
    Stop-Bootstrap $message
}
finally {
    $script:NativeCaptureRoot = $null
    if (Test-Path -LiteralPath $workRoot) { Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
'''


def _render_powershell_bootstrap(*, version: str, dist_base_url: str, manifest_url: str, r2_base_url: str) -> str:
    return (
        _POWERSHELL_BOOTSTRAP_TEMPLATE
        .replace("__VERSION__", _powershell_literal(version))
        .replace("__DIST_BASE_URL__", _powershell_literal(dist_base_url))
        .replace("__MANIFEST_URL__", _powershell_literal(manifest_url))
        .replace("__R2_BASE_URL__", _powershell_literal(r2_base_url))
    )


_PUBLIC_PYTHON_BOOTSTRAP_TEMPLATE = r'''#!/usr/bin/env python3
"""Public Windows installer executed by Codex's managed primary CPython 3.12.

This file is intentionally self-contained until the signed, hash-verified
Windows payload has been staged.  It uses only the Python standard library for
network and archive work, then loads the payload's existing transactional
controller in this same interpreter.  No shell or platform package manager is
part of the public install path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import ntpath
import os
import platform
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


VERSION = __VERSION__
PLATFORM = "windows-amd64"
DIST_BASE_URL = __DIST_BASE_URL__
MANIFEST_URL = __MANIFEST_URL__
R2_BASE_URL = __R2_BASE_URL__
INSTALLER_USER_AGENT = f"ImagePPTGen-Installer/{VERSION}"
MANIFEST_SIZE = __MANIFEST_SIZE__
MANIFEST_SHA256 = __MANIFEST_SHA256__
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_PAYLOAD_BYTES = 512 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 12000
_SHA256_RE = r"^[0-9a-f]{64}$"
_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$"
_VERSION_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_REQUIRED_PAYLOAD_PATHS = (
    "app/runtime_manager.py",
    "app/image-launcher.py",
    "app/release-identity.json",
    "app/skills/generate-image-presentation/SKILL.md",
    "windows/requirements.lock",
    "windows/fallback-lock.json",
    "windows/windows_installer.py",
    "licenses/windows-amd64-licenses.zip",
)


class BootstrapError(RuntimeError):
    """A stable public bootstrap error which must not be masked by cleanup."""

    def __init__(self, code: str, message: str, *, exit_code: int = 3) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(message)


def _bounded(value: object) -> str:
    text = str(value)
    return text[-MAX_DIAGNOSTIC_CHARS:]


def _record(
    stages: list[dict[str, object]],
    stage_id: str,
    *,
    status: str,
    error_code: str | None = None,
    exit_code: int = 0,
    evidence_paths: list[str] | None = None,
    stdout: str = "",
    stderr: str = "",
) -> None:
    stages.append(
        {
            "stage_id": stage_id,
            "status": status,
            "error_code": error_code,
            "exit_code": exit_code,
            "evidence_paths": list(evidence_paths or []),
            "stdout": _bounded(stdout),
            "stderr": _bounded(stderr),
        }
    )


def _run_stage(stages: list[dict[str, object]], stage_id: str, action):
    try:
        result = action()
    except BootstrapError as exc:
        _record(
            stages,
            stage_id,
            status="failed",
            error_code=exc.code,
            exit_code=exc.exit_code,
            stderr=str(exc),
        )
        raise
    except Exception as exc:
        error_code = getattr(exc, "code", None)
        if not isinstance(error_code, str) or not error_code:
            error_code = "stage_failed"
        failure = BootstrapError(
            error_code,
            f"{stage_id}: {_bounded(exc)}",
        )
        _record(
            stages,
            stage_id,
            status="failed",
            error_code=failure.code,
            exit_code=failure.exit_code,
            stderr=str(failure),
        )
        raise failure from exc
    _record(stages, stage_id, status="succeeded")
    return result


def _safe_url(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BootstrapError("invalid_url", f"{field} URL is missing")
    if any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
        raise BootstrapError("invalid_url", f"{field} URL contains whitespace or controls")
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BootstrapError("invalid_url", f"{field} URL is not a safe HTTP(S) URL")
    return value


def _safe_relative(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BootstrapError("invalid_manifest", f"{field} path is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BootstrapError("invalid_manifest", f"{field} path is unsafe")
    return path.as_posix()


def _object_url(metadata: dict[str, object], *, field: str) -> str:
    path = _safe_relative(metadata.get("path"), field=f"{field}.path")
    direct = metadata.get("url")
    if direct is not None:
        return _safe_url(direct, field=field)
    return _safe_url(R2_BASE_URL.rstrip("/") + "/" + path, field=field)


def _metadata(metadata: object, *, field: str, expected_name: str | None = None) -> tuple[str, int, str, str]:
    if not isinstance(metadata, dict):
        raise BootstrapError("invalid_manifest", f"{field} metadata is missing")
    name, size, sha256 = metadata.get("name"), metadata.get("size"), metadata.get("sha256")
    if (
        not isinstance(name, str)
        or re.fullmatch(_NAME_RE, name) is None
        or (expected_name is not None and name != expected_name)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or not isinstance(sha256, str)
        or re.fullmatch(_SHA256_RE, sha256.lower()) is None
    ):
        raise BootstrapError("invalid_manifest", f"{field} metadata is invalid")
    return name, size, sha256.lower(), _object_url(metadata, field=field)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_verified(
    url: str,
    destination: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    max_bytes: int,
) -> None:
    """Download into a fresh file and verify size/hash before it is consumed."""

    _safe_url(url, field="download")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise BootstrapError("download_target_exists", "download target is not fresh")
    observed = 0
    digest = hashlib.sha256()
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": INSTALLER_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as output:
            status = getattr(response, "status", 200)
            if status not in (None, 200):
                raise BootstrapError("download_failed", f"download returned HTTP {status}")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > max_bytes or (expected_size is not None and observed > expected_size):
                    raise BootstrapError("download_size_mismatch", "download exceeded its declared size")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    except BootstrapError:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    except (OSError, urllib.error.URLError, ValueError) as exc:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise BootstrapError("download_failed", f"download failed: {_bounded(exc)}") from exc
    observed_sha256 = digest.hexdigest()
    if expected_size is not None and observed != expected_size:
        destination.unlink(missing_ok=True)
        raise BootstrapError("download_size_mismatch", "download size does not match manifest")
    if expected_sha256 is not None and observed_sha256 != expected_sha256.lower():
        destination.unlink(missing_ok=True)
        raise BootstrapError("download_sha256_mismatch", "download SHA-256 does not match manifest")


def _unsafe_member(info: zipfile.ZipInfo) -> str | None:
    raw = info.filename
    if not raw or "\x00" in raw or "\\" in raw:
        return "empty, NUL, or backslash path"
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return "absolute or traversal path"
    for part in path.parts:
        if ":" in part or part.endswith((" ", ".")):
            return "Windows alternate-stream or ambiguous path"
        stem = part.rstrip(" .").split(".", 1)[0].upper()
        if stem in _RESERVED_WINDOWS_NAMES:
            return "Windows reserved device name"
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode) or (file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
        return "symbolic link or non-regular member"
    if info.flag_bits & 0x1:
        return "encrypted member"
    return None


def _safe_extract_payload(archive: Path, destination: Path, version: str) -> Path:
    expected_root = f"image-pptgen-{version}"
    destination.mkdir(parents=True, exist_ok=False)
    destination_root = destination.resolve()
    seen: set[str] = set()
    names: list[str] = []
    total = 0
    try:
        handle = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BootstrapError("payload_invalid", "Windows payload is not a valid ZIP") from exc
    try:
        infos = handle.infolist()
        if not infos:
            raise BootstrapError("payload_invalid", "Windows payload ZIP is empty")
        for info in infos:
            reason = _unsafe_member(info)
            folded = info.filename.rstrip("/").casefold()
            if reason or not folded or folded in seen:
                raise BootstrapError("unsafe_payload_member", f"Unsafe ZIP member: {info.filename}")
            seen.add(folded)
            names.append(info.filename)
            total += info.file_size
            if total > MAX_UNCOMPRESSED_PAYLOAD_BYTES:
                raise BootstrapError("payload_expansion_too_large", "Windows payload is too large")
        roots = {PurePosixPath(name).parts[0] for name in names}
        if roots != {expected_root}:
            raise BootstrapError("payload_root_mismatch", "Windows payload root does not match version")
        required = {f"{expected_root}/{relative}".casefold() for relative in _REQUIRED_PAYLOAD_PATHS}
        if not required <= seen:
            raise BootstrapError("payload_member_missing", "Windows payload is missing required members")
        if not any(
            name.casefold().startswith(f"{expected_root}/wheelhouse/".casefold())
            and name.casefold().endswith(".whl")
            for name in names
        ):
            raise BootstrapError("payload_member_missing", "Windows payload wheelhouse is empty")
        for info in infos:
            parts = PurePosixPath(info.filename).parts
            target = destination.joinpath(*parts)
            try:
                target.parent.resolve().relative_to(destination_root)
            except ValueError as exc:
                raise BootstrapError("unsafe_payload_member", "ZIP member escaped staging") from exc
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info) as source, target.open("xb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
    finally:
        handle.close()
    return destination / expected_root


def _load_controller(release_root: Path):
    controller_path = release_root / "windows" / "windows_installer.py"
    if controller_path.is_symlink() or not controller_path.is_file():
        raise BootstrapError("controller_missing", "Windows transactional controller is missing")
    spec = importlib.util.spec_from_file_location("image_windows_installer_public", controller_path)
    if spec is None or spec.loader is None:
        raise BootstrapError("controller_invalid", "Windows transactional controller is unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise BootstrapError("controller_invalid", f"Windows transactional controller failed to load: {_bounded(exc)}") from exc
    return module


_BOOTSTRAP_PYTHON_VERSION = (3, 12)
_MANAGED_RUNTIME_NAME = "codex-primary-runtime"
_MANAGED_PYTHON_RELATIVE = ("dependencies", "python", "python.exe")
_MANAGED_PLUGIN_RELATIVE = ("plugins", "openai-primary-runtime")
_MANAGED_RUNTIME_COMPAT_RELATIVE = (
    (".cache", "codex-runtimes", _MANAGED_RUNTIME_NAME),
    ("AppData", "Local", ".cache", "codex-runtimes", _MANAGED_RUNTIME_NAME),
    ("AppData", "Local", "OpenAI", "Codex", "runtimes", _MANAGED_RUNTIME_NAME),
    ("AppData", "Local", "OpenAI", "Codex", _MANAGED_RUNTIME_NAME),
)


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _managed_path_text(value: object) -> str:
    """Normalize Windows and POSIX fixture paths for exact identity checks."""

    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip().replace("/", "\\")
    # Windows extended-length paths are an alternate spelling of the same
    # path.  The runtime evidence uses this form in config.toml.
    for prefix in ("\\\\?\\", "\\\\.\\", "\\??\\"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return ntpath.normcase(ntpath.normpath(text))


def _assert_managed_regular(path: Path, *, label: str, directory: bool) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise BootstrapError(
            "managed_runtime_path_unavailable",
            f"{label} cannot be inspected: {_bounded(exc)}",
        ) from exc
    if stat.S_ISLNK(file_stat.st_mode) or _is_reparse_point(file_stat):
        raise BootstrapError(
            "managed_runtime_reparse",
            f"{label} must not be a symlink or reparse point",
        )
    is_expected_type = stat.S_ISDIR(file_stat.st_mode) if directory else stat.S_ISREG(file_stat.st_mode)
    if not is_expected_type:
        expected = "directory" if directory else "regular file"
        raise BootstrapError(
            "managed_runtime_not_regular",
            f"{label} must be a regular {expected}",
        )


def _desktop_profile_root() -> Path:
    """Return the current Desktop user's profile without inspecting other users."""

    value = os.environ.get("USERPROFILE")
    profile = Path(value).expanduser() if isinstance(value, str) and value.strip() else Path.home()
    if not profile.is_absolute():
        raise BootstrapError(
            "managed_runtime_profile_invalid",
            "the current Desktop user profile must be an absolute path",
        )
    _assert_managed_regular(profile, label="Desktop user profile", directory=True)
    return profile


def _managed_runtime_roots(profile: Path) -> tuple[Path, ...]:
    return tuple(profile.joinpath(*relative) for relative in _MANAGED_RUNTIME_COMPAT_RELATIVE)


def _assert_profile_layout(runtime_root: Path, profile: Path) -> None:
    """Check every candidate-layout segment, but never profile ancestors."""

    normalized_root = _managed_path_text(str(runtime_root))
    allowed = {_managed_path_text(str(root)) for root in _managed_runtime_roots(profile)}
    if normalized_root not in allowed:
        raise BootstrapError(
            "bootstrap_python_not_managed_runtime",
            "--bootstrap-python must be under the current Desktop user's "
            "Codex primary runtime cache",
        )
    try:
        relative = runtime_root.relative_to(profile)
    except ValueError as exc:
        raise BootstrapError(
            "bootstrap_python_not_managed_runtime",
            "managed runtime is outside the current Desktop user's profile",
        ) from exc
    current = profile
    for part in relative.parts:
        current = current / part
        _assert_managed_regular(
            current,
            label="managed runtime layout segment",
            directory=True,
        )


def _runtime_root_from_python(supplied: Path) -> Path:
    """Derive and authorize the primary runtime root from its fixed layout."""

    parts = supplied.parts
    if len(parts) < 4 or supplied.name.casefold() != "python.exe":
        raise BootstrapError(
            "bootstrap_python_not_managed_runtime",
            "--bootstrap-python must be Codex managed primary runtime "
            "dependencies/python/python.exe, not an external or virtualenv interpreter",
        )
    runtime_root = supplied.parent.parent.parent
    if runtime_root.name.casefold() != _MANAGED_RUNTIME_NAME:
        raise BootstrapError(
            "bootstrap_python_not_managed_runtime",
            "--bootstrap-python must be under codex-primary-runtime/dependencies/python/python.exe",
        )
    expected = runtime_root.joinpath(*_MANAGED_PYTHON_RELATIVE)
    if _managed_path_text(str(expected)) != _managed_path_text(str(supplied)):
        raise BootstrapError(
            "bootstrap_python_not_managed_runtime",
            "--bootstrap-python must use the fixed managed primary runtime interpreter",
        )
    profile = _desktop_profile_root()
    _assert_profile_layout(runtime_root, profile)
    _assert_managed_regular(runtime_root, label="managed runtime root", directory=True)
    _assert_managed_regular(
        runtime_root / "dependencies",
        label="managed runtime dependencies directory",
        directory=True,
    )
    _assert_managed_regular(
        runtime_root / "dependencies" / "python",
        label="managed runtime Python directory",
        directory=True,
    )
    return runtime_root


def _assert_managed_profile(runtime_root: Path) -> Path:
    profile = _desktop_profile_root()
    _assert_profile_layout(runtime_root, profile)
    profile_dir = profile / ".codex"
    _assert_managed_regular(profile_dir, label="Desktop profile directory", directory=True)
    config_path = profile_dir / "config.toml"
    _assert_managed_regular(config_path, label="Desktop profile config", directory=False)
    try:
        import tomllib
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BootstrapError(
            "managed_runtime_config_invalid",
            f"Desktop profile config is not valid TOML: {_bounded(exc)}",
        ) from exc
    marketplaces = config.get("marketplaces") if isinstance(config, dict) else None
    runtime_entry = (
        marketplaces.get("openai-primary-runtime")
        if isinstance(marketplaces, dict)
        else None
    )
    source_type = runtime_entry.get("source_type") if isinstance(runtime_entry, dict) else None
    source = runtime_entry.get("source") if isinstance(runtime_entry, dict) else None
    expected_source = runtime_root.joinpath(*_MANAGED_PLUGIN_RELATIVE)
    expected_text = _managed_path_text(str(expected_source))
    if source_type != "local" or not isinstance(source, str) or _managed_path_text(source) != expected_text:
        raise BootstrapError(
            "managed_runtime_config_mismatch",
            "Desktop profile config must bind marketplaces.openai-primary-runtime "
            "with source_type=local and the exact runtime plugin source",
        )
    return config_path


def _assert_managed_runtime_metadata(runtime_root: Path) -> Path:
    metadata_path = runtime_root / "runtime.json"
    _assert_managed_regular(metadata_path, label="managed runtime metadata", directory=False)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(
            "managed_runtime_metadata_invalid",
            f"managed runtime metadata is not valid JSON: {_bounded(exc)}",
        ) from exc
    version = getattr(sys, "version_info", ())
    expected_version = ".".join(str(part) for part in tuple(version[:3]))
    plugin_marker = _managed_path_text("/".join(_MANAGED_PLUGIN_RELATIVE))

    def contains_plugin(value: object) -> bool:
        if isinstance(value, str):
            normalized = _managed_path_text(value)
            return normalized == plugin_marker or normalized.endswith("\\" + plugin_marker)
        if isinstance(value, dict):
            return any(contains_plugin(child) for child in value.values())
        if isinstance(value, list):
            return any(contains_plugin(child) for child in value)
        return False

    if (
        not isinstance(metadata, dict)
        or metadata.get("bundleFormatVersion") != 2
        or not isinstance(metadata.get("bundleVersion"), str)
        or not metadata["bundleVersion"].strip()
        or metadata.get("targetPlatform") != "win32"
        or metadata.get("targetArch") != "x64"
        or metadata.get("pythonVersion") != expected_version
        or not contains_plugin(metadata.get("bundledPlugins"))
    ):
        raise BootstrapError(
            "managed_runtime_metadata_mismatch",
            "runtime.json must declare targetPlatform=win32, targetArch=x64, "
            f"pythonVersion={expected_version}, bundleFormatVersion=2, a non-empty "
            "bundleVersion, and bundledPlugins containing plugins/openai-primary-runtime",
        )
    return metadata_path


def _bootstrap_python(value: object) -> Path:
    managed_prerequisite = (
        "Codex managed primary CPython 3.12 is required; pass its absolute "
        "dependencies/python/python.exe path with --bootstrap-python"
    )
    if not isinstance(value, str) or not value.strip():
        raise BootstrapError("bootstrap_python_missing", managed_prerequisite)
    candidate = Path(value)
    if not candidate.is_absolute():
        raise BootstrapError(
            "bootstrap_python_not_absolute",
            "--bootstrap-python must be an absolute path; " + managed_prerequisite,
        )
    # Keep a non-resolving absolute spelling for layout and reparse checks.
    # ``Path.resolve`` follows parent junctions/symlinks and would erase the
    # evidence that the caller supplied an untrusted path.
    lexical_candidate = Path(os.path.abspath(candidate))
    try:
        file_stat = lexical_candidate.lstat()
    except OSError as exc:
        raise BootstrapError(
            "bootstrap_python_unavailable",
            f"--bootstrap-python cannot be inspected: {_bounded(exc)}; {managed_prerequisite}",
        ) from exc
    if stat.S_ISLNK(file_stat.st_mode) or _is_reparse_point(file_stat):
        raise BootstrapError(
            "bootstrap_python_reparse",
            "--bootstrap-python must not be a symlink or reparse point; "
            + managed_prerequisite,
        )
    if not stat.S_ISREG(file_stat.st_mode):
        raise BootstrapError(
            "bootstrap_python_not_regular",
            "--bootstrap-python must identify a regular file; " + managed_prerequisite,
        )
    try:
        supplied = lexical_candidate.resolve(strict=True)
        current = Path(sys.executable).resolve(strict=True)
    except OSError as exc:
        raise BootstrapError(
            "bootstrap_python_unavailable",
            f"--bootstrap-python cannot be resolved: {_bounded(exc)}; {managed_prerequisite}",
        ) from exc
    if supplied != current:
        raise BootstrapError(
            "bootstrap_python_mismatch",
            "--bootstrap-python must resolve to sys.executable; " + managed_prerequisite,
        )
    implementation = getattr(getattr(sys, "implementation", None), "name", "")
    if not isinstance(implementation, str) or implementation.casefold() != "cpython":
        raise BootstrapError(
            "bootstrap_python_not_cpython",
            "--bootstrap-python must execute CPython; " + managed_prerequisite,
        )
    version = getattr(sys, "version_info", ())
    if tuple(version[:2]) != _BOOTSTRAP_PYTHON_VERSION:
        required = ".".join(str(part) for part in _BOOTSTRAP_PYTHON_VERSION)
        observed = ".".join(str(part) for part in tuple(version[:2]))
        raise BootstrapError(
            "bootstrap_python_version_mismatch",
            f"--bootstrap-python must execute CPython {required}, not {observed}; "
            + managed_prerequisite,
        )
    # Validate the lexical parent chain after interpreter identity/version
    # checks but before trusting the resolved path. Resolving first would
    # allow a junction or symlink parent to hide an untrusted path.
    runtime_root = _runtime_root_from_python(lexical_candidate)
    _assert_managed_runtime_metadata(runtime_root)
    _assert_managed_profile(runtime_root)
    return supplied


def _assert_supported_platform() -> None:
    if (
        platform.system().casefold() != "windows"
        or platform.machine().casefold() not in {"amd64", "x86_64"}
    ):
        raise BootstrapError("unsupported_platform", "install.py only supports Windows AMD64")


def _manifest(path: Path, manifest_url: str) -> tuple[dict[str, object], dict[str, object], str, int, str, str]:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("manifest_invalid", "aggregate manifest is unreadable") from exc
    if not isinstance(root, dict) or root.get("schema_version") != 2 or root.get("product") != "image-pptgen" or root.get("version") != VERSION:
        raise BootstrapError("manifest_invalid", "aggregate manifest schema/product/version mismatch")
    platforms = root.get("platforms")
    entry = platforms.get(PLATFORM) if isinstance(platforms, dict) else None
    if not isinstance(entry, dict):
        raise BootstrapError("manifest_platform_missing", "aggregate manifest has no Windows AMD64 entry")
    archive = entry.get("archive")
    expected_name = f"image-pptgen-{VERSION}-windows-amd64.zip"
    name, size, sha256, url = _metadata(archive, field="archive", expected_name=expected_name)
    return root, entry, name, size, sha256, url


def _default_install_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ImagePPTGen"
    return Path.home() / "AppData" / "Local" / "ImagePPTGen"


def _default_skill_root() -> Path:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile) / ".agents" / "skills"
    return Path.home() / ".agents" / "skills"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install Image PPTGen on Windows AMD64")
    parser.add_argument(
        "--bootstrap-python",
        help="absolute Codex managed primary CPython 3.12 executing this file",
    )
    parser.add_argument("--manifest-url", default=MANIFEST_URL)
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--skill-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    stages: list[dict[str, object]] = []
    result: dict[str, object] = {
        "ok": False,
        "platform": PLATFORM,
        "version": VERSION,
        "stages": stages,
    }
    try:
        bootstrap_python = _run_stage(
            stages,
            "bootstrap-python",
            lambda: _bootstrap_python(args.bootstrap_python),
        )
        _run_stage(stages, "platform", _assert_supported_platform)
        with tempfile.TemporaryDirectory(prefix="image-pptgen-install-") as temporary:
            work_root = Path(temporary)
            manifest_path = work_root / "aggregate-manifest.json"
            _run_stage(
                stages,
                "manifest-download",
                lambda: _download_verified(
                    args.manifest_url,
                    manifest_path,
                    expected_size=MANIFEST_SIZE,
                    expected_sha256=MANIFEST_SHA256,
                    max_bytes=MAX_MANIFEST_BYTES,
                ),
            )
            _root, _entry, archive_name, archive_size, archive_sha256, archive_url = _run_stage(
                stages,
                "manifest-validate",
                lambda: _manifest(manifest_path, args.manifest_url),
            )
            archive_path = work_root / archive_name
            _run_stage(
                stages,
                "payload-download",
                lambda: _download_verified(
                    archive_url,
                    archive_path,
                    expected_size=archive_size,
                    expected_sha256=archive_sha256,
                    max_bytes=MAX_UNCOMPRESSED_PAYLOAD_BYTES,
                ),
            )
            extract_root = work_root / "application"
            release_root = _run_stage(
                stages,
                "payload-extract",
                lambda: _safe_extract_payload(archive_path, extract_root, VERSION),
            )
            controller = _run_stage(
                stages,
                "controller-load",
                lambda: _load_controller(release_root),
            )
            install_root_value = args.install_root
            if install_root_value is None:
                install_root_value = (
                    Path(os.environ["IMAGE_PPTGEN_INSTALL_ROOT"])
                    if os.environ.get("IMAGE_PPTGEN_INSTALL_ROOT")
                    else _default_install_root()
                )
            skill_root_value = args.skill_root
            if skill_root_value is None:
                skill_root_value = (
                    Path(os.environ["IMAGE_PPTGEN_SKILL_HOME"])
                    if os.environ.get("IMAGE_PPTGEN_SKILL_HOME")
                    else _default_skill_root()
                )
            install_root = install_root_value.expanduser().resolve()
            skill_root = skill_root_value.expanduser().resolve()
            request = controller.InstallRequest(
                payload=archive_path,
                payload_sha256=archive_sha256,
                payload_size=archive_size,
                version=VERSION,
                install_root=install_root,
                skill_root=skill_root,
                base_python=bootstrap_python,
                runtime_source="official",
                platform_root=release_root / "windows",
                runtime_selection_receipt=None,
            )
            installed = _run_stage(
                stages,
                "transactional-install",
                lambda: controller.install_release(request),
            )
            result.update(installed if isinstance(installed, dict) else {})
            result["ok"] = True
            if isinstance(installed, dict) and isinstance(installed.get("active"), dict):
                result["evidence_paths"] = [str(install_root / "state" / "windows-install-state.json")]
    except BootstrapError as exc:
        result.update({"error_code": exc.code, "message": _bounded(exc)})
        sys.stderr.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        return exc.exit_code
    except Exception as exc:
        result.update({"error_code": "install_failed", "message": _bounded(exc)})
        sys.stderr.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        return 3
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _render_public_python_bootstrap(
    *,
    version: str,
    dist_base_url: str,
    manifest_url: str,
    r2_base_url: str,
    manifest_size: int | None = None,
    manifest_sha256: str | None = None,
) -> str:
    """Render the preferred public Windows entrypoint.

    The generated script is deliberately independent of repository imports:
    the only non-stdlib code it loads is the controller from the verified
    release ZIP.  ``dist_base_url`` remains part of the versioned receipt and
    keeps the same explicit distribution provenance as the legacy bootstraps.
    """

    return (
        _PUBLIC_PYTHON_BOOTSTRAP_TEMPLATE
        .replace("__VERSION__", json.dumps(version, ensure_ascii=False))
        .replace("__DIST_BASE_URL__", json.dumps(dist_base_url, ensure_ascii=False))
        .replace("__MANIFEST_URL__", json.dumps(manifest_url, ensure_ascii=False))
        .replace("__R2_BASE_URL__", json.dumps(r2_base_url, ensure_ascii=False))
        .replace("__MANIFEST_SIZE__", "None" if manifest_size is None else str(manifest_size))
        .replace(
            "__MANIFEST_SHA256__",
            "None" if manifest_sha256 is None else json.dumps(manifest_sha256),
        )
    )


def _render_pages(
    pages_root: Path,
    *,
    version: str,
    dist_base_url: str,
    manifest_url: str,
    r2_base_url: str,
    manifest: Mapping[str, Any],
) -> Path:
    manifest_path = _safe_output_target(
        pages_root, f"releases/{version}/manifest.json"
    )
    _write_text_immutable(manifest_path, _json_bytes(manifest))
    shell = _render_shell_bootstrap(
        version=version,
        dist_base_url=dist_base_url,
        manifest_url=manifest_url,
        r2_base_url=r2_base_url,
    ).encode()
    powershell = _render_powershell_bootstrap(
        version=version,
        dist_base_url=dist_base_url,
        manifest_url=manifest_url,
        r2_base_url=r2_base_url,
    ).encode()
    manifest_bytes = _json_bytes(manifest)
    public_python = _render_public_python_bootstrap(
        version=version,
        dist_base_url=dist_base_url,
        manifest_url=manifest_url,
        r2_base_url=r2_base_url,
        manifest_size=len(manifest_bytes),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    ).encode()
    install_md = (
        "# Image PPTGen versioned installer\n\n"
        f"Distribution: {dist_base_url}\n\nManifest: {manifest_url}\n\n"
        f"R2 payload base: {r2_base_url}\n\n"
        "Preferred Windows entry: run this versioned install.py with the absolute Codex "
        "managed primary CPython 3.12 and --bootstrap-python pointing to that same "
        "interpreter. The legacy "
        "install.ps1 remains available for compatibility only. Linux and macOS keep their "
        "existing bootstrap semantics.\n"
    ).encode()
    install_sh = _safe_output_target(pages_root, "install.sh")
    _write_text_immutable(install_sh, shell)
    install_sh.chmod(0o755)
    _write_text_immutable(_safe_output_target(pages_root, "install.ps1"), powershell)
    _write_text_immutable(_safe_output_target(pages_root, "install.py"), public_python)
    _write_text_immutable(
        _safe_output_target(pages_root, f"releases/{version}/install.py"), public_python
    )
    _write_text_immutable(_safe_output_target(pages_root, "install.md"), install_md)
    _write_text_immutable(
        _safe_output_target(pages_root, "_headers"),
        b"/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n  X-Content-Type-Options: nosniff\n  Cache-Control: no-store\n",
    )
    _write_text_immutable(
        _safe_output_target(pages_root, "robots.txt"), b"User-agent: *\nDisallow: /\n"
    )
    _write_text_immutable(
        _safe_output_target(pages_root, "index.html"),
        b'<!doctype html><meta name="robots" content="noindex,nofollow"><title>Image PPTGen</title><p>Temporary Image PPTGen distribution.</p>\n',
    )
    _assert_pages_text_only(pages_root)
    return manifest_path


def build_multiplatform_release(
    repo_root: Path,
    output_root: Path,
    *,
    version: str,
    dist_base_url: str,
    manifest_url: str,
    r2_root: Path,
    r2_prefix: str,
    r2_base_url: str,
    r2_ledger_path: Path,
    fallback_assets_root: Path,
) -> dict[str, Any]:
    """Build and stage one immutable three-platform release."""

    if not _VERSION_RE.fullmatch(version):
        raise BuildError("release version is invalid")
    repo_root = repo_root.expanduser().resolve(strict=True)
    output_root = output_root.expanduser().resolve()
    r2_root = r2_root.expanduser().resolve()
    r2_ledger_path = r2_ledger_path.expanduser().resolve()
    fallback_assets_root = fallback_assets_root.expanduser().resolve(strict=True)
    if not fallback_assets_root.is_dir():
        raise BuildError("fallback_assets_root must be a verified asset directory")
    dist_base_url = _validate_url(dist_base_url, field="dist_base_url")
    manifest_url = _validate_url(manifest_url, field="manifest_url")
    r2_base_url = _validate_url(r2_base_url, field="r2_base_url")
    r2_prefix = _validate_prefix(r2_prefix)
    pages_root = output_root / "pages-dist"
    if _paths_overlap(pages_root, r2_root):
        raise BuildError("Pages and R2 output roots must be physically separate")
    if r2_ledger_path == pages_root or r2_ledger_path.is_relative_to(pages_root):
        raise BuildError("R2 upload ledger must not be written inside the Pages tree")

    legacy = _load_legacy_builder()
    fallback_lock = _load_fallback_lock()
    if not isinstance(fallback_lock.get("freeze_id"), str):
        raise BuildError("fallback authority has no freeze identity")

    platforms: dict[str, Any] = {}
    ledger: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="image-pptgen-multiplatform-") as temporary:
        temporary_root = Path(temporary)
        for platform_id in TARGET_PLATFORMS:
            bundle = temporary_root / platform_id / f"image-pptgen-{version}"
            identity, fallback_runtime = _populate_platform_bundle(
                legacy=legacy,
                repo_root=repo_root,
                bundle=bundle,
                version=version,
                platform_id=platform_id,
                fallback_lock=fallback_lock,
                fallback_assets_root=fallback_assets_root,
            )
            suffix = ".zip" if platform_id == "windows-amd64" else ".tar.gz"
            archive_name = f"image-pptgen-{version}-{platform_id}{suffix}"
            built_archive = temporary_root / "archives" / archive_name
            if suffix == ".zip":
                _create_zip(bundle, built_archive)
            else:
                _create_tar(bundle, built_archive)
            object_path = f"{r2_prefix}/{version}/{identity['build_id']}/{archive_name}"
            target_archive = _safe_output_target(r2_root, object_path)
            _write_immutable(built_archive, target_archive)
            archive_meta = _object_metadata(
                target_archive,
                object_path=object_path,
                r2_base_url=r2_base_url,
                kind="application",
                platform_id=platform_id,
            )
            ledger.append(dict(archive_meta))
            entry: dict[str, Any] = {
                "build_id": identity["build_id"],
                "archive": archive_meta,
                "identity": identity,
            }
            if fallback_runtime is not None:
                source_runtime = Path(fallback_runtime.pop("source_path"))
                runtime_path = (
                    f"{r2_prefix}/{version}/{identity['build_id']}/fallback/"
                    f"{source_runtime.name}"
                )
                target_runtime = _safe_output_target(r2_root, runtime_path)
                _write_immutable(source_runtime, target_runtime)
                runtime_meta = _object_metadata(
                    target_runtime,
                    object_path=runtime_path,
                    r2_base_url=r2_base_url,
                    kind="fallback-runtime",
                    platform_id=platform_id,
                )
                if (
                    runtime_meta["sha256"] != fallback_runtime["sha256"]
                    or runtime_meta["size"] != fallback_runtime["size"]
                ):
                    raise BuildError(f"staged fallback Runtime drifted: {platform_id}")
                runtime_meta["freeze_id"] = fallback_runtime["freeze_id"]
                entry["fallback_runtime"] = runtime_meta
                ledger.append(dict(runtime_meta))
            platforms[platform_id] = entry

    manifest = {
        "schema_version": 2,
        "product": "image-pptgen",
        "version": version,
        "manifest_url": manifest_url,
        "dist_base_url": dist_base_url,
        "r2_base_url": r2_base_url,
        "platforms": platforms,
    }
    manifest_path = _render_pages(
        pages_root,
        version=version,
        dist_base_url=dist_base_url,
        manifest_url=manifest_url,
        r2_base_url=r2_base_url,
        manifest=manifest,
    )
    ledger_payload = {
        "schema_version": 1,
        "product": "image-pptgen",
        "version": version,
        "r2_prefix": r2_prefix,
        "entries": sorted(ledger, key=lambda item: str(item["path"])),
    }
    _write_text_immutable(r2_ledger_path, _json_bytes(ledger_payload))
    return {
        "pages_root": pages_root,
        "manifest_path": manifest_path,
        "r2_root": r2_root,
        "r2_ledger_path": r2_ledger_path,
        "platforms": platforms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist-base-url", required=True)
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--r2-root", type=Path, required=True)
    parser.add_argument("--r2-prefix", required=True)
    parser.add_argument("--r2-base-url", required=True)
    parser.add_argument("--r2-ledger-path", type=Path, required=True)
    parser.add_argument("--fallback-assets-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_multiplatform_release(
            args.repo_root,
            args.output_root,
            version=args.version,
            dist_base_url=args.dist_base_url,
            manifest_url=args.manifest_url,
            r2_root=args.r2_root,
            r2_prefix=args.r2_prefix,
            r2_base_url=args.r2_base_url,
            r2_ledger_path=args.r2_ledger_path,
            fallback_assets_root=args.fallback_assets_root,
        )
    except BuildError as exc:
        parser.exit(2, f"multiplatform release build failed: {exc}\n")
    print(result["manifest_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
