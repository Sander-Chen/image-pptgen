"""Default selection invariants for prompts and config combinations."""

from __future__ import annotations

import db as dbmod


def set_default_config(config_id: int) -> dict | None:
    if not dbmod.get_config(config_id):
        return None
    db = dbmod.get_db()
    db.execute("UPDATE configs SET is_default = 0")
    db.execute("UPDATE configs SET is_default = 1, updated_at = datetime('now') WHERE id = ?", (config_id,))
    db.commit()
    db.close()
    return dbmod.get_config(config_id)


def ensure_config_delete_allowed(config_id: int) -> None:
    config = dbmod.get_config(config_id)
    if not config:
        return
    if not config.get("is_default"):
        return
    db = dbmod.get_db()
    row = db.execute("SELECT COUNT(*) AS count FROM configs WHERE id != ?", (config_id,)).fetchone()
    db.close()
    if row["count"] == 0:
        raise ValueError("Cannot delete the last default config combination")


def promote_default_config_if_needed(deleted_config_id: int) -> None:
    db = dbmod.get_db()
    current = db.execute("SELECT id FROM configs WHERE is_default = 1 LIMIT 1").fetchone()
    if current:
        db.close()
        return
    replacement = db.execute(
        "SELECT id FROM configs WHERE id != ? ORDER BY id LIMIT 1",
        (deleted_config_id,),
    ).fetchone()
    if replacement:
        db.execute("UPDATE configs SET is_default = 1 WHERE id = ?", (replacement["id"],))
        db.commit()
    db.close()


def set_default_prompt(prompt_id: int) -> dict | None:
    prompt = dbmod.get_prompt(prompt_id)
    if not prompt:
        return None
    db = dbmod.get_db()
    db.execute("UPDATE prompts SET is_default = 0 WHERE agent_type = ?", (prompt["agent_type"],))
    db.execute("UPDATE prompts SET is_default = 1 WHERE id = ?", (prompt_id,))
    db.commit()
    db.close()
    return dbmod.get_prompt(prompt_id)


def ensure_prompt_archive_allowed(prompt_id: int) -> None:
    prompt = dbmod.get_prompt(prompt_id)
    if not prompt:
        return
    if not prompt.get("is_default"):
        return
    db = dbmod.get_db()
    row = db.execute(
        """SELECT COUNT(*) AS count FROM prompts
           WHERE id != ?
             AND agent_type = ?
             AND status = 'active'
             AND COALESCE(lifecycle_status, 'active') = 'active'""",
        (prompt_id, prompt["agent_type"]),
    ).fetchone()
    db.close()
    if row["count"] == 0:
        raise ValueError("Cannot archive the last default prompt for this role")


def promote_default_prompt_if_needed(agent_type: str) -> None:
    db = dbmod.get_db()
    current = db.execute(
        """SELECT id FROM prompts
           WHERE agent_type = ?
             AND is_default = 1
             AND status = 'active'
             AND COALESCE(lifecycle_status, 'active') = 'active'
           LIMIT 1""",
        (agent_type,),
    ).fetchone()
    if current:
        db.close()
        return
    replacement = db.execute(
        """SELECT id FROM prompts
           WHERE agent_type = ?
             AND status = 'active'
             AND COALESCE(lifecycle_status, 'active') = 'active'
           ORDER BY id DESC LIMIT 1""",
        (agent_type,),
    ).fetchone()
    if replacement:
        db.execute("UPDATE prompts SET is_default = 1 WHERE id = ?", (replacement["id"],))
        db.commit()
    db.close()
