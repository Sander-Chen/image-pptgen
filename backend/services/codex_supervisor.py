"""Deterministic lease and fencing primitives for supervised Codex work items."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import db as dbmod
from backend.domain.status import TERMINAL_STATUSES


UTC = timezone.utc
DEFAULT_LEASE_SECONDS = 30


@dataclass(frozen=True)
class LeaseResult:
    acquired: bool
    reason: str
    owner: str | None = None
    fencing_token: int | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lease(
    *,
    work_item_id: int,
    owner: str,
    now: datetime,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    pid: int | None = None,
    session_id: str | None = None,
    pid_exists: Callable[[int], bool] = _process_exists,
    recovery: bool = False,
) -> LeaseResult:
    """Acquire one fenced owner lease, or return the durable rejection reason."""
    if not owner:
        raise ValueError("owner is required")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    current_time = _utc(now)
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM codex_work_items WHERE id = ?",
            (work_item_id,),
        ).fetchone()
        if row is None:
            db.rollback()
            return LeaseResult(False, "work_item_missing")
        item = dict(row)
        if item["status"] in TERMINAL_STATUSES:
            db.rollback()
            return LeaseResult(False, "terminal")

        previous_owner = item.get("lease_owner")
        previous_pid = item.get("pid")
        lease_expires_at = _parse_timestamp(item.get("lease_expires_at"))
        if previous_owner:
            if lease_expires_at is not None and lease_expires_at > current_time:
                db.rollback()
                return LeaseResult(False, "lease_held", previous_owner, item["fencing_token"])
            if previous_pid is not None and pid_exists(int(previous_pid)):
                db.rollback()
                return LeaseResult(False, "process_alive", previous_owner, item["fencing_token"])
            if not recovery:
                db.rollback()
                return LeaseResult(False, "recovery_required", previous_owner, item["fencing_token"])

        is_recovery = bool(previous_owner and recovery)
        if is_recovery and int(item["recovery_count"]) >= int(item["max_recoveries"]):
            db.rollback()
            return LeaseResult(
                False,
                "recovery_budget_exhausted",
                previous_owner,
                item["fencing_token"],
            )

        next_token = int(item["fencing_token"]) + 1
        cursor = db.execute(
            """UPDATE codex_work_items
               SET status = 'running',
                   lease_owner = ?,
                   lease_expires_at = ?,
                   heartbeat_at = ?,
                   fencing_token = ?,
                   attempt_count = attempt_count + 1,
                   recovery_count = recovery_count + ?,
                   pid = ?,
                   session_id = ?,
                   started_at = COALESCE(started_at, ?),
                   updated_at = ?
               WHERE id = ? AND fencing_token = ?""",
            (
                owner,
                _iso(current_time + timedelta(seconds=lease_seconds)),
                _iso(current_time),
                next_token,
                1 if is_recovery else 0,
                pid,
                session_id,
                _iso(current_time),
                _iso(current_time),
                work_item_id,
                item["fencing_token"],
            ),
        )
        if cursor.rowcount != 1:
            db.rollback()
            return LeaseResult(False, "lease_raced")
        db.commit()
        return LeaseResult(
            True,
            "recovered" if is_recovery else "acquired",
            owner,
            next_token,
        )
    except sqlite3.Error:
        db.rollback()
        raise
    finally:
        db.close()


def heartbeat(
    *,
    work_item_id: int,
    owner: str,
    fencing_token: int,
    now: datetime,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    """Renew the current fenced lease without invoking a model or changing usage."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    current_time = _utc(now)
    terminal_placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    db = dbmod.get_db()
    try:
        cursor = db.execute(
            f"""UPDATE codex_work_items
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND lease_owner = ? AND fencing_token = ?
                  AND status NOT IN ({terminal_placeholders})""",
            (
                _iso(current_time),
                _iso(current_time + timedelta(seconds=lease_seconds)),
                _iso(current_time),
                work_item_id,
                owner,
                fencing_token,
                *sorted(TERMINAL_STATUSES),
            ),
        )
        db.commit()
        return cursor.rowcount == 1
    finally:
        db.close()


def bind_process(
    *,
    work_item_id: int,
    owner: str,
    fencing_token: int,
    pid: int,
    session_id: str | None = None,
) -> bool:
    """Attach the current child identity only to the active fenced owner."""
    db = dbmod.get_db()
    try:
        cursor = db.execute(
            """UPDATE codex_work_items
               SET pid = ?, session_id = COALESCE(?, session_id), updated_at = datetime('now')
               WHERE id = ? AND lease_owner = ? AND fencing_token = ?
                 AND status NOT IN ('completed', 'completed_with_failures', 'failed', 'timed_out', 'cancelled')""",
            (pid, session_id, work_item_id, owner, fencing_token),
        )
        db.commit()
        return cursor.rowcount == 1
    finally:
        db.close()


def mark_attempt_lost(
    *,
    work_item_id: int,
    owner: str,
    fencing_token: int,
    reason: str,
    now: datetime,
) -> bool:
    """Expire one failed child attempt so a bounded recovery may take over."""
    current_time = _utc(now)
    db = dbmod.get_db()
    try:
        cursor = db.execute(
            """UPDATE codex_work_items
               SET status = 'running', error_class = 'child_process_lost',
                   terminal_reason = ?, pid = NULL,
                   heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
               WHERE id = ? AND lease_owner = ? AND fencing_token = ?
                 AND status NOT IN ('completed', 'completed_with_failures', 'failed', 'timed_out', 'cancelled')""",
            (
                reason,
                _iso(current_time),
                _iso(current_time),
                _iso(current_time),
                work_item_id,
                owner,
                fencing_token,
            ),
        )
        db.commit()
        return cursor.rowcount == 1
    finally:
        db.close()


def mark_result_received(
    *,
    work_item_id: int,
    owner: str,
    fencing_token: int,
    now: datetime,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    """Persist that model output exists while business projection is still pending."""
    current_time = _utc(now)
    db = dbmod.get_db()
    try:
        cursor = db.execute(
            """UPDATE codex_work_items
               SET status = 'result_received', error_class = NULL,
                   terminal_reason = NULL, pid = NULL,
                   heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
               WHERE id = ? AND lease_owner = ? AND fencing_token = ?
                 AND status NOT IN ('completed', 'completed_with_failures', 'failed', 'timed_out', 'cancelled')""",
            (
                _iso(current_time),
                _iso(current_time + timedelta(seconds=lease_seconds)),
                _iso(current_time),
                work_item_id,
                owner,
                fencing_token,
            ),
        )
        db.commit()
        return cursor.rowcount == 1
    finally:
        db.close()


def mark_terminal(
    *,
    work_item_id: int,
    owner: str,
    fencing_token: int,
    status: str,
    reason: str | None,
    now: datetime,
) -> bool:
    """Commit one terminal state only from the current fenced owner."""
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"status is not terminal: {status}")
    current_time = _utc(now)
    terminal_placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    db = dbmod.get_db()
    try:
        cursor = db.execute(
            f"""UPDATE codex_work_items
                SET status = ?, terminal_reason = ?, ended_at = ?,
                    lease_owner = NULL, lease_expires_at = NULL,
                    heartbeat_at = ?, pid = NULL, updated_at = ?
                WHERE id = ? AND lease_owner = ? AND fencing_token = ?
                  AND status NOT IN ({terminal_placeholders})""",
            (
                status,
                reason,
                _iso(current_time),
                _iso(current_time),
                _iso(current_time),
                work_item_id,
                owner,
                fencing_token,
                *sorted(TERMINAL_STATUSES),
            ),
        )
        db.commit()
        return cursor.rowcount == 1
    finally:
        db.close()
