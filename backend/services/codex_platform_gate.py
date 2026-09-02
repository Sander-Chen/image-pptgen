"""Process-local admission gate for Platform-owned Codex child processes."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time
from typing import Deque

from backend.services.platform_runtime import (
    PlatformRuntimeError,
    read_available_memory_mib,
)


CODEX_CHILD_HARD_MAX_CONCURRENCY = 4
CODEX_CHILD_HOST_FLOOR_MIB = 512
CODEX_CHILD_RESERVATION_MIB = 512
DEFAULT_CODEX_CHILD_MAX_CONCURRENCY = CODEX_CHILD_HARD_MAX_CONCURRENCY
DEFAULT_CODEX_MIN_AVAILABLE_MIB = 2048
DEFAULT_CODEX_CHILD_RESERVATION_MIB = CODEX_CHILD_RESERVATION_MIB
MEMINFO_PATH = Path("/proc/meminfo")
_EVIDENCE_LIMIT = 256

# This is intentionally the only cross-thread coordination primitive.  The
# gate is process-local: it cannot observe, count, signal, or wait on a child
# it did not spawn through a lease below.
_CONDITION = threading.Condition()


class CodexGateCapacityTimeout(TimeoutError):
    """Raised when a Platform child cannot be admitted within its deadline."""


class CodexGateMemoryUnavailable(RuntimeError):
    """Raised when ``MemAvailable`` is absent or cannot be parsed safely."""


@dataclass(frozen=True)
class CodexChildLease:
    """An in-memory capability for exactly one Platform-spawned child."""

    lease_id: int
    admitted_at_monotonic: float
    mem_available_mib: int
    reservation_mib: int
    safe_capacity: int


@dataclass
class _Waiter:
    sequence: int
    cancelled: bool = False
    lease: CodexChildLease | None = None


def _bounded_positive_env(name: str, default: int, *, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return min(value, maximum) if maximum is not None else value


def _read_mem_available_mib_from_proc() -> int:
    try:
        return read_available_memory_mib(
            platform_name="linux", linux_meminfo_path=MEMINFO_PATH
        )
    except PlatformRuntimeError as exc:
        raise CodexGateMemoryUnavailable(str(exc)) from exc


def read_mem_available_mib() -> int:
    """Read fresh physical memory; deliberately no cache or estimate exists."""
    try:
        return read_available_memory_mib()
    except PlatformRuntimeError as exc:
        raise CodexGateMemoryUnavailable(str(exc)) from exc


class CodexPlatformChildGate:
    """FIFO-enough lease admission backed by the module-level condition."""

    def __init__(self) -> None:
        self._installed_profile = os.environ.get("PPTGEN_IMAGE_RUNTIME_MODE") == "installed"
        self._max_concurrency = _bounded_positive_env(
            "PPTGEN_CODEX_CHILD_MAX_CONCURRENCY",
            DEFAULT_CODEX_CHILD_MAX_CONCURRENCY,
            maximum=CODEX_CHILD_HARD_MAX_CONCURRENCY,
        )
        if self._installed_profile:
            # The installed profile's memory contract is deliberately fixed:
            # only the configured concurrency ceiling may be lowered.  In
            # particular, an inherited threshold or reservation must not turn
            # the documented 512/512 formula into a user-selected one.
            self._min_available_mib = CODEX_CHILD_HOST_FLOOR_MIB
            self._reservation_mib = CODEX_CHILD_RESERVATION_MIB
        else:
            self._min_available_mib = _bounded_positive_env(
                "PPTGEN_CODEX_MIN_AVAILABLE_MIB", DEFAULT_CODEX_MIN_AVAILABLE_MIB
            )
            self._reservation_mib = _bounded_positive_env(
                "PPTGEN_CODEX_CHILD_RESERVATION_MIB", DEFAULT_CODEX_CHILD_RESERVATION_MIB
            )
        self._active: dict[int, CodexChildLease] = {}
        self._owned_pids: dict[int, int] = {}
        self._waiters: Deque[_Waiter] = deque()
        self._events: Deque[dict[str, int | float | str | None]] = deque(maxlen=_EVIDENCE_LIMIT)
        self._next_lease_id = 1
        self._next_waiter_sequence = 1

    def _append_event(self, event: str, *, reason: str, **fields: int | float | str | None) -> None:
        self._events.append({"event": event, "reason": reason, **fields})

    def _discard_waiter_locked(self, waiter: _Waiter) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            pass

    def _is_first_live_waiter_locked(self, waiter: _Waiter) -> bool:
        while self._waiters and self._waiters[0].cancelled:
            self._waiters.popleft()
        return bool(self._waiters) and self._waiters[0] is waiter

    def _safe_capacity_for_available_mib(self, available_mib: int) -> int:
        """Return the installed profile's fresh-memory admission capacity."""
        if not self._installed_profile:
            raise RuntimeError("dynamic capacity is available only for installed profile")
        capacity = (available_mib - CODEX_CHILD_HOST_FLOOR_MIB) // CODEX_CHILD_RESERVATION_MIB
        return max(0, min(self._max_concurrency, CODEX_CHILD_HARD_MAX_CONCURRENCY, capacity))

    def _admission_capacity_locked(self) -> tuple[int, int]:
        available_mib = read_mem_available_mib()
        if self._installed_profile:
            return self._safe_capacity_for_available_mib(available_mib), available_mib
        projected_available_mib = available_mib - self._reservation_mib * (len(self._active) + 1)
        capacity = self._max_concurrency if projected_available_mib >= self._min_available_mib else 0
        return capacity, available_mib

    def _admission_memory_ok_locked(self) -> tuple[bool, int]:
        capacity, available_mib = self._admission_capacity_locked()
        return len(self._active) < capacity, available_mib

    def try_acquire(self, *, waiter: _Waiter, wait_seconds: float) -> CodexChildLease | None:
        """Wait at most ``wait_seconds`` synchronously for the supplied waiter."""
        deadline = time.monotonic() + max(0.0, wait_seconds)
        with _CONDITION:
            if waiter.cancelled:
                return None
            if waiter not in self._waiters:
                self._waiters.append(waiter)
            while not waiter.cancelled:
                if self._is_first_live_waiter_locked(waiter):
                    if not self._installed_profile and len(self._active) >= self._max_concurrency:
                        self._append_event(
                            "waiting",
                            reason="max_owned_children",
                            active_owned_children=len(self._active),
                        )
                    else:
                        try:
                            safe_capacity, available_mib = self._admission_capacity_locked()
                        except CodexGateMemoryUnavailable:
                            self._discard_waiter_locked(waiter)
                            _CONDITION.notify_all()
                            raise
                        if len(self._active) < safe_capacity:
                            self._waiters.popleft()
                            lease = CodexChildLease(
                                lease_id=self._next_lease_id,
                                admitted_at_monotonic=time.monotonic(),
                                mem_available_mib=available_mib,
                                reservation_mib=self._reservation_mib,
                                safe_capacity=safe_capacity,
                            )
                            self._next_lease_id += 1
                            self._active[lease.lease_id] = lease
                            waiter.lease = lease
                            self._append_event(
                                "admitted",
                                reason="admitted",
                                lease_id=lease.lease_id,
                                active_owned_children=len(self._active),
                                mem_available_mib=available_mib,
                                reservation_mib=self._reservation_mib,
                                safe_capacity=safe_capacity,
                            )
                            _CONDITION.notify_all()
                            return lease
                        reason = (
                            "insufficient_mem_available"
                            if safe_capacity == 0
                            else "safe_capacity_exhausted"
                        )
                        self._append_event(
                            "waiting",
                            reason=reason,
                            active_owned_children=len(self._active),
                            mem_available_mib=available_mib,
                            safe_capacity=safe_capacity,
                        )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                _CONDITION.wait(remaining)
            return None

    def _release_locked(self, lease: CodexChildLease, *, reason: str) -> bool:
        if self._active.pop(lease.lease_id, None) is None:
            return False
        pid = self._owned_pids.pop(lease.lease_id, None)
        self._append_event(
            "released",
            reason=reason,
            lease_id=lease.lease_id,
            active_owned_children=len(self._active),
            pid=pid,
        )
        _CONDITION.notify_all()
        return True

    def cancel_waiter(self, waiter: _Waiter) -> None:
        """Remove a timed-out/cancelled async waiter, revoking a raced lease."""
        with _CONDITION:
            waiter.cancelled = True
            self._discard_waiter_locked(waiter)
            if waiter.lease is not None:
                self._release_locked(waiter.lease, reason="cancelled_before_delivery")
            _CONDITION.notify_all()

    async def acquire_async(self, *, timeout_seconds: float) -> CodexChildLease:
        """Acquire with bounded 250ms worker-thread polls and cancellation cleanup."""
        waiter = _Waiter(sequence=self._next_waiter_sequence)
        with _CONDITION:
            self._next_waiter_sequence += 1
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        delivered = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexGateCapacityTimeout("Platform Codex child admission timed out")
                lease = await asyncio.to_thread(
                    self.try_acquire,
                    waiter=waiter,
                    wait_seconds=min(0.25, remaining),
                )
                if lease is not None:
                    delivered = True
                    return lease
        finally:
            if not delivered:
                self.cancel_waiter(waiter)

    def record_spawned_child(self, lease: CodexChildLease, *, pid: int) -> None:
        """Record only the PID returned by the lease-bound spawn call."""
        with _CONDITION:
            if self._active.get(lease.lease_id) != lease:
                raise RuntimeError("cannot bind a PID to an inactive Codex child lease")
            self._owned_pids[lease.lease_id] = pid
            self._append_event(
                "spawned",
                reason="owned_child_spawned",
                lease_id=lease.lease_id,
                active_owned_children=len(self._active),
                pid=pid,
            )

    def release(self, lease: CodexChildLease, *, reason: str) -> bool:
        """Release at most once after the bound child and all readers settle."""
        with _CONDITION:
            return self._release_locked(lease, reason=reason)

    def snapshot(self) -> dict[str, int]:
        with _CONDITION:
            return {"active_owned_children": len(self._active), "waiting": len(self._waiters)}

    def evidence(self) -> tuple[dict[str, int | float | str | None], ...]:
        with _CONDITION:
            return tuple(self._events)


_PLATFORM_CODEX_CHILD_GATE = CodexPlatformChildGate()


def get_platform_codex_child_gate() -> CodexPlatformChildGate:
    return _PLATFORM_CODEX_CHILD_GATE


def reset_platform_gate_for_testing() -> None:
    """Replace test state while retaining the sole module-level condition."""
    global _PLATFORM_CODEX_CHILD_GATE
    _PLATFORM_CODEX_CHILD_GATE = CodexPlatformChildGate()
