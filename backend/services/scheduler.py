"""Batch queue scheduling and background run launch helpers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import db as dbmod
from backend.domain import status as run_status
from backend.services import system_settings
from pipeline import run_pipeline_from_db

log = logging.getLogger("ppt-server")

RunLauncher = Callable[[int, str], object]


def _run_queue_limit() -> int:
    return max(1, system_settings.get_run_queue_concurrency())


def launch_run_thread(run_id: int, db_path: str):
    dbmod.mark_created_run_generation_history_running(run_id)
    thread = threading.Thread(
        target=run_pipeline_from_db,
        args=(run_id, db_path),
        daemon=True,
        name=f"pipeline-run-{run_id}",
    )
    thread.start()
    log.info("Launched background thread for run %d", run_id)
    return thread


def run_pipeline_and_pump(run_id: int, db_path: str) -> None:
    run_pipeline_from_db(run_id, db_path)
    run = dbmod.get_run(run_id)
    if run and run.get("batch_id"):
        pump_batch_queue(run["batch_id"], db_path)


def launch_run_for_batch(run_id: int, db_path: str):
    dbmod.update_run(run_id, status=run_status.RUNNING)
    dbmod.mark_created_run_generation_history_running(run_id)
    thread = threading.Thread(
        target=run_pipeline_and_pump,
        args=(run_id, db_path),
        daemon=True,
        name=f"pipeline-run-{run_id}",
    )
    thread.start()
    log.info("Launched background thread for run %d", run_id)
    return thread


def pump_batch_queue(
    batch_id: int,
    db_path: str,
    max_concurrent_runs: int | None = None,
    run_launcher: RunLauncher = launch_run_for_batch,
) -> list[int]:
    _ = max_concurrent_runs
    limit = _run_queue_limit()
    running_count = dbmod.count_running_runs()
    available_slots = max(0, limit - running_count)
    if available_slots == 0:
        return []

    launched: list[int] = []
    for run in dbmod.claim_pending_runs_for_batch(
        batch_id,
        available_slots,
        max_running_global=limit,
    ):
        run_launcher(run["id"], db_path)
        launched.append(run["id"])
    if launched:
        dbmod.update_batch_statuses()
        log.info("Launched %d queued run(s) for batch %d", len(launched), batch_id)
    return launched


def launch_batch_runs(
    run_ids: list[int],
    db_path: str,
    max_concurrent_runs: int,
    run_launcher: RunLauncher = launch_run_for_batch,
) -> list[int]:
    if not run_ids:
        return []
    first_run = dbmod.get_run(run_ids[0])
    batch_id = first_run.get("batch_id") if first_run else None
    if not batch_id:
        _ = max_concurrent_runs
        available_slots = max(0, _run_queue_limit() - dbmod.count_running_runs())
        launched = []
        for run_id in run_ids[:available_slots]:
            run_launcher(run_id, db_path)
            launched.append(run_id)
        return launched
    return pump_batch_queue(batch_id, db_path, max_concurrent_runs, run_launcher=run_launcher)
