"""Small fail-closed cross-platform runtime primitives.

Only standard-library facilities live here. Product lifecycle code uses these
helpers instead of importing platform-specific modules at module import time.
"""

from __future__ import annotations

import contextlib
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping
import uuid

from backend.services.private_file_permissions import restrict_owner_only_fd


class PlatformRuntimeError(RuntimeError):
    """A required platform primitive could not be proved safely."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class ProcessIdentity:
    """OS-proved identity for one process incarnation."""

    pid: int
    owner_id: str
    start_token: str
    executable: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ListenerSnapshot:
    """Current TCP listener ownership evidence."""

    present: bool
    pids: frozenset[int]
    complete: bool = True
    source: str = "unknown"


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def _path_without_resolving_final(path: Path) -> Path:
    expanded = path.expanduser()
    parent = expanded.parent.resolve()
    _private_directory(parent)
    return parent / expanded.name


def _reject_unsafe_final_component(path: Path) -> None:
    try:
        file_stat = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PlatformRuntimeError(
            "unsafe_path", "runtime path cannot be inspected safely", path=str(path)
        ) from exc

    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    if stat.S_ISLNK(file_stat.st_mode) or file_attributes & reparse_flag:
        raise PlatformRuntimeError(
            "unsafe_path", "runtime path cannot be a symlink or reparse point", path=str(path)
        )
    if not stat.S_ISREG(file_stat.st_mode):
        raise PlatformRuntimeError(
            "unsafe_path", "runtime path must be a regular file", path=str(path)
        )


def _prepare_lock_file(handle) -> None:
    """Windows byte-range locks require one durable byte to exist."""

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _open_posix_lock_file(path: Path):
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise PlatformRuntimeError(
                "unsafe_path", "runtime lock cannot follow a symlink", path=str(path)
            ) from exc
        raise PlatformRuntimeError(
            "lock_unavailable", "runtime lock file cannot be opened", path=str(path)
        ) from exc

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise PlatformRuntimeError(
                "unsafe_path", "runtime lock must be a regular file", path=str(path)
            )
        restrict_owner_only_fd(descriptor)
        return os.fdopen(descriptor, "a+b")
    except BaseException:
        os.close(descriptor)
        raise


def _open_windows_lock_file(path: Path):
    import msvcrt  # noqa: PLC0415 - unavailable on POSIX by design
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFileType.argtypes = (wintypes.HANDLE,)
    kernel32.GetFileType.restype = wintypes.DWORD
    kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = (("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD))

    generic_read = 0x80000000
    generic_write = 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_always = 4
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    file_attribute_reparse_point = 0x00000400
    file_attribute_tag_info = 9
    file_type_disk = 0x0001
    invalid_handle_value = ctypes.c_void_p(-1).value

    raw_handle = kernel32.CreateFileW(
        str(path),
        generic_read | generic_write,
        share_all,
        None,
        open_always,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if raw_handle == invalid_handle_value:
        error_code = ctypes.get_last_error()
        raise PlatformRuntimeError(
            "lock_unavailable",
            "runtime lock file cannot be opened",
            path=str(path),
            winerror=error_code,
        )

    transferred = False
    try:
        info = FileAttributeTagInfo()
        if not kernel32.GetFileInformationByHandleEx(
            raw_handle,
            file_attribute_tag_info,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise PlatformRuntimeError(
                "unsafe_path",
                "runtime lock identity cannot be inspected",
                path=str(path),
                winerror=ctypes.get_last_error(),
            )
        if info.FileAttributes & file_attribute_reparse_point:
            raise PlatformRuntimeError(
                "unsafe_path",
                "runtime lock cannot be a reparse point",
                path=str(path),
            )
        if kernel32.GetFileType(raw_handle) != file_type_disk:
            raise PlatformRuntimeError(
                "unsafe_path", "runtime lock must be a disk file", path=str(path)
            )

        descriptor_flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        descriptor_flags |= getattr(os, "O_NOINHERIT", 0)
        descriptor = msvcrt.open_osfhandle(int(raw_handle), descriptor_flags)
        transferred = True
        return os.fdopen(descriptor, "a+b")
    finally:
        if not transferred:
            kernel32.CloseHandle(raw_handle)


def _windows_lock_api():
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class Overlapped(ctypes.Structure):
        _fields_ = (
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        )

    kernel32.LockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    )
    kernel32.LockFileEx.restype = wintypes.BOOL
    kernel32.UnlockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    )
    kernel32.UnlockFileEx.restype = wintypes.BOOL
    return kernel32, Overlapped


def _acquire_windows_lock(
    handle, *, timeout_seconds: float | None, poll_seconds: float
) -> None:
    import msvcrt  # noqa: PLC0415 - unavailable on POSIX by design

    _prepare_lock_file(handle)
    kernel32, overlapped_type = _windows_lock_api()
    os_handle = msvcrt.get_osfhandle(handle.fileno())
    lockfile_exclusive_lock = 0x00000002
    lockfile_fail_immediately = 0x00000001
    error_lock_violation = 33

    if timeout_seconds is None:
        overlapped = overlapped_type()
        if kernel32.LockFileEx(
            os_handle, lockfile_exclusive_lock, 0, 1, 0, ctypes.byref(overlapped)
        ):
            return
        raise PlatformRuntimeError(
            "lock_unavailable",
            "Windows runtime lock failed",
            winerror=ctypes.get_last_error(),
        )

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        overlapped = overlapped_type()
        if kernel32.LockFileEx(
            os_handle,
            lockfile_exclusive_lock | lockfile_fail_immediately,
            0,
            1,
            0,
            ctypes.byref(overlapped),
        ):
            return
        error_code = ctypes.get_last_error()
        if error_code != error_lock_violation:
            raise PlatformRuntimeError(
                "lock_unavailable", "Windows runtime lock failed", winerror=error_code
            )
        if time.monotonic() >= deadline:
            raise PlatformRuntimeError(
                "lock_timeout", "Windows runtime lock timed out"
            )
        time.sleep(max(0.001, poll_seconds))


def _release_windows_lock(handle) -> None:
    import msvcrt  # noqa: PLC0415 - unavailable on POSIX by design

    kernel32, overlapped_type = _windows_lock_api()
    overlapped = overlapped_type()
    os_handle = msvcrt.get_osfhandle(handle.fileno())
    if not kernel32.UnlockFileEx(os_handle, 0, 1, 0, ctypes.byref(overlapped)):
        raise PlatformRuntimeError(
            "lock_unavailable",
            "Windows runtime lock could not be released",
            winerror=ctypes.get_last_error(),
        )


def _acquire_posix_lock(
    handle, *, timeout_seconds: float | None, poll_seconds: float
) -> None:
    import fcntl  # noqa: PLC0415 - unavailable on Windows by design

    if timeout_seconds is None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            return
        except OSError as exc:
            raise PlatformRuntimeError(
                "lock_unavailable", "POSIX runtime lock failed"
            ) from exc

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as exc:
            if time.monotonic() >= deadline:
                raise PlatformRuntimeError(
                    "lock_timeout", "POSIX runtime lock timed out"
                ) from exc
            time.sleep(max(0.001, poll_seconds))
        except OSError as exc:
            raise PlatformRuntimeError(
                "lock_unavailable", "POSIX runtime lock failed"
            ) from exc


def _release_posix_lock(handle) -> None:
    import fcntl  # noqa: PLC0415 - unavailable on Windows by design

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout_seconds: float | None = 30.0,
    poll_seconds: float = 0.05,
    platform_name: str | None = None,
) -> Iterable[None]:
    """Hold one cooperating file lock or fail without entering the body."""

    lock_path = _path_without_resolving_final(path)
    selected = platform_name or os.name
    if selected == "nt":
        handle = _open_windows_lock_file(lock_path)
    elif selected == "posix":
        handle = _open_posix_lock_file(lock_path)
    else:
        raise PlatformRuntimeError(
            "unsupported_platform",
            f"unsupported runtime lock platform: {selected}",
            platform=selected,
        )

    acquired = False
    try:
        if selected == "nt":
            _acquire_windows_lock(
                handle,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
        else:
            _acquire_posix_lock(
                handle,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
        acquired = True
        yield
    finally:
        try:
            if acquired:
                if selected == "nt":
                    _release_windows_lock(handle)
                else:
                    _release_posix_lock(handle)
        finally:
            handle.close()


def _fsync_parent_directory(path: Path) -> None:
    """Persist a POSIX rename; Windows has no directory-fsync equivalent."""

    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(str(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one private JSON file by same-directory atomic replacement."""

    target = _path_without_resolving_final(path)
    _reject_unsafe_final_component(target)
    staged = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    encoded = (
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor: int | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(staged), flags, 0o600)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise PlatformRuntimeError(
                "unsafe_path", "runtime JSON staging path is not a regular file"
            )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _reject_unsafe_final_component(target)
        os.replace(staged, target)
        _fsync_parent_directory(target.parent)
        with contextlib.suppress(OSError):
            target.chmod(0o600)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            staged.unlink()
        raise


def _memory_error(message: str, **details: Any) -> PlatformRuntimeError:
    return PlatformRuntimeError("memory_unavailable", message, **details)


def _parse_linux_meminfo_mib(text: str) -> int:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw_value = line.partition(":")
        if key not in {"MemTotal", "MemAvailable"}:
            continue
        if not separator or key in values:
            raise _memory_error("Linux memory values are missing or ambiguous")
        parts = raw_value.split()
        if len(parts) != 2 or parts[1] != "kB":
            raise _memory_error("Linux memory values are malformed")
        try:
            value_kib = int(parts[0], 10)
        except ValueError as exc:
            raise _memory_error("Linux memory values are malformed") from exc
        if value_kib < 0:
            raise _memory_error("Linux memory values cannot be negative")
        values[key] = value_kib

    if set(values) != {"MemTotal", "MemAvailable"}:
        raise _memory_error("Linux memory values are missing")
    total_kib = values["MemTotal"]
    available_kib = values["MemAvailable"]
    if total_kib <= 0 or available_kib > total_kib:
        raise _memory_error("Linux available memory is outside physical bounds")
    return available_kib // 1024


_VM_STAT_HEADER = re.compile(
    r"^Mach Virtual Memory Statistics: \(page size of (?P<size>[0-9]+) bytes\)$"
)
_VM_STAT_VALUE = re.compile(r"^(?P<name>Pages free|Pages inactive):\s*(?P<count>[0-9]+)\.$")


def _parse_macos_vm_stat_mib(text: str) -> int:
    lines = text.splitlines()
    if not lines:
        raise _memory_error("macOS vm_stat output is empty")
    header = _VM_STAT_HEADER.fullmatch(lines[0].strip())
    if header is None:
        raise _memory_error("macOS vm_stat page size is malformed")
    page_size = int(header.group("size"), 10)
    if page_size <= 0:
        raise _memory_error("macOS vm_stat page size is invalid")

    pages: dict[str, int] = {}
    for line in lines[1:]:
        match = _VM_STAT_VALUE.fullmatch(line.strip())
        if match is None:
            continue
        name = match.group("name")
        if name in pages:
            raise _memory_error("macOS vm_stat values are ambiguous")
        pages[name] = int(match.group("count"), 10)
    if set(pages) != {"Pages free", "Pages inactive"}:
        raise _memory_error("macOS vm_stat values are missing")
    return (pages["Pages free"] + pages["Pages inactive"]) * page_size // 1024**2


def _read_macos_available_memory_mib() -> int:
    try:
        completed = subprocess.run(
            ["/usr/bin/vm_stat"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
            env={"LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _memory_error("macOS vm_stat could not be executed") from exc
    if completed.returncode != 0 or completed.stderr.strip():
        raise _memory_error(
            "macOS vm_stat failed", returncode=completed.returncode
        )
    return _parse_macos_vm_stat_mib(completed.stdout)


def _read_windows_memory_bytes() -> tuple[int, int]:
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = (
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    state = MemoryStatusEx()
    state.dwLength = ctypes.sizeof(state)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(MemoryStatusEx),)
    kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise _memory_error(
            "Windows physical memory could not be read",
            winerror=ctypes.get_last_error(),
        )
    return int(state.ullTotalPhys), int(state.ullAvailPhys)


def read_available_memory_mib(
    *,
    platform_name: str | None = None,
    linux_meminfo_path: Path | None = None,
) -> int:
    """Read fresh available physical memory or fail closed."""

    selected = platform_name or sys.platform
    if selected.startswith("linux"):
        source = linux_meminfo_path or Path("/proc/meminfo")
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise _memory_error("Linux memory information cannot be read") from exc
        return _parse_linux_meminfo_mib(text)
    if selected == "darwin":
        return _read_macos_available_memory_mib()
    if selected in {"win32", "cygwin", "nt"}:
        total_bytes, available_bytes = _read_windows_memory_bytes()
        if total_bytes <= 0 or available_bytes < 0 or available_bytes > total_bytes:
            raise _memory_error("Windows available memory is outside physical bounds")
        return available_bytes // 1024**2
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"available memory is unsupported on platform: {selected}",
        platform=selected,
    )


def _process_error(message: str, *, pid: int, **details: Any) -> PlatformRuntimeError:
    return PlatformRuntimeError("process_unavailable", message, pid=pid, **details)


def _process_disappeared(exc: OSError) -> bool:
    return exc.errno in {errno.ENOENT, errno.ESRCH}


def _linux_process_identity(pid: int) -> ProcessIdentity | None:
    process_root = Path("/proc") / str(pid)
    try:
        status_text = (process_root / "status").read_text(
            encoding="utf-8", errors="strict"
        )
        stat_text = (process_root / "stat").read_text(
            encoding="utf-8", errors="strict"
        )
        command_line = (process_root / "cmdline").read_bytes()
        executable_raw = os.readlink(process_root / "exe")
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except OSError as exc:
        if _process_disappeared(exc):
            return None
        raise _process_error("Linux process identity cannot be read", pid=pid) from exc
    except UnicodeError as exc:
        raise _process_error("Linux process identity is malformed", pid=pid) from exc

    effective_uid: int | None = None
    for line in status_text.splitlines():
        if not line.startswith("Uid:"):
            continue
        fields = line.split()
        try:
            effective_uid = int(fields[2], 10)
        except (IndexError, ValueError) as exc:
            raise _process_error("Linux process owner is malformed", pid=pid) from exc
        break
    if effective_uid is None:
        raise _process_error("Linux process owner is missing", pid=pid)

    closing = stat_text.rfind(")")
    if closing < 0:
        raise _process_error("Linux process start identity is malformed", pid=pid)
    remaining_fields = stat_text[closing + 1 :].split()
    if len(remaining_fields) <= 19:
        raise _process_error("Linux process start identity is incomplete", pid=pid)
    start_ticks = remaining_fields[19]
    if not start_ticks.isdigit() or not boot_id:
        raise _process_error("Linux process start identity is invalid", pid=pid)

    argv = tuple(
        os.fsdecode(part) for part in command_line.split(b"\0") if part
    )
    executable = str(Path(executable_raw).resolve())
    return ProcessIdentity(
        pid=pid,
        owner_id=f"uid:{effective_uid}",
        start_token=f"linux:{boot_id}:{start_ticks}",
        executable=executable,
        argv=argv,
    )


def _macos_bsd_info(pid: int):
    from ctypes import util as ctypes_util  # noqa: PLC0415

    class ProcBsdInfo(ctypes.Structure):
        _fields_ = (
            ("pbi_flags", ctypes.c_uint32),
            ("pbi_status", ctypes.c_uint32),
            ("pbi_xstatus", ctypes.c_uint32),
            ("pbi_pid", ctypes.c_uint32),
            ("pbi_ppid", ctypes.c_uint32),
            ("pbi_uid", ctypes.c_uint32),
            ("pbi_gid", ctypes.c_uint32),
            ("pbi_ruid", ctypes.c_uint32),
            ("pbi_rgid", ctypes.c_uint32),
            ("pbi_svuid", ctypes.c_uint32),
            ("pbi_svgid", ctypes.c_uint32),
            ("rfu_1", ctypes.c_uint32),
            ("pbi_comm", ctypes.c_char * 16),
            ("pbi_name", ctypes.c_char * 32),
            ("pbi_nfiles", ctypes.c_uint32),
            ("pbi_pgid", ctypes.c_uint32),
            ("pbi_pjobc", ctypes.c_uint32),
            ("e_tdev", ctypes.c_uint32),
            ("e_tpgid", ctypes.c_uint32),
            ("pbi_nice", ctypes.c_int32),
            ("pbi_start_tvsec", ctypes.c_uint64),
            ("pbi_start_tvusec", ctypes.c_uint64),
        )

    libproc_path = ctypes_util.find_library("proc") or "/usr/lib/libproc.dylib"
    libproc = ctypes.CDLL(libproc_path, use_errno=True)
    libproc.proc_pidinfo.argtypes = (
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    )
    libproc.proc_pidinfo.restype = ctypes.c_int
    info = ProcBsdInfo()
    result = libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if result == 0:
        error_code = ctypes.get_errno()
        if error_code in {errno.ENOENT, errno.ESRCH}:
            return None, libproc
        raise _process_error(
            "macOS process identity cannot be read", pid=pid, errno=error_code
        )
    if result < ctypes.sizeof(info):
        raise _process_error("macOS process identity is incomplete", pid=pid)
    return info, libproc


def _macos_process_argv(pid: int) -> tuple[str, ...]:
    from ctypes import util as ctypes_util  # noqa: PLC0415

    libc_path = ctypes_util.find_library("c") or "/usr/lib/libSystem.B.dylib"
    libc = ctypes.CDLL(libc_path, use_errno=True)
    libc.sysctl.argtypes = (
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    libc.sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t()
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value <= 4:
        raise _process_error(
            "macOS process argv size cannot be read", pid=pid, errno=ctypes.get_errno()
        )
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        error_code = ctypes.get_errno()
        if error_code in {errno.ENOENT, errno.ESRCH}:
            raise ProcessLookupError(pid)
        raise _process_error(
            "macOS process argv cannot be read", pid=pid, errno=error_code
        )
    raw = bytes(buffer.raw[: size.value])
    argument_count = int.from_bytes(raw[:4], byteorder=sys.byteorder, signed=True)
    if argument_count < 0:
        raise _process_error("macOS process argv count is invalid", pid=pid)
    cursor = 4
    executable_end = raw.find(b"\0", cursor)
    if executable_end < 0:
        raise _process_error("macOS process argv is malformed", pid=pid)
    cursor = executable_end
    while cursor < len(raw) and raw[cursor] == 0:
        cursor += 1
    arguments: list[str] = []
    while len(arguments) < argument_count and cursor < len(raw):
        end = raw.find(b"\0", cursor)
        if end < 0:
            break
        arguments.append(os.fsdecode(raw[cursor:end]))
        cursor = end + 1
    if len(arguments) != argument_count:
        raise _process_error("macOS process argv is incomplete", pid=pid)
    return tuple(arguments)


def _macos_process_identity(pid: int) -> ProcessIdentity | None:
    try:
        info, libproc = _macos_bsd_info(pid)
    except ProcessLookupError:
        return None
    if info is None:
        return None
    buffer = ctypes.create_string_buffer(4096)
    libproc.proc_pidpath.argtypes = (
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    libproc.proc_pidpath.restype = ctypes.c_int
    length = libproc.proc_pidpath(pid, buffer, len(buffer))
    if length <= 0:
        error_code = ctypes.get_errno()
        if error_code in {errno.ENOENT, errno.ESRCH}:
            return None
        raise _process_error(
            "macOS process executable cannot be read", pid=pid, errno=error_code
        )
    try:
        argv = _macos_process_argv(pid)
    except ProcessLookupError:
        return None
    executable = os.fsdecode(buffer.raw[:length])
    return ProcessIdentity(
        pid=pid,
        owner_id=f"uid:{int(info.pbi_uid)}",
        start_token=(
            f"darwin:{int(info.pbi_start_tvsec)}:{int(info.pbi_start_tvusec)}"
        ),
        executable=str(Path(executable).resolve()),
        argv=argv,
    )


def _windows_process_api():
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32


def _open_windows_process(pid: int, *, terminate: bool = False):
    kernel32 = _windows_process_api()
    access = 0x1000 | 0x0400 | 0x0010 | 0x00100000
    if terminate:
        access |= 0x0001
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code in {87, 1168}:
            return kernel32, None
        raise _process_error(
            "Windows process handle cannot be opened",
            pid=pid,
            winerror=error_code,
        )
    wait_result = kernel32.WaitForSingleObject(handle, 0)
    if wait_result == 0:
        kernel32.CloseHandle(handle)
        return kernel32, None
    if wait_result != 258:
        error_code = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise _process_error(
            "Windows process state cannot be read", pid=pid, winerror=error_code
        )
    return kernel32, handle


def _windows_owner_id_from_handle(handle, *, pid: int) -> str:
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    if not advapi32.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):
        raise _process_error(
            "Windows process owner cannot be opened",
            pid=pid,
            winerror=ctypes.get_last_error(),
        )
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token, 1, None, 0, ctypes.byref(needed)
        )
        if ctypes.get_last_error() != 122 or needed.value == 0:
            raise _process_error(
                "Windows process owner size cannot be read",
                pid=pid,
                winerror=ctypes.get_last_error(),
            )
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, 1, buffer, needed, ctypes.byref(needed)
        ):
            raise _process_error(
                "Windows process owner cannot be read",
                pid=pid,
                winerror=ctypes.get_last_error(),
            )

        class SidAndAttributes(ctypes.Structure):
            _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))

        class TokenUser(ctypes.Structure):
            _fields_ = (("User", SidAndAttributes),)

        token_user = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents
        advapi32.GetLengthSid.argtypes = (ctypes.c_void_p,)
        advapi32.GetLengthSid.restype = wintypes.DWORD
        sid_length = advapi32.GetLengthSid(token_user.User.Sid)
        if sid_length == 0:
            raise _process_error("Windows process owner SID is invalid", pid=pid)
        sid_bytes = ctypes.string_at(token_user.User.Sid, sid_length)
        return f"sid:{sid_bytes.hex()}"
    finally:
        kernel32.CloseHandle(token)


def _windows_process_argv(handle, *, pid: int) -> tuple[str, ...]:
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtQueryInformationProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    )
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    return_length = wintypes.ULONG()
    status = ntdll.NtQueryInformationProcess(
        handle, 60, None, 0, ctypes.byref(return_length)
    )
    if return_length.value == 0:
        raise _process_error(
            "Windows process command line size cannot be read",
            pid=pid,
            ntstatus=int(status),
        )
    buffer = ctypes.create_string_buffer(return_length.value)
    status = ntdll.NtQueryInformationProcess(
        handle,
        60,
        buffer,
        return_length,
        ctypes.byref(return_length),
    )
    if status != 0:
        raise _process_error(
            "Windows process command line cannot be read",
            pid=pid,
            ntstatus=int(status),
        )

    class UnicodeString(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        )

    value = ctypes.cast(buffer, ctypes.POINTER(UnicodeString)).contents
    if value.Length == 0 or value.Buffer is None:
        raise _process_error("Windows process command line is empty", pid=pid)
    command_line = ctypes.wstring_at(value.Buffer, value.Length // ctypes.sizeof(ctypes.c_wchar))

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_int),
    )
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    argument_count = ctypes.c_int()
    arguments = shell32.CommandLineToArgvW(command_line, ctypes.byref(argument_count))
    if not arguments or argument_count.value <= 0:
        raise _process_error(
            "Windows process command line cannot be parsed",
            pid=pid,
            winerror=ctypes.get_last_error(),
        )
    try:
        return tuple(arguments[index] for index in range(argument_count.value))
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.LocalFree(ctypes.cast(arguments, wintypes.HLOCAL))


def _windows_process_identity_from_handle(pid: int, handle) -> ProcessIdentity:
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    created = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise _process_error(
            "Windows process start identity cannot be read",
            pid=pid,
            winerror=ctypes.get_last_error(),
        )
    creation_ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    size = wintypes.DWORD(32768)
    executable_buffer = ctypes.create_unicode_buffer(size.value)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    if not kernel32.QueryFullProcessImageNameW(
        handle, 0, executable_buffer, ctypes.byref(size)
    ):
        raise _process_error(
            "Windows process executable cannot be read",
            pid=pid,
            winerror=ctypes.get_last_error(),
        )
    return ProcessIdentity(
        pid=pid,
        owner_id=_windows_owner_id_from_handle(handle, pid=pid),
        start_token=f"win32:{creation_ticks}",
        executable=str(Path(executable_buffer.value).resolve()),
        argv=_windows_process_argv(handle, pid=pid),
    )


def _windows_process_identity(pid: int) -> ProcessIdentity | None:
    kernel32, handle = _open_windows_process(pid)
    if handle is None:
        return None
    try:
        return _windows_process_identity_from_handle(pid, handle)
    finally:
        kernel32.CloseHandle(handle)


def current_owner_id(*, platform_name: str | None = None) -> str:
    """Return a stable current-user identifier for process ownership checks."""

    selected = platform_name or sys.platform
    if selected.startswith("linux") or selected == "darwin":
        return f"uid:{os.geteuid()}"
    if selected in {"win32", "cygwin", "nt"}:
        from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        return _windows_owner_id_from_handle(
            kernel32.GetCurrentProcess(), pid=os.getpid()
        )
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"process ownership is unsupported on platform: {selected}",
        platform=selected,
    )


def inspect_process(
    pid: int, *, platform_name: str | None = None
) -> ProcessIdentity | None:
    """Return one complete live identity; only proven absence returns ``None``."""

    if not isinstance(pid, int) or pid <= 0:
        return None
    selected = platform_name or sys.platform
    if selected.startswith("linux"):
        return _linux_process_identity(pid)
    if selected == "darwin":
        return _macos_process_identity(pid)
    if selected in {"win32", "cygwin", "nt"}:
        return _windows_process_identity(pid)
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"process inspection is unsupported on platform: {selected}",
        platform=selected,
    )


def _windows_parent_process_id(pid: int) -> int | None:
    """Read one process parent from the native Windows process snapshot."""

    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
    )
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
    )
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    snapshot_value = getattr(snapshot, "value", snapshot)
    if snapshot is None or snapshot_value == invalid_handle:
        raise _process_error(
            "Windows process parent cannot be enumerated",
            pid=pid,
            winerror=ctypes.get_last_error(),
        )
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            if error == 18:  # ERROR_NO_MORE_FILES: the PID is not present.
                return None
            raise _process_error(
                "Windows process parent cannot be enumerated",
                pid=pid,
                winerror=error,
            )
        while True:
            if int(entry.th32ProcessID) == pid:
                return int(entry.th32ParentProcessID)
            if kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                continue
            error = ctypes.get_last_error()
            if error == 18:  # ERROR_NO_MORE_FILES: the PID is not present.
                return None
            raise _process_error(
                "Windows process parent cannot be enumerated",
                pid=pid,
                winerror=error,
            )
    finally:
        kernel32.CloseHandle(snapshot)


def parent_process_id(pid: int, *, platform_name: str | None = None) -> int | None:
    """Return one native parent PID; currently only Windows has this seam."""

    if not isinstance(pid, int) or pid <= 0:
        return None
    selected = platform_name or sys.platform
    if selected in {"win32", "cygwin", "nt"}:
        return _windows_parent_process_id(pid)
    if selected.startswith("linux") or selected == "darwin":
        return None
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"process parent inspection is unsupported on platform: {selected}",
        platform=selected,
    )


def _bind_listener_probe(host: str, port: int, *, source: str) -> ListenerSnapshot:
    handle = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        handle.bind((host, port))
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, errno.EACCES}:
            return ListenerSnapshot(
                present=True, pids=frozenset(), complete=False, source=source
            )
        return ListenerSnapshot(
            present=False, pids=frozenset(), complete=False, source=source
        )
    finally:
        handle.close()
    return ListenerSnapshot(
        present=False, pids=frozenset(), complete=True, source=source
    )


def _linux_tcp_listener(host: str, port: int) -> ListenerSnapshot:
    inodes: set[str] = set()
    readable_tables = 0
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table.read_text(encoding="ascii").splitlines()[1:]
        except (OSError, UnicodeError):
            continue
        readable_tables += 1
        for line in lines:
            fields = line.split()
            if len(fields) < 10:
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port and fields[3] == "0A":
                inodes.add(fields[9])
    if not inodes:
        if readable_tables:
            return ListenerSnapshot(
                present=False,
                pids=frozenset(),
                complete=True,
                source="linux_procfs",
            )
        return _bind_listener_probe(host, port, source="linux_bind_probe")

    found_inodes: set[str] = set()
    pids: set[int] = set()
    try:
        process_entries = list(Path("/proc").glob("[0-9]*"))
    except OSError:
        process_entries = []
    for process_entry in process_entries:
        try:
            descriptors = list((process_entry / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if not target.startswith("socket:[") or not target.endswith("]"):
                continue
            inode = target[8:-1]
            if inode in inodes:
                found_inodes.add(inode)
                pids.add(int(process_entry.name))
    return ListenerSnapshot(
        present=True,
        pids=frozenset(pids),
        complete=found_inodes == inodes and bool(pids),
        source="linux_procfs",
    )


def _macos_tcp_listener(host: str, port: int) -> ListenerSnapshot:
    try:
        result = subprocess.run(
            [
                "/usr/sbin/lsof",
                "-nP",
                "-a",
                "-Fpn",
                f"-iTCP:{port}",
                "-sTCP:LISTEN",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
            env={"LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return _bind_listener_probe(host, port, source="macos_lsof_failed")
    pids: set[int] = set()
    malformed = False
    for line in result.stdout.splitlines():
        if not line.startswith("p"):
            continue
        value = line[1:]
        if not value.isdigit():
            malformed = True
            continue
        pids.add(int(value, 10))
    if result.returncode == 0 and pids and not malformed:
        return ListenerSnapshot(
            present=True,
            pids=frozenset(pids),
            complete=True,
            source="macos_lsof",
        )
    if (
        result.returncode == 1
        and not result.stdout.strip()
        and not result.stderr.strip()
    ):
        return ListenerSnapshot(
            present=False,
            pids=frozenset(),
            complete=True,
            source="macos_lsof_absent",
        )
    return _bind_listener_probe(host, port, source="macos_lsof_incomplete")


def _windows_tcp_listener(host: str, port: int) -> ListenerSnapshot:
    from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

    class TcpRowOwnerPid(ctypes.Structure):
        _fields_ = (
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        )

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    iphlpapi.GetExtendedTcpTable.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    )
    iphlpapi.GetExtendedTcpTable.restype = wintypes.ULONG
    size = wintypes.ULONG()
    first = iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
    if first not in {0, 122} or size.value < ctypes.sizeof(wintypes.DWORD):
        return _bind_listener_probe(host, port, source="windows_tcp_table_failed")
    buffer = ctypes.create_string_buffer(size.value)
    result = iphlpapi.GetExtendedTcpTable(
        buffer, ctypes.byref(size), False, 2, 3, 0
    )
    if result != 0:
        return _bind_listener_probe(host, port, source="windows_tcp_table_failed")
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    row_size = ctypes.sizeof(TcpRowOwnerPid)
    required = ctypes.sizeof(wintypes.DWORD) + count * row_size
    if required > size.value:
        return ListenerSnapshot(
            present=False,
            pids=frozenset(),
            complete=False,
            source="windows_tcp_table_malformed",
        )
    pids: set[int] = set()
    for index in range(count):
        offset = ctypes.sizeof(wintypes.DWORD) + index * row_size
        row = TcpRowOwnerPid.from_buffer_copy(buffer.raw[offset : offset + row_size])
        if socket.ntohs(int(row.dwLocalPort) & 0xFFFF) == port:
            pids.add(int(row.dwOwningPid))
    return ListenerSnapshot(
        present=bool(pids),
        pids=frozenset(pids),
        complete=True,
        source="windows_tcp_table",
    )


def find_tcp_listener(
    host: str, port: int, *, platform_name: str | None = None
) -> ListenerSnapshot:
    """Return listener presence plus whether every owning PID is proved."""

    if host != "127.0.0.1" or not isinstance(port, int) or not 0 < port <= 65535:
        raise PlatformRuntimeError(
            "listener_unavailable",
            "runtime listener probe accepts only IPv4 loopback and a valid port",
            host=host,
            port=port,
        )
    selected = platform_name or sys.platform
    if selected.startswith("linux"):
        return _linux_tcp_listener(host, port)
    if selected == "darwin":
        return _macos_tcp_listener(host, port)
    if selected in {"win32", "cygwin", "nt"}:
        return _windows_tcp_listener(host, port)
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"listener inspection is unsupported on platform: {selected}",
        platform=selected,
    )


def _terminate_posix_process(
    expected: ProcessIdentity,
    *,
    graceful_timeout: float,
    poll_interval: float,
    platform_name: str,
) -> None:
    current = inspect_process(expected.pid, platform_name=platform_name)
    if current is None:
        return
    if current != expected:
        raise PlatformRuntimeError(
            "process_identity_changed",
            "process identity changed before termination",
            pid=expected.pid,
        )
    try:
        os.kill(expected.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise _process_error("process could not be terminated", pid=expected.pid) from exc

    deadline = time.monotonic() + max(0.0, graceful_timeout)
    while True:
        current = inspect_process(expected.pid, platform_name=platform_name)
        if current is None or current != expected:
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0.001, poll_interval))

    current = inspect_process(expected.pid, platform_name=platform_name)
    if current is None or current != expected:
        return
    try:
        os.kill(expected.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise _process_error("process could not be force-terminated", pid=expected.pid) from exc

    force_deadline = time.monotonic() + max(1.0, graceful_timeout)
    while time.monotonic() < force_deadline:
        current = inspect_process(expected.pid, platform_name=platform_name)
        if current is None or current != expected:
            return
        time.sleep(max(0.001, poll_interval))
    raise PlatformRuntimeError(
        "termination_timeout",
        "process did not exit after force termination",
        pid=expected.pid,
    )


def _terminate_windows_process(
    expected: ProcessIdentity, *, graceful_timeout: float, poll_interval: float
) -> None:
    kernel32, handle = _open_windows_process(expected.pid, terminate=True)
    if handle is None:
        return
    try:
        current = _windows_process_identity_from_handle(expected.pid, handle)
        if current != expected:
            raise PlatformRuntimeError(
                "process_identity_changed",
                "process identity changed before termination",
                pid=expected.pid,
            )
        from ctypes import wintypes  # noqa: PLC0415 - Windows-only definitions

        kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateProcess.restype = wintypes.BOOL
        if not kernel32.TerminateProcess(handle, 1):
            raise _process_error(
                "Windows process could not be terminated",
                pid=expected.pid,
                winerror=ctypes.get_last_error(),
            )
        wait_milliseconds = int(max(1.0, graceful_timeout) * 1000)
        result = kernel32.WaitForSingleObject(handle, wait_milliseconds)
        if result != 0:
            raise PlatformRuntimeError(
                "termination_timeout",
                "Windows process did not exit after termination",
                pid=expected.pid,
            )
    finally:
        kernel32.CloseHandle(handle)


def terminate_process(
    expected: ProcessIdentity,
    *,
    graceful_timeout: float,
    poll_interval: float,
    platform_name: str | None = None,
) -> None:
    """Terminate only the still-matching process incarnation."""

    selected = platform_name or sys.platform
    if selected.startswith("linux") or selected == "darwin":
        _terminate_posix_process(
            expected,
            graceful_timeout=graceful_timeout,
            poll_interval=poll_interval,
            platform_name=selected,
        )
        return
    if selected in {"win32", "cygwin", "nt"}:
        _terminate_windows_process(
            expected,
            graceful_timeout=graceful_timeout,
            poll_interval=poll_interval,
        )
        return
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"process termination is unsupported on platform: {selected}",
        platform=selected,
    )


def background_popen_kwargs(
    *, platform_name: str | None = None
) -> dict[str, Any]:
    """Return platform-correct detached service creation flags."""

    selected = platform_name or sys.platform
    if selected.startswith("linux") or selected == "darwin":
        return {"start_new_session": True, "close_fds": True}
    if selected in {"win32", "cygwin", "nt"}:
        create_new_process_group = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {
            "creationflags": create_new_process_group | create_no_window,
            "close_fds": True,
        }
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"background process creation is unsupported on platform: {selected}",
        platform=selected,
    )


def venv_python(
    venv_root: Path, *, platform_name: str | None = None
) -> Path:
    """Return the platform-specific interpreter inside a virtual environment."""

    selected = platform_name or sys.platform
    if selected in {"win32", "cygwin", "nt"}:
        return venv_root / "Scripts" / "python.exe"
    if selected.startswith("linux") or selected == "darwin":
        return venv_root / "bin" / "python"
    raise PlatformRuntimeError(
        "unsupported_platform",
        f"virtual environments are unsupported on platform: {selected}",
        platform=selected,
    )
