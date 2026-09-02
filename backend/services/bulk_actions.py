"""Bulk action coordinator for Data, Prompt, and History pages."""

from __future__ import annotations

import db as dbmod
from backend.services import codex_evidence_lifecycle, data_lifecycle, defaults, folders

DATA_TYPES = {"deck", "requirement", "color"}


def _delete_run(run_id: int) -> dict:
    run = dbmod.get_run(run_id)
    if not run:
        raise ValueError("Run not found")
    if run["status"] == "running":
        raise ValueError("Cannot delete a running pipeline")
    if not codex_evidence_lifecycle.delete_run_with_raw_evidence(run_id):
        raise ValueError("Run not found")
    return {"id": run_id}


def _delete_batch(batch_id: int) -> dict:
    batch = dbmod.get_batch(batch_id)
    if not batch:
        raise ValueError("Batch not found")
    db = dbmod.get_db()
    try:
        running = db.execute(
            "SELECT COUNT(*) AS count FROM runs WHERE batch_id = ? AND status = 'running'",
            (batch_id,),
        ).fetchone()
    finally:
        db.close()
    if batch["status"] == "running" or running["count"]:
        raise ValueError("Cannot delete a running batch")
    if not codex_evidence_lifecycle.delete_batch_with_raw_evidence(batch_id):
        raise ValueError("Batch not found")
    return {"id": batch_id}


def _apply_one(entity_type: str, action: str, entity_id: int, folder_ids: list[int]) -> dict:
    if entity_type in DATA_TYPES:
        if action == "archive":
            record = data_lifecycle.archive_data_entity(entity_type, entity_id)
        elif action == "delete":
            record = data_lifecycle.move_to_recycle_bin(entity_type, entity_id)
        elif action == "restore":
            record = data_lifecycle.restore_data_entity(entity_type, entity_id)
        elif action == "force_delete":
            record = data_lifecycle.force_delete_data_entity(entity_type, entity_id)
        elif action == "move_to_folder":
            folder_ids = folders.assign_entity_folders(entity_type, entity_id, folder_ids)
            return {"id": entity_id, "folder_ids": folder_ids}
        else:
            raise ValueError("Unsupported data bulk action")
        if not record:
            raise ValueError(f"{entity_type} not found")
        return record

    if entity_type == "prompt":
        prompt = dbmod.get_prompt(entity_id)
        if not prompt:
            raise ValueError("Prompt not found")
        if action == "archive":
            defaults.ensure_prompt_archive_allowed(entity_id)
            if not dbmod.delete_prompt(entity_id):
                raise ValueError("Prompt not found")
            defaults.promote_default_prompt_if_needed(prompt["agent_type"])
            return dbmod.get_prompt(entity_id)
        if action == "restore":
            dbmod.update_prompt(entity_id, lifecycle_status="active", archived_at=None)
            defaults.promote_default_prompt_if_needed(prompt["agent_type"])
            return dbmod.get_prompt(entity_id)
        if action == "move_to_folder":
            folder_ids = folders.assign_entity_folders("prompt", entity_id, folder_ids)
            return {"id": entity_id, "folder_ids": folder_ids}
        raise ValueError("Unsupported prompt bulk action")

    if entity_type == "run":
        if action != "delete":
            raise ValueError("Unsupported history bulk action")
        return _delete_run(entity_id)

    if entity_type == "batch":
        if action != "delete":
            raise ValueError("Unsupported history bulk action")
        return _delete_batch(entity_id)

    raise ValueError("entity_type must be deck, requirement, color, prompt, run, or batch")


def apply_bulk_action(data: dict) -> tuple[list[dict], bool]:
    entity_type = data.get("entity_type")
    action = data.get("action")
    ids = data.get("ids")
    if not entity_type or not action or not isinstance(ids, list) or not ids:
        raise ValueError("entity_type, action, and ids are required")
    folder_ids = data.get("folder_ids") or []
    results: list[dict] = []
    has_error = False
    for raw_id in ids:
        entity_id = int(raw_id)
        try:
            payload = _apply_one(entity_type, action, entity_id, folder_ids)
            results.append({"id": entity_id, "status": "ok", "record": payload})
        except ValueError as exc:
            has_error = True
            results.append({"id": entity_id, "status": "error", "error": str(exc)})
    return results, has_error
