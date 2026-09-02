from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import plistlib
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANAGER_PATH = ROOT / "packaging" / "image" / "runtime_manager.py"
_SPEC = importlib.util.spec_from_file_location("image_runtime_manager", MANAGER_PATH)
assert _SPEC and _SPEC.loader
runtime_manager = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runtime_manager
_SPEC.loader.exec_module(runtime_manager)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _release(tmp_path: Path) -> tuple[Path, Path, dict[str, str], int]:
    data_root = tmp_path / "image-data"
    app_root = data_root / "releases" / "r1" / "app"
    app_root.mkdir(parents=True)
    identity = {
        "build_id": "build-runtime-test",
        "version": "0.1.3",
        "source_commit": "source-runtime-test",
        "skill_sha256": "skill-runtime-test",
        "runtime_content_sha256": "content-runtime-test",
        "product": "image-pptgen",
        "service": "image-pptgen-server",
        "surface": "public_image_3_0",
    }
    (app_root / "release-identity.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )
    (app_root / "image-launcher.py").write_text(
        textwrap.dedent(
            """
            import argparse
            import json
            import os
            from http.server import BaseHTTPRequestHandler, HTTPServer
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--host", required=True)
            parser.add_argument("--port", required=True, type=int)
            args = parser.parse_args()
            release = json.loads(
                (Path(__file__).resolve().parent / "release-identity.json").read_text()
            )
            instance = json.loads(
                open(os.environ["PPTGEN_INSTANCE_ID_PATH"], encoding="utf-8").read()
            )["instance_id"]
            payload = json.dumps(
                {
                    "artifacts_root": "image-pptgen/state/data/artifacts",
                    "base_url": f"http://127.0.0.1:{args.port}",
                    "build_id": release["build_id"],
                    "data_root": "image-pptgen/state/data",
                    "instance_id": instance,
                    "product": "image-pptgen",
                    "service": "image-pptgen-server",
                    "skill_sha256": release["skill_sha256"],
                    "source_commit": release["source_commit"],
                    "surface": "public_image_3_0",
                    "runtime_content_sha256": release["runtime_content_sha256"],
                    "version": release["version"],
                }
            ).encode("utf-8")

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path != "/api/runtime-identity":
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, *_args):
                    pass

            HTTPServer((args.host, args.port), Handler).serve_forever()
            """
        ),
        encoding="utf-8",
    )
    return data_root, app_root, identity, _free_port()


def _manager(data_root: Path, app_root: Path, port: int):
    manager = runtime_manager.RuntimeManager(
        app_root=app_root, data_root=data_root, startup_timeout=8
    )
    # The production manager is intentionally fixed to 3130.  A disposable
    # port keeps this focused test isolated from any user service on 3130.
    manager.port = port
    manager.base_url = f"http://127.0.0.1:{port}"
    return manager


def _windows_child_chain_fixture(tmp_path: Path, *, active_state: bool = False):
    """Build a disposable Windows ownership chain without touching a VM."""

    data_root, app_root, _identity, port = _release(tmp_path)
    manager = _manager(data_root, app_root, port)
    manager._is_windows = lambda: True  # type: ignore[method-assign]
    venv_root = (
        data_root / "venvs" / "r1"
        if active_state
        else data_root / "current-venv"
    )
    manager_executable = venv_root / "Scripts" / "python.exe"
    base_home = tmp_path / "managed-primary"
    base_executable = base_home / "python.exe"
    manager_executable.parent.mkdir(parents=True)
    base_home.mkdir(parents=True)
    manager_executable.write_text("venv redirector", encoding="utf-8")
    base_executable.write_text("managed primary", encoding="utf-8")
    (venv_root / "pyvenv.cfg").write_text(
        "home = %s\n"
        "include-system-site-packages = false\n"
        "version = 3.12.13\n"
        "executable = %s\n"
        % (base_home, base_executable),
        encoding="utf-8",
    )
    if active_state:
        release_root = app_root.parent.resolve()
        (app_root / "runtime_manager.py").write_text(
            "# runtime manager fixture\n", encoding="utf-8"
        )
        (venv_root / "Scripts" / "image-pptgen.exe").write_text(
            "managed cli fixture\n", encoding="utf-8"
        )
        active_entry = {
            "install_id": "r1",
            "version": "0.1.3",
            "payload_sha256": "a" * 64,
            "payload_size": 123,
            "release_root": str(release_root),
            "venv_root": str(venv_root.resolve()),
            "runtime_source": "official",
            "base_python": str(base_executable.resolve()),
            "skill_root": str((tmp_path / "skills").resolve()),
        }
        (release_root / ".windows-install.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "platform": "windows-amd64",
                    "entry": active_entry,
                }
            ),
            encoding="utf-8",
        )
        (data_root / "state").mkdir(parents=True)
        (data_root / "state" / "windows-install-state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "platform": "windows-amd64",
                    "active": active_entry,
                }
            ),
            encoding="utf-8",
        )
    manager._python_executable = lambda: str(manager_executable.resolve())  # type: ignore[method-assign]
    owner_id = "sid:S-1-5-21-r47-admin"
    manager_pid = 4088
    listener_pid = 5864
    command = tuple(manager._launch_command())
    manager_identity = runtime_manager.ProcessIdentity(
        pid=manager_pid,
        owner_id=owner_id,
        start_token="win32:manager-start",
        executable=str(manager_executable.resolve()),
        argv=command,
    )
    metadata = manager._metadata(
        manager_identity, manager._launch_env(), list(manager_identity.argv)
    )
    runtime_manager._write_private_json(manager.paths.pid_path, metadata)
    listener_identity = runtime_manager.ProcessIdentity(
        pid=listener_pid,
        owner_id=owner_id,
        start_token="win32:listener-start",
        executable=str(base_executable.resolve()),
        argv=(str(base_executable.resolve()), *command[1:]),
    )
    expected = {
        **manager._expected_identity(),
        "data_root": "image-pptgen/state/data",
        "artifacts_root": "image-pptgen/state/data/artifacts",
    }
    return manager, manager_identity, listener_identity, metadata, expected


def _install_windows_chain_probe(
    monkeypatch: pytest.MonkeyPatch,
    manager,
    manager_identity: runtime_manager.ProcessIdentity,
    listener_identity: runtime_manager.ProcessIdentity,
    expected: dict[str, str],
    *,
    parent_pid: int | None = 4088,
    listener_pids: frozenset[int] | None = None,
):
    identities = {
        manager_identity.pid: manager_identity,
        listener_identity.pid: listener_identity,
    }
    monkeypatch.setattr(
        runtime_manager,
        "inspect_process",
        lambda pid: identities.get(pid),
    )
    monkeypatch.setattr(
        runtime_manager,
        "current_owner_id",
        lambda: manager_identity.owner_id,
    )
    parent_calls: list[int] = []

    def parent_lookup(pid: int):
        parent_calls.append(pid)
        return parent_pid

    monkeypatch.setattr(
        runtime_manager, "parent_process_id", parent_lookup, raising=False
    )
    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(
            True,
            listener_pids
            if listener_pids is not None
            else frozenset({listener_identity.pid}),
        ),
    )
    monkeypatch.setattr(manager, "_health_request", lambda: expected)
    return parent_calls


def test_runtime_manager_root_resolution_matches_windows_active_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit_root = tmp_path / "explicit"
    active_root = tmp_path / "active"
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("IMAGE_PPTGEN_DATA_ROOT", str(explicit_root))
    monkeypatch.setenv("PPTGEN_DATA_ROOT", str(active_root))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert runtime_manager._data_root_from_environment() == explicit_root.resolve()

    monkeypatch.delenv("IMAGE_PPTGEN_DATA_ROOT")
    assert runtime_manager._data_root_from_environment() == active_root.resolve()

    monkeypatch.delenv("PPTGEN_DATA_ROOT")
    monkeypatch.setattr(
        runtime_manager,
        "os",
        SimpleNamespace(name="nt", environ=os.environ),
    )
    assert runtime_manager._data_root_from_environment() == (
        local_app_data / "ImagePPTGen"
    ).resolve()


def test_installed_runtime_uses_safe_desktop_child_profile_and_preserves_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_root, app_root, _identity, port = _release(tmp_path)
    for key in (
        "PPTGEN_CODEX_CHILD_MAX_CONCURRENCY",
        "PPTGEN_CODEX_MIN_AVAILABLE_MIB",
        "PPTGEN_CODEX_CHILD_RESERVATION_MIB",
    ):
        monkeypatch.delenv(key, raising=False)
    manager = _manager(data_root, app_root, port)

    defaults = manager._launch_env()
    assert defaults["PPTGEN_CODEX_CHILD_MAX_CONCURRENCY"] == "4"
    assert defaults["PPTGEN_CODEX_MIN_AVAILABLE_MIB"] == "512"
    assert defaults["PPTGEN_CODEX_CHILD_RESERVATION_MIB"] == "512"

    monkeypatch.setenv("PPTGEN_CODEX_CHILD_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("PPTGEN_CODEX_MIN_AVAILABLE_MIB", "1536")
    monkeypatch.setenv("PPTGEN_CODEX_CHILD_RESERVATION_MIB", "768")
    overridden = manager._launch_env()
    assert overridden["PPTGEN_CODEX_CHILD_MAX_CONCURRENCY"] == "2"
    assert overridden["PPTGEN_CODEX_MIN_AVAILABLE_MIB"] == "512"
    assert overridden["PPTGEN_CODEX_CHILD_RESERVATION_MIB"] == "512"

    monkeypatch.setenv("PPTGEN_CODEX_CHILD_MAX_CONCURRENCY", "99")
    capped = manager._launch_env()
    assert capped["PPTGEN_CODEX_CHILD_MAX_CONCURRENCY"] == "4"

    identity = runtime_manager.ProcessIdentity(
        pid=31_337,
        owner_id="owner",
        start_token="start",
        executable=sys.executable,
        argv=tuple(manager._launch_command()),
    )
    metadata = manager._metadata(identity, overridden, list(identity.argv))
    assert metadata["env"]["PPTGEN_CODEX_CHILD_MAX_CONCURRENCY"] == "2"
    assert metadata["env"]["PPTGEN_CODEX_MIN_AVAILABLE_MIB"] == "512"
    assert metadata["env"]["PPTGEN_CODEX_CHILD_RESERVATION_MIB"] == "512"


def test_darwin_service_uses_user_launchd_and_preserves_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The Darwin lifecycle is contract-tested without claiming macOS proof."""

    data_root, app_root, _identity, port = _release(tmp_path)
    calls: list[list[str]] = []
    launchd_pid = 42_424
    manager = _manager(data_root, app_root, port)
    manager._is_darwin = lambda: True  # type: ignore[method-assign]
    monkeypatch.setenv("CODEX_HOME", "/Users/agent/.codex")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-plist")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-enter-plist-either")
    expected_proxies = {
        "HTTP_PROXY": "http://upper-proxy.invalid:8080",
        "HTTPS_PROXY": "https://upper-proxy.invalid:8443",
        "ALL_PROXY": "socks5://upper-proxy.invalid:1080",
        "NO_PROXY": "localhost,127.0.0.1",
        "http_proxy": "http://lower-proxy.invalid:18080",
        "https_proxy": "https://lower-proxy.invalid:18443",
        "all_proxy": "socks5://lower-proxy.invalid:11080",
        "no_proxy": "localhost,127.0.0.1",
    }
    for key, value in expected_proxies.items():
        monkeypatch.setenv(key, value)

    command = manager._launch_command()
    process_identity = runtime_manager.ProcessIdentity(
        pid=launchd_pid,
        owner_id=runtime_manager.current_owner_id(),
        start_token="darwin:test:1",
        executable=str(Path(sys.executable).resolve()),
        argv=tuple(command),
    )

    def launchctl(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        if arguments[0] == "print":
            return subprocess.CompletedProcess(
                arguments, 0, "gui/1000/com.openai.codex.image-pptgen\n\tpid = 42424\n", ""
            )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    manager._launchctl_runner = launchctl
    monkeypatch.setattr(
        runtime_manager,
        "inspect_process",
        lambda pid: process_identity if pid == launchd_pid else None,
    )
    monkeypatch.setattr(
        runtime_manager,
        "terminate_process",
        lambda identity, **_kwargs: calls.append(["terminate", str(identity.pid)]),
    )

    metadata = manager._spawn()
    plist = plistlib.loads(manager.launchd_plist_path.read_bytes())
    assert metadata["launchd_label"] == runtime_manager.DARWIN_LAUNCHD_LABEL
    assert metadata["launchd_domain"] == manager._launchd_domain()
    assert plist["Label"] == runtime_manager.DARWIN_LAUNCHD_LABEL
    assert plist["ProgramArguments"] == command
    assert plist["EnvironmentVariables"]["CODEX_HOME"] == "/Users/agent/.codex"
    for key, value in expected_proxies.items():
        assert plist["EnvironmentVariables"][key] == value
    assert "OPENAI_API_KEY" not in plist["EnvironmentVariables"]
    assert "CODEX_ACCESS_TOKEN" not in plist["EnvironmentVariables"]
    assert plist["KeepAlive"] is False
    assert ["bootstrap", manager._launchd_domain(), str(manager.launchd_plist_path)] in calls

    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(True, frozenset({launchd_pid})),
    )
    stopped = manager.stop()
    assert stopped["stopped"] is True
    assert ["bootout", manager._launchd_target()] in calls
    assert ["terminate", str(launchd_pid)] in calls


def test_darwin_launchd_bootstrap_failure_is_fail_closed(tmp_path: Path):
    data_root, app_root, _identity, port = _release(tmp_path)
    calls: list[list[str]] = []
    manager = _manager(data_root, app_root, port)
    manager._is_darwin = lambda: True  # type: ignore[method-assign]

    def launchctl(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        return subprocess.CompletedProcess(arguments, 5, "", "Input/output error")

    manager._launchctl_runner = launchctl
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager._spawn()
    assert failure.value.code == "launchd_start_failed"
    assert calls == [["bootstrap", manager._launchd_domain(), str(manager.launchd_plist_path)]]
    assert not manager.paths.pid_path.exists()


def test_darwin_launchd_identity_retries_transient_process_visibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_root, app_root, _identity, port = _release(tmp_path)
    manager = _manager(data_root, app_root, port)
    manager.poll_interval = 0.001
    launchd_pid = 42_424
    command = manager._launch_command()
    expected = runtime_manager.ProcessIdentity(
        pid=launchd_pid,
        owner_id=runtime_manager.current_owner_id(),
        start_token="darwin:test:transient",
        executable=str(Path(sys.executable).resolve()),
        argv=tuple(command),
    )
    manager._launchctl_runner = lambda arguments: subprocess.CompletedProcess(
        arguments, 0, "\tpid = 42424\n", ""
    )
    attempts = 0

    def inspect(pid: int):
        nonlocal attempts
        assert pid == launchd_pid
        attempts += 1
        if attempts == 1:
            raise runtime_manager.PlatformRuntimeError(
                "process_unavailable", "macOS process identity cannot be read", pid=pid
            )
        return expected

    monkeypatch.setattr(runtime_manager, "inspect_process", inspect)

    assert manager._launchd_wait_for_identity(command=command) == expected
    assert attempts == 2


def test_darwin_launchd_identity_remains_fail_closed_after_retry_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_root, app_root, _identity, port = _release(tmp_path)
    manager = _manager(data_root, app_root, port)
    manager.startup_timeout = 0.5
    manager.poll_interval = 0.001
    manager._launchctl_runner = lambda arguments: subprocess.CompletedProcess(
        arguments, 0, "\tpid = 42424\n", ""
    )
    monotonic_values = iter((0.0, 0.1, 0.2, 0.6))
    monkeypatch.setattr(runtime_manager.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(runtime_manager.time, "sleep", lambda _seconds: None)
    attempts = 0

    def inspect(pid: int):
        nonlocal attempts
        attempts += 1
        raise runtime_manager.PlatformRuntimeError(
            "process_unavailable", "macOS process identity cannot be read", pid=pid
        )

    monkeypatch.setattr(runtime_manager, "inspect_process", inspect)

    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager._launchd_wait_for_identity(command=manager._launch_command())
    assert failure.value.code == "process_unavailable"
    assert failure.value.details == {
        "pid": 42_424,
        "platform_code": "process_unavailable",
    }
    assert attempts == 2


def test_first_start_health_reuse_and_stop_recovery(tmp_path, monkeypatch):
    data_root, app_root, _identity, port = _release(tmp_path)
    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(False, frozenset()),
    )
    manager = _manager(data_root, app_root, port)
    try:
        first = manager.ensure_ready()
        assert first["ok"] is True
        assert first["reused"] is False
        pid = json.loads(manager.paths.pid_path.read_text(encoding="utf-8"))["pid"]
        metadata = manager._read_metadata()
        assert metadata["schema_version"] == 2
        assert metadata["owner_id"]
        assert metadata["start_token"]
        assert metadata["executable"]
        assert metadata["argv"]
        assert manager._owned_process(pid, metadata) is True

        reused = manager.ensure_ready()
        assert reused["ok"] is True
        assert reused["reused"] is True

        assert manager.stop()["stopped"] is True
        recovered = manager.ensure_ready()
        assert recovered["ok"] is True
        assert recovered["reused"] is False
    finally:
        manager.stop()


def test_concurrent_cold_start_has_one_managed_process(tmp_path):
    data_root, app_root, _identity, port = _release(tmp_path)
    child = textwrap.dedent(
        """
        import importlib.util
        import json
        import sys
        from pathlib import Path

        manager_path = Path(sys.argv[1])
        spec = importlib.util.spec_from_file_location("runtime_manager", manager_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        manager = module.RuntimeManager(
            app_root=Path(sys.argv[2]), data_root=Path(sys.argv[3]), startup_timeout=8
        )
        manager.port = int(sys.argv[4])
        manager.base_url = f"http://127.0.0.1:{manager.port}"
        print(json.dumps(manager.ensure_ready()), flush=True)
        """
    )
    args = [sys.executable, "-c", child, str(MANAGER_PATH), str(app_root), str(data_root), str(port)]
    first = subprocess.Popen(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = subprocess.Popen(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first_out, first_err = first.communicate(timeout=20)
    second_out, second_err = second.communicate(timeout=20)
    assert first.returncode == 0, first_err
    assert second.returncode == 0, second_err
    assert json.loads(first_out)["ok"] is True
    assert json.loads(second_out)["ok"] is True
    manager = _manager(data_root, app_root, port)
    try:
        metadata = json.loads(manager.paths.pid_path.read_text(encoding="utf-8"))
        assert isinstance(metadata["pid"], int)
        assert manager._owned_process(metadata["pid"], metadata) is True
    finally:
        manager.stop()


def test_unknown_listener_fails_without_signalling(tmp_path, monkeypatch):
    data_root, app_root, _identity, _port = _release(tmp_path)
    manager = _manager(data_root, app_root, _free_port())
    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(True, frozenset()),
    )
    monkeypatch.setattr(
        manager,
        "_health_request",
        lambda: (_ for _ in ()).throw(runtime_manager.RuntimeManagerError("down", "down")),
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"
    assert not manager.paths.pid_path.exists()


def test_windows_repeat_doctor_accepts_verified_one_hop_child_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    parent_calls = _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )

    result = manager.ensure_ready()

    assert result["ok"] is True
    assert result["reused"] is True
    assert parent_calls == [listener_identity.pid]


def test_windows_repeat_doctor_preserves_direct_manager_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    parent_calls = _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
        listener_pids=frozenset({manager_identity.pid}),
    )

    result = manager.ensure_ready()

    assert result["ok"] is True
    assert result["reused"] is True
    assert parent_calls == []


def test_windows_stop_signals_manager_and_proves_child_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    parent_calls = _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    signalled: list[int] = []

    def terminate(identity, **_kwargs):
        signalled.append(identity.pid)

    monkeypatch.setattr(runtime_manager, "terminate_process", terminate)
    identities = {
        manager_identity.pid: manager_identity,
        listener_identity.pid: listener_identity,
    }

    def inspect(pid: int):
        if pid == listener_identity.pid and signalled:
            return None
        return identities.get(pid)

    monkeypatch.setattr(runtime_manager, "inspect_process", inspect)

    result = manager.stop()

    assert result == {"ok": True, "stopped": True, "pid": manager_identity.pid}
    assert signalled == [manager_identity.pid]
    assert parent_calls == [listener_identity.pid]
    assert not manager.paths.pid_path.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda manager, manager_id, listener_id: (4087, None), id="wrong-parent"),
        pytest.param(
            lambda manager, manager_id, listener_id: (
                4088,
                dataclasses.replace(listener_id, owner_id="sid:S-1-5-21-other"),
            ),
            id="wrong-sid",
        ),
        pytest.param(
            lambda manager, manager_id, listener_id: (
                4088,
                dataclasses.replace(
                    listener_id,
                    argv=listener_id.argv[:-1] + ("3131",),
                ),
            ),
            id="wrong-argv",
        ),
        pytest.param(
            lambda manager, manager_id, listener_id: (
                4088,
                dataclasses.replace(
                    listener_id,
                    argv=(
                        listener_id.argv[0],
                        str(manager.app_root / "other-launcher.py"),
                        *listener_id.argv[2:],
                    ),
                ),
            ),
            id="wrong-release-launcher",
        ),
        pytest.param(
            lambda manager, manager_id, listener_id: (
                4088,
                dataclasses.replace(
                    listener_id,
                    executable=listener_id.executable + "-other",
                ),
            ),
            id="wrong-executable",
        ),
    ],
)
def test_windows_child_listener_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    parent_pid, mutated_listener = mutation(
        manager, manager_identity, listener_identity
    )
    if mutated_listener is not None:
        listener_identity = mutated_listener
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
        parent_pid=parent_pid,
    )

    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()

    assert failure.value.code == "unknown_listener"


def test_windows_manager_pid_reuse_and_parent_lookup_failure_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    parent_calls = _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    reused = dict(metadata)
    reused["start_token"] = "win32:reused-manager"
    runtime_manager._write_private_json(manager.paths.pid_path, reused)

    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()

    assert failure.value.code == "unknown_listener"
    assert parent_calls == []

    runtime_manager._write_private_json(manager.paths.pid_path, metadata)
    monkeypatch.setattr(
        runtime_manager,
        "parent_process_id",
        lambda _pid: (_ for _ in ()).throw(
            runtime_manager.PlatformRuntimeError(
                "parent_lookup_unavailable", "parent lookup unavailable"
            )
        ),
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as unavailable:
        manager.ensure_ready()

    assert unavailable.value.code == "unknown_listener"


def test_windows_manager_changes_after_parent_lookup_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    monkeypatch.setattr(runtime_manager, "current_owner_id", lambda: manager_identity.owner_id)
    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(
            True, frozenset({listener_identity.pid})
        ),
    )
    monkeypatch.setattr(manager, "_health_request", lambda: expected)
    monkeypatch.setattr(
        runtime_manager,
        "parent_process_id",
        lambda _pid: manager_identity.pid,
    )
    changed = dataclasses.replace(
        manager_identity,
        start_token="win32:manager-reused",
    )
    manager_inspections = 0

    def inspect(pid: int):
        nonlocal manager_inspections
        if pid == manager_identity.pid:
            manager_inspections += 1
            return manager_identity if manager_inspections == 1 else changed
        if pid == listener_identity.pid:
            return listener_identity
        return None

    monkeypatch.setattr(runtime_manager, "inspect_process", inspect)

    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()

    assert failure.value.code == "unknown_listener"
    assert manager_inspections >= 2


def test_windows_multiple_listeners_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
        listener_pids=frozenset({listener_identity.pid, 5865}),
    )

    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()

    assert failure.value.code == "unknown_listener"


@pytest.mark.parametrize(
    "config_case",
    [
        pytest.param("missing", id="missing"),
        pytest.param("duplicate-home", id="duplicate-home-case"),
        pytest.param("duplicate-executable", id="duplicate-executable-case"),
        pytest.param("malformed", id="malformed"),
        pytest.param("bom", id="bom"),
        pytest.param("non-utf8", id="non-utf8"),
        pytest.param("reparse", id="reparse"),
        pytest.param("out-of-root", id="out-of-root"),
        pytest.param("inconsistent", id="inconsistent"),
        pytest.param("wrong-base", id="wrong-base"),
        pytest.param("relative-base", id="relative-base"),
        pytest.param("empty-base", id="empty-base"),
        pytest.param("nonexistent-base", id="nonexistent-base"),
    ],
)
def test_windows_redirector_config_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_case: str
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    config_path = Path(manager_identity.executable).parent.parent / "pyvenv.cfg"
    original = config_path.read_text(encoding="utf-8")
    if config_case == "missing":
        config_path.unlink()
    elif config_case == "duplicate-home":
        config_path.write_text(
            original
            + "HOME = "
            + original.split("home = ", 1)[1].splitlines()[0]
            + "\n",
            encoding="utf-8",
        )
    elif config_case == "duplicate-executable":
        config_path.write_text(
            original
            + "EXECUTABLE = "
            + original.split("executable = ", 1)[1].splitlines()[0]
            + "\n",
            encoding="utf-8",
        )
    elif config_case == "malformed":
        config_path.write_text("home is not a setting\n", encoding="utf-8")
    elif config_case == "bom":
        config_path.write_bytes(b"\xef\xbb\xbf" + original.encode("utf-8"))
    elif config_case == "non-utf8":
        config_path.write_bytes(b"home = \xff\n")
    elif config_case == "reparse":
        target = config_path.with_name("pyvenv-target.cfg")
        target.write_text(original, encoding="utf-8")
        config_path.unlink()
        try:
            config_path.symlink_to(target.name)
        except OSError:
            pytest.skip("symlink creation is unavailable in this test environment")
    elif config_case == "out-of-root":
        config_path.unlink()
        config_path.parent.joinpath("Scripts", "pyvenv.cfg").write_text(
            original, encoding="utf-8"
        )
    elif config_case == "inconsistent":
        config_path.write_text(
            original.replace(
                original.split("home = ", 1)[1].splitlines()[0],
                str(config_path.parent / "other-home"),
                1,
            ),
            encoding="utf-8",
        )
        (config_path.parent / "other-home").mkdir()
    elif config_case == "wrong-base":
        wrong_home = config_path.parent.parent / "other-primary"
        wrong_home.mkdir()
        wrong_executable = wrong_home / "python.exe"
        wrong_executable.write_text("other primary", encoding="utf-8")
        config_path.write_text(
            original.replace(
                original.split("executable = ", 1)[1].splitlines()[0],
                str(wrong_executable),
                1,
            ),
            encoding="utf-8",
        )
    elif config_case == "relative-base":
        config_path.write_text(
            original.replace(
                original.split("executable = ", 1)[1].splitlines()[0],
                "python.exe",
                1,
            ),
            encoding="utf-8",
        )
    elif config_case == "empty-base":
        config_path.write_text(
            original.replace(
                original.split("executable = ", 1)[1].splitlines()[0],
                "",
                1,
            ),
            encoding="utf-8",
        )
    elif config_case == "nonexistent-base":
        config_path.write_text(
            original.replace(
                original.split("executable = ", 1)[1].splitlines()[0],
                str(config_path.parent.parent / "missing-primary" / "python.exe"),
                1,
            ),
            encoding="utf-8",
        )

    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"


def test_windows_redirector_manager_must_be_current_venv_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    invalid_manager = dataclasses.replace(
        manager_identity,
        executable=str(Path(sys.executable).resolve()),
    )
    invalid_metadata = manager._metadata(
        invalid_manager, manager._launch_env(), list(invalid_manager.argv)
    )
    runtime_manager._write_private_json(manager.paths.pid_path, invalid_metadata)
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        invalid_manager,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"
    assert metadata["executable"] != invalid_metadata["executable"]


def test_windows_redirector_binds_active_install_state_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path, active_state=True)
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    result = manager.ensure_ready()
    assert result["ok"] is True
    assert result["reused"] is True


def test_windows_redirector_rejects_invalid_active_install_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path, active_state=True)
    state_path = manager.paths.data_root / "state" / "windows-install-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["platform"] = "windows"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"


@pytest.mark.parametrize(
    "contract_case",
    [
        pytest.param("release-mismatch", id="release-mismatch"),
        pytest.param("marker-missing", id="marker-missing"),
        pytest.param("marker-mismatch", id="marker-mismatch"),
        pytest.param("install-id-escape", id="install-id-escape"),
        pytest.param("runtime-selection-invalid", id="runtime-selection-invalid"),
    ],
)
def test_windows_redirector_rejects_active_release_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contract_case: str,
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path, active_state=True)
    data_root = manager.paths.data_root
    state_path = data_root / "state" / "windows-install-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    marker_path = data_root / "releases" / "r1" / ".windows-install.json"
    if contract_case == "release-mismatch":
        state["active"]["release_root"] = str((data_root / "releases" / "r2").resolve())
    elif contract_case == "marker-missing":
        marker_path.unlink()
    elif contract_case == "marker-mismatch":
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["entry"]["payload_size"] = 456
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
    elif contract_case == "install-id-escape":
        state["active"]["install_id"] = "r1/../r2"
    elif contract_case == "runtime-selection-invalid":
        state["active"]["runtime_selection"] = {}
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["entry"] = state["active"]
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"


def test_windows_redirector_rejects_stale_active_release_against_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path, active_state=True)
    data_root = manager.paths.data_root
    release_root = data_root / "releases" / "r2"
    venv_root = data_root / "venvs" / "r2"
    (release_root / "app").mkdir(parents=True)
    (release_root / "app" / "runtime_manager.py").write_text(
        "# stale release fixture\n", encoding="utf-8"
    )
    (venv_root / "Scripts").mkdir(parents=True)
    (venv_root / "Scripts" / "python.exe").write_text(
        "stale manager fixture\n", encoding="utf-8"
    )
    (venv_root / "Scripts" / "image-pptgen.exe").write_text(
        "stale cli fixture\n", encoding="utf-8"
    )
    state_path = data_root / "state" / "windows-install-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    stale_entry = dict(state["active"])
    stale_entry.update(
        {
            "install_id": "r2",
            "release_root": str(release_root.resolve()),
            "venv_root": str(venv_root.resolve()),
        }
    )
    (release_root / ".windows-install.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": "windows-amd64",
                "entry": stale_entry,
            }
        ),
        encoding="utf-8",
    )
    state["active"] = stale_entry
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"


def test_windows_redirector_rejects_manager_outside_current_install_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    outside_root = tmp_path / "outside-venv"
    outside_executable = outside_root / "Scripts" / "python.exe"
    outside_executable.parent.mkdir(parents=True)
    outside_executable.write_text("outside manager", encoding="utf-8")
    outside_identity = dataclasses.replace(
        manager_identity,
        executable=str(outside_executable.resolve()),
        argv=(str(outside_executable.resolve()), *manager_identity.argv[1:]),
    )
    outside_metadata = manager._metadata(
        outside_identity, manager._launch_env(), list(outside_identity.argv)
    )
    runtime_manager._write_private_json(manager.paths.pid_path, outside_metadata)
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        outside_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"


def test_windows_redirector_rejects_windows_reparse_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    config_path = Path(manager_identity.executable).parent.parent / "pyvenv.cfg"
    real_lstat = runtime_manager.os.lstat

    def fake_lstat(path):
        if Path(path) == config_path:
            return type(
                "ReparseStat",
                (),
                {"st_mode": stat.S_IFREG, "st_file_attributes": 0x400},
            )()
        return real_lstat(path)

    monkeypatch.setattr(runtime_manager.os, "lstat", fake_lstat)
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"


def test_windows_redirector_rejects_active_state_ancestor_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path, active_state=True)
    data_root = manager.paths.data_root
    venv_root = Path(manager_identity.executable).parent.parent
    escaped_root = tmp_path / "escaped-venv"
    escaped_executable = escaped_root / "Scripts" / "python.exe"
    escaped_executable.parent.mkdir(parents=True)
    escaped_executable.write_text("escaped manager", encoding="utf-8")
    state_path = data_root / "state" / "windows-install-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["active"]["venv_root"] = str(escaped_root.resolve())
    state_path.write_text(json.dumps(state), encoding="utf-8")
    escaped_identity = dataclasses.replace(
        manager_identity,
        executable=str(escaped_executable.resolve()),
        argv=(str(escaped_executable.resolve()), *manager_identity.argv[1:]),
    )
    escaped_metadata = manager._metadata(
        escaped_identity, manager._launch_env(), list(escaped_identity.argv)
    )
    runtime_manager._write_private_json(manager.paths.pid_path, escaped_metadata)
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        escaped_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"
    assert venv_root != escaped_root


def test_windows_redirector_rejects_extra_pre_launcher_argv_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    listener_identity = dataclasses.replace(
        listener_identity,
        argv=(listener_identity.argv[0], "--unexpected", *listener_identity.argv[1:]),
    )
    _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"


def test_windows_redirector_listener_inspection_gap_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        manager,
        manager_identity,
        listener_identity,
        _metadata,
        expected,
    ) = _windows_child_chain_fixture(tmp_path)
    parent_calls = _install_windows_chain_probe(
        monkeypatch,
        manager,
        manager_identity,
        listener_identity,
        expected,
    )
    monkeypatch.setattr(
        runtime_manager,
        "inspect_process",
        lambda pid: (
            (_ for _ in ()).throw(
                runtime_manager.PlatformRuntimeError(
                    "process_inspection_unavailable", "listener inspection unavailable"
                )
            )
            if pid == listener_identity.pid
            else manager_identity
        ),
    )
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "unknown_listener"
    assert parent_calls == []


def test_incomplete_listener_probe_fails_before_spawn(tmp_path, monkeypatch):
    data_root, app_root, _identity, _port = _release(tmp_path)
    manager = _manager(data_root, app_root, _free_port())
    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(
            False, frozenset(), complete=False, source="probe_failed"
        ),
    )
    monkeypatch.setattr(
        manager,
        "_health_request",
        lambda: (_ for _ in ()).throw(
            runtime_manager.RuntimeManagerError("down", "down")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_spawn",
        lambda: (_ for _ in ()).throw(AssertionError("spawn must stay closed")),
    )

    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()

    assert failure.value.code == "listener_unavailable"


def test_start_token_mismatch_refuses_stop_without_signalling(tmp_path):
    data_root, app_root, _identity, port = _release(tmp_path)
    manager = _manager(data_root, app_root, port)
    try:
        manager.ensure_ready()
        original = manager._read_metadata()
        tampered = dict(original)
        tampered["start_token"] = str(original["start_token"]) + "-reused"
        runtime_manager._write_private_json(manager.paths.pid_path, tampered)

        with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
            manager.stop()

        assert failure.value.code == "ownership_unproven"
        assert runtime_manager._proc_alive(original["pid"]) is True
        runtime_manager._write_private_json(manager.paths.pid_path, original)
    finally:
        manager.stop()


def test_managed_release_upgrade_replaces_only_the_owned_listener(tmp_path, monkeypatch):
    data_root, old_app, _identity, port = _release(tmp_path)
    new_app = data_root / "releases" / "r2" / "app"
    new_app.parent.mkdir(parents=True)
    shutil.copytree(old_app, new_app)
    new_identity = json.loads((new_app / "release-identity.json").read_text())
    new_identity.update(
        {
            "build_id": "build-runtime-test-v2",
            "version": "0.1.4",
            "source_commit": "source-runtime-test-v2",
        }
    )
    (new_app / "release-identity.json").write_text(json.dumps(new_identity))
    manager = _manager(data_root, old_app, port)
    original_listener = runtime_manager.find_listener

    def owned_listener(_port):
        metadata = manager._read_metadata()
        if metadata and isinstance(metadata.get("pid"), int) and runtime_manager._proc_alive(metadata["pid"]):
            return runtime_manager.ListenerSnapshot(True, frozenset({metadata["pid"]}))
        return runtime_manager.ListenerSnapshot(False, frozenset())

    monkeypatch.setattr(runtime_manager, "find_listener", owned_listener)
    try:
        manager.ensure_ready()
        old_pid = manager._read_metadata()["pid"]
        manager.app_root = new_app.resolve()
        upgraded = manager.ensure_ready()
        new_pid = manager._read_metadata()["pid"]
        assert upgraded["version"] == "0.1.4"
        assert new_pid != old_pid
        assert runtime_manager._proc_alive(new_pid) is True
        assert runtime_manager._proc_alive(old_pid) is False
    finally:
        manager.stop()


def test_release_identity_mismatch_refuses_upgrade_and_leaves_old_process(tmp_path, monkeypatch):
    data_root, old_app, _identity, port = _release(tmp_path)
    new_app = data_root / "releases" / "r2" / "app"
    new_app.parent.mkdir(parents=True)
    shutil.copytree(old_app, new_app)
    new_identity = json.loads((new_app / "release-identity.json").read_text())
    new_identity["build_id"] = "build-runtime-test-v2"
    (new_app / "release-identity.json").write_text(json.dumps(new_identity))
    manager = _manager(data_root, old_app, port)

    def owned_listener(_port):
        metadata = manager._read_metadata()
        if metadata and isinstance(metadata.get("pid"), int) and runtime_manager._proc_alive(metadata["pid"]):
            return runtime_manager.ListenerSnapshot(True, frozenset({metadata["pid"]}))
        return runtime_manager.ListenerSnapshot(False, frozenset())

    monkeypatch.setattr(runtime_manager, "find_listener", owned_listener)
    try:
        manager.ensure_ready()
        old_pid = manager._read_metadata()["pid"]
        original = manager._read_metadata()
        tampered = dict(original)
        tampered["build_id"] = "tampered-metadata"
        runtime_manager._write_private_json(manager.paths.pid_path, tampered)
        manager.app_root = new_app.resolve()
        with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
            manager.ensure_ready()
        assert failure.value.code == "unknown_listener"
        assert runtime_manager._proc_alive(old_pid) is True
        runtime_manager._write_private_json(manager.paths.pid_path, original)
    finally:
        manager.stop()


def test_failed_upgrade_restores_old_absolute_command_and_health(
    tmp_path, monkeypatch
):
    data_root, old_app, _identity, port = _release(tmp_path)
    new_app = data_root / "releases" / "r2" / "app"
    new_app.parent.mkdir(parents=True)
    shutil.copytree(old_app, new_app)
    new_identity = json.loads((new_app / "release-identity.json").read_text())
    new_identity.update(
        {
            "build_id": "build-runtime-test-v2",
            "version": "0.1.4",
            "source_commit": "source-runtime-test-v2",
        }
    )
    (new_app / "release-identity.json").write_text(json.dumps(new_identity))
    (new_app / "image-launcher.py").write_text(
        "raise SystemExit('intentional upgrade failure')\n", encoding="utf-8"
    )
    manager = _manager(data_root, old_app, port)

    def owned_listener(_port):
        metadata = manager._read_metadata()
        if (
            metadata
            and isinstance(metadata.get("pid"), int)
            and runtime_manager._proc_alive(metadata["pid"])
        ):
            return runtime_manager.ListenerSnapshot(
                True, frozenset({metadata["pid"]})
            )
        return runtime_manager.ListenerSnapshot(False, frozenset())

    monkeypatch.setattr(runtime_manager, "find_listener", owned_listener)
    try:
        manager.ensure_ready()
        old_metadata = manager._read_metadata()
        old_argv = list(old_metadata["argv"])
        assert Path(old_argv[0]).is_absolute()
        manager.app_root = new_app.resolve()

        with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
            manager.ensure_ready()

        assert failure.value.code == "start_failed"
        restored = manager._read_metadata()
        assert restored["build_id"] == "build-runtime-test"
        assert restored["argv"] == old_argv
        assert manager._owned_process(restored["pid"], restored) is True
        observed = manager._health_request()
        assert observed["build_id"] == "build-runtime-test"
    finally:
        manager.stop()


def test_metadata_write_failure_does_not_leave_spawned_child(
    tmp_path, monkeypatch
):
    data_root, app_root, _identity, port = _release(tmp_path)
    manager = _manager(data_root, app_root, port)
    manager._expected_identity()
    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(False, frozenset()),
    )
    real_write = runtime_manager._write_private_json

    def failing_write(path, payload):
        if path == manager.paths.pid_path:
            raise OSError("injected metadata write failure")
        return real_write(path, payload)

    spawned = []
    real_popen = runtime_manager.subprocess.Popen

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(runtime_manager, "_write_private_json", failing_write)
    monkeypatch.setattr(runtime_manager.subprocess, "Popen", recording_popen)

    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()

    assert failure.value.code == "start_failed"
    assert spawned and all(process.poll() is not None for process in spawned)
    assert not manager.paths.pid_path.exists()


def _wrapper_fixture(tmp_path: Path, *, manager_exit: int = 0):
    data_home = tmp_path / "xdg-data"
    config_home = tmp_path / "xdg-config"
    current = data_home / "image-pptgen" / "current"
    venv_bin = data_home / "image-pptgen" / "current-venv" / "bin"
    (current / "app").mkdir(parents=True)
    venv_bin.mkdir(parents=True)
    (current / "app" / "runtime_manager.py").write_text("# fixture\n", encoding="utf-8")
    calls = tmp_path / "calls.log"
    python = venv_bin / "python"
    python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf 'manager %s\\n' \"$*\" >> {calls!s}\n"
        f"exit {manager_exit}\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    cli = venv_bin / "image-pptgen"
    cli.write_text(
        "#!/usr/bin/env bash\n"
        f"printf 'cli %s\\n' \"$*\" >> {calls!s}\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return data_home, config_home, calls


def _run_wrapper(tmp_path: Path, args: list[str], *, manager_exit: int = 0):
    data_home, config_home, calls = _wrapper_fixture(tmp_path, manager_exit=manager_exit)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_DATA_HOME": str(data_home),
            "XDG_CONFIG_HOME": str(config_home),
        }
    )
    result = subprocess.run(
        ["bash", str(ROOT / "packaging" / "image" / "image-pptgen-wrapper.sh"), *args],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["--base-url", "http://remote.example", "doctor", "--json"],
        ["--base-url=http://remote.example", "doctor", "--json"],
    ],
)
def test_wrapper_help_and_non_default_url_bypass_local_manager(tmp_path, args):
    result, calls = _run_wrapper(tmp_path, args)
    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "cli " + " ".join(args)
    ]


def test_wrapper_preserves_argv_and_sends_one_preflight_before_mutation(tmp_path):
    command = [
        "material",
        "submit",
        "--title",
        "一次请求",
        "--text-file",
        "/tmp/source.md",
        "--json",
    ]
    result, calls = _run_wrapper(tmp_path, command)
    assert result.returncode == 0, result.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("manager ")
    assert lines[0].endswith("runtime_manager.py ensure-ready --json")
    assert lines[1] == "cli " + " ".join(command)


def test_wrapper_returns_structured_manager_failure_without_running_cli(tmp_path):
    result, calls = _run_wrapper(tmp_path, ["generate", "--deck-id", "1", "--json"], manager_exit=7)
    assert result.returncode == 7
    assert calls.read_text(encoding="utf-8").splitlines()[0].endswith(
        "runtime_manager.py ensure-ready --json"
    )
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 1


def test_installer_calls_shared_readiness_and_keeps_server_diagnostic_optional():
    installer = (ROOT / "packaging" / "image" / "install.sh").read_text(encoding="utf-8")
    assert 'runtime_manager.py" \\' in installer
    assert "ensure-ready --json" in installer
    output_region = installer.split("printf '\\nImage PPTGen", 1)[1]
    primary_success = output_region.split("printf '\\nDiagnostics and startup:", 1)[0]
    assert "image-pptgen-server" not in primary_success
    assert "$generate-image-presentation" in primary_success


def test_start_failure_has_structured_error_and_clears_pid_metadata(tmp_path, monkeypatch):
    data_root, app_root, _identity, port = _release(tmp_path)
    (app_root / "image-launcher.py").write_text(
        "raise SystemExit('intentional startup failure')\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        runtime_manager,
        "find_listener",
        lambda _port: runtime_manager.ListenerSnapshot(False, frozenset()),
    )
    manager = _manager(data_root, app_root, port)
    with pytest.raises(runtime_manager.RuntimeManagerError) as failure:
        manager.ensure_ready()
    assert failure.value.code == "start_failed"
    payload = failure.value.payload()
    assert payload["error"] == "platform_unavailable"
    assert "start_failed" in payload["message"]
    assert not manager.paths.pid_path.exists()
