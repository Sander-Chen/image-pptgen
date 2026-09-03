#!/usr/bin/env python3
"""Private lifecycle manager for the installed Image PPTGen service.

The public Image CLI deliberately remains a thin request client.  This module
owns only the local, loopback service lifecycle: it probes the identity
endpoint, starts one managed launcher when necessary, and refuses to signal a
process whose ownership cannot be proved.  It never retries a CLI request.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import ntpath
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Iterable, Mapping
from urllib import error as url_error
from urllib import request as url_request

from backend.services.platform_runtime import (
    ListenerSnapshot,
    PlatformRuntimeError,
    ProcessIdentity,
    background_popen_kwargs,
    current_owner_id,
    exclusive_file_lock,
    find_tcp_listener,
    inspect_process,
    parent_process_id,
    terminate_process,
    venv_python,
    write_private_json,
)


IMAGE_PRODUCT = "image-pptgen"
IMAGE_SERVICE = "image-pptgen-server"
IMAGE_SURFACE = "public_image_3_0"
IMAGE_HOST = "127.0.0.1"
IMAGE_PORT = 3130
IMAGE_BASE_URL = f"http://{IMAGE_HOST}:{IMAGE_PORT}"
IMAGE_DATA_NAMESPACE = "image-pptgen"
IMAGE_RUNTIME_MODE = "installed"
INSTALLED_CODEX_CHILD_MAX_CONCURRENCY = "4"
INSTALLED_CODEX_MIN_AVAILABLE_MIB = "512"
INSTALLED_CODEX_CHILD_RESERVATION_MIB = "512"
INSTALLED_CODEX_CHILD_HARD_MAX_CONCURRENCY = 4

# macOS Codex Desktop tasks can run inside a restricted child session.  A
# user launchd job is outside that session while remaining scoped to the
# logged-in user; it therefore gives the Image service the same filesystem and
# Codex state view as the user, without changing CODEX_HOME or copying auth.
# Keep the label stable so upgrade, stop, and rollback all address exactly one
# product-owned job.  The ``gui/<uid>`` domain is intentionally user-scoped;
# no root launch daemon or elevated permission is involved.
DARWIN_LAUNCHD_LABEL = "com.openai.codex.image-pptgen"
DARWIN_LAUNCHD_PRINT_PID_RE = re.compile(r"(?m)^\s*pid\s*=\s*(\d+)\s*$")
DARWIN_LAUNCHD_ENV_KEYS = frozenset(
    {
        "ALL_PROXY",
        "all_proxy",
        "CODEX_BASE_URL",
        "CODEX_HOME",
        "HOME",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "NO_PROXY",
        "no_proxy",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "PATH",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "USER",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "IMAGE_PPTGEN_BASE_URL",
        "IMAGE_PPTGEN_DATA_ROOT",
        "IMAGE_PPTGEN_HOST",
        "IMAGE_PPTGEN_PORT",
        "PPTGEN_ARTIFACTS_DIR",
        "PPTGEN_BASE_URL",
        "PPTGEN_CODEX_CHILD_MAX_CONCURRENCY",
        "PPTGEN_CODEX_INHERIT_USER_CONFIG",
        "PPTGEN_CODEX_MIN_AVAILABLE_MIB",
        "PPTGEN_CODEX_CHILD_RESERVATION_MIB",
        "PPTGEN_DATA_ROOT",
        "PPTGEN_HISTORICAL_DATA_DIR",
        "PPTGEN_HOST",
        "PPTGEN_IMAGE_RUNTIME_BUILD_ID",
        "PPTGEN_IMAGE_RUNTIME_MODE",
        "PPTGEN_INSTANCE_ID_PATH",
        "PPTGEN_PORT",
        "PPTGEN_PUBLIC_DATA_DIR",
        "PPTGEN_RELEASE_IDENTITY_PATH",
        "PPTGEN_RELEASE_ROOT",
        "PPT_DB_PATH",
        "PPT_ARTIFACTS_DIR",
        "PORT",
    }
)


class RuntimeManagerError(RuntimeError):
    """A structured, safe-to-report readiness failure."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = details
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        # Keep the public CLI error projection stable while exposing a reason
        # to the private installer/diagnostic entry point.
        payload: dict[str, Any] = {
            "error": "platform_unavailable",
            "message": f"Image PPTGen runtime unavailable: {self.code}: {self}",
        }
        if self.details:
            payload["details"] = self.details
        return payload


def _installed_child_max_concurrency(raw: str | None) -> str:
    """Normalize the installed operator ceiling without exceeding hard max."""

    if raw is None:
        return INSTALLED_CODEX_CHILD_MAX_CONCURRENCY
    try:
        configured = int(raw, 10)
    except (TypeError, ValueError):
        return INSTALLED_CODEX_CHILD_MAX_CONCURRENCY
    if configured <= 0:
        return INSTALLED_CODEX_CHILD_MAX_CONCURRENCY
    return str(min(configured, INSTALLED_CODEX_CHILD_HARD_MAX_CONCURRENCY))


@dataclasses.dataclass(frozen=True)
class RuntimePaths:
    """User-private paths used by one Image installation."""

    data_root: Path
    state_root: Path
    manager_root: Path
    pid_path: Path
    lock_path: Path
    log_path: Path
    instance_path: Path
    public_data_root: Path
    db_path: Path
    artifacts_root: Path
    historical_root: Path

    @classmethod
    def for_data_root(cls, data_root: Path) -> "RuntimePaths":
        root = data_root.expanduser().resolve()
        state = root / "state"
        manager = state / "runtime-manager"
        return cls(
            data_root=root,
            state_root=state,
            manager_root=manager,
            pid_path=manager / "service.pid.json",
            lock_path=manager / "service.lock",
            log_path=manager / "service.log",
            instance_path=state / "runtime-instance.json",
            public_data_root=state / "data",
            db_path=state / "data" / "ppt.db",
            artifacts_root=state / "data" / "artifacts",
            historical_root=state / "data" / "historical-data",
        )


@dataclasses.dataclass(frozen=True)
class ManagedSnapshot:
    """Enough private state to restore a known old release on upgrade failure."""

    pid: int
    metadata: dict[str, Any]
    environ: dict[str, str]


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    write_private_json(path, payload)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _data_root_from_environment() -> Path:
    explicit = os.environ.get("IMAGE_PPTGEN_DATA_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    active_root = os.environ.get("PPTGEN_DATA_ROOT")
    if active_root:
        return Path(active_root).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data).expanduser() / "ImagePPTGen").resolve()
        return (Path.home() / "AppData" / "Local" / "ImagePPTGen").resolve()
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser()
    return (data_home / IMAGE_DATA_NAMESPACE).resolve()


def _app_root_default() -> Path:
    return Path(__file__).resolve().parent


def _load_env_file() -> None:
    """Load the installer env file without evaluating shell syntax."""

    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ).expanduser()
    path = config_home / IMAGE_DATA_NAMESPACE / "env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key.isidentifier() and key not in os.environ:
            os.environ[key] = value


def _safe_relative(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _regular_non_reparse(path: Path, *, directory: bool) -> bool:
    """Return whether *path* is a local regular, non-reparse object."""

    try:
        file_stat = os.lstat(path)
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(file_stat.st_mode):
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(file_stat, "st_file_attributes", 0) & reparse_flag:
        return False
    return stat.S_ISDIR(file_stat.st_mode) if directory else stat.S_ISREG(file_stat.st_mode)


def _canonical_existing_path(value: str, *, directory: bool) -> Path | None:
    """Resolve one absolute config path only after a no-reparse inspection."""

    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute() or not _regular_non_reparse(candidate, directory=directory):
        return None
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _windows_path_equal(left: Path | str, right: Path | str) -> bool:
    """Compare Windows paths without trusting a basename or PATH lookup."""

    return ntpath.normcase(ntpath.normpath(str(left))) == ntpath.normcase(
        ntpath.normpath(str(right))
    )


def _path_lexists(path: Path) -> bool:
    """Inspect a path's directory entry without following a reparse target."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


_WINDOWS_RUNTIME_SELECTION_APPROACHES = (
    "venv-ensurepip",
    "venv-explicit-ensurepip",
)
_WINDOWS_RUNTIME_SELECTION_ATTEMPT_FIELDS = {
    "approach",
    "result",
    "stage",
    "exit_code",
    "stderr",
    "stdout",
    "exception",
}
_WINDOWS_FALLBACK_FREEZE_ID = "pbs-20260718-cp311-plus-cp312-v4"
_WINDOWS_FALLBACK_ARCHIVE_SHA256 = (
    "a48c2dbe832319f61aa8557c9900caec70f7fed0cbee391a4c9ff9f98b50222d"
)
_WINDOWS_FALLBACK_ARCHIVE_BYTES = 25678291


def _windows_canonical_diagnostic(value: Any) -> str | None:
    """Match windows_installer's persisted diagnostic normalization."""

    if not isinstance(value, str) or len(value) > 12000:
        return None
    value = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|token|authorization|password|secret|cookie)\s*[:=]\s*[^\s\r\n]+",
        "[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+[^\s\r\n]+", "Bearer [REDACTED]", value)
    return re.sub(r"(?im)(?:[A-Z]:\\Users\\|\\\\)[^\r\n]+", "<redacted-path>", value)


def _windows_persisted_runtime_selection_is_valid(
    value: Any, *, runtime_source: str
) -> bool:
    """Validate the durable runtime-selection form from windows_installer."""

    if not isinstance(value, dict):
        return False
    attempts = value.get("official_attempts")
    if not isinstance(attempts, list):
        return False
    normalized_attempts: list[dict[str, Any]] = []
    for attempt in attempts:
        if (
            not isinstance(attempt, dict)
            or set(attempt) != _WINDOWS_RUNTIME_SELECTION_ATTEMPT_FIELDS
        ):
            return False
        approach = attempt["approach"]
        result = attempt["result"]
        stage = attempt["stage"]
        exit_code = attempt["exit_code"]
        if (
            approach not in _WINDOWS_RUNTIME_SELECTION_APPROACHES
            or result not in {"failed", "succeeded"}
            or not isinstance(stage, str)
            or not stage.startswith(f"official-probe:{approach}:")
            or isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or not 0 <= exit_code <= 255
            or (result == "succeeded") != (exit_code == 0)
        ):
            return False
        diagnostics: dict[str, str] = {}
        for key in ("stderr", "stdout", "exception"):
            diagnostic = _windows_canonical_diagnostic(attempt[key])
            if diagnostic is None or diagnostic != attempt[key]:
                return False
            diagnostics[key] = diagnostic
        normalized_attempts.append(
            {
                "approach": approach,
                "result": result,
                "stage": stage,
                "exit_code": exit_code,
                **diagnostics,
            }
        )

    if runtime_source == "official":
        if (
            set(value)
            != {
                "schema_version",
                "platform",
                "decision",
                "selected_approach",
                "official_attempts",
            }
            or value.get("schema_version") != 1
            or value.get("platform") != "windows-amd64"
            or value.get("decision") != "official_selected"
            or value.get("selected_approach") not in _WINDOWS_RUNTIME_SELECTION_APPROACHES
            or not 1 <= len(normalized_attempts) <= 2
            or [attempt["approach"] for attempt in normalized_attempts]
            != list(_WINDOWS_RUNTIME_SELECTION_APPROACHES[: len(normalized_attempts)])
            or normalized_attempts[-1]["approach"] != value.get("selected_approach")
            or normalized_attempts[-1]["result"] != "succeeded"
            or any(attempt["result"] != "failed" for attempt in normalized_attempts[:-1])
        ):
            return False
    else:
        fallback = value.get("fallback_runtime")
        if (
            set(value)
            != {
                "schema_version",
                "platform",
                "decision",
                "official_attempts",
                "fallback_runtime",
            }
            or value.get("schema_version") != 1
            or value.get("platform") != "windows-amd64"
            or value.get("decision") != "fallback_authorized"
            or fallback
            != {
                "freeze_id": _WINDOWS_FALLBACK_FREEZE_ID,
                "archive_sha256": _WINDOWS_FALLBACK_ARCHIVE_SHA256,
                "archive_bytes": _WINDOWS_FALLBACK_ARCHIVE_BYTES,
            }
            or len(normalized_attempts) != 2
            or [attempt["approach"] for attempt in normalized_attempts]
            != list(_WINDOWS_RUNTIME_SELECTION_APPROACHES)
            or any(attempt["result"] != "failed" for attempt in normalized_attempts)
        ):
            return False
    return normalized_attempts == value.get("official_attempts")


def _proc_alive(pid: int) -> bool:
    """Compatibility test seam; production decisions use full identities."""

    try:
        return inspect_process(pid) is not None
    except PlatformRuntimeError:
        return False


def find_listener(port: int = IMAGE_PORT) -> ListenerSnapshot:
    """Compatibility seam around the shared cross-platform listener oracle."""

    return find_tcp_listener(IMAGE_HOST, port)


class RuntimeManager:
    """Manage one user-private Image PPTGen release."""

    def __init__(
        self,
        *,
        app_root: Path | None = None,
        data_root: Path | None = None,
        host: str = IMAGE_HOST,
        port: int = IMAGE_PORT,
        startup_timeout: float = 30.0,
        health_timeout: float = 1.0,
        poll_interval: float = 0.1,
        launchctl_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        if host != IMAGE_HOST:
            raise RuntimeManagerError(
                "unsupported_host", "Image PPTGen binds only to 127.0.0.1"
            )
        if port != IMAGE_PORT:
            raise RuntimeManagerError(
                "unsupported_port", "Image PPTGen listens only on port 3130"
            )
        self.app_root = (app_root or _app_root_default()).expanduser().resolve()
        self.paths = RuntimePaths.for_data_root(data_root or _data_root_from_environment())
        self.host = host
        self.port = port
        self.base_url = IMAGE_BASE_URL
        self.startup_timeout = max(0.1, float(startup_timeout))
        self.health_timeout = max(0.1, float(health_timeout))
        self.poll_interval = max(0.01, float(poll_interval))
        # Injectable for Linux-hosted contract tests.  Production always
        # resolves to the system launchctl binary on Darwin; no test fixture
        # is allowed to masquerade as a real VM/service proof.
        self._launchctl_runner = launchctl_runner or self._run_launchctl

    def _is_darwin(self) -> bool:
        """Return whether this manager is running on Apple Silicon macOS.

        Kept as a seam so focused tests can exercise the launchd contract on
        a Linux CI host without pretending that Linux is a macOS acceptance
        result.
        """

        return sys.platform == "darwin"

    def _is_windows(self) -> bool:
        """Return whether the Windows-only child-listener seam is active."""

        return sys.platform in {"win32", "cygwin", "nt"}

    @property
    def launchd_plist_path(self) -> Path:
        """Stable private plist used by this user's Image launchd job."""

        return self.paths.manager_root / f"{DARWIN_LAUNCHD_LABEL}.plist"

    def _launchd_domain(self) -> str:
        try:
            uid = os.geteuid()
        except AttributeError as exc:  # pragma: no cover - Darwin always has geteuid
            raise RuntimeManagerError(
                "launchd_unavailable", "current user identity cannot be read"
            ) from exc
        return f"gui/{uid}"

    def _launchd_target(self) -> str:
        return f"{self._launchd_domain()}/{DARWIN_LAUNCHD_LABEL}"

    @staticmethod
    def _run_launchctl(command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["/bin/launchctl", *command],
                check=False,
                capture_output=True,
                text=True,
                timeout=10.0,
                env={"LC_ALL": "C", "LANG": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeManagerError(
                "launchd_unavailable", "macOS launchctl could not be invoked"
            ) from exc

    def _launchctl(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = self._launchctl_runner(command)
        except RuntimeManagerError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeManagerError(
                "launchd_unavailable", "macOS launchctl could not be invoked"
            ) from exc
        if not isinstance(result, subprocess.CompletedProcess):
            raise RuntimeManagerError(
                "launchd_contract_invalid", "launchctl runner returned an invalid result"
            )
        return result

    @property
    def release_identity_path(self) -> Path:
        return self.app_root / "release-identity.json"

    @property
    def launcher_path(self) -> Path:
        for name in ("image-launcher.py", "launcher.py"):
            candidate = self.app_root / name
            if candidate.is_file():
                return candidate
        raise RuntimeManagerError(
            "launcher_missing", f"Image runtime launcher is unavailable: {self.app_root}"
        )

    def _release_identity(self) -> dict[str, str]:
        release = _read_json(self.release_identity_path)
        required = (
            "build_id",
            "version",
            "source_commit",
            "skill_sha256",
            "runtime_content_sha256",
        )
        if release is None or any(
            not isinstance(release.get(field), str) or not release[field].strip()
            for field in required
        ):
            raise RuntimeManagerError(
                "release_identity_missing", f"Invalid Image release identity: {self.release_identity_path}"
            )
        if (
            release.get("product") != IMAGE_PRODUCT
            or release.get("service") != IMAGE_SERVICE
            or release.get("surface") != IMAGE_SURFACE
        ):
            raise RuntimeManagerError(
                "release_identity_invalid", "Image release identity does not belong to this service"
            )
        return {field: str(value) for field, value in release.items() if isinstance(value, str)}

    def _instance_id(self) -> str:
        instance = _read_json(self.paths.instance_path)
        if instance and isinstance(instance.get("instance_id"), str) and instance["instance_id"].strip():
            with contextlib.suppress(OSError):
                self.paths.instance_path.chmod(0o600)
            return instance["instance_id"]
        value = str(uuid.uuid4())
        _write_private_json(self.paths.instance_path, {"instance_id": value})
        return value

    def _expected_identity(self) -> dict[str, str]:
        release = self._release_identity()
        return {
            "base_url": self.base_url,
            "build_id": release["build_id"],
            "instance_id": self._instance_id(),
            "product": IMAGE_PRODUCT,
            "service": IMAGE_SERVICE,
            "surface": IMAGE_SURFACE,
            "version": release["version"],
            "source_commit": release["source_commit"],
            "skill_sha256": release["skill_sha256"],
            "runtime_content_sha256": release["runtime_content_sha256"],
        }

    def _health_request(self) -> dict[str, Any]:
        req = url_request.Request(
            f"{self.base_url}/api/runtime-identity",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with url_request.urlopen(req, timeout=self.health_timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (url_error.URLError, url_error.HTTPError, TimeoutError, OSError, socket.timeout, json.JSONDecodeError) as exc:
            raise RuntimeManagerError("health_unavailable", "health endpoint is not reachable") from exc
        if not isinstance(value, dict):
            raise RuntimeManagerError("health_invalid", "health endpoint returned a non-object")
        return value

    def _health_matches(self, observed: Mapping[str, Any], expected: Mapping[str, str]) -> bool:
        # Compare every immutable value returned by the installed Image
        # identity endpoint.  Relative data/artifact roots are fixed by the
        # public contract and intentionally not compared to host paths here.
        for field in (
            "base_url",
            "build_id",
            "instance_id",
            "product",
            "service",
            "surface",
            "version",
            "source_commit",
            "skill_sha256",
            "runtime_content_sha256",
        ):
            if observed.get(field) != expected.get(field):
                return False
        return observed.get("data_root") == f"{IMAGE_DATA_NAMESPACE}/state/data" and observed.get(
            "artifacts_root"
        ) == f"{IMAGE_DATA_NAMESPACE}/state/data/artifacts"

    def probe(self, expected: Mapping[str, str] | None = None) -> dict[str, Any] | None:
        """Return matching health identity, or ``None`` without mutating state."""

        expected_identity = dict(expected or self._expected_identity())
        try:
            observed = self._health_request()
        except RuntimeManagerError:
            return None
        if self._health_matches(observed, expected_identity):
            if not self._is_windows() or self._windows_listener_proven(
                self._read_metadata()
            ):
                return observed
        return None

    def _python_executable(self) -> str:
        configured = os.environ.get("IMAGE_PPTGEN_PYTHON")
        if configured and Path(configured).is_file():
            return str(Path(configured).resolve())
        data_python = venv_python(self.paths.data_root / "current-venv")
        if data_python.is_file() and os.access(data_python, os.X_OK):
            return str(data_python)
        return sys.executable

    def _launch_env(self, *, release_root: Path | None = None) -> dict[str, str]:
        expected = self._expected_identity()
        release_dir = (release_root or self.app_root.parent).resolve()
        env = os.environ.copy()
        env.update(
            {
                "IMAGE_PPTGEN_BASE_URL": self.base_url,
                "IMAGE_PPTGEN_HOST": self.host,
                "IMAGE_PPTGEN_PORT": str(self.port),
                "PPTGEN_BASE_URL": self.base_url,
                "PPTGEN_DATA_ROOT": str(self.paths.data_root),
                "PPTGEN_INSTANCE_ID_PATH": str(self.paths.instance_path),
                "PPTGEN_RELEASE_IDENTITY_PATH": str(release_dir / "app" / "release-identity.json"),
                "PPTGEN_RELEASE_ROOT": str(release_dir),
                "PPTGEN_PUBLIC_DATA_DIR": str(self.paths.public_data_root),
                "PPTGEN_HISTORICAL_DATA_DIR": str(self.paths.historical_root),
                "PPTGEN_IMAGE_RUNTIME_MODE": IMAGE_RUNTIME_MODE,
                "PPT_DB_PATH": str(self.paths.db_path),
                "PPT_ARTIFACTS_DIR": str(self.paths.artifacts_root),
                "PORT": str(self.port),
                "PPTGEN_HOST": self.host,
                "PPTGEN_PORT": str(self.port),
            }
        )
        # Installed runtimes use the shared gate's dynamic 512/512 memory
        # profile.  An operator may lower the concurrency ceiling, but stale
        # environment values must not raise it above the hard maximum or
        # replace the fixed host floor/reservation contract.
        env["PPTGEN_CODEX_CHILD_MAX_CONCURRENCY"] = _installed_child_max_concurrency(
            env.get("PPTGEN_CODEX_CHILD_MAX_CONCURRENCY")
        )
        env["PPTGEN_CODEX_MIN_AVAILABLE_MIB"] = INSTALLED_CODEX_MIN_AVAILABLE_MIB
        env["PPTGEN_CODEX_CHILD_RESERVATION_MIB"] = INSTALLED_CODEX_CHILD_RESERVATION_MIB
        # Keep this explicit field available for ownership checks and for
        # restart snapshots; it contains no credentials.
        env["PPTGEN_IMAGE_RUNTIME_BUILD_ID"] = expected["build_id"]
        return env

    def _launch_command(self, *, launcher: Path | None = None) -> list[str]:
        target = launcher or self.launcher_path
        return [self._python_executable(), str(target.resolve()), "--host", self.host, "--port", str(self.port)]

    @staticmethod
    def _launchd_environment(env: Mapping[str, str]) -> dict[str, str]:
        """Keep launchd's persisted environment minimal and credential-free.

        Authentication remains in the user's existing ``CODEX_HOME``.  The
        only Codex-specific value copied into the plist is that path itself;
        API keys and arbitrary Desktop-task environment values are deliberately
        excluded from the persistent job definition.
        """

        selected = {
            key: str(value)
            for key, value in env.items()
            if key in DARWIN_LAUNCHD_ENV_KEYS and isinstance(value, str) and value
        }
        selected.setdefault("HOME", str(Path.home()))
        selected.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        # The service's Codex child must use the user's existing login/config
        # domain.  This does not create, copy, or migrate any auth material.
        selected.setdefault("PPTGEN_CODEX_INHERIT_USER_CONFIG", "1")
        return selected

    def _launchd_plist_payload(
        self,
        *,
        command: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> dict[str, Any]:
        launchd_env = dict(env)
        # ``launcher.py`` derives its roots from XDG variables.  The Mac
        # installer uses ~/.codex/image-pptgen, so make that same root
        # deterministic even when a later Desktop task did not inherit XDG.
        launchd_env.setdefault("XDG_DATA_HOME", str(self.paths.data_root.parent))
        launchd_env.setdefault("XDG_CONFIG_HOME", str(self.paths.data_root.parent))
        return {
            "Label": DARWIN_LAUNCHD_LABEL,
            "ProgramArguments": list(command),
            "WorkingDirectory": str(cwd.resolve()),
            "EnvironmentVariables": self._launchd_environment(launchd_env),
            "StandardOutPath": str(self.paths.log_path),
            "StandardErrorPath": str(self.paths.log_path),
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Background",
        }

    def _write_launchd_plist(
        self,
        *,
        command: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> None:
        """Atomically write and read back the exact launchd contract."""

        _private_directory(self.paths.manager_root)
        payload = self._launchd_plist_payload(command=command, env=env, cwd=cwd)
        staged = self.launchd_plist_path.with_name(
            f".{self.launchd_plist_path.name}.new-{uuid.uuid4().hex}"
        )
        try:
            staged.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))
            staged.chmod(0o600)
            os.replace(staged, self.launchd_plist_path)
            observed = plistlib.loads(self.launchd_plist_path.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError) as exc:
            raise RuntimeManagerError(
                "launchd_contract_invalid", "macOS launchd plist could not be written"
            ) from exc
        finally:
            with contextlib.suppress(OSError):
                staged.unlink()
        if not isinstance(observed, dict) or observed != payload:
            raise RuntimeManagerError(
                "launchd_contract_invalid", "macOS launchd plist readback did not match"
            )

    def _launchd_pid(self) -> int | None:
        result = self._launchctl(["print", self._launchd_target()])
        if result.returncode != 0:
            return None
        match = DARWIN_LAUNCHD_PRINT_PID_RE.search(result.stdout or "")
        if match is None:
            return None
        try:
            return int(match.group(1), 10)
        except ValueError:
            return None

    def _launchd_wait_for_identity(
        self,
        *,
        command: list[str],
    ) -> ProcessIdentity:
        """Wait for launchd's exact job PID and prove its process identity."""

        deadline = time.monotonic() + self.startup_timeout
        last_inspection_error: PlatformRuntimeError | None = None
        last_pid: int | None = None
        while time.monotonic() < deadline:
            pid = self._launchd_pid()
            if pid is not None:
                last_pid = pid
                try:
                    identity = inspect_process(pid)
                except PlatformRuntimeError as exc:
                    # ``launchctl bootstrap`` can publish the job PID just
                    # before libproc/sysctl can read the new process.  Treat
                    # that narrow startup window like a not-yet-visible PID;
                    # ownership is still accepted only after the complete
                    # argv-bearing identity matches the launch contract.
                    last_inspection_error = exc
                    time.sleep(self.poll_interval)
                    continue
                if identity is not None and tuple(command) == identity.argv:
                    return identity
            time.sleep(self.poll_interval)
        if last_inspection_error is not None:
            raise RuntimeManagerError(
                "process_unavailable",
                "launchd service identity did not become readable before timeout",
                pid=last_pid,
                platform_code=last_inspection_error.code,
            ) from last_inspection_error
        raise RuntimeManagerError(
            "start_failed",
            "launchd service identity did not become readable",
            launchd_target=self._launchd_target(),
        )

    def _launchd_unload(self, *, tolerate_absent: bool = True) -> None:
        """Unload only this exact user job; never kill by process name."""

        result = self._launchctl(["bootout", self._launchd_target()])
        if result.returncode == 0 or tolerate_absent and self._launchd_absent_result(result):
            return
        detail = (result.stderr or result.stdout or "launchctl bootout failed").strip()
        raise RuntimeManagerError(
            "launchd_stop_failed",
            f"could not unload the owned macOS launchd job: {detail[-1000:]}",
            launchd_target=self._launchd_target(),
            returncode=result.returncode,
        )

    @staticmethod
    def _launchd_absent_result(result: subprocess.CompletedProcess[str]) -> bool:
        text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        return any(
            marker in text
            for marker in (
                "could not find service",
                "no such process",
                "service not found",
                "unknown service",
            )
        )

    def _launchd_unload_for_metadata(self, metadata: Mapping[str, Any]) -> None:
        if not self._is_darwin() or metadata.get("launchd_label") != DARWIN_LAUNCHD_LABEL:
            return
        if metadata.get("launchd_domain") != self._launchd_domain():
            raise RuntimeManagerError(
                "ownership_unproven",
                "refusing to unload a launchd job in another user domain",
            )
        if metadata.get("launchd_plist_path") != str(self.launchd_plist_path):
            raise RuntimeManagerError(
                "ownership_unproven",
                "refusing to unload a launchd job with an unexpected plist",
            )
        self._launchd_unload()

    def _metadata(
        self,
        identity: ProcessIdentity,
        env: Mapping[str, str],
        command: list[str],
    ) -> dict[str, Any]:
        expected = self._expected_identity()
        return {
            "schema_version": 2,
            "pid": identity.pid,
            "owner_id": identity.owner_id,
            "start_token": identity.start_token,
            "executable": identity.executable,
            "started_at": time.time(),
            "argv": list(command),
            "command": IMAGE_SERVICE,
            "product": IMAGE_PRODUCT,
            "service": IMAGE_SERVICE,
            "surface": IMAGE_SURFACE,
            "base_url": self.base_url,
            "build_id": expected["build_id"],
            "version": expected["version"],
            "source_commit": expected["source_commit"],
            "skill_sha256": expected["skill_sha256"],
            "runtime_content_sha256": expected["runtime_content_sha256"],
            "instance_id": expected["instance_id"],
            "release_root": str(self.app_root.parent.resolve()),
            "release_identity_path": str(self.release_identity_path.resolve()),
            "data_root": str(self.paths.data_root),
            "env": {
                key: env[key]
                for key in (
                    "IMAGE_PPTGEN_BASE_URL",
                    "IMAGE_PPTGEN_HOST",
                    "IMAGE_PPTGEN_PORT",
                    "PPTGEN_BASE_URL",
                    "PPTGEN_DATA_ROOT",
                    "PPTGEN_INSTANCE_ID_PATH",
                    "PPTGEN_RELEASE_IDENTITY_PATH",
                    "PPTGEN_RELEASE_ROOT",
                    "PPTGEN_PUBLIC_DATA_DIR",
                    "PPTGEN_HISTORICAL_DATA_DIR",
                    "PPTGEN_IMAGE_RUNTIME_MODE",
                    "PPT_DB_PATH",
                    "PPT_ARTIFACTS_DIR",
                    "PORT",
                    "PPTGEN_HOST",
                    "PPTGEN_PORT",
                    "PPTGEN_CODEX_CHILD_MAX_CONCURRENCY",
                    "PPTGEN_CODEX_INHERIT_USER_CONFIG",
                    "PPTGEN_CODEX_MIN_AVAILABLE_MIB",
                    "PPTGEN_CODEX_CHILD_RESERVATION_MIB",
                    "PPTGEN_IMAGE_RUNTIME_BUILD_ID",
                    "CODEX_HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                )
                if key in env
            },
        }

    def _decorate_launchd_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if self._is_darwin():
            metadata.update(
                {
                    "launchd_label": DARWIN_LAUNCHD_LABEL,
                    "launchd_domain": self._launchd_domain(),
                    "launchd_plist_path": str(self.launchd_plist_path),
                }
            )
        return metadata

    def _append_log(self, line: str) -> None:
        _private_directory(self.paths.manager_root)
        with self.paths.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")
        with contextlib.suppress(OSError):
            self.paths.log_path.chmod(0o600)

    def _read_metadata(self) -> dict[str, Any] | None:
        return _read_json(self.paths.pid_path)

    @staticmethod
    def _identity_from_metadata(
        metadata: Mapping[str, Any] | None,
    ) -> ProcessIdentity | None:
        if metadata is None or metadata.get("schema_version") != 2:
            return None
        argv = metadata.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(value, str) for value in argv
        ):
            return None
        pid = metadata.get("pid")
        owner_id = metadata.get("owner_id")
        start_token = metadata.get("start_token")
        executable = metadata.get("executable")
        if (
            not isinstance(pid, int)
            or not isinstance(owner_id, str)
            or not owner_id
            or not isinstance(start_token, str)
            or not start_token
            or not isinstance(executable, str)
            or not executable
        ):
            return None
        return ProcessIdentity(
            pid=pid,
            owner_id=owner_id,
            start_token=start_token,
            executable=executable,
            argv=tuple(argv),
        )

    def _validate_owned_identity(
        self, observed: ProcessIdentity, metadata: Mapping[str, Any]
    ) -> bool:
        recorded = self._identity_from_metadata(metadata)
        if recorded is None or recorded != observed:
            return False
        if observed.owner_id != current_owner_id():
            return False
        command = list(observed.argv)
        launcher_args = [
            Path(token).resolve()
            for token in command
            if token.endswith(("image-launcher.py", "launcher.py"))
            and Path(token).is_file()
        ]
        if len(launcher_args) != 1:
            return False
        launcher = launcher_args[0]
        app_root = launcher.parent.resolve()
        release_root = app_root.parent
        if not _safe_relative(release_root, self.paths.data_root / "releases"):
            return False
        identity = _read_json(app_root / "release-identity.json")
        if (
            not identity
            or identity.get("product") != IMAGE_PRODUCT
            or identity.get("service") != IMAGE_SERVICE
            or identity.get("surface") != IMAGE_SURFACE
        ):
            return False
        try:
            host_index = command.index("--host") + 1
            port_index = command.index("--port") + 1
            if command[host_index] != self.host or int(command[port_index]) != self.port:
                return False
        except (IndexError, ValueError):
            return False
        if "launchd_label" in metadata:
            if (
                not self._is_darwin()
                or metadata.get("launchd_label") != DARWIN_LAUNCHD_LABEL
                or metadata.get("launchd_domain") != self._launchd_domain()
                or metadata.get("launchd_plist_path") != str(self.launchd_plist_path)
            ):
                return False
        return (
            metadata.get("product") == IMAGE_PRODUCT
            and metadata.get("service") == IMAGE_SERVICE
            and metadata.get("surface") == IMAGE_SURFACE
            and metadata.get("build_id") == identity.get("build_id")
            and metadata.get("release_root") == str(release_root)
        )

    @staticmethod
    def _windows_launcher_tail(argv: tuple[str, ...]) -> tuple[str, ...] | None:
        """Return the exact argv suffix beginning at the launcher token."""

        launcher_indexes = [
            index
            for index, token in enumerate(argv)
            if token.endswith(("image-launcher.py", "launcher.py"))
        ]
        # The installed launch contract places the launcher immediately after
        # the interpreter.  Requiring that shape keeps the only permitted
        # difference to argv[0] (the venv redirector vs. base interpreter).
        if launcher_indexes != [1]:
            return None
        return argv[1:]

    def _windows_verified_manager_release_root(self, manager: ProcessIdentity) -> Path:
        """Return the release root proved by the manager's launcher argv."""

        launcher_indexes = [
            index
            for index, token in enumerate(manager.argv)
            if token.endswith(("image-launcher.py", "launcher.py"))
            and Path(token).is_file()
        ]
        if launcher_indexes != [1]:
            raise RuntimeManagerError(
                "unknown_listener",
                "verified Windows manager has no exact launcher app root",
                pid=manager.pid,
            )
        try:
            launcher = Path(manager.argv[1]).resolve(strict=True)
            app_root = launcher.parent.resolve(strict=True)
            release_root = app_root.parent.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeManagerError(
                "unknown_listener",
                "verified Windows manager launcher root cannot be resolved",
                pid=manager.pid,
            ) from exc
        if (
            not _regular_non_reparse(app_root, directory=True)
            or not _regular_non_reparse(release_root, directory=True)
            or not _windows_path_equal(app_root, self.app_root)
            or not _safe_relative(release_root, self.paths.data_root / "releases")
        ):
            raise RuntimeManagerError(
                "unknown_listener",
                "verified Windows manager release is outside the active app root",
                pid=manager.pid,
            )
        return release_root

    def _windows_current_venv_root(self, *, manager: ProcessIdentity) -> Path:
        """Resolve the one active Windows venv and release for this install."""

        data_root = self.paths.data_root.resolve()
        manager_release_root = self._windows_verified_manager_release_root(manager)
        candidates: list[Path] = []
        current_root = data_root / "current-venv"
        if _path_lexists(current_root):
            if not _regular_non_reparse(current_root, directory=True):
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows current-venv is missing or a reparse point",
                    pid=manager.pid,
                )
            try:
                candidates.append(current_root.resolve(strict=True))
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows current-venv cannot be resolved",
                    pid=manager.pid,
                ) from exc
        state_dir = data_root / "state"
        state_path = state_dir / "windows-install-state.json"
        if _path_lexists(state_path):
            if not _regular_non_reparse(state_path, directory=False):
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows active install state is missing or unsafe",
                    pid=manager.pid,
                )
            if not _regular_non_reparse(state_dir, directory=True):
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows active install state parent is unsafe",
                    pid=manager.pid,
                )
            state = _read_json(state_path)
            if (
                state is None
                or state.get("schema_version") != 1
                or state.get("platform") != "windows-amd64"
            ):
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows active install state has an unsupported schema",
                    pid=manager.pid,
                )
            active = state.get("active")
            install_id = active.get("install_id") if isinstance(active, dict) else None
            raw_release_root = (
                active.get("release_root") if isinstance(active, dict) else None
            )
            raw_venv_root = (
                active.get("venv_root") if isinstance(active, dict) else None
            )
            runtime_source = (
                active.get("runtime_source") if isinstance(active, dict) else None
            )
            runtime_selection = (
                active.get("runtime_selection") if isinstance(active, dict) else None
            )
            if (
                not isinstance(install_id, str)
                or not install_id
                or not isinstance(raw_release_root, str)
                or not raw_release_root
                or not isinstance(raw_venv_root, str)
                or not raw_venv_root
                or runtime_source not in {"official", "fallback"}
                or (
                    runtime_selection is not None
                    and not _windows_persisted_runtime_selection_is_valid(
                        runtime_selection, runtime_source=runtime_source
                    )
                )
            ):
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows active install state has no valid active venv",
                    pid=manager.pid,
                )
            releases_root = data_root / "releases"
            venvs_root = data_root / "venvs"
            # Keep this validation in lockstep with the bootstrap's
            # windows_installer._entry_is_usable contract (lines 756-790).
            # The bootstrap module is not part of the release app payload, so
            # the runtime manager checks the same durable entry and marker
            # fields locally before accepting the active venv.
            state_release_root = _canonical_existing_path(
                raw_release_root, directory=True
            )
            state_venv_root = _canonical_existing_path(raw_venv_root, directory=True)
            if (
                state_release_root is None
                or state_venv_root is None
                or not _regular_non_reparse(releases_root, directory=True)
                or not _regular_non_reparse(venvs_root, directory=True)
                or state_release_root.parent != releases_root.resolve()
                or state_release_root.name != install_id
                or state_venv_root.parent != venvs_root.resolve()
                or state_venv_root.name != install_id
                or not _windows_path_equal(state_release_root, manager_release_root)
            ):
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows active install release/venv is outside the controlled root",
                    pid=manager.pid,
                )
            marker_path = state_release_root / ".windows-install.json"
            marker = _read_json(marker_path)
            if (
                not _regular_non_reparse(marker_path, directory=False)
                or marker is None
                or marker.get("schema_version") != 1
                or marker.get("entry") != active
                or not _regular_non_reparse(
                    state_release_root / "app" / "runtime_manager.py",
                    directory=False,
                )
                or not _regular_non_reparse(
                    state_venv_root / "Scripts" / "python.exe",
                    directory=False,
                )
                or not _regular_non_reparse(
                    state_venv_root / "Scripts" / "image-pptgen.exe",
                    directory=False,
                )
            ):
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows active install marker or runtime files are invalid",
                    pid=manager.pid,
                )
            candidates.append(state_venv_root)

        if not candidates:
            raise RuntimeManagerError(
                "unknown_listener",
                "Windows active install venv cannot be established",
                pid=manager.pid,
            )
        first = candidates[0]
        if any(not _windows_path_equal(first, candidate) for candidate in candidates[1:]):
            raise RuntimeManagerError(
                "unknown_listener",
                "Windows current-venv and active install state disagree",
                pid=manager.pid,
            )
        return first

    def _windows_venv_base_executable(self, manager: ProcessIdentity) -> Path:
        """Resolve the exact base interpreter named by the manager's venv."""

        manager_executable = _canonical_existing_path(
            manager.executable, directory=False
        )
        if (
            manager_executable is None
            or manager_executable.name.casefold() != "python.exe"
            or manager_executable.parent.name.casefold() != "scripts"
            or not manager.argv
            or not _windows_path_equal(manager.executable, manager.argv[0])
        ):
            raise RuntimeManagerError(
                "unknown_listener",
                "recorded Windows manager is not a venv Scripts/python.exe",
                pid=manager.pid,
            )

        venv_root = manager_executable.parent.parent
        if not _regular_non_reparse(venv_root, directory=True):
            raise RuntimeManagerError(
                "unknown_listener",
                "recorded Windows manager venv root is not a local directory",
                pid=manager.pid,
            )
        try:
            venv_root = venv_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeManagerError(
                "unknown_listener",
                "recorded Windows manager venv root cannot be resolved",
                pid=manager.pid,
            ) from exc
        expected_venv_root = self._windows_current_venv_root(manager=manager)
        if not _windows_path_equal(venv_root, expected_venv_root):
            raise RuntimeManagerError(
                "unknown_listener",
                "recorded Windows manager is not the active install venv",
                pid=manager.pid,
            )
        config_path = venv_root / "pyvenv.cfg"
        if (
            config_path.parent != venv_root
            or not _regular_non_reparse(config_path, directory=False)
        ):
            raise RuntimeManagerError(
                "unknown_listener",
                "Windows venv pyvenv.cfg is missing or unsafe",
                pid=manager.pid,
            )
        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise RuntimeManagerError(
                "unknown_listener",
                "Windows venv pyvenv.cfg cannot be read safely",
                pid=manager.pid,
            ) from exc

        values: dict[str, str] = {}
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            key, separator, raw_value = line.partition("=")
            key = key.strip().casefold()
            value = raw_value.strip()
            if not separator or not key or not value or key in values:
                raise RuntimeManagerError(
                    "unknown_listener",
                    "Windows venv pyvenv.cfg is malformed or ambiguous",
                    pid=manager.pid,
                )
            values[key] = value

        home = _canonical_existing_path(values.get("home", ""), directory=True)
        base_executable = _canonical_existing_path(
            values.get("executable", ""), directory=False
        )
        if home is None or base_executable is None:
            raise RuntimeManagerError(
                "unknown_listener",
                "Windows venv pyvenv.cfg has no safe home/executable binding",
                pid=manager.pid,
            )
        if (
            base_executable.name.casefold() != "python.exe"
            or base_executable.parent != home
            or home == venv_root
        ):
            raise RuntimeManagerError(
                "unknown_listener",
                "Windows venv pyvenv.cfg home/executable binding is inconsistent",
                pid=manager.pid,
            )
        return base_executable

    def _windows_listener_child(
        self,
        snapshot: ListenerSnapshot,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[ManagedSnapshot, ProcessIdentity | None] | None:
        """Prove the sole Windows listener is the recorded manager's child."""

        if not snapshot.complete:
            code = "unknown_listener" if snapshot.present else "listener_unavailable"
            raise RuntimeManagerError(
                code,
                f"port {self.port} ownership could not be inspected completely",
                port=self.port,
                source=snapshot.source,
            )
        if not snapshot.present:
            return None
        if len(snapshot.pids) != 1:
            raise RuntimeManagerError(
                "unknown_listener",
                f"port {self.port} listener ownership is not unique and complete",
                port=self.port,
            )
        if metadata is None or not isinstance(metadata.get("pid"), int):
            raise RuntimeManagerError(
                "unknown_listener",
                f"port {self.port} listener has no recorded Image manager",
                port=self.port,
            )

        manager_pid = metadata["pid"]
        manager_identity = self._owned_identity(manager_pid, metadata)
        if manager_identity is None:
            raise RuntimeManagerError(
                "unknown_listener",
                f"recorded manager PID {manager_pid} is not a proved Image process",
                pid=manager_pid,
            )

        listener_pid = next(iter(snapshot.pids))
        if listener_pid == manager_pid:
            # Preserve the existing direct-owner Windows contract.  The
            # stricter parent proof applies only when the listener is a
            # distinct process from the recorded manager.
            return self._snapshot(manager_pid, dict(metadata)), None
        base_executable = self._windows_venv_base_executable(manager_identity)
        try:
            listener_identity = inspect_process(listener_pid)
        except PlatformRuntimeError as exc:
            raise RuntimeManagerError(
                "unknown_listener",
                f"listener PID {listener_pid} identity cannot be inspected",
                pid=listener_pid,
                platform_code=exc.code,
            ) from exc
        if listener_identity is None:
            raise RuntimeManagerError(
                "unknown_listener",
                f"listener PID {listener_pid} is no longer present",
                pid=listener_pid,
            )

        listener_metadata = dict(metadata)
        listener_metadata.update(
            {
                "pid": listener_identity.pid,
                "owner_id": listener_identity.owner_id,
                "start_token": listener_identity.start_token,
                "executable": listener_identity.executable,
                "argv": list(listener_identity.argv),
            }
        )
        launcher = next(
            (
                Path(token).resolve()
                for token in listener_identity.argv
                if token.endswith(("image-launcher.py", "launcher.py"))
                and Path(token).is_file()
            ),
            None,
        )
        release_identity = (
            _read_json(launcher.parent / "release-identity.json")
            if launcher is not None
            else None
        )
        release_fields_match = release_identity is not None and all(
            listener_metadata.get(field) == release_identity.get(field)
            for field in (
                "build_id",
                "version",
                "source_commit",
                "skill_sha256",
                "runtime_content_sha256",
            )
        )
        manager_launcher_tail = self._windows_launcher_tail(manager_identity.argv)
        listener_launcher_tail = self._windows_launcher_tail(listener_identity.argv)
        if (
            listener_identity.owner_id != manager_identity.owner_id
            or not _windows_path_equal(listener_identity.executable, base_executable)
            or not listener_identity.argv
            or not _windows_path_equal(
                listener_identity.executable, listener_identity.argv[0]
            )
            or manager_launcher_tail is None
            or listener_launcher_tail is None
            or listener_launcher_tail != manager_launcher_tail
            or not release_fields_match
            or not self._validate_owned_identity(listener_identity, listener_metadata)
        ):
            raise RuntimeManagerError(
                "unknown_listener",
                f"listener PID {listener_pid} does not match the recorded Image release",
                pid=listener_pid,
            )
        try:
            parent_pid = parent_process_id(listener_pid)
        except PlatformRuntimeError as exc:
            raise RuntimeManagerError(
                "unknown_listener",
                f"listener PID {listener_pid} parent cannot be proved",
                pid=listener_pid,
                platform_code=exc.code,
            ) from exc
        if parent_pid != manager_pid:
            raise RuntimeManagerError(
                "unknown_listener",
                f"listener PID {listener_pid} parent is not the recorded manager",
                pid=listener_pid,
                parent_pid=parent_pid,
                manager_pid=manager_pid,
            )
        try:
            manager_after_parent = self._owned_identity(manager_pid, metadata)
        except RuntimeManagerError as exc:
            raise RuntimeManagerError(
                "unknown_listener",
                f"recorded manager PID {manager_pid} changed during listener proof",
                pid=manager_pid,
            ) from exc
        if manager_after_parent is None or manager_after_parent != manager_identity:
            raise RuntimeManagerError(
                "unknown_listener",
                f"recorded manager PID {manager_pid} changed during listener proof",
                pid=manager_pid,
            )
        return self._snapshot(manager_pid, dict(metadata)), listener_identity

    def _windows_listener_proven(self, metadata: Mapping[str, Any] | None) -> bool:
        """Return true only after repeat-Doctor ownership is fully rechecked."""

        if metadata is None:
            return False
        try:
            snapshot = find_listener(self.port)
            proven = self._windows_listener_child(snapshot, metadata)
        except (PlatformRuntimeError, RuntimeManagerError):
            return False
        return proven is not None

    def _wait_for_windows_listener_exit(self, expected: ProcessIdentity) -> None:
        """Prove the original child exited without ever signalling its PID."""

        deadline = time.monotonic() + min(5.0, self.startup_timeout)
        while True:
            try:
                current = inspect_process(expected.pid)
            except PlatformRuntimeError as exc:
                raise RuntimeManagerError(
                    "process_unavailable",
                    f"listener PID {expected.pid} exit cannot be proved",
                    pid=expected.pid,
                    platform_code=exc.code,
                ) from exc
            if current is None or current != expected:
                return
            if time.monotonic() >= deadline:
                raise RuntimeManagerError(
                    "termination_timeout",
                    f"listener PID {expected.pid} remained after manager termination",
                    pid=expected.pid,
                )
            time.sleep(self.poll_interval)

    def _owned_identity(
        self, pid: int, metadata: Mapping[str, Any] | None = None
    ) -> ProcessIdentity | None:
        if metadata is None:
            return None
        try:
            observed = inspect_process(pid)
        except PlatformRuntimeError as exc:
            raise RuntimeManagerError(
                "process_unavailable",
                f"process identity for PID {pid} cannot be inspected",
                pid=pid,
                platform_code=exc.code,
            ) from exc
        if observed is None or not self._validate_owned_identity(observed, metadata):
            return None
        return observed

    def _owned_process(
        self, pid: int, metadata: Mapping[str, Any] | None = None
    ) -> bool:
        """Compatibility predicate for tests; mutation paths use exact identity."""

        try:
            return self._owned_identity(pid, metadata) is not None
        except RuntimeManagerError:
            return False

    def _snapshot(self, pid: int, metadata: dict[str, Any]) -> ManagedSnapshot:
        env = {
            str(key): str(value)
            for key, value in (metadata.get("env") or {}).items()
            if isinstance(key, str) and isinstance(value, str)
        }
        return ManagedSnapshot(pid=pid, metadata=dict(metadata), environ=env)

    def _signal_owned(self, pid: int, metadata: Mapping[str, Any]) -> None:
        identity = self._owned_identity(pid, metadata)
        if identity is None:
            raise RuntimeManagerError(
                "ownership_unproven",
                f"refusing to signal PID {pid}: Image ownership proof is incomplete",
                pid=pid,
            )
        try:
            terminate_process(
                identity,
                graceful_timeout=min(5.0, self.startup_timeout),
                poll_interval=self.poll_interval,
            )
        except PlatformRuntimeError as exc:
            raise RuntimeManagerError(
                exc.code,
                f"refusing to terminate PID {pid}: {exc}",
                pid=pid,
            ) from exc

    def _stop_managed_listener(self, snapshot: ListenerSnapshot) -> ManagedSnapshot | None:
        metadata = self._read_metadata()
        if self._is_windows() and snapshot.present:
            managed_and_child = self._windows_listener_child(snapshot, metadata)
            if managed_and_child is None:  # pragma: no cover - present was checked
                raise RuntimeManagerError(
                    "unknown_listener",
                    f"port {self.port} listener ownership is not proven",
                    port=self.port,
                )
            managed, listener_identity = managed_and_child
            self._signal_owned(managed.pid, managed.metadata)
            if listener_identity is not None:
                self._wait_for_windows_listener_exit(listener_identity)
            with contextlib.suppress(OSError):
                self.paths.pid_path.unlink()
            return managed
        if not snapshot.complete:
            code = "unknown_listener" if snapshot.present else "listener_unavailable"
            raise RuntimeManagerError(
                code,
                f"port {self.port} ownership could not be inspected completely",
                port=self.port,
                source=snapshot.source,
            )
        if not snapshot.present:
            if metadata and isinstance(metadata.get("pid"), int):
                pid = metadata["pid"]
                try:
                    observed = inspect_process(pid)
                except PlatformRuntimeError as exc:
                    raise RuntimeManagerError(
                        "process_unavailable",
                        f"recorded PID {pid} cannot be inspected",
                        pid=pid,
                        platform_code=exc.code,
                    ) from exc
                if observed is None:
                    # A launchd job may remain loaded even after its child has
                    # exited.  Unload our exact job before dropping metadata so
                    # the next bootstrap cannot collide with a stale label.
                    self._launchd_unload_for_metadata(metadata)
                    with contextlib.suppress(OSError):
                        self.paths.pid_path.unlink()
                    return None
                if not self._validate_owned_identity(observed, metadata):
                    raise RuntimeManagerError(
                        "ownership_unproven",
                        f"recorded PID {pid} is live but not a proved Image process",
                        pid=pid,
                    )
                managed = self._snapshot(pid, metadata)
                self._signal_owned(pid, metadata)
                self._launchd_unload_for_metadata(metadata)
                with contextlib.suppress(OSError):
                    self.paths.pid_path.unlink()
                return managed
            return None
        if not snapshot.pids or len(snapshot.pids) != 1:
            raise RuntimeManagerError(
                "unknown_listener",
                f"port {self.port} listener ownership is not unique and complete",
                port=self.port,
            )
        managed: list[ManagedSnapshot] = []
        for pid in sorted(snapshot.pids):
            if metadata and metadata.get("pid") == pid and self._owned_process(pid, metadata):
                managed.append(self._snapshot(pid, metadata))
                continue
            # A listener must be tied to the manager's metadata.  Even a
            # process whose command looks Image-like is unknown without the
            # matching private record, so it is never signalled.
            raise RuntimeManagerError(
                "unknown_listener",
                f"port {self.port} is occupied by an unmanaged listener (PID {pid})",
                port=self.port,
                pid=pid,
            )
        for item in managed:
            self._signal_owned(item.pid, item.metadata)
            self._launchd_unload_for_metadata(item.metadata)
        with contextlib.suppress(OSError):
            self.paths.pid_path.unlink()
        return managed[0] if managed else None

    @staticmethod
    def _terminate_spawned_process(process: subprocess.Popen[Any]) -> None:
        """Clean up the exact child represented by this still-held Popen."""

        if process.poll() is not None:
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            process.terminate()
        try:
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            process.kill()
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            process.wait(timeout=3)

    def _spawn_launchd(
        self,
        *,
        launcher: Path | None = None,
        release_root: Path | None = None,
        command_override: list[str] | None = None,
        env_override: Mapping[str, str] | None = None,
        metadata_template: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start the Darwin service through one user-scoped launchd job.

        ``launchctl bootstrap`` detaches the service from the Desktop task's
        sandbox.  The manager still records and validates the launchd-created
        PID exactly like the existing cross-platform path, so port ownership,
        upgrade rollback, and stop remain fail-closed.
        """

        _private_directory(self.paths.manager_root)
        env = dict(env_override or self._launch_env(release_root=release_root))
        command = list(command_override or self._launch_command(launcher=launcher))
        cwd = (launcher.parent if launcher else self.app_root).resolve()
        self._write_launchd_plist(command=command, env=env, cwd=cwd)
        bootstrapped = False
        try:
            result = self._launchctl(
                ["bootstrap", self._launchd_domain(), str(self.launchd_plist_path)]
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "launchctl bootstrap failed").strip()
                raise RuntimeManagerError(
                    "launchd_start_failed",
                    f"could not bootstrap the owned macOS launchd job: {detail[-1000:]}",
                    launchd_target=self._launchd_target(),
                    returncode=result.returncode,
                )
            bootstrapped = True
            identity = self._launchd_wait_for_identity(
                command=command,
            )
            if metadata_template is None:
                metadata = self._metadata(identity, env, command)
            else:
                metadata = dict(metadata_template)
                metadata.update(
                    {
                        "schema_version": 2,
                        "pid": identity.pid,
                        "owner_id": identity.owner_id,
                        "start_token": identity.start_token,
                        "executable": identity.executable,
                        "started_at": time.time(),
                        "argv": list(command),
                    }
                )
            self._decorate_launchd_metadata(metadata)
            if tuple(command) != identity.argv or not self._validate_owned_identity(
                identity, metadata
            ):
                raise RuntimeManagerError(
                    "ownership_unproven",
                    "launchd Image service identity does not match its launch contract",
                    pid=identity.pid,
                )
            _write_private_json(self.paths.pid_path, metadata)
        except (OSError, PlatformRuntimeError, RuntimeManagerError) as exc:
            if bootstrapped:
                with contextlib.suppress(Exception):
                    self._launchd_unload()
            if isinstance(exc, RuntimeManagerError):
                raise
            raise RuntimeManagerError(
                "start_failed",
                f"launchd Image service identity could not be persisted: {exc}",
            ) from exc
        self._append_log(
            f"started pid={metadata['pid']} build_id={metadata['build_id']} launchd={DARWIN_LAUNCHD_LABEL}"
        )
        return metadata

    def _spawn(
        self,
        *,
        launcher: Path | None = None,
        release_root: Path | None = None,
        command_override: list[str] | None = None,
        env_override: Mapping[str, str] | None = None,
        metadata_template: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._is_darwin():
            return self._spawn_launchd(
                launcher=launcher,
                release_root=release_root,
                command_override=command_override,
                env_override=env_override,
                metadata_template=metadata_template,
            )
        _private_directory(self.paths.manager_root)
        env = dict(env_override or self._launch_env(release_root=release_root))
        command = list(command_override or self._launch_command(launcher=launcher))
        process: subprocess.Popen[Any] | None = None
        try:
            log_handle = self.paths.log_path.open("a", encoding="utf-8")
            self.paths.log_path.chmod(0o600)
            process = subprocess.Popen(
                command,
                cwd=str((launcher.parent if launcher else self.app_root).resolve()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                **background_popen_kwargs(),
            )
        except (OSError, PlatformRuntimeError, subprocess.SubprocessError) as exc:
            with contextlib.suppress(UnboundLocalError):
                log_handle.close()
            raise RuntimeManagerError("start_failed", f"could not start Image service: {exc}") from exc
        finally:
            with contextlib.suppress(UnboundLocalError):
                log_handle.close()
        assert process is not None
        try:
            identity = inspect_process(process.pid)
            if identity is None:
                raise RuntimeManagerError(
                    "start_failed",
                    "Image service exited before its identity could be captured",
                    pid=process.pid,
                )
            if metadata_template is None:
                metadata = self._metadata(identity, env, command)
            else:
                metadata = dict(metadata_template)
                metadata.update(
                    {
                        "schema_version": 2,
                        "pid": identity.pid,
                        "owner_id": identity.owner_id,
                        "start_token": identity.start_token,
                        "executable": identity.executable,
                        "started_at": time.time(),
                        "argv": list(command),
                    }
                )
            if tuple(command) != identity.argv or not self._validate_owned_identity(
                identity, metadata
            ):
                raise RuntimeManagerError(
                    "ownership_unproven",
                    "new Image service identity does not match its launch contract",
                    pid=process.pid,
                )
            _write_private_json(self.paths.pid_path, metadata)
        except (OSError, PlatformRuntimeError, RuntimeManagerError) as exc:
            self._terminate_spawned_process(process)
            if isinstance(exc, RuntimeManagerError):
                raise
            raise RuntimeManagerError(
                "start_failed",
                f"new Image service identity could not be persisted: {exc}",
                pid=process.pid,
            ) from exc
        self._append_log(f"started pid={process.pid} build_id={metadata['build_id']}")
        return metadata

    def _wait_for_health(self, expected: Mapping[str, str], metadata: Mapping[str, Any]) -> dict[str, Any]:
        def clear_metadata() -> None:
            current = self._read_metadata()
            if current and current.get("pid") == metadata.get("pid"):
                with contextlib.suppress(OSError):
                    self.paths.pid_path.unlink()

        deadline = time.monotonic() + self.startup_timeout
        last_error = "health endpoint did not become ready"
        while time.monotonic() < deadline:
            observed: dict[str, Any] | None = None
            try:
                observed = self._health_request()
            except RuntimeManagerError as exc:
                last_error = str(exc)
            if observed is not None and self._health_matches(observed, expected):
                return observed
            pid = metadata.get("pid")
            if isinstance(pid, int):
                try:
                    process_identity = inspect_process(pid)
                except PlatformRuntimeError as exc:
                    raise RuntimeManagerError(
                        "process_unavailable",
                        f"Image service identity became unreadable: {exc}",
                        pid=pid,
                    ) from exc
                if process_identity is None:
                    try:
                        self._launchd_unload_for_metadata(metadata)
                    except RuntimeManagerError as cleanup_exc:
                        clear_metadata()
                        raise RuntimeManagerError(
                            "launchd_cleanup_failed",
                            "Image service exited and its launchd job could not be unloaded",
                            original_code="start_failed",
                            cleanup_code=cleanup_exc.code,
                        ) from cleanup_exc
                    clear_metadata()
                    tail = ""
                    try:
                        tail = self.paths.log_path.read_text(encoding="utf-8")[-1200:]
                    except OSError:
                        pass
                    raise RuntimeManagerError(
                        "start_failed",
                        f"Image service exited before readiness: {tail.strip() or last_error}",
                        pid=pid,
                    )
                if not self._validate_owned_identity(process_identity, metadata):
                    raise RuntimeManagerError(
                        "ownership_changed",
                        f"Image service identity changed before readiness (PID {pid})",
                        pid=pid,
                    )
            time.sleep(self.poll_interval)
        pid = metadata.get("pid")
        if isinstance(pid, int) and self._owned_process(pid, metadata):
            with contextlib.suppress(Exception):
                self._signal_owned(pid, metadata)
                self._launchd_unload_for_metadata(metadata)
        clear_metadata()
        raise RuntimeManagerError("startup_timeout", last_error, pid=pid)

    def _restart_snapshot(self, snapshot: ManagedSnapshot) -> None:
        old_argv = snapshot.metadata.get("argv")
        if not isinstance(old_argv, list) or not old_argv or not all(
            isinstance(value, str) for value in old_argv
        ):
            raise RuntimeManagerError(
                "rollback_unavailable", "old Image launch command is incomplete"
            )
        if not Path(old_argv[0]).is_absolute():
            raise RuntimeManagerError(
                "rollback_unavailable", "old Image interpreter is not absolute"
            )
        old_launcher = next(
            (
                Path(token)
                for token in old_argv
                if token.endswith(("image-launcher.py", "launcher.py"))
            ),
            None,
        )
        if (
            old_launcher is None
            or not old_launcher.is_absolute()
            or not old_launcher.is_file()
            or not _safe_relative(
                old_launcher.resolve(), self.paths.data_root / "releases"
            )
        ):
            raise RuntimeManagerError(
                "rollback_unavailable", "old Image launcher is unavailable or unsafe"
            )
        old_expected: dict[str, str] = {}
        for field in (
            "base_url",
            "build_id",
            "instance_id",
            "product",
            "service",
            "surface",
            "version",
            "source_commit",
            "skill_sha256",
            "runtime_content_sha256",
        ):
            value = snapshot.metadata.get(field)
            if not isinstance(value, str) or not value:
                raise RuntimeManagerError(
                    "rollback_unavailable",
                    f"old Image identity field is missing: {field}",
                )
            old_expected[field] = value
        env = os.environ.copy()
        env.update(snapshot.environ)
        restored = self._spawn(
            launcher=old_launcher,
            command_override=list(old_argv),
            env_override=env,
            metadata_template=snapshot.metadata,
        )
        self._wait_for_health(old_expected, restored)

    def _ensure_ready_locked(self) -> dict[str, Any]:
        expected = self._expected_identity()
        healthy = self.probe(expected)
        if healthy is not None:
            return healthy

        try:
            listener = find_listener(self.port)
        except PlatformRuntimeError as exc:
            raise RuntimeManagerError(
                "listener_unavailable",
                f"Image listener ownership cannot be inspected: {exc}",
                platform_code=exc.code,
            ) from exc
        old_snapshot = self._stop_managed_listener(listener)
        try:
            metadata = self._spawn()
            return self._wait_for_health(expected, metadata)
        except RuntimeManagerError as exc:
            # A failed upgrade must not strand a previously healthy managed
            # release.  Restart it only from the same ownership snapshot.
            if old_snapshot is not None:
                try:
                    self._restart_snapshot(old_snapshot)
                except RuntimeManagerError as rollback_exc:
                    raise RuntimeManagerError(
                        "rollback_failed",
                        f"new Image release failed and old release did not recover: {rollback_exc}",
                        original_code=exc.code,
                        rollback_code=rollback_exc.code,
                    ) from exc
            raise exc

    @contextlib.contextmanager
    def _lock(self) -> Iterable[None]:
        try:
            with exclusive_file_lock(
                self.paths.lock_path,
                timeout_seconds=max(5.0, self.startup_timeout),
                poll_seconds=self.poll_interval,
            ):
                yield
        except PlatformRuntimeError as exc:
            raise RuntimeManagerError("lock_unavailable", str(exc)) from exc

    def ensure_ready(self) -> dict[str, Any]:
        expected = self._expected_identity()
        healthy = self.probe(expected)
        if healthy is not None:
            return {**healthy, "ok": True, "reused": True}
        with self._lock():
            healthy = self.probe(expected)
            if healthy is not None:
                return {**healthy, "ok": True, "reused": True}
            result = self._ensure_ready_locked()
            return {**result, "ok": True, "reused": False}

    def stop(self) -> dict[str, Any]:
        with self._lock():
            if self._is_windows():
                try:
                    snapshot = find_listener(self.port)
                except PlatformRuntimeError as exc:
                    raise RuntimeManagerError(
                        "listener_unavailable",
                        f"Image listener ownership cannot be inspected: {exc}",
                        platform_code=exc.code,
                    ) from exc
                managed = self._stop_managed_listener(snapshot)
                if managed is None:
                    return {"ok": True, "stopped": False}
                self._append_log(f"stopped pid={managed.pid}")
                return {"ok": True, "stopped": True, "pid": managed.pid}
            metadata = self._read_metadata()
            if not metadata or not isinstance(metadata.get("pid"), int):
                return {"ok": True, "stopped": False}
            pid = metadata["pid"]
            try:
                observed = inspect_process(pid)
            except PlatformRuntimeError as exc:
                raise RuntimeManagerError(
                    "process_unavailable",
                    f"refusing to inspect PID {pid}: {exc}",
                    pid=pid,
                ) from exc
            if observed is not None:
                if not self._validate_owned_identity(observed, metadata):
                    raise RuntimeManagerError(
                        "ownership_unproven",
                        f"refusing to signal PID {pid}: Image ownership proof is incomplete",
                        pid=pid,
                    )
                self._signal_owned(pid, metadata)
                self._launchd_unload_for_metadata(metadata)
            else:
                self._launchd_unload_for_metadata(metadata)
            with contextlib.suppress(OSError):
                self.paths.pid_path.unlink()
            self._append_log(f"stopped pid={pid}")
            return {"ok": True, "stopped": True, "pid": pid}


def ensure_ready(**kwargs: Any) -> dict[str, Any]:
    """Convenience entry point used by tests and the shell wrapper."""

    return RuntimeManager(**kwargs).ensure_ready()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the installed Image PPTGen runtime")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("ensure-ready", "stop"),
        default="ensure-ready",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--app-root", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env_file()
    args = _build_parser().parse_args(argv)
    try:
        manager = RuntimeManager(
            app_root=args.app_root,
            data_root=args.data_root,
            startup_timeout=args.startup_timeout,
        )
        result = manager.ensure_ready() if args.command == "ensure-ready" else manager.stop()
    except RuntimeManagerError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 3
    if args.json or args.command == "ensure-ready":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
