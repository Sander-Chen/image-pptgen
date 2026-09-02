"""Folder CRUD and many-to-many entity assignment helpers."""

from __future__ import annotations

import db as dbmod

VALID_SCOPES = {"deck", "requirement", "color", "prompt"}
MAX_DEPTH = 3


def _row_to_dict(row):
    return dict(row) if row else None


def validate_scope(scope: str) -> None:
    if scope not in VALID_SCOPES:
        raise ValueError("scope must be deck, requirement, color, or prompt")


def folder_depth(parent_id: int | None) -> int:
    if not parent_id:
        return 1
    db = dbmod.get_db()
    depth = 1
    seen = set()
    current = parent_id
    try:
        while current:
            if current in seen:
                raise ValueError("Folder cycle detected")
            seen.add(current)
            row = db.execute("SELECT parent_id FROM folders WHERE id = ?", (current,)).fetchone()
            if not row:
                raise ValueError("Parent folder not found")
            depth += 1
            current = row["parent_id"]
    finally:
        db.close()
    return depth


def list_folders(scope: str | None = None) -> list[dict]:
    clauses = []
    params = []
    if scope:
        validate_scope(scope)
        clauses.append("scope = ?")
        params.append(scope)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    db = dbmod.get_db()
    try:
        rows = db.execute(
            f"SELECT * FROM folders {where} ORDER BY scope, parent_id IS NOT NULL, parent_id, name",
            params,
        ).fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def get_folder(folder_id: int) -> dict | None:
    db = dbmod.get_db()
    try:
        row = db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
    finally:
        db.close()
    return _row_to_dict(row)


def create_folder(scope: str, name: str, parent_id: int | None = None) -> dict:
    validate_scope(scope)
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("name is required")
    if folder_depth(parent_id) > MAX_DEPTH:
        raise ValueError("Folders support a maximum depth of 3")
    parent = get_folder(parent_id) if parent_id else None
    if parent and parent["scope"] != scope:
        raise ValueError("Parent folder scope must match child scope")

    db = dbmod.get_db()
    try:
        cur = db.execute(
            """INSERT INTO folders (scope, name, parent_id, updated_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (scope, clean_name, parent_id),
        )
        db.commit()
        folder_id = cur.lastrowid
    finally:
        db.close()
    return get_folder(folder_id)


def update_folder(folder_id: int, data: dict[str, object]) -> dict | None:
    folder = get_folder(folder_id)
    if not folder:
        return None
    fields: dict[str, object] = {}
    if "name" in data:
        clean_name = str(data["name"] or "").strip()
        if not clean_name:
            raise ValueError("name is required")
        fields["name"] = clean_name
    if "parent_id" in data:
        parent_id = data["parent_id"]
        parent_id = int(parent_id) if parent_id is not None else None
        if parent_id == folder_id:
            raise ValueError("Folder cannot be its own parent")
        if folder_depth(parent_id) > MAX_DEPTH:
            raise ValueError("Folders support a maximum depth of 3")
        parent = get_folder(parent_id) if parent_id else None
        if parent and parent["scope"] != folder["scope"]:
            raise ValueError("Parent folder scope must match child scope")
        fields["parent_id"] = parent_id
    if not fields:
        return folder

    assignments = ", ".join([f"{key} = ?" for key in fields])
    params = list(fields.values()) + [folder_id]
    db = dbmod.get_db()
    try:
        db.execute(
            f"UPDATE folders SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            params,
        )
        db.commit()
    finally:
        db.close()
    return get_folder(folder_id)


def get_entity_folder_ids(entity_type: str, entity_id: int) -> list[int]:
    db = dbmod.get_db()
    try:
        rows = db.execute(
            """SELECT folder_id FROM folder_memberships
               WHERE entity_type = ? AND entity_id = ?
               ORDER BY folder_id""",
            (entity_type, entity_id),
        ).fetchall()
    finally:
        db.close()
    return [row["folder_id"] for row in rows]


def assign_entity_folders(entity_type: str, entity_id: int, folder_ids: list[int]) -> list[int]:
    validate_scope(entity_type)
    clean_ids = sorted({int(folder_id) for folder_id in folder_ids})
    db = dbmod.get_db()
    try:
        for folder_id in clean_ids:
            row = db.execute("SELECT scope FROM folders WHERE id = ?", (folder_id,)).fetchone()
            if not row:
                raise ValueError(f"Folder {folder_id} not found")
            if row["scope"] != entity_type:
                raise ValueError("Folder scope must match entity type")
        db.execute(
            "DELETE FROM folder_memberships WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        for folder_id in clean_ids:
            db.execute(
                """INSERT OR IGNORE INTO folder_memberships
                   (folder_id, entity_type, entity_id)
                   VALUES (?, ?, ?)""",
                (folder_id, entity_type, entity_id),
            )
        db.commit()
    finally:
        db.close()
    return get_entity_folder_ids(entity_type, entity_id)


def attach_folder_ids(entity_type: str, records: list[dict]) -> list[dict]:
    for record in records:
        record["folder_ids"] = get_entity_folder_ids(entity_type, record["id"])
    return records
