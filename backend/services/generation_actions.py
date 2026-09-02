"""Retry, continue, and force-regenerate actions for generation history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import db as dbmod
from backend.domain import status as run_status
from backend.services.scheduler import pump_batch_queue as default_pump_batch_queue


RETRYABLE_CLASSES = {"empty_image_response", "timeout"}
TERMINAL_CLASSES = {"bad_request", "configuration"}
ACTION_NAMES = {"continue", "retry", "force", "force_regenerate"}
SCOPES = {"batch", "run", "slide", "image"}
FORCE_MODES = {"overwrite_current", "new_run", "new_batch"}


@dataclass
class GenerationActionError(Exception):
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def apply_generation_action(
    payload: dict[str, Any],
    *,
    db_path: str,
    pump_queue=default_pump_batch_queue,
) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip()
    scope = str(payload.get("scope") or "").strip()
    target_id = _int(payload.get("target_id") or payload.get("id"))
    if action not in ACTION_NAMES:
        raise GenerationActionError("action must be retry or force_regenerate", 400)
    if action == "continue":
        raise GenerationActionError("Legacy Continue is history-only. Use Retry for targets without a displayable active artifact or Force for explicit reruns.", 400)
    if scope not in SCOPES:
        raise GenerationActionError("scope must be batch, run, slide, or image", 400)
    if target_id is None:
        raise GenerationActionError("target_id is required", 400)
    if action == "force":
        action = "force_regenerate"
    force_mode = str(payload.get("force_mode") or payload.get("mode") or "").strip() or None
    if action == "force_regenerate":
        if not force_mode:
            force_mode = "new_batch" if scope == "batch" else "overwrite_current"
        if force_mode not in FORCE_MODES:
            raise GenerationActionError("force_mode must be overwrite_current, new_run, or new_batch", 400)
        if scope == "batch" and force_mode != "new_batch":
            raise GenerationActionError("Batch Force creates a new Batch; use force_mode=new_batch", 400)
        if scope != "batch" and force_mode == "new_batch":
            raise GenerationActionError("force_mode=new_batch is only available at Batch scope", 400)
    elif force_mode:
        raise GenerationActionError("force_mode is only valid for Force actions", 400)

    if scope == "batch":
        runs = _runs_for_batch(target_id)
        if not runs:
            raise GenerationActionError("Batch not found or has no runs", 404)
        source_batch_id = target_id
    elif scope == "run":
        run = dbmod.get_run(target_id)
        if not run:
            raise GenerationActionError("Run not found", 404)
        runs = [run]
        source_batch_id = run.get("batch_id")
    else:
        slide = _get_run_slide(target_id)
        if not slide:
            raise GenerationActionError("Run slide not found", 404)
        run = dbmod.get_run(slide["run_id"])
        if not run:
            raise GenerationActionError("Run not found", 404)
        runs = [run]
        source_batch_id = run.get("batch_id")

    created_run_ids: list[int] = []
    affected_slide_ids: list[int] = []
    skipped: list[dict[str, Any]] = []
    new_run_count = 0
    created_batch_id: int | None = None

    if action == "retry" and _target_has_any_displayable_artifact(runs, scope, target_id):
        raise GenerationActionError("No eligible targets: target already has a displayable active artifact", 409)

    if action == "force_regenerate" and scope == "batch" and force_mode == "new_batch":
        created_batch_id, created_run_ids, affected_slide_ids = _create_force_new_batch(
            source_batch_id=target_id,
            source_runs=runs,
            source_target_id=target_id,
        )
        dbmod.record_generation_history(
            action=action,
            scope="batch",
            target_id=target_id,
            source_batch_id=source_batch_id,
            created_batch_id=created_batch_id,
            status="queued",
            force_mode=force_mode,
            summary="Force Batch created a new batch rerun.",
            metadata={"source_run_ids": [run["id"] for run in runs]},
        )
        launched = pump_queue(int(created_batch_id), db_path)
        dbmod.update_batch_statuses()
        return {
            "ok": True,
            "action": action,
            "scope": scope,
            "target_id": target_id,
            "force_mode": force_mode,
            "source_batch_id": source_batch_id,
            "created_batch_id": created_batch_id,
            "created_run_ids": created_run_ids,
            "affected_slide_ids": affected_slide_ids,
            "launched_run_ids": launched,
            "skipped": skipped,
        }

    for run in runs:
        selected_slides = _slides_for_action(run, action, scope, target_id, force_mode=force_mode)
        if not selected_slides:
            reason = "displayable_active_artifact_exists" if action == "retry" else "no_matching_targets"
            skipped.append({"run_id": run["id"], "reason": reason})
            continue
        existing_child = _existing_action_child(
            run["id"],
            action=action,
            scope=scope,
            source_target_id=target_id,
            force_mode=force_mode,
        )
        if existing_child and existing_child.get("status") in {run_status.PENDING, run_status.QUEUED, run_status.RUNNING}:
            created_run_ids.append(existing_child["id"])
            affected_slide_ids.extend(slide["id"] for slide in selected_slides)
            skipped.append({"run_id": run["id"], "reason": "existing_follow_up", "existing_run_id": existing_child["id"]})
            continue
        if action == "retry" and not _is_retryable(run, selected_slides):
            skipped.append({"run_id": run["id"], "reason": "not_retryable", "retry_signal": retry_signal(run)})
            continue
        if action in {"continue", "retry"} and run.get("status") == run_status.RUNNING:
            skipped.append({"run_id": run["id"], "reason": "running"})
            continue
        new_run_id = _clone_run_with_slides(
            run,
            selected_slides,
            action=action,
            scope=scope,
            source_target_id=target_id,
            force_mode=force_mode,
        )
        new_run_count += 1
        created_run_ids.append(new_run_id)
        affected_slide_ids.extend(slide["id"] for slide in selected_slides)
        _record_action_history_for_slides(
            action=action,
            scope=scope,
            target_id=target_id,
            force_mode=force_mode,
            run=run,
            created_run_id=new_run_id,
            selected_slides=selected_slides,
        )

    if not created_run_ids:
        if action == "retry" and any(item["reason"] == "displayable_active_artifact_exists" for item in skipped):
            raise GenerationActionError("No eligible targets: target already has a displayable active artifact", 409)
        raise GenerationActionError("No eligible targets for this action", 409)

    if source_batch_id and new_run_count:
        _increment_batch_total(int(source_batch_id), new_run_count)
        launched = pump_queue(int(source_batch_id), db_path)
    else:
        launched = []
    dbmod.update_batch_statuses()
    return {
        "ok": True,
        "action": action,
        "scope": scope,
        "target_id": target_id,
        "force_mode": force_mode,
        "source_batch_id": source_batch_id,
        "created_batch_id": created_batch_id,
        "created_run_ids": created_run_ids,
        "affected_slide_ids": affected_slide_ids,
        "launched_run_ids": launched,
        "skipped": skipped,
    }


def due_retry_runs() -> list[dict[str, Any]]:
    """Return retryable terminal runs. Kept simple so polling can trigger recovery."""
    return [
        run for run in dbmod.list_runs()
        if run.get("status") in {run_status.FAILED, run_status.TIMED_OUT}
        and retry_signal(run) == "retryable_provider_or_timeout"
        and _retry_due(run)
    ]


def run_due_retry_poll(*, db_path: str, pump_queue=default_pump_batch_queue) -> dict[str, Any]:
    created_run_ids: list[int] = []
    skipped: list[dict[str, Any]] = []
    for run in due_retry_runs():
        slides = _unfinished_slides(run["id"])
        if not slides:
            skipped.append({"run_id": run["id"], "reason": "no_unfinished_slides"})
            continue
        attempts = _action_attempt_count(run, "auto_retry")
        if attempts >= 3:
            skipped.append({"run_id": run["id"], "reason": "retry_cap_reached"})
            continue
        existing_child = _existing_action_child(run["id"], action="auto_retry", scope="run", source_target_id=run["id"])
        if existing_child and existing_child.get("status") in {run_status.PENDING, run_status.QUEUED, run_status.RUNNING}:
            skipped.append({"run_id": run["id"], "reason": "existing_auto_retry", "existing_run_id": existing_child["id"]})
            continue
        if existing_child and existing_child.get("status") == run_status.COMPLETED:
            skipped.append({"run_id": run["id"], "reason": "auto_retry_already_succeeded", "existing_run_id": existing_child["id"]})
            continue
        new_run_id = _clone_run_with_slides(run, slides, action="auto_retry", scope="run", source_target_id=run["id"])
        _record_source_attempt(run["id"], "auto_retry")
        created_run_ids.append(new_run_id)
        if run.get("batch_id"):
            _increment_batch_total(int(run["batch_id"]), 1)
            pump_queue(int(run["batch_id"]), db_path)
    dbmod.update_batch_statuses()
    return {"created_run_ids": created_run_ids, "skipped": skipped}


def retry_signal(run: dict[str, Any]) -> str:
    error_class = classify_error(run)
    if error_class in RETRYABLE_CLASSES:
        return "retryable_provider_or_timeout"
    if error_class in TERMINAL_CLASSES:
        return "terminal_request_or_config"
    return "needs_review"


def classify_error(run: dict[str, Any]) -> str:
    if run.get("status") == run_status.TIMED_OUT:
        return "timeout"
    message = str(run.get("error_message") or "").lower()
    if "no inline image bytes" in message or "empty provider text" in message or "empty image" in message:
        return "empty_image_response"
    if "400" in message or "bad request" in message:
        return "bad_request"
    if any(token in message for token in ("model", "profile", "credential", "key")):
        return "configuration"
    return "unknown"


def _runs_for_batch(batch_id: int) -> list[dict[str, Any]]:
    batch = dbmod.get_batch_detail(batch_id)
    return batch.get("runs", []) if batch else []


def _create_force_new_batch(
    *,
    source_batch_id: int,
    source_runs: list[dict[str, Any]],
    source_target_id: int,
) -> tuple[int, list[int], list[int]]:
    source_batch = dbmod.get_batch(source_batch_id)
    if not source_batch:
        raise GenerationActionError("Batch not found", 404)
    requirements = source_batch.get("requirements") or []
    colors = source_batch.get("colors") or []
    requirement_ids = [item["id"] for item in requirements] or [run["requirement_id"] for run in source_runs]
    color_ids = [item["id"] for item in colors] or [run["color_id"] for run in source_runs]
    new_batch_id = dbmod.create_batch(
        source_batch["deck_id"],
        source_batch["config_id"],
        requirement_ids,
        color_ids,
        designer_prompt_id=source_batch.get("designer_prompt_id"),
        html_prompt_id=source_batch.get("html_prompt_id"),
        total_runs=len(source_runs),
    )
    created_run_ids: list[int] = []
    affected_slide_ids: list[int] = []
    for run in source_runs:
        slides = dbmod.list_run_slides(run["id"])
        if not slides:
            continue
        new_run_id = _clone_run_with_slides(
            run,
            slides,
            action="force_regenerate",
            scope="batch",
            source_target_id=source_target_id,
            force_mode="new_batch",
            target_batch_id=new_batch_id,
        )
        created_run_ids.append(new_run_id)
        affected_slide_ids.extend(slide["id"] for slide in slides)
    return new_batch_id, created_run_ids, affected_slide_ids


def _record_action_history_for_slides(
    *,
    action: str,
    scope: str,
    target_id: int,
    force_mode: str | None,
    run: dict[str, Any],
    created_run_id: int,
    selected_slides: list[dict[str, Any]],
) -> None:
    created_run = dbmod.get_run(created_run_id) or {}
    created_artifacts = _parse_json(created_run.get("stage_artifacts"))
    lineage = (
        created_artifacts.get("lineage")
        if isinstance(created_artifacts, dict)
        and isinstance(created_artifacts.get("lineage"), dict)
        else {}
    )
    for slide in selected_slides:
        target_run_slide_id = slide["id"]
        dbmod.record_generation_history(
            action=action,
            scope=scope,
            target_id=target_id,
            target_run_slide_id=target_run_slide_id,
            artifact_run_slide_id=None,
            source_run_id=run["id"],
            source_batch_id=run.get("batch_id"),
            created_run_id=created_run_id,
            status="queued",
            force_mode=force_mode,
            summary=f"{action.replace('_', ' ')} queued for Slide {slide.get('position')}.",
            metadata={**lineage, "source_run_slide_id": slide["id"]},
        )


def _get_run_slide(run_slide_id: int) -> dict[str, Any] | None:
    conn = dbmod.get_db()
    row = conn.execute(
        """SELECT rs.*, s.title AS slide_title, s.content AS slide_content
           FROM run_slides rs
           JOIN slides s ON rs.slide_id = s.id
           WHERE rs.id = ?""",
        (run_slide_id,),
    ).fetchone()
    conn.close()
    return dbmod.row_to_dict(row)


def _slides_for_action(
    run: dict[str, Any],
    action: str,
    scope: str,
    target_id: int,
    *,
    force_mode: str | None = None,
) -> list[dict[str, Any]]:
    slides = dbmod.list_run_slides(run["id"])
    if scope in {"slide", "image"}:
        slides = [slide for slide in slides if slide["id"] == target_id]
    if action == "force_regenerate":
        return slides
    return [slide for slide in slides if not _has_displayable_active_artifact(slide)]


def _target_has_any_displayable_artifact(runs: list[dict[str, Any]], scope: str, target_id: int) -> bool:
    for run in runs:
        slides = dbmod.list_run_slides(run["id"])
        if scope in {"slide", "image"}:
            slides = [slide for slide in slides if slide["id"] == target_id]
        if any(_has_displayable_active_artifact(slide) for slide in slides):
            return True
    return False


def _has_displayable_active_artifact(slide: dict[str, Any]) -> bool:
    active = slide.get("active_version")
    if active:
        return bool(active.get("final_image_path") or active.get("screenshot_path") or active.get("html_path") or active.get("clean_html"))
    return bool(
        slide.get("status") == run_status.COMPLETED
        and (slide.get("final_image_path") or slide.get("screenshot_path") or slide.get("html_path") or slide.get("clean_html"))
    )


def _unfinished_slides(run_id: int) -> list[dict[str, Any]]:
    return [slide for slide in dbmod.list_run_slides(run_id) if slide.get("status") != run_status.COMPLETED]


def _is_retryable(run: dict[str, Any], slides: list[dict[str, Any]]) -> bool:
    if retry_signal(run) == "retryable_provider_or_timeout":
        return True
    return any("empty" in str(slide.get("error_message") or "").lower() for slide in slides)


def _clone_run_with_slides(
    run: dict[str, Any],
    slides: list[dict[str, Any]],
    *,
    action: str,
    scope: str,
    source_target_id: int,
    force_mode: str | None = None,
    target_batch_id: int | None = None,
) -> int:
    lineage = _lineage(run, action, scope, source_target_id, slides, force_mode=force_mode)
    run_id = dbmod.create_run(
        run["deck_id"],
        run["requirement_id"],
        run["color_id"],
        run["config_id"],
        batch_id=target_batch_id if target_batch_id is not None else run.get("batch_id"),
        engine=run.get("engine"),
        strategy=run.get("strategy"),
        route_metadata=run.get("route_metadata"),
    )
    update_fields: dict[str, Any] = {
        "stage_artifacts": json.dumps({"lineage": lineage}, ensure_ascii=False),
        "model_call_metadata": json.dumps({"action": action, "source_run_id": run["id"]}, ensure_ascii=False),
    }
    for key in ("designer_prompt_id", "html_prompt_id", "auto_candidate_index"):
        if run.get(key) is not None:
            update_fields[key] = run[key]
    dbmod.update_run(run_id, **update_fields)
    for slide in slides:
        new_slide_id = dbmod.create_run_slide(
            run_id,
            slide["slide_id"],
            slide["position"],
            slide_type=slide.get("slide_type") or "content",
        )
        dbmod.update_run_slide(
            new_slide_id,
            stage_artifacts=json.dumps({"lineage": {**lineage, "source_run_slide_id": slide["id"]}}, ensure_ascii=False),
        )
    return run_id


def _lineage(
    run: dict[str, Any],
    action: str,
    scope: str,
    source_target_id: int,
    slides: list[dict[str, Any]],
    *,
    force_mode: str | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "scope": scope,
        "force_mode": force_mode,
        "source_target_id": source_target_id,
        "source_run_id": run["id"],
        "source_batch_id": run.get("batch_id"),
        "source_run_slide_ids": [slide["id"] for slide in slides],
        "version_index": _next_version_index(run["id"]),
        "retention_policy": {"mode": "preserve_source", "cleanup": "manual_future_task"},
    }


def _next_version_index(source_run_id: int) -> int:
    conn = dbmod.get_db()
    rows = conn.execute(
        "SELECT stage_artifacts FROM runs WHERE id = ? OR stage_artifacts LIKE ?",
        (source_run_id, f'%"source_run_id": {source_run_id}%'),
    ).fetchall()
    conn.close()
    highest = 1
    for row in rows:
        try:
            value = json.loads(row["stage_artifacts"]) if row["stage_artifacts"] else {}
        except (TypeError, json.JSONDecodeError):
            continue
        lineage = value.get("lineage") if isinstance(value, dict) else None
        if not isinstance(lineage, dict) or lineage.get("source_run_id") != source_run_id:
            continue
        version_index = lineage.get("version_index")
        if isinstance(version_index, int) and not isinstance(version_index, bool):
            highest = max(highest, version_index)
    return highest + 1


def _increment_batch_total(batch_id: int, amount: int) -> None:
    conn = dbmod.get_db()
    conn.execute("UPDATE batches SET total_runs = COALESCE(total_runs, 0) + ? WHERE id = ?", (amount, batch_id))
    conn.commit()
    conn.close()


def _existing_action_child(
    source_run_id: int,
    *,
    action: str,
    scope: str,
    source_target_id: int,
    force_mode: str | None = None,
) -> dict[str, Any] | None:
    conn = dbmod.get_db()
    rows = conn.execute(
        "SELECT * FROM runs WHERE id != ? AND stage_artifacts LIKE ? ORDER BY id DESC",
        (source_run_id, f'%"source_run_id": {source_run_id}%'),
    ).fetchall()
    conn.close()
    for row in rows:
        run = dbmod.row_to_dict(row)
        value = _parse_json(run.get("stage_artifacts"))
        lineage = value.get("lineage") if isinstance(value.get("lineage"), dict) else None
        if (
            isinstance(lineage, dict)
            and lineage.get("source_run_id") == source_run_id
            and lineage.get("action") == action
            and lineage.get("scope") == scope
            and lineage.get("source_target_id") == source_target_id
            and (force_mode is None or lineage.get("force_mode") == force_mode)
        ):
            return run
    return None


def _record_source_attempt(run_id: int, action: str) -> None:
    run = dbmod.get_run(run_id)
    artifacts = _parse_json(run.get("stage_artifacts") if run else None)
    attempts = artifacts.setdefault("action_attempts", {})
    attempts[action] = int(attempts.get(action) or 0) + 1
    dbmod.update_run(run_id, stage_artifacts=json.dumps(artifacts, ensure_ascii=False))


def _action_attempt_count(run: dict[str, Any], action: str) -> int:
    artifacts = _parse_json(run.get("stage_artifacts"))
    return int((artifacts.get("action_attempts") or {}).get(action) or 0)


def _retry_due(run: dict[str, Any]) -> bool:
    attempts = _action_attempt_count(run, "auto_retry")
    if attempts >= 3:
        return False
    delay = [1, 5, 10][attempts]
    base = run.get("completed_at") or run.get("created_at")
    if not base:
        return False
    conn = dbmod.get_db()
    row = conn.execute(
        "SELECT datetime(?) <= datetime('now', ?) AS due",
        (base, f"-{delay} minutes"),
    ).fetchone()
    conn.close()
    return bool(row and row["due"])


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
