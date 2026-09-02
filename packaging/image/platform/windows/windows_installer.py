#!/usr/bin/env python3
"""Local-only Windows AMD64 installer and lifecycle controller.

The distribution layer owns downloads and manifest authority.  This module
accepts one already-downloaded payload plus an exact size and SHA-256, creates
an immutable user-private release/venv pair, and atomically switches one state
file.  It never searches for Python and never accesses the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Mapping, NamedTuple
import uuid
import zipfile


PLATFORM = "windows-amd64"
STATE_SCHEMA = 1
MARKER_SCHEMA = 1
RUNTIME_SELECTION_SCHEMA = 1
MAX_UNCOMPRESSED_PAYLOAD_BYTES = 512 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
_FALLBACK_FREEZE_ID = "pbs-20260718-cp311-plus-cp312-v4"
_FALLBACK_ARCHIVE_SHA256 = "a48c2dbe832319f61aa8557c9900caec70f7fed0cbee391a4c9ff9f98b50222d"
_FALLBACK_ARCHIVE_BYTES = 25678291
_OFFICIAL_APPROACHES = ("venv-ensurepip", "venv-explicit-ensurepip")
_ATTEMPT_FIELDS = (
    "approach",
    "result",
    "stage",
    "exit_code",
    "stderr",
    "stdout",
    "exception",
)
_MAX_DIAGNOSTIC_CHARS = 12000
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
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
    "licenses/windows-amd64-licenses.zip",
)
_PLATFORM_TOOL_NAMES = (
    "windows_installer.py",
    "image-pptgen.ps1",
    "image-pptgen.cmd",
    "image-pptgen-server.ps1",
    "image-pptgen-server.cmd",
    "image-pptgen-manage.ps1",
    "image-pptgen-manage.cmd",
)


class InstallerError(RuntimeError):
    """Stable machine-readable Windows installer failure."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = details
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "ok": False,
            "error": self.code,
            "message": str(self),
            "platform": PLATFORM,
        }
        if self.details:
            value["details"] = self.details
        return value


class PayloadContract(NamedTuple):
    root_name: str
    sha256: str
    size: int
    members: tuple[str, ...]


class InstallRequest(NamedTuple):
    payload: Path
    payload_sha256: str
    payload_size: int
    version: str
    install_root: Path
    skill_root: Path
    base_python: Path
    runtime_source: str
    platform_root: Path
    runtime_selection_receipt: Path | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    parent = _private_directory(path.parent)
    target = parent / path.name
    stage = parent / f".{path.name}.staging-{uuid.uuid4().hex}"
    try:
        with stage.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, target)
    finally:
        try:
            stage.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _selection_error(message: str) -> InstallerError:
    return InstallerError("runtime_selection_invalid", message)


def _redact_diagnostic(value: str) -> str:
    """Defence-in-depth normalization for bootstrap-provided diagnostics."""

    if len(value) > _MAX_DIAGNOSTIC_CHARS:
        raise _selection_error("Runtime selection diagnostic exceeds its bounded limit")
    value = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|token|authorization|password|secret|cookie)\s*[:=]\s*[^\s\r\n]+",
        "[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+[^\s\r\n]+", "Bearer [REDACTED]", value)
    return re.sub(r"(?im)(?:[A-Z]:\\Users\\|\\\\)[^\r\n]+", "<redacted-path>", value)


def _normalize_official_attempts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _selection_error("Runtime selection official_attempts must be a list")
    normalized: list[dict[str, Any]] = []
    for attempt in value:
        if not isinstance(attempt, dict) or set(attempt) != set(_ATTEMPT_FIELDS):
            raise _selection_error("Runtime selection attempt fields are invalid")
        approach = attempt["approach"]
        result = attempt["result"]
        stage = attempt["stage"]
        exit_code = attempt["exit_code"]
        if approach not in _OFFICIAL_APPROACHES or result not in {"failed", "succeeded"}:
            raise _selection_error("Runtime selection attempt result is invalid")
        if not isinstance(stage, str) or not stage.startswith(f"official-probe:{approach}:"):
            raise _selection_error("Runtime selection attempt stage is invalid")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not 0 <= exit_code <= 255:
            raise _selection_error("Runtime selection attempt exit code is invalid")
        if (result == "succeeded") != (exit_code == 0):
            raise _selection_error("Runtime selection attempt result and exit code disagree")
        diagnostics: dict[str, str] = {}
        for key in ("stderr", "stdout", "exception"):
            field = attempt[key]
            if not isinstance(field, str):
                raise _selection_error("Runtime selection diagnostic is invalid")
            diagnostics[key] = _redact_diagnostic(field)
        normalized.append(
            {
                "approach": approach,
                "result": result,
                "stage": stage,
                "exit_code": exit_code,
                **diagnostics,
            }
        )
    return normalized


def _normalize_runtime_selection_receipt(
    receipt_path: Path, *, runtime_source: str, base_python: Path
) -> dict[str, Any]:
    receipt = _read_object(receipt_path.expanduser().resolve())
    if receipt is None:
        raise _selection_error("Runtime selection receipt is unavailable or invalid")
    attempts = _normalize_official_attempts(receipt.get("official_attempts"))
    decision = receipt.get("decision")
    if receipt.get("schema_version") != RUNTIME_SELECTION_SCHEMA or receipt.get("platform") != PLATFORM:
        raise _selection_error("Runtime selection receipt identity is invalid")

    if runtime_source == "official":
        if set(receipt) != {
            "schema_version",
            "platform",
            "decision",
            "selected_approach",
            "official_attempts",
        } or decision != "official_selected":
            raise _selection_error("Official Runtime selection receipt is invalid")
        selected = receipt.get("selected_approach")
        if selected not in _OFFICIAL_APPROACHES or not 1 <= len(attempts) <= 2:
            raise _selection_error("Official Runtime selection receipt is incomplete")
        expected_approaches = list(_OFFICIAL_APPROACHES[: len(attempts)])
        if [attempt["approach"] for attempt in attempts] != expected_approaches:
            raise _selection_error("Official Runtime selection approaches are invalid")
        if attempts[-1]["approach"] != selected or attempts[-1]["result"] != "succeeded":
            raise _selection_error("Official Runtime selection does not bind its successful approach")
        if any(attempt["result"] != "failed" for attempt in attempts[:-1]):
            raise _selection_error("Official Runtime selection contains an unexpected success")
        return {
            "schema_version": RUNTIME_SELECTION_SCHEMA,
            "platform": PLATFORM,
            "decision": "official_selected",
            "selected_approach": selected,
            "official_attempts": attempts,
        }

    if set(receipt) != {
        "schema_version",
        "platform",
        "freeze_id",
        "decision",
        "official_attempts",
        "fallback_runtime",
    } or decision != "fallback_authorized":
        raise _selection_error("Fallback Runtime selection receipt is invalid")
    fallback = receipt.get("fallback_runtime")
    if (
        not isinstance(fallback, dict)
        or set(fallback) != {"archive_sha256", "archive_bytes", "extracted_root", "python_path"}
        or receipt.get("freeze_id") != _FALLBACK_FREEZE_ID
        or len(attempts) != 2
        or [attempt["approach"] for attempt in attempts] != list(_OFFICIAL_APPROACHES)
        or any(attempt["result"] != "failed" for attempt in attempts)
        or fallback.get("archive_sha256") != _FALLBACK_ARCHIVE_SHA256
        or fallback.get("archive_bytes") != _FALLBACK_ARCHIVE_BYTES
    ):
        raise _selection_error("Fallback Runtime selection receipt is incomplete")
    extracted_root = fallback.get("extracted_root")
    python_path = fallback.get("python_path")
    if not isinstance(extracted_root, str) or not isinstance(python_path, str):
        raise _selection_error("Fallback Runtime selection receipt paths are invalid")
    try:
        root = Path(extracted_root).expanduser().resolve(strict=True)
        selected_python = Path(python_path).expanduser().resolve(strict=True)
        supplied_python = base_python.expanduser().resolve(strict=True)
    except OSError as exc:
        raise _selection_error("Fallback Runtime selection receipt paths are unavailable") from exc
    if root / "python.exe" != selected_python or selected_python != supplied_python:
        raise _selection_error("Fallback Runtime selection receipt does not bind the selected Python")
    return {
        "schema_version": RUNTIME_SELECTION_SCHEMA,
        "platform": PLATFORM,
        "decision": "fallback_authorized",
        "official_attempts": attempts,
        "fallback_runtime": {
            "freeze_id": _FALLBACK_FREEZE_ID,
            "archive_sha256": _FALLBACK_ARCHIVE_SHA256,
            "archive_bytes": _FALLBACK_ARCHIVE_BYTES,
        },
    }


def _validate_persisted_runtime_selection(
    value: Any, *, runtime_source: str
) -> dict[str, Any]:
    """Validate the path-free, durable form embedded in an installed entry."""

    if not isinstance(value, dict):
        raise _selection_error("Persisted runtime selection is invalid")
    attempts = _normalize_official_attempts(value.get("official_attempts"))
    if runtime_source == "official":
        expected_keys = {
            "schema_version",
            "platform",
            "decision",
            "selected_approach",
            "official_attempts",
        }
        selected = value.get("selected_approach")
        if (
            set(value) != expected_keys
            or value.get("schema_version") != RUNTIME_SELECTION_SCHEMA
            or value.get("platform") != PLATFORM
            or value.get("decision") != "official_selected"
            or selected not in _OFFICIAL_APPROACHES
            or not 1 <= len(attempts) <= 2
            or [attempt["approach"] for attempt in attempts]
            != list(_OFFICIAL_APPROACHES[: len(attempts)])
            or attempts[-1]["approach"] != selected
            or attempts[-1]["result"] != "succeeded"
            or any(attempt["result"] != "failed" for attempt in attempts[:-1])
        ):
            raise _selection_error("Persisted official Runtime selection is invalid")
        normalized = {
            "schema_version": RUNTIME_SELECTION_SCHEMA,
            "platform": PLATFORM,
            "decision": "official_selected",
            "selected_approach": selected,
            "official_attempts": attempts,
        }
    else:
        fallback = value.get("fallback_runtime")
        expected_keys = {
            "schema_version",
            "platform",
            "decision",
            "official_attempts",
            "fallback_runtime",
        }
        if (
            set(value) != expected_keys
            or value.get("schema_version") != RUNTIME_SELECTION_SCHEMA
            or value.get("platform") != PLATFORM
            or value.get("decision") != "fallback_authorized"
            or not isinstance(fallback, dict)
            or fallback
            != {
                "freeze_id": _FALLBACK_FREEZE_ID,
                "archive_sha256": _FALLBACK_ARCHIVE_SHA256,
                "archive_bytes": _FALLBACK_ARCHIVE_BYTES,
            }
            or len(attempts) != 2
            or [attempt["approach"] for attempt in attempts] != list(_OFFICIAL_APPROACHES)
            or any(attempt["result"] != "failed" for attempt in attempts)
        ):
            raise _selection_error("Persisted fallback Runtime selection is invalid")
        normalized = {
            "schema_version": RUNTIME_SELECTION_SCHEMA,
            "platform": PLATFORM,
            "decision": "fallback_authorized",
            "official_attempts": attempts,
            "fallback_runtime": dict(fallback),
        }
    if normalized != value:
        raise _selection_error("Persisted runtime selection is not canonical")
    return normalized


def _unsafe_member_reason(info: zipfile.ZipInfo) -> str | None:
    name = info.filename
    if not name or "\x00" in name or "\\" in name:
        return "empty, NUL, or backslash path"
    path = PurePosixPath(name)
    if path.is_absolute() or name.startswith(("/", "\\")):
        return "absolute path"
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return "relative traversal"
    for part in path.parts:
        if ":" in part or part.endswith((" ", ".")):
            return "Windows alternate-stream or ambiguous path"
        stem = part.rstrip(" .").split(".", 1)[0].upper()
        if stem in _RESERVED_WINDOWS_NAMES:
            return "Windows reserved device name"
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if stat.S_ISLNK(unix_mode):
        return "symbolic link"
    if file_type and not (stat.S_ISREG(unix_mode) or stat.S_ISDIR(unix_mode)):
        return "non-regular member"
    if info.flag_bits & 0x1:
        return "encrypted member"
    return None


def validate_payload(
    payload: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    version: str,
) -> PayloadContract:
    """Validate exact archive identity and every Windows extraction name."""

    archive = payload.expanduser().resolve()
    if not archive.is_file():
        raise InstallerError("payload_missing", "Windows payload ZIP does not exist")
    if not _VERSION_RE.fullmatch(version):
        raise InstallerError("invalid_version", "Windows payload version is invalid")
    if expected_size < 0 or archive.stat().st_size != expected_size:
        raise InstallerError(
            "payload_size_mismatch",
            "Windows payload size does not match the manifest",
            expected=expected_size,
            observed=archive.stat().st_size,
        )
    normalized_sha256 = expected_sha256.strip().lower()
    if not _SHA256_RE.fullmatch(normalized_sha256):
        raise InstallerError("invalid_payload_sha256", "Payload SHA-256 is invalid")
    observed_sha256 = _sha256_file(archive)
    if observed_sha256 != normalized_sha256:
        raise InstallerError(
            "payload_sha256_mismatch",
            "Windows payload SHA-256 does not match the manifest",
            expected=normalized_sha256,
            observed=observed_sha256,
        )
    expected_root = f"image-pptgen-{version}"
    names: list[str] = []
    seen: set[str] = set()
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                reason = _unsafe_member_reason(info)
                folded = info.filename.rstrip("/").casefold()
                if reason or not folded or folded in seen:
                    raise InstallerError(
                        "unsafe_payload_member",
                        f"Unsafe Windows ZIP member: {info.filename}",
                        reason=reason or "case-insensitive duplicate",
                    )
                seen.add(folded)
                names.append(info.filename)
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_PAYLOAD_BYTES:
                    raise InstallerError(
                        "payload_expansion_too_large",
                        "Windows payload exceeds the extraction safety limit",
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallerError("payload_invalid", "Windows payload is not a valid ZIP") from exc
    roots = {PurePosixPath(name).parts[0] for name in names}
    if roots != {expected_root}:
        raise InstallerError(
            "payload_root_mismatch",
            "Windows payload must contain exactly the expected release root",
            expected=expected_root,
            observed=sorted(roots),
        )
    for relative in _REQUIRED_PAYLOAD_PATHS:
        expected_member = f"{expected_root}/{relative}".casefold()
        if expected_member not in seen:
            raise InstallerError(
                "payload_member_missing",
                f"Windows payload is missing required member: {relative}",
            )
    if not any(
        name.casefold().startswith(f"{expected_root}/wheelhouse/".casefold())
        and name.casefold().endswith(".whl")
        for name in names
    ):
        raise InstallerError(
            "payload_member_missing", "Windows payload wheelhouse is empty"
        )
    return PayloadContract(
        expected_root, observed_sha256, archive.stat().st_size, tuple(names)
    )


def _extract_payload(payload: Path, destination: Path, contract: PayloadContract) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(payload) as handle:
        for info in handle.infolist():
            if _unsafe_member_reason(info):
                raise InstallerError(
                    "unsafe_payload_member",
                    f"Unsafe Windows ZIP member changed after validation: {info.filename}",
                )
            parts = PurePosixPath(info.filename).parts
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            with handle.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    root = destination / contract.root_name
    if not root.is_dir():
        raise InstallerError("payload_invalid", "Extracted Windows release root is missing")
    return root


def _validate_release_identity(release_root: Path, version: str) -> None:
    identity = _read_object(release_root / "app" / "release-identity.json")
    if not identity:
        raise InstallerError(
            "release_identity_invalid", "Windows release identity is unreadable"
        )
    expected = {
        "product": "image-pptgen",
        "service": "image-pptgen-server",
        "surface": "public_image_3_0",
        "version": version,
    }
    if any(identity.get(key) != value for key, value in expected.items()):
        raise InstallerError(
            "release_identity_mismatch",
            "Windows release identity does not match the install request",
        )
    for key, length in (
        ("build_id", None),
        ("source_commit", 40),
        ("skill_sha256", 64),
        ("runtime_content_sha256", 64),
    ):
        value = identity.get(key)
        if not isinstance(value, str) or not value or (
            length is not None and len(value) != length
        ):
            raise InstallerError(
                "release_identity_invalid",
                f"Windows release identity field is invalid: {key}",
            )


def _toolkit_wheel(toolkit: Path) -> Path | None:
    """Return the release's pre-built pure-Python toolkit wheel, if present.

    Current Desktop archives carry this wheel so the official Runtime does
    not need setuptools to execute a PEP 517 source build.  Older archives
    may not have it; those continue through the source-install compatibility
    path below and fail explicitly if their runtime lacks the build backend.
    """

    dist = toolkit / "dist"
    if not dist.is_dir():
        return None
    candidates = sorted(
        path for path in dist.glob("*.whl") if path.is_file() and not path.is_symlink()
    )
    if len(candidates) > 1:
        raise InstallerError(
            "toolkit_wheel_ambiguous",
            "Windows payload contains more than one toolkit wheel",
        )
    return candidates[0] if candidates else None


def _run_checked(command: list[str], *, code: str, cwd: Path | None = None) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise InstallerError(code, f"Windows runtime command could not start: {exc}") from exc
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout)[-2000:]
        raise InstallerError(code, "Windows runtime command failed", output=tail)


def _install_local_toolkit(
    python: Path, release_root: Path, *, force_reinstall: bool
) -> None:
    """Install the release-local toolkit without consulting an index.

    Windows console launchers embed the venv interpreter path.  The initial
    staging install and the final-path rebind must therefore both use the same
    frozen payload input, rather than trying to patch the generated launcher.
    """

    wheelhouse = release_root / "wheelhouse"
    toolkit = release_root / "app" / "packages" / "pptgen_toolkit"
    if not wheelhouse.is_dir() or not toolkit.is_dir():
        raise InstallerError(
            "offline_dependencies_missing",
            "Windows payload does not contain its locked offline toolkit inputs",
        )
    pip_base = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--find-links",
        str(wheelhouse),
    ]
    toolkit_wheel = _toolkit_wheel(toolkit)
    toolkit_target = toolkit_wheel or toolkit
    toolkit_build_flags = [] if toolkit_wheel is not None else ["--no-build-isolation"]
    reinstall_flags = ["--force-reinstall"] if force_reinstall else []
    _run_checked(
        [
            *pip_base,
            "--no-deps",
            *reinstall_flags,
            *toolkit_build_flags,
            str(toolkit_target),
        ],
        code="toolkit_install_failed",
    )


def _probe_final_runtime(python: Path, venv_root: Path) -> None:
    """Fail closed unless the final venv can run its product launcher."""

    _run_checked(
        [
            str(python),
            "-c",
            "import flask,PIL,flask_cors,waitress,pptgen_toolkit",
        ],
        code="runtime_probe_failed",
    )
    cli = venv_root / "Scripts" / "image-pptgen.exe"
    if not cli.is_file():
        raise InstallerError("runtime_probe_failed", "Windows Image CLI is missing")
    _run_checked([str(cli), "--help"], code="runtime_probe_failed")


def _create_venv(base_python: Path, venv_root: Path, release_root: Path) -> None:
    """Create the app environment strictly from the local payload wheelhouse."""

    if not base_python.is_file():
        raise InstallerError("python_missing", "Selected Windows Python does not exist")
    _run_checked(
        [
            str(base_python),
            "-I",
            "-c",
            "import sys,venv; raise SystemExit(0 if sys.version_info >= (3,11) else 4)",
        ],
        code="python_incompatible",
    )
    requirements = release_root / "windows" / "requirements.lock"
    wheelhouse = release_root / "wheelhouse"
    toolkit = release_root / "app" / "packages" / "pptgen_toolkit"
    if not requirements.is_file() or not wheelhouse.is_dir() or not toolkit.is_dir():
        raise InstallerError(
            "offline_dependencies_missing",
            "Windows payload does not contain its locked offline dependency inputs",
        )
    _run_checked(
        [str(base_python), "-m", "venv", str(venv_root)], code="venv_failed"
    )
    python = venv_root / "Scripts" / "python.exe"
    if not python.is_file():
        raise InstallerError(
            "venv_failed", "Windows venv did not create Scripts\\python.exe"
        )
    pip_base = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--find-links",
        str(wheelhouse),
    ]
    _run_checked(
        [*pip_base, "--require-hashes", "-r", str(requirements)],
        code="offline_dependency_install_failed",
    )
    _install_local_toolkit(python, release_root, force_reinstall=False)
    _probe_final_runtime(python, venv_root)


def _rebind_final_console_launcher(venv_root: Path, release_root: Path) -> None:
    """Recreate Windows console launchers after a staging venv is promoted.

    ``venv`` environments and their installed scripts are intentionally
    location-bound.  The venv interpreter remains usable after our controlled
    directory promotion, but the toolkit's generated ``image-pptgen.exe`` can
    still point to the deleted staging interpreter.  A forced offline toolkit
    reinstall from the final interpreter regenerates that launcher at the
    durable path before any state is committed.
    """

    python = venv_root / "Scripts" / "python.exe"
    if not python.is_file():
        raise InstallerError(
            "runtime_probe_failed", "Windows final venv did not create Scripts\\python.exe"
        )
    _install_local_toolkit(python, release_root, force_reinstall=True)
    _probe_final_runtime(python, venv_root)


def _state_path(install_root: Path) -> Path:
    return install_root / "state" / "windows-install-state.json"


def _entry(
    request: InstallRequest,
    *,
    contract: PayloadContract,
    install_id: str,
    release_root: Path,
    venv_root: Path,
    runtime_selection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "install_id": install_id,
        "version": request.version,
        "payload_sha256": contract.sha256,
        "payload_size": contract.size,
        "release_root": str(release_root.resolve()),
        "venv_root": str(venv_root.resolve()),
        "runtime_source": request.runtime_source,
        # A fallback interpreter is deliberately extracted inside bootstrap's
        # disposable workRoot.  The application only uses its committed venv,
        # so never retain that temporary source path in durable state.
        "base_python": (
            str(request.base_python.expanduser().resolve())
            if request.runtime_source == "official"
            else None
        ),
        "skill_root": str(request.skill_root.expanduser().resolve()),
    }
    if runtime_selection is not None:
        entry["runtime_selection"] = dict(runtime_selection)
    return entry


def _marker_path(release_root: Path) -> Path:
    return release_root / ".windows-install.json"


def _entry_is_usable(entry: Mapping[str, Any], install_root: Path) -> bool:
    install_id = entry.get("install_id")
    if not isinstance(install_id, str) or not install_id:
        return False
    release_root = Path(str(entry.get("release_root", ""))).resolve()
    venv_root = Path(str(entry.get("venv_root", ""))).resolve()
    releases = (install_root / "releases").resolve()
    venvs = (install_root / "venvs").resolve()
    try:
        release_root.relative_to(releases)
        venv_root.relative_to(venvs)
    except ValueError:
        return False
    if release_root.name != install_id or venv_root.name != install_id:
        return False
    runtime_source = entry.get("runtime_source")
    if runtime_source not in {"official", "fallback"}:
        return False
    if entry.get("runtime_selection") is not None:
        try:
            _validate_persisted_runtime_selection(
                entry["runtime_selection"], runtime_source=runtime_source
            )
        except InstallerError:
            return False
    marker = _read_object(_marker_path(release_root))
    if not marker or marker.get("schema_version") != MARKER_SCHEMA:
        return False
    expected = dict(entry)
    return (
        marker.get("entry") == expected
        and (release_root / "app" / "runtime_manager.py").is_file()
        and (venv_root / "Scripts" / "python.exe").is_file()
        and (venv_root / "Scripts" / "image-pptgen.exe").is_file()
    )


def _read_state(install_root: Path) -> dict[str, Any] | None:
    state = _read_object(_state_path(install_root))
    if state is None:
        return None
    if state.get("schema_version") != STATE_SCHEMA or state.get("platform") != PLATFORM:
        raise InstallerError("state_invalid", "Windows installer state is invalid")
    active = state.get("active")
    previous = state.get("previous")
    if not isinstance(active, dict) or (
        previous is not None and not isinstance(previous, dict)
    ):
        raise InstallerError("state_invalid", "Windows installer state is incomplete")
    return state


def _install_platform_tools(request: InstallRequest) -> None:
    bin_root = _private_directory(request.install_root / "bin")
    platform_root = request.platform_root.expanduser().resolve()
    for name in _PLATFORM_TOOL_NAMES:
        source = platform_root / name
        if not source.is_file():
            raise InstallerError(
                "platform_tool_missing", f"Windows platform tool is missing: {name}"
            )
        _atomic_write_bytes(bin_root / name, source.read_bytes())


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
        elif path.is_symlink():
            raise InstallerError("unsafe_skill_target", "Skill contains a symbolic link")
        digest.update(b"\x00")
    return digest.hexdigest()


def _deploy_skill(
    release_root: Path, skill_root: Path, install_root: Path
) -> tuple[Path | None, bool]:
    source = release_root / "app" / "skills" / "generate-image-presentation"
    if not source.is_dir():
        raise InstallerError("skill_missing", "Image presentation Skill is missing")
    skill_root = _private_directory(skill_root)
    target = skill_root / "generate-image-presentation"
    if target.is_symlink():
        raise InstallerError("unsafe_skill_target", "Skill target is a symbolic link")
    stage = skill_root / f".generate-image-presentation.staging-{uuid.uuid4().hex}"
    backup: Path | None = None
    try:
        shutil.copytree(source, stage)
        if target.is_dir() and _tree_digest(target) == _tree_digest(stage):
            shutil.rmtree(stage)
            return None, False
        if target.exists():
            backup_root = _private_directory(install_root / "backups" / "skills")
            backup = backup_root / f"generate-image-presentation-{uuid.uuid4().hex}"
            os.replace(target, backup)
        os.replace(stage, target)
        return backup, True
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise


def _restore_skill(skill_root: Path, backup: Path | None, changed: bool) -> None:
    if not changed:
        return
    target = skill_root.resolve() / "generate-image-presentation"
    if target.exists() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    if backup is not None and backup.exists():
        os.replace(backup, target)


def install_release(request: InstallRequest) -> dict[str, Any]:
    """Install or activate one immutable Windows release transactionally."""

    if request.runtime_source not in {"official", "fallback"}:
        raise InstallerError(
            "runtime_source_invalid", "Runtime source must be official or fallback"
        )
    runtime_selection = (
        _normalize_runtime_selection_receipt(
            request.runtime_selection_receipt,
            runtime_source=request.runtime_source,
            base_python=request.base_python,
        )
        if request.runtime_selection_receipt is not None
        else None
    )
    contract = validate_payload(
        request.payload,
        expected_size=request.payload_size,
        expected_sha256=request.payload_sha256,
        version=request.version,
    )
    install_root = _private_directory(request.install_root)
    releases = _private_directory(install_root / "releases")
    venvs = _private_directory(install_root / "venvs")
    _private_directory(install_root / "state")
    runtime_identity = (
        "fallback:"
        + str(runtime_selection["fallback_runtime"]["archive_sha256"])
        if request.runtime_source == "fallback" and runtime_selection is not None
        else (
            "fallback:unattested"
            if request.runtime_source == "fallback"
            else "official:" + str(request.base_python.expanduser().resolve()).casefold()
        )
    )
    runtime_key = hashlib.sha256(runtime_identity.encode("utf-8")).hexdigest()[:8]
    install_id = (
        f"{request.version}-{contract.sha256[:12]}-"
        f"{request.runtime_source}-{runtime_key}"
    )
    release_target = releases / install_id
    venv_target = venvs / install_id
    entry = _entry(
        request,
        contract=contract,
        install_id=install_id,
        release_root=release_target,
        venv_root=venv_target,
        runtime_selection=runtime_selection,
    )
    current = _read_state(install_root)
    if _entry_is_usable(entry, install_root):
        _install_platform_tools(request)
        skill_backup, skill_changed = _deploy_skill(
            release_target, request.skill_root, install_root
        )
        if current and current.get("active") == entry:
            return {
                "ok": True,
                "action": "install",
                "platform": PLATFORM,
                "reused": True,
                "active": entry,
                "skill_backup": str(skill_backup) if skill_backup else None,
                "bin_dir": str((install_root / "bin").resolve()),
            }
        next_state = {
            "schema_version": STATE_SCHEMA,
            "platform": PLATFORM,
            "active": entry,
            "previous": current.get("active") if current else None,
        }
        try:
            _atomic_write_json(_state_path(install_root), next_state)
        except Exception:
            _restore_skill(request.skill_root, skill_backup, skill_changed)
            raise
        return {
            "ok": True,
            "action": "install",
            "platform": PLATFORM,
            "reused": True,
            "active": entry,
            "skill_backup": str(skill_backup) if skill_backup else None,
            "bin_dir": str((install_root / "bin").resolve()),
        }
    # A same-identity interrupted install is inactive and safe to reconstruct.
    referenced_ids = {
        value.get("install_id")
        for value in ((current or {}).get("active"), (current or {}).get("previous"))
        if isinstance(value, dict)
    }
    if install_id in referenced_ids:
        raise InstallerError(
            "referenced_install_invalid",
            "A referenced Windows install is incomplete and was not modified",
            install_id=install_id,
        )
    for target in (release_target, venv_target):
        if target.exists() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.is_symlink():
            raise InstallerError(
                "unsafe_install_target", "Windows install target is a symbolic link"
            )
    token = uuid.uuid4().hex
    # Keep transaction paths independent from the user-facing release version.
    # The final targets remain versioned by install_id below; only temporary
    # names use the short, unique transaction token.
    extract_stage = releases / f".staging-{token}"
    payload_stage = releases / f".payload-{token}.zip"
    release_stage = releases / f"staging-{token}"
    venv_stage = venvs / f"staging-{token}"
    release_committed = False
    venv_committed = False
    skill_backup: Path | None = None
    skill_changed = False
    try:
        with request.payload.expanduser().resolve().open("rb") as source, payload_stage.open(
            "xb"
        ) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if (
            payload_stage.stat().st_size != contract.size
            or _sha256_file(payload_stage) != contract.sha256
        ):
            raise InstallerError(
                "payload_changed", "Windows payload changed after validation"
            )
        extracted = _extract_payload(payload_stage, extract_stage, contract)
        os.replace(extracted, release_stage)
        shutil.rmtree(extract_stage)
        _validate_release_identity(release_stage, request.version)
        _create_venv(request.base_python.expanduser().resolve(), venv_stage, release_stage)
        os.replace(release_stage, release_target)
        release_committed = True
        os.replace(venv_stage, venv_target)
        venv_committed = True
        # The initial toolkit install ran under the disposable staging path.
        # Recreate its console launcher from the promoted venv before the
        # marker/state makes this release active.
        _rebind_final_console_launcher(venv_target, release_target)
        _atomic_write_json(
            _marker_path(release_target),
            {
                "schema_version": MARKER_SCHEMA,
                "platform": PLATFORM,
                "entry": entry,
            },
        )
        _install_platform_tools(request)
        skill_backup, skill_changed = _deploy_skill(
            release_target, request.skill_root, install_root
        )
        next_state = {
            "schema_version": STATE_SCHEMA,
            "platform": PLATFORM,
            "active": entry,
            "previous": current.get("active") if current else None,
        }
        _atomic_write_json(_state_path(install_root), next_state)
    except Exception:
        _restore_skill(request.skill_root, skill_backup, skill_changed)
        if venv_committed and venv_target.exists():
            shutil.rmtree(venv_target, ignore_errors=True)
        if release_committed and release_target.exists():
            shutil.rmtree(release_target, ignore_errors=True)
        raise
    finally:
        for stage in (extract_stage, release_stage, venv_stage):
            if stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage, ignore_errors=True)
        try:
            payload_stage.unlink()
        except FileNotFoundError:
            pass
    return {
        "ok": True,
        "action": "install",
        "platform": PLATFORM,
        "reused": False,
        "active": entry,
        "skill_backup": str(skill_backup) if skill_backup else None,
        "bin_dir": str((install_root / "bin").resolve()),
    }


def _active_entry(install_root: Path) -> dict[str, Any]:
    root = install_root.expanduser().resolve()
    state = _read_state(root)
    if state is None:
        raise InstallerError("not_installed", "Image PPTGen is not installed")
    active = state["active"]
    if not _entry_is_usable(active, root):
        raise InstallerError("active_install_invalid", "Active Windows install is invalid")
    return active


def _manager_command(install_root: Path, active: Mapping[str, Any], action: str) -> list[str]:
    release_root = Path(str(active["release_root"]))
    venv_root = Path(str(active["venv_root"]))
    return [
        str(venv_root / "Scripts" / "python.exe"),
        str(release_root / "app" / "runtime_manager.py"),
        action,
        "--json",
        "--app-root",
        str(release_root / "app"),
        "--data-root",
        str(install_root.resolve()),
    ]


def _run_json(command: list[str], *, env: Mapping[str, str], code: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise InstallerError(code, f"Windows lifecycle command could not start: {exc}") from exc
    if completed.returncode != 0:
        raise InstallerError(
            code,
            "Windows lifecycle command failed",
            returncode=completed.returncode,
            output=(completed.stderr or completed.stdout)[-2000:],
        )
    for line in reversed(completed.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise InstallerError(
        code,
        "Windows lifecycle command did not return JSON",
        returncode=completed.returncode,
        output=(completed.stderr or completed.stdout)[-2000:],
    )


def _active_environment(active: Mapping[str, Any], *, install_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    data_root = install_root.expanduser().resolve()
    env["IMAGE_PPTGEN_PYTHON"] = str(
        Path(str(active["venv_root"])) / "Scripts" / "python.exe"
    )
    # The installed Windows service must not fall back to Unix/XDG defaults
    # when an interactive Codex Desktop process does not inherit HOME/XDG.
    # These are controlled active-install values, not caller configuration.
    env["IMAGE_PPTGEN_DATA_ROOT"] = str(data_root)
    env["PPTGEN_DATA_ROOT"] = str(data_root)
    env.setdefault("IMAGE_PPTGEN_BASE_URL", "http://127.0.0.1:3130")
    return env


def doctor(install_root: Path) -> dict[str, Any]:
    root = install_root.expanduser().resolve()
    active = _active_entry(root)
    env = _active_environment(active, install_root=root)
    runtime = _run_json(
        _manager_command(root, active, "ensure-ready"),
        env=env,
        code="doctor_runtime_failed",
    )
    cli = Path(str(active["venv_root"])) / "Scripts" / "image-pptgen.exe"
    product = _run_json(
        [str(cli), "doctor", "--json"], env=env, code="doctor_product_failed"
    )
    return {
        "ok": True,
        "action": "doctor",
        "platform": PLATFORM,
        "active": active,
        "runtime": runtime,
        "product": product,
    }


def stop(install_root: Path) -> dict[str, Any]:
    root = install_root.expanduser().resolve()
    active = _active_entry(root)
    result = _run_json(
        _manager_command(root, active, "stop"),
        env=_active_environment(active, install_root=root),
        code="stop_failed",
    )
    return {
        "ok": True,
        "action": "stop",
        "platform": PLATFORM,
        "active": active,
        "runtime": result,
    }


def rollback(install_root: Path, *, stop_active: bool = True) -> dict[str, Any]:
    root = install_root.expanduser().resolve()
    state = _read_state(root)
    if state is None:
        raise InstallerError("not_installed", "Image PPTGen is not installed")
    active = state["active"]
    previous = state.get("previous")
    if not isinstance(previous, dict) or not _entry_is_usable(previous, root):
        raise InstallerError("rollback_unavailable", "No usable previous release exists")
    if not _entry_is_usable(active, root):
        raise InstallerError("active_install_invalid", "Active Windows install is invalid")
    stopped: dict[str, Any] | None = None
    if stop_active:
        stopped = _run_json(
            _manager_command(root, active, "stop"),
            env=_active_environment(active, install_root=root),
            code="rollback_stop_failed",
        )
    skill_root = Path(str(previous["skill_root"]))
    skill_backup, skill_changed = _deploy_skill(
        Path(str(previous["release_root"])), skill_root, root
    )
    next_state = {
        "schema_version": STATE_SCHEMA,
        "platform": PLATFORM,
        "active": previous,
        "previous": active,
    }
    try:
        _atomic_write_json(_state_path(root), next_state)
    except Exception:
        _restore_skill(skill_root, skill_backup, skill_changed)
        raise
    return {
        "ok": True,
        "action": "rollback",
        "platform": PLATFORM,
        "active": previous,
        "previous": active,
        "stopped": stopped,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Image PPTGen on Windows AMD64")
    parser.add_argument("action", choices=("install", "doctor", "stop", "rollback"))
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--payload-sha256")
    parser.add_argument("--payload-size", type=int)
    parser.add_argument("--version")
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--base-python", type=Path)
    parser.add_argument("--runtime-source", choices=("official", "fallback"))
    parser.add_argument("--runtime-selection-receipt", type=Path)
    parser.add_argument("--platform-root", type=Path, default=Path(__file__).parent)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "install":
            required = {
                "payload": args.payload,
                "payload_sha256": args.payload_sha256,
                "payload_size": args.payload_size,
                "version": args.version,
                "base_python": args.base_python,
                "runtime_source": args.runtime_source,
            }
            missing = sorted(key for key, value in required.items() if value is None)
            if missing:
                raise InstallerError(
                    "install_argument_missing",
                    "Windows install arguments are incomplete",
                    missing=missing,
                )
            result = install_release(
                InstallRequest(
                    payload=args.payload,
                    payload_sha256=args.payload_sha256,
                    payload_size=args.payload_size,
                    version=args.version,
                    install_root=args.install_root,
                    skill_root=args.skill_root or Path.home() / ".agents" / "skills",
                    base_python=args.base_python,
                    runtime_source=args.runtime_source,
                    platform_root=args.platform_root,
                    runtime_selection_receipt=args.runtime_selection_receipt,
                )
            )
        elif args.action == "doctor":
            result = doctor(args.install_root)
        elif args.action == "stop":
            result = stop(args.install_root)
        else:
            result = rollback(args.install_root)
    except InstallerError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 3
    except (OSError, subprocess.SubprocessError) as exc:
        failure = InstallerError("platform_io_failed", str(exc))
        print(json.dumps(failure.payload(), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
