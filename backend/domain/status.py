"""Shared status constants for batches, runs, and run slides."""

QUEUED = "queued"
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
COMPLETED_WITH_FAILURES = "completed_with_failures"
FAILED = "failed"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"

ACTIVE_STATUSES = {QUEUED, PENDING, RUNNING}
TERMINAL_STATUSES = {COMPLETED, COMPLETED_WITH_FAILURES, FAILED, TIMED_OUT, CANCELLED}


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL_STATUSES
