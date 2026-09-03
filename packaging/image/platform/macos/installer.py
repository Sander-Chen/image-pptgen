#!/usr/bin/env python3
"""Offline, user-scoped installer for Image PPTGen on Apple Silicon macOS.

This entry point deliberately owns no network behavior.  Its caller supplies a
local release manifest, archive, wheelhouse, and the exact Codex Desktop Python
candidate.  A local fallback directory may also be supplied; there is no
interactive runtime choice and no path discovery outside those inputs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Mapping, NamedTuple
import uuid


PLATFORM_ID = "macos-arm64"
SCHEMA_VERSION = 1
MINIMUM_PYTHON = (3, 11)
OFFICIAL_PROBE_ATTEMPTS = 2
FALLBACK_FREEZE_ID = "pbs-20260718-cp311-plus-cp312-v4"
FALLBACK_RUNTIME_ARCHIVE_SHA256 = (
    "b21dbc3f3e01932fcc3f0f4c51e5a7ef61888cb454d23eee6e8207c6f52d0b04"
)
FALLBACK_RUNTIME_ARCHIVE_BYTES = 27115492
FALLBACK_PYTHON_RELATIVE_PATH = Path("bin/python3.11")
OFFICIAL_PROVISIONING_APPROACHES = ("venv-ensurepip", "venv-host-pip")
COMMAND_TIMEOUT_SECONDS = 120.0
PROBE_TIMEOUT_SECONDS = 10.0
_INSTALL_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
_NATIVE_WHEEL_DISTRIBUTIONS = ("charset_normalizer", "markupsafe", "pillow")
_ABI3_WHEEL_RE = re.compile(r"-cp3(?P<minor>[0-9]+)-abi3-macosx_")


class InstallerError(RuntimeError):
    """Stable, machine-readable installer failure."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = details
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "error": "platform_unavailable",
            "code": self.code,
            "message": str(self),
        }
        if self.details:
            result["details"] = self.details
        return result


class ArchiveSpec(NamedTuple):
    name: str
    sha256: str
    size: int


class ReleaseManifest(NamedTuple):
    version: str
    platform: str
    archive: ArchiveSpec


class RuntimeChoice(NamedTuple):
    source: str
    executable: Path
    version: str


class ProvisionResult(NamedTuple):
    runtime: RuntimeChoice
    official_attempts: tuple[dict[str, str], ...]
    fallback_authorization: "FallbackAuthorization | None" = None


class FallbackAuthorization(NamedTuple):
    official_attempts: tuple[dict[str, str], ...]
    fallback_root: Path
    python_path: Path

    def record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "platform": PLATFORM_ID,
            "freeze_id": FALLBACK_FREEZE_ID,
            "decision": "fallback_authorized",
            "official_attempts": [dict(attempt) for attempt in self.official_attempts],
            "fallback_runtime": {
                "archive_sha256": FALLBACK_RUNTIME_ARCHIVE_SHA256,
                "archive_bytes": FALLBACK_RUNTIME_ARCHIVE_BYTES,
                "extracted_root": str(self.fallback_root),
                "python_path": str(self.python_path),
            },
        }


class SkillActivation(NamedTuple):
    target: Path
    backup: Path | None
    changed: bool


class ActivationSnapshot(NamedTuple):
    marker_bytes: bytes | None
    release_link_target: str | None
    venv_link_target: str | None


class InstallLayout(NamedTuple):
    home: Path
    root: Path
    releases: Path
    venvs: Path
    state: Path
    staging: Path
    backups: Path
    licenses: Path
    active_marker: Path
    current_release: Path
    current_venv: Path
    env_file: Path
    bin_home: Path
    skill_home: Path

    @classmethod
    def for_home(
        cls,
        home: Path,
        *,
        install_root: Path | None = None,
        bin_home: Path | None = None,
        skill_home: Path | None = None,
    ) -> "InstallLayout":
        resolved_home = home.expanduser().resolve()
        # Codex Desktop can run this installer through its managed Runtime,
        # whose macOS sandbox need not permit the generic Application Support
        # directory.  Keep all product state below Codex's own user-scoped
        # directory by default.  Callers can still provide a narrower
        # user-owned compatibility override through --install-root.
        root = (
            install_root or resolved_home / ".codex" / "image-pptgen"
        ).expanduser().resolve()
        if root.name != "image-pptgen":
            raise InstallerError(
                "install_root_namespace_invalid",
                "install_root must use the image-pptgen namespace",
                path=str(root),
            )
        commands = (bin_home or resolved_home / ".codex" / "bin").expanduser().resolve()
        if skill_home is not None:
            skills = skill_home.expanduser().resolve()
        else:
            # The generic Agent Skills discovery docs list ~/.agents/skills,
            # while Codex's bundled skill-installer still installs user skills
            # under CODEX_HOME/skills (default ~/.codex/skills).  This is the
            # Codex Desktop installer, so follow the installed host and leave
            # --skill-home as the explicit compatibility override.  In
            # particular, do not create the unrelated ~/.agents tree from a
            # sandboxed Desktop task.
            codex_home = os.environ.get("CODEX_HOME")
            codex_home_path = (
                Path(codex_home).expanduser().resolve() if codex_home else None
            )
            if codex_home_path is not None:
                try:
                    codex_home_path.relative_to(resolved_home)
                except ValueError:
                    # An inherited host CODEX_HOME must not escape the
                    # selected user-scoped installation home.
                    codex_home_path = None
            skills = (
                (codex_home_path or resolved_home / ".codex") / "skills"
            ).resolve()
        for candidate, label in (
            (root, "install_root"),
            (commands, "bin_home"),
            (skills, "skill_home"),
        ):
            try:
                candidate.relative_to(resolved_home)
            except ValueError as exc:
                raise InstallerError(
                    "non_user_install_path",
                    f"{label} must stay inside the current user home",
                    path=str(candidate),
                ) from exc
        return cls(
            home=resolved_home,
            root=root,
            releases=root / "releases",
            venvs=root / "venvs",
            state=root / "state",
            staging=root / ".staging",
            backups=root / "backups",
            licenses=root / "licenses",
            active_marker=root / "active.json",
            current_release=root / "current",
            current_venv=root / "current-venv",
            env_file=root / "env",
            bin_home=commands,
            skill_home=skills,
        )


def _mapping(value: Any, *, code: str, message: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise InstallerError(code, message)
    return value


def load_manifest(path: Path) -> ReleaseManifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallerError("manifest_unavailable", "release manifest cannot be read") from exc
    root = _mapping(data, code="manifest_invalid", message="release manifest must be an object")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise InstallerError("manifest_schema_mismatch", "release manifest schema is unsupported")
    if root.get("platform") != PLATFORM_ID:
        raise InstallerError(
            "manifest_platform_mismatch",
            f"release manifest must target {PLATFORM_ID}",
        )
    version = root.get("version")
    if not isinstance(version, str) or not _INSTALL_ID_RE.fullmatch(version):
        raise InstallerError("manifest_version_invalid", "release version is invalid")
    archive = _mapping(
        root.get("archive"),
        code="manifest_archive_invalid",
        message="release archive metadata is invalid",
    )
    name = archive.get("name")
    sha256 = archive.get("sha256")
    size = archive.get("size")
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or not name.endswith(".tar.gz")
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise InstallerError("manifest_archive_invalid", "release archive metadata is invalid")
    return ReleaseManifest(version, PLATFORM_ID, ArchiveSpec(name, sha256, size))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise InstallerError("archive_unavailable", "release archive cannot be read") from exc
    return digest.hexdigest()


def verify_archive(manifest: ReleaseManifest, archive_path: Path) -> None:
    try:
        observed_size = archive_path.stat().st_size
    except OSError as exc:
        raise InstallerError("archive_unavailable", "release archive cannot be read") from exc
    if archive_path.name != manifest.archive.name:
        raise InstallerError("archive_name_mismatch", "release archive name does not match manifest")
    if observed_size != manifest.archive.size:
        raise InstallerError(
            "archive_size_mismatch",
            "release archive size does not match manifest",
            expected=manifest.archive.size,
            observed=observed_size,
        )
    observed_sha = _sha256(archive_path)
    if observed_sha != manifest.archive.sha256:
        raise InstallerError(
            "archive_sha256_mismatch",
            "release archive SHA-256 does not match manifest",
            expected=manifest.archive.sha256,
            observed=observed_sha,
        )


def _safe_member(member: tarfile.TarInfo, seen: set[str]) -> PurePosixPath:
    name = member.name
    path = PurePosixPath(name)
    if (
        not name
        or "\x00" in name
        or path.is_absolute()
        or ".." in path.parts
        or name in seen
        or not (member.isfile() or member.isdir())
        or member.issym()
        or member.islnk()
        or member.isdev()
        or member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    ):
        raise InstallerError(
            "unsafe_archive_member",
            f"release archive contains an unsafe member: {name}",
        )
    seen.add(name)
    return path


def safe_extract_archive(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    destination_root = destination.resolve()
    top_levels: set[str] = set()
    seen: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise InstallerError("archive_empty", "release archive is empty")
            validated = [(member, _safe_member(member, seen)) for member in members]
            for member, path in validated:
                top_levels.add(path.parts[0])
                target = destination.joinpath(*path.parts)
                try:
                    target.parent.resolve().relative_to(destination_root)
                except ValueError as exc:
                    raise InstallerError(
                        "unsafe_archive_member", f"release archive escapes staging: {member.name}"
                    ) from exc
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(member.mode & 0o755 or 0o755)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise InstallerError(
                        "archive_member_unreadable", f"release member cannot be read: {member.name}"
                    )
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(target, flags, member.mode & 0o755 or 0o644)
                with os.fdopen(descriptor, "wb") as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                target.chmod(member.mode & 0o755 or 0o644)
    except (tarfile.TarError, OSError) as exc:
        if isinstance(exc, InstallerError):
            raise
        raise InstallerError("archive_invalid", "release archive cannot be extracted") from exc
    if len(top_levels) != 1:
        raise InstallerError("archive_layout_invalid", "release archive must have one top-level directory")
    return destination / next(iter(top_levels))


def _probe_python(candidate: Path) -> tuple[int, int, int]:
    script = (
        "import json,platform,sqlite3,ssl,sys,venv; "
        "print(json.dumps({'version': list(sys.version_info[:3]), "
        "'machine': platform.machine().lower(), 'prefix': sys.prefix}))"
    )
    try:
        completed = subprocess.run(
            [str(candidate), "-I", "-c", script],
            text=True,
            capture_output=True,
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallerError("python_probe_failed", "Python capability probe failed") from exc
    if completed.returncode != 0:
        raise InstallerError(
            "python_probe_failed",
            "Python capability probe failed",
            returncode=completed.returncode,
        )
    try:
        payload = json.loads(completed.stdout)
        version = payload["version"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InstallerError("python_probe_invalid", "Python capability probe was invalid") from exc
    if (
        not isinstance(version, list)
        or len(version) != 3
        or not all(isinstance(value, int) for value in version)
        or tuple(version[:2]) < MINIMUM_PYTHON
    ):
        raise InstallerError(
            "python_version_unsupported",
            f"Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer is required",
        )
    if payload.get("machine") not in {"arm64", "aarch64"}:
        raise InstallerError(
            "python_architecture_unsupported",
            "Python must run natively on Apple Silicon",
            machine=payload.get("machine"),
        )
    return tuple(version)  # type: ignore[return-value]


def _exact_executable(path: Path, *, code: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise InstallerError(code, f"Python executable is unavailable: {path}") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise InstallerError(code, f"Python executable is unavailable: {path}")
    return resolved


def _fallback_executable(root: Path) -> Path:
    try:
        resolved_root = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise InstallerError("fallback_unavailable", "fallback Python directory is unavailable") from exc
    if not resolved_root.is_dir():
        raise InstallerError("fallback_unavailable", "fallback Python directory is unavailable")
    candidate = resolved_root / FALLBACK_PYTHON_RELATIVE_PATH
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise InstallerError(
            "fallback_unavailable",
            "fallback directory has no usable frozen Python",
        ) from exc
    if resolved.is_file() and os.access(resolved, os.X_OK):
        return resolved
    raise InstallerError("fallback_unavailable", "fallback directory has no usable Python")


def _read_fallback_authorization(
    receipt_path: Path | None,
    fallback_python_dir: Path | None,
) -> FallbackAuthorization:
    if receipt_path is None or fallback_python_dir is None:
        raise InstallerError(
            "fallback_not_authorized",
            "fallback requires a bounded official-runtime failure receipt and frozen Runtime directory",
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallerError(
            "fallback_not_authorized", "fallback authorization receipt is unavailable or invalid"
        ) from exc
    root = _mapping(
        receipt,
        code="fallback_not_authorized",
        message="fallback authorization receipt must be an object",
    )
    attempts = root.get("official_attempts")
    fallback_runtime = root.get("fallback_runtime")
    if not isinstance(attempts, list) or not isinstance(fallback_runtime, dict):
        raise InstallerError(
            "fallback_not_authorized", "fallback authorization receipt is incomplete"
        )
    normalized_attempts: list[dict[str, str]] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise InstallerError(
                "fallback_not_authorized", "fallback authorization receipt is invalid"
            )
        approach = attempt.get("approach")
        result = attempt.get("result")
        if not isinstance(approach, str) or not isinstance(result, str):
            raise InstallerError(
                "fallback_not_authorized", "fallback authorization receipt is invalid"
            )
        normalized_attempts.append({"approach": approach, "result": result})
    expected_attempts = set(OFFICIAL_PROVISIONING_APPROACHES)
    if (
        root.get("schema_version") != SCHEMA_VERSION
        or root.get("platform") != PLATFORM_ID
        or root.get("freeze_id") != FALLBACK_FREEZE_ID
        or root.get("decision") != "fallback_authorized"
        or len(normalized_attempts) != OFFICIAL_PROBE_ATTEMPTS
        or {attempt["approach"] for attempt in normalized_attempts} != expected_attempts
        or any(attempt["result"] != "failed" for attempt in normalized_attempts)
        or fallback_runtime.get("archive_sha256") != FALLBACK_RUNTIME_ARCHIVE_SHA256
        or fallback_runtime.get("archive_bytes") != FALLBACK_RUNTIME_ARCHIVE_BYTES
    ):
        raise InstallerError(
            "fallback_not_authorized",
            "fallback requires two failed, approach-different official Runtime attempts and the frozen local payload",
        )
    extracted_root = fallback_runtime.get("extracted_root")
    python_path = fallback_runtime.get("python_path")
    if not isinstance(extracted_root, str) or not isinstance(python_path, str):
        raise InstallerError(
            "fallback_not_authorized", "fallback authorization receipt is incomplete"
        )
    try:
        supplied_root = fallback_python_dir.expanduser().resolve(strict=True)
        receipt_root = Path(extracted_root).expanduser().resolve(strict=True)
        receipt_python = Path(python_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise InstallerError(
            "fallback_not_authorized", "fallback authorization receipt paths are unavailable"
        ) from exc
    expected_python = _fallback_executable(supplied_root)
    if (
        not supplied_root.is_dir()
        or receipt_root != supplied_root
        or receipt_python != expected_python
    ):
        raise InstallerError(
            "fallback_not_authorized",
            "fallback receipt does not bind the supplied frozen Runtime directory",
        )
    return FallbackAuthorization(tuple(normalized_attempts), supplied_root, expected_python)


def _select_fallback_runtime(root: Path) -> RuntimeChoice:
    fallback = _fallback_executable(root)
    version = _probe_python(fallback)
    return RuntimeChoice("fallback", fallback, ".".join(map(str, version)))


def select_runtime(
    official_python: Path,
    fallback_python_dir: Path | None,
    *,
    fallback_authorization_receipt: Path | None = None,
) -> RuntimeChoice:
    try:
        official = _exact_executable(official_python, code="official_python_unavailable")
        version = _probe_python(official)
    except InstallerError as exc:
        if fallback_python_dir is not None and fallback_authorization_receipt is not None:
            _read_fallback_authorization(
                fallback_authorization_receipt, fallback_python_dir
            )
            return _select_fallback_runtime(fallback_python_dir)
        raise InstallerError(
            "runtime_unavailable",
            "the caller-supplied official Codex Desktop Python is unavailable or cannot run the installer probe",
            official_code=exc.code,
        ) from exc
    return RuntimeChoice("official", official, ".".join(map(str, version)))


def _ensure_layout(layout: InstallLayout) -> None:
    for directory in (
        layout.root,
        layout.releases,
        layout.venvs,
        layout.state / "data" / "artifacts",
        layout.state / "data" / "historical-data",
        layout.staging,
        layout.backups,
        layout.licenses,
        layout.bin_home,
        layout.skill_home,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            directory.chmod(0o700)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise InstallerError("unsafe_marker", f"refusing symlink marker: {path}")
    staged = path.parent / f".{path.name}.new-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise InstallerError("unsafe_marker", f"refusing symlink marker: {path}")
        os.replace(staged, path)
    finally:
        with contextlib.suppress(OSError):
            staged.unlink()


def _read_active(layout: InstallLayout) -> dict[str, Any] | None:
    try:
        value = json.loads(layout.active_marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return None
    install_id = value.get("install_id")
    return value if isinstance(install_id, str) and _INSTALL_ID_RE.fullmatch(install_id) else None


def _atomic_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        raise InstallerError("unsafe_activation_path", f"activation path is not a symlink: {link}")
    staged = link.parent / f".{link.name}.new-{uuid.uuid4().hex}"
    try:
        staged.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
        os.replace(staged, link)
    finally:
        with contextlib.suppress(OSError):
            staged.unlink()


def _activation_snapshot(layout: InstallLayout) -> ActivationSnapshot:
    def link_target(link: Path) -> str | None:
        if link.is_symlink():
            try:
                return os.readlink(link)
            except OSError as exc:
                raise InstallerError(
                    "unsafe_activation_path", f"activation link is unreadable: {link}"
                ) from exc
        if link.exists():
            raise InstallerError(
                "unsafe_activation_path", f"activation path is not a symlink: {link}"
            )
        return None

    marker_bytes: bytes | None = None
    if layout.active_marker.is_symlink():
        raise InstallerError(
            "unsafe_marker", f"refusing symlink marker: {layout.active_marker}"
        )
    if layout.active_marker.exists():
        if not layout.active_marker.is_file():
            raise InstallerError(
                "unsafe_marker", f"active marker is not a regular file: {layout.active_marker}"
            )
        try:
            marker_bytes = layout.active_marker.read_bytes()
        except OSError as exc:
            raise InstallerError(
                "unsafe_marker", f"active marker cannot be read: {layout.active_marker}"
            ) from exc
    return ActivationSnapshot(
        marker_bytes,
        link_target(layout.current_release),
        link_target(layout.current_venv),
    )


def _restore_symlink(link: Path, target: str | None) -> None:
    if link.exists() or link.is_symlink():
        if not link.is_symlink():
            raise InstallerError(
                "activation_restore_failed", f"activation path is not a symlink: {link}"
            )
        link.unlink()
    if target is None:
        return
    staged = link.parent / f".{link.name}.restore-{uuid.uuid4().hex}"
    try:
        staged.symlink_to(target, target_is_directory=True)
        os.replace(staged, link)
    finally:
        with contextlib.suppress(OSError):
            staged.unlink()


def _restore_marker(layout: InstallLayout, marker_bytes: bytes | None) -> None:
    marker = layout.active_marker
    if marker.is_symlink():
        raise InstallerError("activation_restore_failed", f"marker is a symlink: {marker}")
    if marker.exists():
        marker.unlink()
    if marker_bytes is None:
        return
    staged = marker.parent / f".{marker.name}.restore-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(marker_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, marker)
    finally:
        with contextlib.suppress(OSError):
            staged.unlink()


def _restore_activation(layout: InstallLayout, snapshot: ActivationSnapshot) -> None:
    try:
        _restore_symlink(layout.current_release, snapshot.release_link_target)
        _restore_symlink(layout.current_venv, snapshot.venv_link_target)
        _restore_marker(layout, snapshot.marker_bytes)
    except InstallerError:
        raise
    except OSError as exc:
        raise InstallerError(
            "activation_restore_failed", "activation could not be restored"
        ) from exc


def _validate_install_id(layout: InstallLayout, install_id: str) -> tuple[Path, Path]:
    if not _INSTALL_ID_RE.fullmatch(install_id):
        raise InstallerError("install_id_invalid", "install identifier is invalid")
    release = layout.releases / install_id
    venv = layout.venvs / install_id
    required = (
        release / "app" / "runtime_manager.py",
        release / "app" / "image-launcher.py",
        venv / "bin" / "python",
        venv / "bin" / "image-pptgen",
    )
    if not all(path.is_file() for path in required):
        raise InstallerError("install_incomplete", f"installation is incomplete: {install_id}")
    return release, venv


def _activate_install(layout: InstallLayout, marker: Mapping[str, Any]) -> None:
    _ensure_layout(layout)
    install_id = marker.get("install_id")
    if not isinstance(install_id, str):
        raise InstallerError("install_id_invalid", "install identifier is invalid")
    release, venv = _validate_install_id(layout, install_id)
    snapshot = _activation_snapshot(layout)
    try:
        _atomic_symlink(layout.current_release, release)
        _atomic_symlink(layout.current_venv, venv)
        _atomic_json(layout.active_marker, marker)
    except BaseException:
        _restore_activation(layout, snapshot)
        raise


def _run_checked(
    command: list[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    cwd: Path | None = None,
    stage: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallerError(
            "command_failed",
            "installer command could not run",
            command=command[0],
            stage=stage,
        ) from exc
    if completed.returncode != 0:
        raise InstallerError(
            "command_failed",
            "installer command failed",
            command=command[0],
            returncode=completed.returncode,
            stderr=completed.stderr[-2000:],
            stage=stage,
        )
    return completed


def _cpython_tag(version: str) -> tuple[str, int]:
    match = re.fullmatch(r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.[0-9]+", version)
    if match is None or match.group("major") != "3":
        raise InstallerError(
            "runtime_version_invalid",
            "selected Python version cannot be matched to the offline wheelhouse",
            runtime_version=version,
        )
    minor = int(match.group("minor"))
    return f"cp3{minor}", minor


def _wheel_supports_cpython(filename: str, *, cpython_tag: str, minor: int) -> bool:
    if f"-{cpython_tag}-{cpython_tag}-macosx_" in filename:
        return True
    if filename.endswith("-py3-none-any.whl"):
        return True
    abi3 = _ABI3_WHEEL_RE.search(filename)
    return abi3 is not None and int(abi3.group("minor")) <= minor


def _assert_wheelhouse_supports_runtime(wheelhouse: Path, runtime_version: str) -> None:
    cpython_tag, minor = _cpython_tag(runtime_version)
    try:
        filenames = {path.name for path in wheelhouse.iterdir() if path.is_file()}
    except OSError as exc:
        raise InstallerError(
            "wheelhouse_unavailable", "local dependency wheelhouse is unavailable"
        ) from exc
    missing = [
        distribution
        for distribution in _NATIVE_WHEEL_DISTRIBUTIONS
        if not any(
            filename.startswith(f"{distribution}-")
            and _wheel_supports_cpython(
                filename, cpython_tag=cpython_tag, minor=minor
            )
            for filename in filenames
        )
    ]
    if missing:
        raise InstallerError(
            "wheelhouse_python_abi_incompatible",
            "offline wheelhouse does not support the selected Python ABI",
            stage="wheelhouse-abi-preflight",
            runtime_version=runtime_version,
            expected_cpython_tag=cpython_tag,
            missing_distributions=missing,
        )


def _toolkit_wheel(toolkit: Path) -> Path | None:
    """Return the release's pre-built pure-Python toolkit wheel, if present.

    New Desktop archives carry this wheel so the official Runtime does not
    need setuptools to execute a PEP 517 source build.  Keeping the source
    fallback preserves compatibility with older local fixtures/archives; a
    caller that uses that path still gets the explicit build-backend error
    rather than silently downloading anything.
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
            "macOS payload contains more than one toolkit wheel",
        )
    return candidates[0] if candidates else None


def _provision_venv(
    choice: RuntimeChoice,
    venv_root: Path,
    app_root: Path,
    wheelhouse: Path,
    *,
    approach: str = "venv-ensurepip",
) -> None:
    if not wheelhouse.is_dir():
        raise InstallerError("wheelhouse_unavailable", "local dependency wheelhouse is unavailable")
    _assert_wheelhouse_supports_runtime(wheelhouse, choice.version)
    if approach == "venv-ensurepip":
        _run_checked(
            [str(choice.executable), "-I", "-m", "venv", "--copies", str(venv_root)],
            stage=f"{approach}:venv-create",
        )
        pip_command = [str(venv_root / "bin" / "python"), "-I", "-m", "pip"]
    elif approach == "venv-host-pip":
        _run_checked(
            [
                str(choice.executable),
                "-I",
                "-m",
                "venv",
                "--copies",
                "--without-pip",
                str(venv_root),
            ],
            stage=f"{approach}:venv-create",
        )
        # This second official-Runtime approach does not depend on ensurepip
        # inside the new venv.  It asks the caller-supplied Runtime's own pip
        # to target that venv explicitly.
        pip_command = [
            str(choice.executable),
            "-I",
            "-m",
            "pip",
            "--python",
            str(venv_root / "bin" / "python"),
        ]
    else:
        raise InstallerError(
            "provision_approach_invalid",
            f"unknown runtime provisioning approach: {approach}",
        )
    python = venv_root / "bin" / "python"
    requirements = app_root.parent / "macos" / "requirements.lock"
    toolkit = app_root / "packages" / "pptgen_toolkit"
    if not requirements.is_file():
        raise InstallerError(
            "offline_dependencies_missing",
            "macOS payload does not contain macos/requirements.lock",
        )
    if not toolkit.is_dir():
        raise InstallerError("archive_layout_invalid", "release toolkit is unavailable")
    toolkit_wheel = _toolkit_wheel(toolkit)
    toolkit_target = toolkit_wheel or toolkit
    toolkit_build_flags = [] if toolkit_wheel is not None else ["--no-build-isolation"]
    _run_checked(
        [
            *pip_command,
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        stage=f"{approach}:dependency-install",
    )
    _run_checked(
        [
            *pip_command,
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--no-deps",
            *toolkit_build_flags,
            str(toolkit_target),
        ],
        stage=f"{approach}:toolkit-install",
    )
    _run_checked(
        [
            str(python),
            "-I",
            "-c",
            "import flask, PIL, flask_cors, requests, waitress, pptgen_toolkit",
        ],
        stage=f"{approach}:import-verify",
    )
    _run_checked(
        [str(venv_root / "bin" / "image-pptgen"), "--help"],
        stage=f"{approach}:cli-verify",
    )


def _runtime_env(layout: InstallLayout, venv: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(layout.home),
            "XDG_DATA_HOME": str(layout.root.parent),
            "XDG_CONFIG_HOME": str(layout.root.parent),
            "IMAGE_PPTGEN_INSTALL_ROOT": str(layout.root),
            "IMAGE_PPTGEN_DATA_ROOT": str(layout.root),
            "IMAGE_PPTGEN_PYTHON": str(venv / "bin" / "python"),
            "IMAGE_PPTGEN_BASE_URL": "http://127.0.0.1:3130",
            "IMAGE_PPTGEN_HOST": "127.0.0.1",
            "IMAGE_PPTGEN_PORT": "3130",
        }
    )
    return env


def _runtime_manager_json(
    layout: InstallLayout,
    command: str,
    *,
    install_id: str | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    active = _read_active(layout)
    selected = install_id or (active or {}).get("install_id")
    if not isinstance(selected, str):
        raise InstallerError("active_install_unavailable", "no active installation is available")
    release, venv = _validate_install_id(layout, selected)
    completed = _run_checked(
        [
            str(venv / "bin" / "python"),
            str(release / "app" / "runtime_manager.py"),
            command,
            "--json",
            "--app-root",
            str(release / "app"),
            "--data-root",
            str(layout.root),
        ],
        env=_runtime_env(layout, venv),
        timeout=timeout,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallerError("runtime_response_invalid", "runtime manager returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise InstallerError("runtime_response_invalid", "runtime manager returned invalid JSON")
    return value


def _public_cli_json(layout: InstallLayout, *, install_id: str) -> dict[str, Any]:
    _release, venv = _validate_install_id(layout, install_id)
    completed = _run_checked(
        [str(venv / "bin" / "image-pptgen"), "doctor", "--json"],
        env=_runtime_env(layout, venv),
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallerError("doctor_response_invalid", "Image doctor returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise InstallerError("doctor_response_invalid", "Image doctor returned invalid JSON")
    return value


def _command_scoped_cli_json(
    layout: InstallLayout,
    *arguments: str,
    install_id: str,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    release, venv = _validate_install_id(layout, install_id)
    helper = release / "macos" / "image-pptgen-held-command.sh"
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise InstallerError(
            "command_scoped_runtime_unavailable",
            "macOS command-scoped runtime helper is unavailable",
        )
    completed = _run_checked(
        [str(helper), *arguments],
        env=_runtime_env(layout, venv),
        timeout=timeout,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallerError(
            "command_scoped_response_invalid",
            "macOS command-scoped runtime returned invalid JSON",
        ) from exc
    if not isinstance(value, dict):
        raise InstallerError(
            "command_scoped_response_invalid",
            "macOS command-scoped runtime returned invalid JSON",
        )
    return value


def _write_environment(layout: InstallLayout) -> None:
    values = {
        "PPTGEN_PUBLIC_DATA_DIR": layout.state / "data",
        "PPT_DB_PATH": layout.state / "data" / "ppt.db",
        "PPT_ARTIFACTS_DIR": layout.state / "data" / "artifacts",
        "PPTGEN_HISTORICAL_DATA_DIR": layout.state / "data" / "historical-data",
        "PPTGEN_HOST": "127.0.0.1",
        "PPTGEN_PORT": "3130",
        "IMAGE_PPTGEN_HOST": "127.0.0.1",
        "IMAGE_PPTGEN_PORT": "3130",
        "IMAGE_PPTGEN_BASE_URL": "http://127.0.0.1:3130",
        "IMAGE_PPTGEN_DATA_ROOT": layout.root,
    }
    payload = "".join(f"{key}={value}\n" for key, value in values.items())
    staged = layout.root / f".env.new-{uuid.uuid4().hex}"
    try:
        staged.write_text(payload, encoding="utf-8")
        staged.chmod(0o600)
        os.replace(staged, layout.env_file)
    finally:
        with contextlib.suppress(OSError):
            staged.unlink()


def _same_tree(first: Path, second: Path) -> bool:
    if not first.is_dir() or not second.is_dir():
        return False
    first_files = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
    second_files = sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
    return first_files == second_files and all(
        (first / relative).read_bytes() == (second / relative).read_bytes()
        for relative in first_files
    )


def _install_wrappers(layout: InstallLayout) -> None:
    source_root = Path(__file__).resolve().parent
    for source_name, target_name in (
        ("image-pptgen-wrapper.sh", "image-pptgen"),
        ("image-pptgen-server-wrapper.sh", "image-pptgen-server"),
    ):
        source = source_root / source_name
        staged = layout.bin_home / f".{target_name}.new-{uuid.uuid4().hex}"
        shutil.copyfile(source, staged)
        staged.chmod(0o755)
        os.replace(staged, layout.bin_home / target_name)


def _install_skill(
    layout: InstallLayout, release: Path, install_id: str
) -> SkillActivation:
    source = release / "app" / "skills" / "generate-image-presentation"
    if not source.is_dir():
        raise InstallerError("skill_missing", "release does not contain the Image skill")
    target = layout.skill_home / "generate-image-presentation"
    if _same_tree(source, target):
        return SkillActivation(target, None, False)
    staged = layout.skill_home / f".generate-image-presentation.new-{uuid.uuid4().hex}"
    backup: Path | None = None
    try:
        shutil.copytree(source, staged)
        if target.exists() or target.is_symlink():
            backup = layout.backups / (
                f"generate-image-presentation.before-{install_id}-{uuid.uuid4().hex}"
            )
            os.replace(target, backup)
        os.replace(staged, target)
    except BaseException:
        if staged.exists() or staged.is_symlink():
            shutil.rmtree(staged, ignore_errors=True)
        if backup is not None and backup.exists() and not (target.exists() or target.is_symlink()):
            os.replace(backup, target)
        raise
    return SkillActivation(target, backup, True)


def _restore_skill(activation: SkillActivation, layout: InstallLayout) -> None:
    if not activation.changed:
        return
    if activation.target.exists() or activation.target.is_symlink():
        failed = layout.backups / f"generate-image-presentation.failed-{uuid.uuid4().hex}"
        os.replace(activation.target, failed)
    if activation.backup is not None:
        os.replace(activation.backup, activation.target)


def _quarantine_incomplete(layout: InstallLayout, path: Path, label: str) -> None:
    if not (path.exists() or path.is_symlink()):
        return
    quarantine = layout.backups / f"incomplete-{label}-{uuid.uuid4().hex}"
    os.replace(path, quarantine)


def _provision_with_fallback(
    runtime: RuntimeChoice,
    *,
    fallback_python_dir: Path | None,
    fallback_authorization_receipt: Path | None,
    license_dir: Path | None,
    venv_root: Path,
    app_root: Path,
    wheelhouse: Path,
    fallback_authorization: FallbackAuthorization | None = None,
) -> ProvisionResult:
    failures: list[InstallerError] = []
    official_attempts: list[dict[str, str]] = []
    approaches = (
        OFFICIAL_PROVISIONING_APPROACHES
        if runtime.source == "official"
        else ("venv-ensurepip",)
    )
    for approach in approaches:
        shutil.rmtree(venv_root, ignore_errors=True)
        try:
            _provision_venv(
                runtime,
                venv_root,
                app_root,
                wheelhouse,
                approach=approach,
            )
            if runtime.source == "official":
                official_attempts.append(
                    {"approach": approach, "result": "succeeded"}
                )
            if runtime.source == "fallback" and fallback_authorization is None:
                raise InstallerError(
                    "fallback_not_authorized", "fallback authorization is unavailable"
                )
            return ProvisionResult(
                runtime, tuple(official_attempts), fallback_authorization
            )
        except InstallerError as exc:
            failures.append(exc)
            if runtime.source == "official":
                official_attempts.append(
                    {"approach": approach, "result": "failed", "code": exc.code}
                )
    if runtime.source != "official" or fallback_python_dir is None:
        last = failures[-1]
        raise InstallerError(
            "runtime_provision_failed",
            "selected Python could not provision the Image environment",
            runtime_source=runtime.source,
            cause_code=last.code,
            cause_details=last.details,
        ) from last
    try:
        if license_dir is None or not license_dir.is_dir():
            raise InstallerError(
                "fallback_not_authorized",
                "fallback requires the frozen local license directory",
            )
        authorization = _read_fallback_authorization(
            fallback_authorization_receipt, fallback_python_dir
        )
        fallback = _select_fallback_runtime(fallback_python_dir)
        shutil.rmtree(venv_root, ignore_errors=True)
        _provision_venv(
            fallback,
            venv_root,
            app_root,
            wheelhouse,
            approach="venv-ensurepip",
        )
        return ProvisionResult(
            fallback, tuple(official_attempts), authorization
        )
    except InstallerError as exc:
        if exc.code == "fallback_not_authorized":
            raise
        raise InstallerError(
            "runtime_provision_failed",
            "official and local fallback Python could not provision the Image environment",
            runtime_source="fallback",
            cause_code=exc.code,
            cause_details=exc.details,
        ) from exc


def _install_licenses(
    layout: InstallLayout,
    source: Path | None,
    *,
    install_id: str,
) -> Path | None:
    if source is None:
        return None
    if not source.is_dir():
        raise InstallerError("license_bundle_unavailable", "local license bundle directory is unavailable")
    for path in source.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise InstallerError(
                "license_bundle_unsafe",
                f"license bundle contains an unsafe member: {path.relative_to(source)}",
            )
    target = layout.licenses / install_id
    if _same_tree(source, target):
        return target
    staged = layout.licenses / f".{install_id}.new-{uuid.uuid4().hex}"
    shutil.copytree(source, staged)
    if target.exists() or target.is_symlink():
        _quarantine_incomplete(layout, target, f"licenses-{install_id}")
    os.replace(staged, target)
    return target


def _complete_marker(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _require_fallback_authorization(
    *,
    fallback_python_dir: Path | None,
    fallback_authorization_receipt: Path | None,
    license_dir: Path | None,
) -> FallbackAuthorization:
    if license_dir is None or not license_dir.is_dir():
        raise InstallerError(
            "fallback_not_authorized",
            "fallback requires the frozen local license directory",
        )
    return _read_fallback_authorization(
        fallback_authorization_receipt, fallback_python_dir
    )


def install_release(
    *,
    manifest_path: Path,
    archive_path: Path,
    wheelhouse: Path,
    official_python: Path,
    fallback_python_dir: Path | None,
    layout: InstallLayout,
    fallback_freeze_id: str | None = None,
    license_dir: Path | None = None,
    fallback_authorization_receipt: Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    verify_archive(manifest, archive_path)
    if not wheelhouse.is_dir():
        raise InstallerError("wheelhouse_unavailable", "local dependency wheelhouse is unavailable")
    _ensure_layout(layout)
    install_id = f"{manifest.version}-{manifest.archive.sha256[:12]}"
    release_target = layout.releases / install_id
    venv_target = layout.venvs / install_id
    completion = _complete_marker(release_target / ".image-pptgen-install.json")
    reused = bool(
        completion
        and completion.get("archive_sha256") == manifest.archive.sha256
        and (release_target / "app" / "runtime_manager.py").is_file()
        and (venv_target / "bin" / "python").is_file()
        and (venv_target / "bin" / "image-pptgen").is_file()
        and completion.get("runtime_source") in {"official", "fallback"}
        and isinstance(completion.get("python_version"), str)
    )
    fallback_authorization: FallbackAuthorization | None = None
    if reused:
        runtime = RuntimeChoice(
            str(completion["runtime_source"]),
            venv_target / "bin" / "python",
            str(completion["python_version"]),
        )
        official_attempts = tuple(completion.get("official_attempts") or ())
        if runtime.source == "fallback":
            fallback_authorization = _require_fallback_authorization(
                fallback_python_dir=fallback_python_dir,
                fallback_authorization_receipt=fallback_authorization_receipt,
                license_dir=license_dir,
            )
            if completion.get("fallback_authorization") != fallback_authorization.record():
                raise InstallerError(
                    "fallback_not_authorized",
                    "reused fallback release is not bound to the supplied frozen Runtime receipt",
                )
        active_freeze_id = FALLBACK_FREEZE_ID if runtime.source == "fallback" else None
    else:
        runtime = select_runtime(
            official_python,
            fallback_python_dir,
            fallback_authorization_receipt=fallback_authorization_receipt,
        )
        if runtime.source == "fallback":
            fallback_authorization = _require_fallback_authorization(
                fallback_python_dir=fallback_python_dir,
                fallback_authorization_receipt=fallback_authorization_receipt,
                license_dir=license_dir,
            )
            official_attempts = fallback_authorization.official_attempts
            active_freeze_id = FALLBACK_FREEZE_ID
        else:
            official_attempts = ()
            active_freeze_id = None
    if runtime.source == "fallback" and fallback_freeze_id not in (
        None,
        FALLBACK_FREEZE_ID,
    ):
        raise InstallerError(
            "fallback_not_authorized", "fallback freeze identifier does not match the receipt"
        )
    stage_root = layout.staging / f"install-{uuid.uuid4().hex}"
    targets_complete = reused
    try:
        if not reused:
            extracted_root = safe_extract_archive(archive_path, stage_root / "extract")
            app_root = extracted_root / "app"
            for required in (
                app_root / "runtime_manager.py",
                app_root / "image-launcher.py",
                app_root / "packages" / "pptgen_toolkit",
                extracted_root / "macos" / "requirements.lock",
            ):
                if not required.exists():
                    raise InstallerError("archive_layout_invalid", f"release is missing: {required.name}")
            # Virtual environments embed their absolute path in console-script
            # shebangs.  Build the versioned environment at its final, inactive
            # location; the atomic current marker remains the activation gate.
            _quarantine_incomplete(layout, release_target, f"release-{install_id}")
            _quarantine_incomplete(layout, venv_target, f"venv-{install_id}")
            provision = _provision_with_fallback(
                runtime,
                fallback_python_dir=fallback_python_dir,
                fallback_authorization_receipt=fallback_authorization_receipt,
                license_dir=license_dir,
                venv_root=venv_target,
                app_root=app_root,
                wheelhouse=wheelhouse,
                fallback_authorization=fallback_authorization,
            )
            runtime = provision.runtime
            if runtime.source == "fallback":
                fallback_authorization = provision.fallback_authorization
                if fallback_authorization is None:
                    raise InstallerError(
                        "fallback_not_authorized", "fallback authorization is unavailable"
                    )
                official_attempts = (
                    provision.official_attempts
                    if provision.official_attempts
                    else fallback_authorization.official_attempts
                )
                active_freeze_id = FALLBACK_FREEZE_ID
            else:
                official_attempts = provision.official_attempts
                active_freeze_id = None
            install_record = {
                "schema_version": SCHEMA_VERSION,
                "install_id": install_id,
                "version": manifest.version,
                "platform": PLATFORM_ID,
                "archive_sha256": manifest.archive.sha256,
                "archive_size": manifest.archive.size,
                "runtime_source": runtime.source,
                "python_version": runtime.version,
                "fallback_freeze_id": (
                    active_freeze_id if runtime.source == "fallback" else None
                ),
                "official_attempts": list(official_attempts),
                "licenses_path": (
                    str(layout.licenses / install_id)
                    if runtime.source == "fallback"
                    else None
                ),
                "fallback_authorization": (
                    fallback_authorization.record()
                    if runtime.source == "fallback" and fallback_authorization is not None
                    else None
                ),
            }
            _atomic_json(extracted_root / ".image-pptgen-install.json", install_record)
            os.replace(extracted_root, release_target)
            targets_complete = True

        previous = _read_active(layout)
        licenses_path: Path | None = None
        if runtime.source == "fallback":
            if fallback_authorization is None or license_dir is None:
                raise InstallerError(
                    "fallback_not_authorized", "fallback authorization is unavailable"
                )
            licenses_path = _install_licenses(layout, license_dir, install_id=install_id)
        marker = {
            "schema_version": SCHEMA_VERSION,
            "install_id": install_id,
            "version": manifest.version,
            "platform": PLATFORM_ID,
            "archive_sha256": manifest.archive.sha256,
            "archive_size": manifest.archive.size,
            "runtime_source": runtime.source,
            "python_version": runtime.version,
            "fallback_freeze_id": (
                active_freeze_id if runtime.source == "fallback" else None
            ),
            "official_attempts": list(official_attempts),
            "licenses_path": str(licenses_path) if licenses_path else None,
            "fallback_authorization": (
                fallback_authorization.record()
                if runtime.source == "fallback" and fallback_authorization is not None
                else None
            ),
            "previous_install_id": (
                previous.get("install_id")
                if previous and previous.get("install_id") != install_id
                else previous.get("previous_install_id") if previous else None
            ),
            "installed_at": int(time.time()),
        }
        activation_snapshot = _activation_snapshot(layout)
        skill_activation: SkillActivation | None = None
        readiness_started = False
        try:
            _activate_install(layout, marker)
            _write_environment(layout)
            _install_wrappers(layout)
            skill_activation = _install_skill(layout, release_target, install_id)
            readiness_started = True
            readiness = {
                "ok": True,
                "mode": "command_scoped",
                "doctor": _command_scoped_cli_json(
                    layout,
                    "doctor",
                    "--json",
                    install_id=install_id,
                ),
            }
        except BaseException:
            if readiness_started:
                with contextlib.suppress(InstallerError):
                    _runtime_manager_json(layout, "stop", install_id=install_id)
            if skill_activation is not None:
                _restore_skill(skill_activation, layout)
            _restore_activation(layout, activation_snapshot)
            if previous:
                with contextlib.suppress(InstallerError):
                    _command_scoped_cli_json(
                        layout,
                        "doctor",
                        "--json",
                        install_id=str(previous["install_id"]),
                    )
            raise
        return {
            "ok": True,
            "platform": PLATFORM_ID,
            "install_id": install_id,
            "version": manifest.version,
            "runtime_source": runtime.source,
            "python_version": runtime.version,
            "fallback_freeze_id": (
                active_freeze_id if runtime.source == "fallback" else None
            ),
            "official_attempts": list(official_attempts),
            "archive_sha256": manifest.archive.sha256,
            "archive_size": manifest.archive.size,
            "reused": reused,
            "readiness": readiness,
        }
    except BaseException:
        if not targets_complete:
            _quarantine_incomplete(layout, release_target, f"release-{install_id}")
            _quarantine_incomplete(layout, venv_target, f"venv-{install_id}")
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def doctor(layout: InstallLayout) -> dict[str, Any]:
    active = _read_active(layout)
    if not active:
        raise InstallerError("active_install_unavailable", "no active installation is available")
    install_id = str(active["install_id"])
    release, venv = _validate_install_id(layout, install_id)
    if layout.current_release.resolve() != release.resolve() or layout.current_venv.resolve() != venv.resolve():
        raise InstallerError("activation_mismatch", "active marker and activation links disagree")
    cli = _command_scoped_cli_json(
        layout,
        "doctor",
        "--json",
        install_id=install_id,
    )
    runtime = {"ok": True, "mode": "command_scoped"}
    return {
        "ok": True,
        "platform": PLATFORM_ID,
        "install_id": install_id,
        "version": active.get("version"),
        "runtime_source": active.get("runtime_source"),
        "runtime": runtime,
        "cli": cli,
    }


def stop(layout: InstallLayout) -> dict[str, Any]:
    active = _read_active(layout)
    if not active:
        return {"ok": True, "platform": PLATFORM_ID, "stopped": False}
    install_id = str(active["install_id"])
    result = _runtime_manager_json(layout, "stop", install_id=install_id)
    return {"ok": True, "platform": PLATFORM_ID, "install_id": install_id, **result}


def rollback(layout: InstallLayout) -> dict[str, Any]:
    current = _read_active(layout)
    if not current:
        raise InstallerError("active_install_unavailable", "no active installation is available")
    current_id = str(current["install_id"])
    previous_id = current.get("previous_install_id")
    if not isinstance(previous_id, str):
        raise InstallerError("rollback_unavailable", "no previous installation is available")
    _validate_install_id(layout, previous_id)
    previous_record = _complete_marker(
        layout.releases / previous_id / ".image-pptgen-install.json"
    )
    if not previous_record:
        raise InstallerError("rollback_unavailable", "previous installation record is unavailable")
    if previous_record.get("runtime_source") == "fallback":
        expected_licenses = layout.licenses / previous_id
        if (
            previous_record.get("fallback_authorization") is None
            or previous_record.get("licenses_path") != str(expected_licenses)
            or not expected_licenses.is_dir()
        ):
            raise InstallerError(
                "rollback_unavailable",
                "previous frozen fallback release has no bound authorization and licenses",
            )
    with contextlib.suppress(InstallerError):
        _runtime_manager_json(layout, "stop", install_id=current_id)
    marker = {
        **previous_record,
        "previous_install_id": current_id,
        "installed_at": int(time.time()),
    }
    skill_activation: SkillActivation | None = None
    try:
        skill_activation = _install_skill(
            layout, layout.releases / previous_id, previous_id
        )
        _activate_install(layout, marker)
        readiness = {
            "ok": True,
            "mode": "command_scoped",
            "doctor": _command_scoped_cli_json(
                layout,
                "doctor",
                "--json",
                install_id=previous_id,
            ),
        }
    except BaseException:
        if skill_activation is not None:
            _restore_skill(skill_activation, layout)
        _activate_install(layout, current)
        with contextlib.suppress(InstallerError):
            _command_scoped_cli_json(
                layout,
                "doctor",
                "--json",
                install_id=current_id,
            )
        raise
    return {
        "ok": True,
        "platform": PLATFORM_ID,
        "from_install_id": current_id,
        "install_id": previous_id,
        "readiness": readiness,
    }


def _require_macos_arm64() -> None:
    machine = platform.machine().lower()
    if sys.platform != "darwin" or machine not in {"arm64", "aarch64"}:
        raise InstallerError(
            "unsupported_platform",
            "this installer supports only Apple Silicon macOS",
            system=sys.platform,
            machine=machine,
        )


def _layout_from_args(args: argparse.Namespace) -> InstallLayout:
    home = (args.home or Path.home()).expanduser()
    return InstallLayout.for_home(
        home,
        install_root=args.install_root,
        bin_home=args.bin_home,
        skill_home=args.skill_home,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install and manage Image PPTGen on Apple Silicon macOS")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--install-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--bin-home", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--skill-home", type=Path, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install", help="install one verified local release")
    install.add_argument("--manifest", type=Path, required=True)
    install.add_argument("--archive", type=Path, required=True)
    install.add_argument("--wheelhouse", type=Path, required=True)
    install.add_argument("--official-python", type=Path, required=True)
    install.add_argument("--fallback-python-dir", type=Path)
    install.add_argument("--fallback-freeze-id")
    install.add_argument("--fallback-authorization-receipt", type=Path)
    install.add_argument("--license-dir", type=Path)
    doctor_parser = subparsers.add_parser(
        "doctor", help="verify the active installation and service"
    )
    stop_parser = subparsers.add_parser("stop", help="stop the owned Image service")
    rollback_parser = subparsers.add_parser(
        "rollback", help="atomically reactivate the previous release"
    )
    # Accept common integration flags both before and after the subcommand.
    # The duplicate subparser defaults are suppressed so a preceding value is
    # never overwritten.
    for command_parser in (install, doctor_parser, stop_parser, rollback_parser):
        command_parser.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        command_parser.add_argument(
            "--home", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        command_parser.add_argument(
            "--install-root", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        command_parser.add_argument(
            "--bin-home", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        command_parser.add_argument(
            "--skill-home", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        _require_macos_arm64()
        layout = _layout_from_args(args)
        if args.command == "install":
            result = install_release(
                manifest_path=args.manifest,
                archive_path=args.archive,
                wheelhouse=args.wheelhouse,
                official_python=args.official_python,
                fallback_python_dir=args.fallback_python_dir,
                layout=layout,
                fallback_freeze_id=args.fallback_freeze_id,
                license_dir=args.license_dir,
                fallback_authorization_receipt=args.fallback_authorization_receipt,
            )
        elif args.command == "doctor":
            result = doctor(layout)
        elif args.command == "stop":
            result = stop(layout)
        else:
            result = rollback(layout)
    except InstallerError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
