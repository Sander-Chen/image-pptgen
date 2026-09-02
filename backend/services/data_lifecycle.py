"""Archive, recycle-bin, restore, and purge helpers for Data Management."""

from __future__ import annotations

import db as dbmod
from backend.services import codex_evidence_lifecycle
from backend.services import folders
from backend.services.historical_export import write_historical_export

DATA_TABLES = {
    "deck": "decks",
    "requirement": "requirements",
    "color": "colors",
}


def _table_for(entity_type: str) -> str:
    table = DATA_TABLES.get(entity_type)
    if not table:
        raise ValueError("entity_type must be deck, requirement, or color")
    return table


def get_data_entity(entity_type: str, entity_id: int) -> dict | None:
    table = _table_for(entity_type)
    db = dbmod.get_db()
    try:
        row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    finally:
        db.close()
    if not row:
        return None
    record = dict(row)
    record["folder_ids"] = folders.get_entity_folder_ids(entity_type, entity_id)
    return record


def list_data_entities(
    entity_type: str,
    status: str = "active",
    folder_id: int | None = None,
) -> list[dict]:
    table = _table_for(entity_type)
    clauses = ["COALESCE(lifecycle_status, 'active') = ?"]
    params: list[object] = [status]
    if folder_id:
        clauses.append(
            """id IN (
                SELECT entity_id FROM folder_memberships
                WHERE entity_type = ? AND folder_id = ?
            )"""
        )
        params.extend([entity_type, folder_id])
    where = " AND ".join(clauses)
    db = dbmod.get_db()
    try:
        rows = db.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY id", params).fetchall()
    finally:
        db.close()
    return folders.attach_folder_ids(entity_type, [dict(row) for row in rows])


def _set_lifecycle(entity_type: str, entity_id: int, status: str) -> dict | None:
    table = _table_for(entity_type)
    current = get_data_entity(entity_type, entity_id)
    if not current:
        return None
    previous = current.get("lifecycle_status") or "active"
    db = dbmod.get_db()
    try:
        if status == "archived":
            db.execute(
                f"""UPDATE {table}
                    SET lifecycle_status = 'archived',
                        previous_lifecycle_status = ?,
                        archived_at = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ?""",
                (previous, entity_id),
            )
        elif status == "recycle_bin":
            db.execute(
                f"""UPDATE {table}
                    SET lifecycle_status = 'recycle_bin',
                        previous_lifecycle_status = ?,
                        deleted_at = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ?""",
                (previous, entity_id),
            )
        elif status == "active":
            db.execute(
                f"""UPDATE {table}
                    SET lifecycle_status = 'active',
                        previous_lifecycle_status = ?,
                        updated_at = datetime('now')
                    WHERE id = ?""",
                (previous, entity_id),
            )
        elif status == "purged":
            db.execute(
                f"""UPDATE {table}
                    SET lifecycle_status = 'purged',
                        previous_lifecycle_status = ?,
                        purged_at = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ?""",
                (previous, entity_id),
            )
        else:
            raise ValueError("Unsupported lifecycle status")
        db.commit()
    finally:
        db.close()
    return get_data_entity(entity_type, entity_id)


def archive_data_entity(entity_type: str, entity_id: int) -> dict | None:
    return _set_lifecycle(entity_type, entity_id, "archived")


def move_to_recycle_bin(entity_type: str, entity_id: int) -> dict | None:
    if entity_type == "deck":
        codex_evidence_lifecycle.protect_deck_raw_evidence(entity_id)
    return _set_lifecycle(entity_type, entity_id, "recycle_bin")


def restore_data_entity(entity_type: str, entity_id: int) -> dict | None:
    if entity_type == "deck":
        codex_evidence_lifecycle.protect_deck_raw_evidence(entity_id)
    return _set_lifecycle(entity_type, entity_id, "active")


def _export_payload(entity_type: str, record: dict) -> dict:
    if entity_type != "deck":
        return {}
    db = dbmod.get_db()
    try:
        slides = db.execute(
            "SELECT * FROM slides WHERE deck_id = ? ORDER BY position",
            (record["id"],),
        ).fetchall()
    finally:
        db.close()
    return {"slides": [dict(row) for row in slides]}


def force_delete_data_entity(entity_type: str, entity_id: int) -> dict | None:
    record = get_data_entity(entity_type, entity_id)
    if not record:
        return None
    export_path = write_historical_export(entity_type, record, _export_payload(entity_type, record))
    if entity_type == "deck":
        codex_evidence_lifecycle.purge_deck_raw_evidence_and_mark(
            entity_id,
            record.get("lifecycle_status") or "active",
        )
        purged = get_data_entity(entity_type, entity_id)
    else:
        purged = _set_lifecycle(entity_type, entity_id, "purged")
    if purged:
        purged["historical_export_path"] = export_path
    return purged
