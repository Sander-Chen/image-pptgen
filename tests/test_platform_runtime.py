"""Cross-platform runtime primitive contract tests."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading

import pytest

from backend.services import platform_runtime


def test_platform_runtime_error_exposes_stable_code_and_details() -> None:
    error = platform_runtime.PlatformRuntimeError(
        "lock_timeout", "runtime lock timed out", path="private.lock"
    )

    assert error.code == "lock_timeout"
    assert error.details == {"path": "private.lock"}
    assert str(error) == "runtime lock timed out"


def test_posix_lock_supports_unbounded_wait_and_releases_after_holder(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "runtime.lock"
    acquired = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with platform_runtime.exclusive_file_lock(
            lock_path,
            timeout_seconds=None,
            poll_seconds=0.01,
            platform_name="posix",
        ):
            acquired.set()
            assert release.wait(timeout=2)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert acquired.wait(timeout=2)
    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        with platform_runtime.exclusive_file_lock(
            lock_path,
            timeout_seconds=0.03,
            poll_seconds=0.005,
            platform_name="posix",
        ):
            raise AssertionError("contender unexpectedly acquired the held lock")
    assert captured.value.code == "lock_timeout"
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    with platform_runtime.exclusive_file_lock(
        lock_path,
        timeout_seconds=0.2,
        poll_seconds=0.005,
        platform_name="posix",
    ):
        pass


def test_lock_rejects_symlink_final_component(tmp_path: Path) -> None:
    actual = tmp_path / "actual.lock"
    actual.write_bytes(b"")
    alias = tmp_path / "alias.lock"
    alias.symlink_to(actual)

    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        with platform_runtime.exclusive_file_lock(alias, platform_name="posix"):
            raise AssertionError("symlink lock unexpectedly entered")

    assert captured.value.code == "unsafe_path"


def test_private_json_rejects_symlink_without_modifying_target(tmp_path: Path) -> None:
    actual = tmp_path / "actual.json"
    actual.write_text('{"old": true}\n', encoding="utf-8")
    alias = tmp_path / "alias.json"
    alias.symlink_to(actual)

    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        platform_runtime.write_private_json(alias, {"new": True})

    assert captured.value.code == "unsafe_path"
    assert json.loads(actual.read_text(encoding="utf-8")) == {"old": True}


def test_private_json_replace_failure_preserves_old_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_text('{"old": true}\n', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(platform_runtime.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        platform_runtime.write_private_json(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert not list(tmp_path.glob(".*.tmp"))


def test_linux_available_memory_is_fresh_strict_and_bounded(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       8388608 kB\nMemAvailable:   4194304 kB\n",
        encoding="utf-8",
    )
    assert (
        platform_runtime.read_available_memory_mib(
            platform_name="linux", linux_meminfo_path=meminfo
        )
        == 4096
    )

    meminfo.write_text(
        "MemTotal:       1024 kB\nMemAvailable:   2048 kB\n", encoding="utf-8"
    )
    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        platform_runtime.read_available_memory_mib(
            platform_name="linux", linux_meminfo_path=meminfo
        )
    assert captured.value.code == "memory_unavailable"


def test_macos_vm_stat_parser_uses_free_and_inactive_pages() -> None:
    output = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               1000.
Pages active:                             900.
Pages inactive:                           500.
Pages speculative:                         20.
"""

    assert platform_runtime._parse_macos_vm_stat_mib(output) == 23


def test_windows_memory_values_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        platform_runtime,
        "_read_windows_memory_bytes",
        lambda: (8 * 1024**3, 3 * 1024**3),
    )
    assert platform_runtime.read_available_memory_mib(platform_name="win32") == 3072

    monkeypatch.setattr(
        platform_runtime,
        "_read_windows_memory_bytes",
        lambda: (1024, 2048),
    )
    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        platform_runtime.read_available_memory_mib(platform_name="win32")
    assert captured.value.code == "memory_unavailable"


def test_linux_process_identity_is_fresh_complete_and_current_user() -> None:
    identity = platform_runtime.inspect_process(os.getpid(), platform_name="linux")

    assert identity is not None
    assert identity.pid == os.getpid()
    assert identity.owner_id == platform_runtime.current_owner_id(
        platform_name="linux"
    )
    assert identity.start_token.startswith("linux:")
    assert Path(identity.executable).samefile(sys.executable)
    assert identity.argv
    assert platform_runtime.inspect_process(999_999_999, platform_name="linux") is None


def test_windows_parent_lookup_uses_toolhelp_one_hop_and_closes_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    closed: list[int] = []

    seen_entry: list[tuple[int, int, int, int]] = []

    def first(_snapshot, pointer):
        entry = pointer._obj
        entry.th32ProcessID = 5864
        entry.th32ParentProcessID = 4088
        seen_entry.append(
            (
                entry.dwSize,
                ctypes.sizeof(entry),
                entry.th32ProcessID,
                entry.th32ParentProcessID,
            )
        )
        return True

    class FakeKernel:
        CreateToolhelp32Snapshot = FakeFunction(lambda *_args: 123)
        Process32FirstW = FakeFunction(first)
        Process32NextW = FakeFunction(lambda *_args: False)
        CloseHandle = FakeFunction(lambda handle: closed.append(handle) or True)

    kernel = FakeKernel()
    monkeypatch.setattr(
        platform_runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: kernel,
        raising=False,
    )

    assert platform_runtime.parent_process_id(5864, platform_name="win32") == 4088
    assert closed == [123]
    assert len(seen_entry) == 1
    dw_size, actual_size, process_id, parent_id = seen_entry[0]
    assert dw_size == actual_size
    assert (process_id, parent_id) == (5864, 4088)
    assert kernel.CreateToolhelp32Snapshot.argtypes
    assert kernel.Process32FirstW.argtypes


def test_windows_parent_lookup_invalid_handle_fails_without_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    closed: list[object] = []
    invalid_handle = ctypes.c_void_p(-1).value

    class FakeKernel:
        CreateToolhelp32Snapshot = FakeFunction(lambda *_args: invalid_handle)
        Process32FirstW = FakeFunction(lambda *_args: True)
        Process32NextW = FakeFunction(lambda *_args: True)
        CloseHandle = FakeFunction(lambda handle: closed.append(handle) or True)

    monkeypatch.setattr(
        platform_runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: FakeKernel(),
        raising=False,
    )
    monkeypatch.setattr(
        platform_runtime.ctypes,
        "get_last_error",
        lambda: 6,
        raising=False,
    )

    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        platform_runtime.parent_process_id(5864, platform_name="win32")

    assert captured.value.code == "process_unavailable"
    assert closed == []


@pytest.mark.parametrize(
    ("first_result", "next_result", "last_error"),
    [
        pytest.param(False, True, 5, id="first-unexpected-error"),
        pytest.param(True, False, 5, id="next-unexpected-error"),
    ],
)
def test_windows_parent_lookup_unexpected_toolhelp_error_closes_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    first_result: bool,
    next_result: bool,
    last_error: int,
) -> None:
    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    closed: list[object] = []

    def first(_snapshot, pointer):
        entry = pointer._obj
        entry.th32ProcessID = 5863
        entry.th32ParentProcessID = 4088
        return first_result

    class FakeKernel:
        CreateToolhelp32Snapshot = FakeFunction(lambda *_args: 123)
        Process32FirstW = FakeFunction(first)
        Process32NextW = FakeFunction(lambda *_args: next_result)
        CloseHandle = FakeFunction(lambda handle: closed.append(handle) or True)

    monkeypatch.setattr(
        platform_runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: FakeKernel(),
        raising=False,
    )
    monkeypatch.setattr(
        platform_runtime.ctypes,
        "get_last_error",
        lambda: last_error,
        raising=False,
    )

    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        platform_runtime.parent_process_id(5864, platform_name="win32")

    assert captured.value.code == "process_unavailable"
    assert closed == [123]


def test_windows_parent_lookup_no_more_files_returns_none_and_closes_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    closed: list[object] = []

    class FakeKernel:
        CreateToolhelp32Snapshot = FakeFunction(lambda *_args: 123)
        Process32FirstW = FakeFunction(lambda *_args: False)
        Process32NextW = FakeFunction(lambda *_args: False)
        CloseHandle = FakeFunction(lambda handle: closed.append(handle) or True)

    monkeypatch.setattr(
        platform_runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: FakeKernel(),
        raising=False,
    )
    monkeypatch.setattr(
        platform_runtime.ctypes,
        "get_last_error",
        lambda: 18,
        raising=False,
    )

    assert platform_runtime.parent_process_id(5864, platform_name="win32") is None
    assert closed == [123]


def test_linux_listener_resolves_the_complete_owner_set() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])

        snapshot = platform_runtime.find_tcp_listener(
            "127.0.0.1", port, platform_name="linux"
        )

    assert snapshot.present is True
    assert snapshot.complete is True
    assert os.getpid() in snapshot.pids
    assert snapshot.source == "linux_procfs"


def test_macos_lsof_clean_absence_is_complete_without_bind_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 1, "", "")

    def fail_bind(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("clean lsof absence must not use the bind probe")

    monkeypatch.setattr(platform_runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(platform_runtime, "_bind_listener_probe", fail_bind)

    snapshot = platform_runtime._macos_tcp_listener("127.0.0.1", 3130)

    assert snapshot == platform_runtime.ListenerSnapshot(
        present=False,
        pids=frozenset(),
        complete=True,
        source="macos_lsof_absent",
    )
    assert len(calls) == 1


def test_macos_lsof_stderr_keeps_bind_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = platform_runtime.ListenerSnapshot(
        present=True,
        pids=frozenset({99}),
        complete=False,
        source="sentinel",
    )
    bind_calls: list[tuple[str, int, str]] = []

    monkeypatch.setattr(
        platform_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            "/usr/sbin/lsof", 1, "", "lsof: warning"
        ),
    )

    def fake_bind(host: str, port: int, *, source: str) -> platform_runtime.ListenerSnapshot:
        bind_calls.append((host, port, source))
        return sentinel

    monkeypatch.setattr(platform_runtime, "_bind_listener_probe", fake_bind)

    snapshot = platform_runtime._macos_tcp_listener("127.0.0.1", 3130)

    assert snapshot is sentinel
    assert bind_calls == [("127.0.0.1", 3130, "macos_lsof_incomplete")]


def test_macos_lsof_failure_keeps_bind_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = platform_runtime.ListenerSnapshot(
        present=False,
        pids=frozenset(),
        complete=False,
        source="sentinel",
    )
    bind_calls: list[tuple[str, int, str]] = []

    def fail_run(*_args: object, **_kwargs: object) -> None:
        raise OSError("lsof unavailable")

    monkeypatch.setattr(platform_runtime.subprocess, "run", fail_run)

    def fake_bind(host: str, port: int, *, source: str) -> platform_runtime.ListenerSnapshot:
        bind_calls.append((host, port, source))
        return sentinel

    monkeypatch.setattr(platform_runtime, "_bind_listener_probe", fake_bind)

    snapshot = platform_runtime._macos_tcp_listener("127.0.0.1", 3130)

    assert snapshot is sentinel
    assert bind_calls == [("127.0.0.1", 3130, "macos_lsof_failed")]


def test_macos_lsof_complete_listener_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        platform_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            "/usr/sbin/lsof", 0, "p123\nnpython\n", ""
        ),
    )

    snapshot = platform_runtime._macos_tcp_listener("127.0.0.1", 3130)

    assert snapshot == platform_runtime.ListenerSnapshot(
        present=True,
        pids=frozenset({123}),
        complete=True,
        source="macos_lsof",
    )


def test_process_termination_refuses_identity_drift_without_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = platform_runtime.ProcessIdentity(
        pid=4321,
        owner_id="uid:1000",
        start_token="linux:boot:1",
        executable="/runtime/python",
        argv=("/runtime/python", "launcher.py"),
    )
    changed = platform_runtime.ProcessIdentity(
        pid=4321,
        owner_id="uid:1000",
        start_token="linux:boot:2",
        executable="/runtime/python",
        argv=("/runtime/python", "launcher.py"),
    )
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        platform_runtime,
        "inspect_process",
        lambda _pid, **_kwargs: changed,
    )
    monkeypatch.setattr(
        platform_runtime.os,
        "kill",
        lambda pid, value: signals.append((pid, value)),
    )

    with pytest.raises(platform_runtime.PlatformRuntimeError) as captured:
        platform_runtime.terminate_process(
            expected,
            graceful_timeout=0.01,
            poll_interval=0.001,
            platform_name="linux",
        )

    assert captured.value.code == "process_identity_changed"
    assert signals == []


def test_process_termination_never_escalates_after_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = platform_runtime.ProcessIdentity(
        pid=4321,
        owner_id="uid:1000",
        start_token="linux:boot:1",
        executable="/runtime/python",
        argv=("/runtime/python", "launcher.py"),
    )
    changed = platform_runtime.ProcessIdentity(
        pid=4321,
        owner_id="uid:1000",
        start_token="linux:boot:2",
        executable="/runtime/python",
        argv=("/runtime/python", "launcher.py"),
    )
    observations = iter((expected, expected, changed))
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        platform_runtime,
        "inspect_process",
        lambda _pid, **_kwargs: next(observations),
    )
    monkeypatch.setattr(
        platform_runtime.os,
        "kill",
        lambda pid, value: signals.append((pid, value)),
    )

    platform_runtime.terminate_process(
        expected,
        graceful_timeout=0.0,
        poll_interval=0.001,
        platform_name="linux",
    )

    assert signals == [(expected.pid, signal.SIGTERM)]


def test_spawn_kwargs_and_venv_python_are_platform_specific(tmp_path: Path) -> None:
    posix = platform_runtime.background_popen_kwargs(platform_name="linux")
    windows = platform_runtime.background_popen_kwargs(platform_name="win32")

    assert posix == {"start_new_session": True, "close_fds": True}
    assert windows["close_fds"] is True
    assert windows["creationflags"] & 0x08000000
    assert windows["creationflags"] & 0x00000200
    assert platform_runtime.venv_python(
        tmp_path, platform_name="linux"
    ) == tmp_path / "bin" / "python"
    assert platform_runtime.venv_python(
        tmp_path, platform_name="win32"
    ) == tmp_path / "Scripts" / "python.exe"
