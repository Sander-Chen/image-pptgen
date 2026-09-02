"""Role-scoped system variables and prompt reference discovery."""

from __future__ import annotations

import re

import db as dbmod

VARIABLE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
VALID_STATUSES = {"active", "disabled"}
VALID_AGENT_TYPES = {
    "designer",
    "html_agent",
    "image_cover",
    "image_designer",
    "image_generator",
    "evaluation_visual_qa",
    "xml_cleanup",
}
VALID_AGENT_TYPES.update(dbmod.LEGACY_VARIABLE_SCOPE_ALIASES)


def _row_to_dict(row):
    return dict(row) if row else None


def _validate_agent_type(agent_type: str) -> None:
    if agent_type not in VALID_AGENT_TYPES:
        raise ValueError(f"agent_type must be one of: {', '.join(sorted(VALID_AGENT_TYPES))}")


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError("status must be active or disabled")


def list_system_variables(
    agent_type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    if agent_type:
        _validate_agent_type(agent_type)
        agent_type = dbmod.canonical_system_variable_agent_type(agent_type)
        clauses.append("agent_type = ?")
        params.append(agent_type)
    if status:
        _validate_status(status)
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    db = dbmod.get_db()
    try:
        rows = db.execute(
            f"SELECT * FROM system_variables {where} ORDER BY agent_type, id",
            params,
        ).fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def get_system_variable(variable_id: int) -> dict | None:
    db = dbmod.get_db()
    try:
        row = db.execute("SELECT * FROM system_variables WHERE id = ?", (variable_id,)).fetchone()
    finally:
        db.close()
    return _row_to_dict(row)


def create_system_variable(
    agent_type: str,
    name: str,
    description: str | None = None,
    status: str = "active",
) -> dict:
    _validate_agent_type(agent_type)
    agent_type = dbmod.canonical_system_variable_agent_type(agent_type)
    _validate_status(status)
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("name is required")

    db = dbmod.get_db()
    try:
        cur = db.execute(
            """INSERT INTO system_variables
               (agent_type, name, description, status, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (agent_type, clean_name, description, status),
        )
        db.commit()
        variable_id = cur.lastrowid
    finally:
        db.close()
    return get_system_variable(variable_id)


def update_system_variable(variable_id: int, data: dict[str, object]) -> dict | None:
    fields: dict[str, object] = {}
    if "name" in data:
        clean_name = str(data["name"] or "").strip()
        if not clean_name:
            raise ValueError("name is required")
        fields["name"] = clean_name
    if "description" in data:
        fields["description"] = data["description"]
    if "status" in data:
        status = str(data["status"])
        _validate_status(status)
        fields["status"] = status
    if not fields:
        return get_system_variable(variable_id)

    assignments = ", ".join([f"{key} = ?" for key in fields])
    params = list(fields.values()) + [variable_id]
    db = dbmod.get_db()
    try:
        cur = db.execute(
            f"UPDATE system_variables SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            params,
        )
        db.commit()
    finally:
        db.close()
    if cur.rowcount == 0:
        return None
    return get_system_variable(variable_id)


def reference_snippet(content: str, variable_name: str, radius: int = 60) -> str:
    token_re = re.compile(r"\{\{\s*" + re.escape(variable_name) + r"\s*\}\}")
    match = token_re.search(content or "")
    if not match:
        return ""
    start = max(0, match.start() - radius)
    end = min(len(content), match.end() + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def list_variable_references(variable_id: int) -> list[dict]:
    variable = get_system_variable(variable_id)
    if not variable:
        return []
    prompt_agent_types = dbmod.prompt_agent_types_for_variable_scope(variable["agent_type"])
    placeholders = ", ".join(["?"] * len(prompt_agent_types))
    db = dbmod.get_db()
    try:
        rows = db.execute(
            f"""SELECT id, agent_type, version, name, content
               FROM prompts
               WHERE agent_type IN ({placeholders})
               ORDER BY agent_type, version, id""",
            prompt_agent_types,
        ).fetchall()
    finally:
        db.close()

    references: list[dict] = []
    for row in rows:
        snippet = reference_snippet(row["content"], variable["name"])
        if snippet:
            references.append(
                {
                    "prompt_id": row["id"],
                    "agent_type": row["agent_type"],
                    "version": row["version"],
                    "prompt_name": row["name"],
                    "variable": variable["name"],
                    "snippet": snippet,
                }
            )
    return references
