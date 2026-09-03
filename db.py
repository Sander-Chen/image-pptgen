"""SQLite schema and CRUD helpers for HTML-PPT-Gen."""

import contextlib
import contextvars
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from backend.domain.auto_split import AUTO_SPLIT_MODELS, DEFAULT_AUTO_SPLIT_MODEL
from backend.services.platform_runtime import exclusive_file_lock


def _runtime_db_path() -> Path:
    configured = os.environ.get("PPT_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).parent / "ppt.db"


DB_PATH = _runtime_db_path()
CURRENT_SCHEMA_VERSION = 1
CURRENT_SCHEMA_MIGRATION_NAME = "r3_control_plane_foundation"
_DB_PATH_OVERRIDE: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "pptgen_db_path_override", default=None
)
_MIGRATION_LOCK = threading.RLock()
VALID_CONFIG_TYPES = {"html", "image"}
VALID_EVALUATION_STATUSES = {"draft", "running", "reviewing", "reviewed", "archived", "failed", "partial"}

_OLD_IMAGE_PREFIX = "ba" + "nana"
_IMAGE_NAMING_REPLACEMENTS = (
    (f"{_OLD_IMAGE_PREFIX}_cover_3_1", "image_cover_3_1"),
    (f"{_OLD_IMAGE_PREFIX}_5_0_unified", "image_5_0_unified"),
    (f"{_OLD_IMAGE_PREFIX}_3_2_non_seed", "image_3_2_non_seed"),
    (f"{_OLD_IMAGE_PREFIX}_3_2_seed", "image_3_2_seed"),
    (f"{_OLD_IMAGE_PREFIX}_3_0_non_seed", "image_3_0_non_seed"),
    (f"{_OLD_IMAGE_PREFIX}_3_0_seed", "image_3_0_seed"),
    (f"{_OLD_IMAGE_PREFIX}_director", "image_designer"),
    (f"{_OLD_IMAGE_PREFIX}_image", "image_generator"),
    (f"{_OLD_IMAGE_PREFIX}_cover", "image_cover"),
    (f"{_OLD_IMAGE_PREFIX}_5_0", "image_5_0"),
    (f"{_OLD_IMAGE_PREFIX}_3_2", "image_3_2"),
    (f"{_OLD_IMAGE_PREFIX}_3_0", "image_3_0"),
    (f"{_OLD_IMAGE_PREFIX}_1_0", "image_1_0"),
    (f"{_OLD_IMAGE_PREFIX.title()} Director", "Image Designer"),
    (f"{_OLD_IMAGE_PREFIX.title()} Image", "Image Generator"),
    (_OLD_IMAGE_PREFIX.title(), "Image"),
    (_OLD_IMAGE_PREFIX.upper(), "IMAGE"),
    (_OLD_IMAGE_PREFIX, "image"),
)
IMAGE_DIRECT_MODEL_CONFIG_NAMES = {"Nano Banana 2", "Nano Banana Pro", "GPT image2"}
_IMAGE_NAMING_EXEMPTIONS = (
    "pro production-banana-mini",
    "pro production-banana",
    "Nano Banana 2",
    "Nano Banana Pro",
    '"banana"',
)

LEGACY_VARIABLE_SCOPE_ALIASES = {
    "image_cover_3_1": "image_cover",
    "image_1_0": "image_designer",
    "image_3_0_seed": "image_designer",
    "image_3_0_non_seed": "image_designer",
    "image_3_2_seed": "image_designer",
    "image_3_2_non_seed": "image_designer",
    "image_5_0_unified": "image_designer",
}

SHARED_SYSTEM_VARIABLES = {
    "designer": ("Deck-Full-Content", "Deck-User-Requirement", "Deck-Required-color"),
    "html_agent": ("Deck-Design-principle", "Deck-User-Requirement", "Slide-Content"),
    "image_cover": ("Deck-Full-Content", "Deck-User-Requirement", "Deck-Required-color", "Deck-Title"),
    "image_designer": ("Deck-Full-Content", "Deck-User-Requirement", "Deck-Required-color", "Slide-Content"),
    "image_generator": ("Deck-Full-Content", "Deck-User-Requirement", "Deck-Required-color", "Slide-Content"),
    "xml_cleanup": ("Deck-Full-Content", "Deck-User-Requirement", "Deck-Required-color", "Slide-Content"),
    "evaluation_visual_qa": (),
}
DEFAULT_EVALUATION_VISUAL_QA_PROMPT = """You are the assistant for Slide/Single-Page HTML Visual QA Checks.

Inspect the attached screenshot as the final visual evidence. Use the HTML only to identify corresponding sections when needed.

Hard visual defects include overflow, overlap, misalignment, truncation, cramped spacing, low contrast, font fallback, image distortion, z-index layer problems, scrollbars, missing assets, and visible inconsistency.

Rules:
- Report only defects clearly visible in the screenshot.
- Do not infer defects from code or hypothetical longer text.
- Ignore 1-2px deviations and subjective aesthetic preferences.
- When uncertain, choose pass.

Return JSON only:
{
  "verdict": true,
  "description": "One sentence summary.",
  "issues": [
    {
      "severity": "high|medium|low",
      "dimension": "overflow|overlap|misalignment|truncation|spacing|contrast|font|distortion|z-index|scrollbar|missing_asset|inconsistency",
      "evidence": "Visible screenshot location and manifestation.",
      "code": "Optional selector or snippet."
    }
  ]
}

Use verdict true when no substantive hard visual issue is visible. Use verdict false when one or more hard visual issues are visible."""

IMAGE_PROMPT_SOURCE_ROOT = Path(__file__).parent / "example" / "prompts"
IMAGE_PROMPT_LANGUAGE_CONTRACT = (
    "Keep every user-visible title, heading, body, on-slide phrase, and quoted "
    "cover text in the same language as the source material and the user's request. "
    "Do not translate into English unless the user explicitly asks for English. "
    "If the source or request is Chinese, the presentation content must remain Chinese."
)
PUBLIC_IMAGE_PROMPT_ROLES = (
    "image_cover_3_1",
    "image_3_0_seed",
    "image_3_0_non_seed",
    "image_faithful_split",
    "image_palette_extraction",
)
IMAGE_PROMPT_SOURCE_FILES = {
    "image_cover_3_1": IMAGE_PROMPT_SOURCE_ROOT / "cover.md",
    "image_3_0_seed": IMAGE_PROMPT_SOURCE_ROOT / "seed-slide.md",
    "image_3_0_non_seed": IMAGE_PROMPT_SOURCE_ROOT / "subsequent-slide.md",
    "image_faithful_split": IMAGE_PROMPT_SOURCE_ROOT / "faithful-split.md",
    "image_palette_extraction": IMAGE_PROMPT_SOURCE_ROOT / "palette-extraction.md",
}
IMAGE_PROMPT_SOURCE_NAMES = {
    "image_cover_3_1": "Cover",
    "image_3_0_seed": "Seed Slide",
    "image_3_0_non_seed": "Subsequent Slide",
    "image_faithful_split": "Faithful Split",
    "image_palette_extraction": "Palette Extraction",
}
VARIABLE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def get_image_prompt_source_content(agent_type: str) -> str | None:
    path = IMAGE_PROMPT_SOURCE_FILES.get(agent_type)
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def get_image_prompt_required_variables(agent_type: str) -> list[str] | None:
    content = get_image_prompt_source_content(agent_type)
    if content is None:
        return None
    variables: list[str] = []
    seen = set()
    for match in VARIABLE_RE.finditer(content):
        variable = match.group(1).strip()
        if variable and variable not in seen:
            variables.append(variable)
            seen.add(variable)
    return variables


def canonical_system_variable_agent_type(agent_type: str) -> str:
    return LEGACY_VARIABLE_SCOPE_ALIASES.get(agent_type, agent_type)


def prompt_agent_types_for_variable_scope(agent_type: str) -> list[str]:
    canonical = canonical_system_variable_agent_type(agent_type)
    aliases = [legacy for legacy, target in LEGACY_VARIABLE_SCOPE_ALIASES.items() if target == canonical]
    return [canonical, *aliases]


def normalize_config_type(value: object | None) -> str:
    if value is None or value == "":
        return "html"
    config_type = str(value).strip().lower()
    if config_type not in VALID_CONFIG_TYPES:
        raise ValueError("config type must be html or image")
    return config_type


def _normalize_config_types(db: sqlite3.Connection, *, classify_default_html: bool = False) -> None:
    tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "configs" not in tables:
        return
    columns = {row["name"] for row in db.execute("PRAGMA table_info(configs)").fetchall()}
    if "type" not in columns:
        return
    needs_classification = (
        "type IS NULL OR lower(type) NOT IN ('html', 'image')"
        if not classify_default_html
        else "type IS NULL OR lower(type) NOT IN ('html', 'image') OR lower(type) = 'html'"
    )
    db.execute(
        f"""UPDATE configs
            SET type = 'image'
            WHERE ({needs_classification})
              AND (
                  lower(name) LIKE 'image%'
                  OR lower(name) LIKE ?
                  OR route_model_bindings LIKE '%"image_designer"%'
                  OR route_model_bindings LIKE '%"image_generator"%'
              )""",
        (f"{_OLD_IMAGE_PREFIX}%",),
    )
    db.execute(
        """UPDATE configs
           SET type = 'html'
           WHERE type IS NULL
              OR lower(type) NOT IN ('html', 'image')"""
    )
    db.execute("UPDATE configs SET type = lower(type)")


def _normalize_system_variable_scopes(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT id, agent_type, name, description, status FROM system_variables"
    ).fetchall()
    for row in rows:
        canonical = canonical_system_variable_agent_type(row["agent_type"])
        if canonical == row["agent_type"]:
            continue
        existing = db.execute(
            "SELECT id, description, status FROM system_variables WHERE agent_type = ? AND name = ?",
            (canonical, row["name"]),
        ).fetchone()
        status = "active" if row["status"] == "active" else "disabled"
        if existing:
            next_status = "active" if existing["status"] == "active" or status == "active" else "disabled"
            description = existing["description"] or row["description"]
            db.execute(
                """UPDATE system_variables
                   SET description = ?, status = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (description, next_status, existing["id"]),
            )
        else:
            db.execute(
                """INSERT INTO system_variables (agent_type, name, description, status, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                (canonical, row["name"], row["description"], status),
            )
        db.execute("DELETE FROM system_variables WHERE id = ?", (row["id"],))


def _prune_stale_system_variables(db: sqlite3.Connection) -> None:
    stale_variables = (("image_cover", "Slide-Content"),)
    for agent_type, name in stale_variables:
        db.execute(
            "DELETE FROM system_variables WHERE agent_type = ? AND name = ?",
            (agent_type, name),
        )


def _repair_generation_action_child_lineage(db: sqlite3.Connection) -> list[int]:
    rows = db.execute(
        """SELECT id, route_metadata, stage_artifacts
           FROM runs
           WHERE route_metadata IS NOT NULL
              OR stage_artifacts IS NOT NULL"""
    ).fetchall()
    repaired_slide_ids: list[int] = []
    repaired_slide_id_set: set[int] = set()

    def mark_for_resync(run_slide_id: int) -> None:
        if run_slide_id in repaired_slide_id_set:
            return
        repaired_slide_id_set.add(run_slide_id)
        repaired_slide_ids.append(run_slide_id)

    def parse_legacy_route_lineage(value: str | None) -> dict[str, Any]:
        return _validated_generation_action_lineage(_parse_json_dict(value))

    for run in rows:
        stage_artifacts = _parse_json_dict(run["stage_artifacts"])
        nested_lineage = (
            stage_artifacts.get("lineage")
            if isinstance(stage_artifacts.get("lineage"), dict)
            else None
        )
        lineage = nested_lineage or _validated_generation_action_lineage(
            stage_artifacts
        )
        if not lineage:
            lineage = parse_legacy_route_lineage(run["route_metadata"])
        if not isinstance(lineage, dict) or lineage.get("action") not in {"retry", "auto_retry", "force_regenerate"}:
            continue
        source_slide_ids = lineage.get("source_run_slide_ids")
        if not isinstance(source_slide_ids, list) or not source_slide_ids:
            continue
        child_slides = db.execute(
            "SELECT id, stage_artifacts FROM run_slides WHERE run_id = ? ORDER BY position, id",
            (run["id"],),
        ).fetchall()
        if len(child_slides) != len(source_slide_ids):
            continue
        for child_slide, source_slide_id in zip(child_slides, source_slide_ids):
            artifacts = _parse_json_dict(child_slide["stage_artifacts"])
            child_lineage = artifacts.get("lineage") if isinstance(artifacts.get("lineage"), dict) else {}
            if child_lineage.get("source_run_slide_id") == source_slide_id:
                pending_source_history = db.execute(
                    """SELECT 1
                       FROM generation_history
                       WHERE created_run_id = ?
                         AND target_run_slide_id = ?
                         AND status IN ('queued', 'running')
                       LIMIT 1""",
                    (run["id"], source_slide_id),
                ).fetchone()
                if pending_source_history:
                    mark_for_resync(int(child_slide["id"]))
                continue
            artifacts["lineage"] = {**lineage, "source_run_slide_id": source_slide_id}
            db.execute(
                "UPDATE run_slides SET stage_artifacts = ? WHERE id = ?",
                (json.dumps(artifacts, ensure_ascii=False), child_slide["id"]),
            )
            mark_for_resync(int(child_slide["id"]))
    return repaired_slide_ids


def _replace_image_route_naming(value: str | None) -> str | None:
    if value is None:
        return None
    next_value = value
    protected_literals: list[tuple[str, str]] = []
    for index, literal in enumerate(_IMAGE_NAMING_EXEMPTIONS):
        if literal not in next_value:
            continue
        placeholder = f"__PPTGEN_LITERAL_{index}__"
        next_value = next_value.replace(literal, placeholder)
        protected_literals.append((placeholder, literal))
    for old, new in _IMAGE_NAMING_REPLACEMENTS:
        next_value = next_value.replace(old, new)
    for placeholder, literal in protected_literals:
        next_value = next_value.replace(placeholder, literal)
    return next_value


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _preserve_image_direct_text(table: str, column: str, value: str | None) -> bool:
    if table == "configs" and column == "name" and value in IMAGE_DIRECT_MODEL_CONFIG_NAMES:
        return True
    if not isinstance(value, str):
        return False
    if table in {"runs", "run_slides", "generation_history"} and ("image_direct" in value or "ImageDirect" in value):
        return True
    return False


def _replace_internal_text_columns(db: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> None:
    available = _table_columns(db, table)
    selected_columns = [column for column in columns if column in available]
    if not selected_columns:
        return
    select_clause = ", ".join(["id", *selected_columns])
    rows = db.execute(f"SELECT {select_clause} FROM {table}").fetchall()
    for row in rows:
        updates: dict[str, str] = {}
        for column in selected_columns:
            if _preserve_image_direct_text(table, column, row[column]):
                continue
            replaced = _replace_image_route_naming(row[column])
            if replaced != row[column]:
                updates[column] = replaced
        if not updates:
            continue
        set_clause = ", ".join(f"{column} = ?" for column in updates)
        db.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ?",
            [*updates.values(), row["id"]],
        )


def _migrate_model_profile_naming(db: sqlite3.Connection) -> None:
    if "model_profiles" not in {
        row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    rows = db.execute("SELECT id, role, name FROM model_profiles").fetchall()
    for row in rows:
        role = _replace_image_route_naming(row["role"])
        name = _replace_image_route_naming(row["name"])
        if role == row["role"] and name == row["name"]:
            continue
        existing = db.execute(
            "SELECT id FROM model_profiles WHERE role = ? AND name = ? AND id != ?",
            (role, name, row["id"]),
        ).fetchone()
        if existing:
            name = f"{name} Migrated {row['id']}"
        db.execute(
            """UPDATE model_profiles
               SET role = ?, name = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (role, name, row["id"]),
        )


def _migrate_prompt_naming(db: sqlite3.Connection) -> None:
    if "prompts" not in {
        row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    available = _table_columns(db, "prompts")
    optional_columns = [
        column
        for column in ("description", "publish_baseline_content")
        if column in available
    ]
    select_clause = ", ".join(["id", "agent_type", "version", "name", "content", *optional_columns])
    rows = db.execute(f"SELECT {select_clause} FROM prompts").fetchall()
    for row in rows:
        updates = {
            "agent_type": _replace_image_route_naming(row["agent_type"]),
            "version": _replace_image_route_naming(row["version"]),
            "name": _replace_image_route_naming(row["name"]),
            "content": _replace_image_route_naming(row["content"]),
        }
        for column in optional_columns:
            updates[column] = _replace_image_route_naming(row[column])
        if all(updates[column] == row[column] for column in updates):
            continue
        existing = db.execute(
            "SELECT id FROM prompts WHERE agent_type = ? AND version = ? AND id != ?",
            (updates["agent_type"], updates["version"], row["id"]),
        ).fetchone()
        if existing:
            updates["version"] = f"{updates['version']}-migrated-{row['id']}"
        set_clause = ", ".join(f"{column} = ?" for column in updates)
        db.execute(
            f"UPDATE prompts SET {set_clause} WHERE id = ?",
            [*updates.values(), row["id"]],
        )


def _migrate_system_variable_naming(db: sqlite3.Connection) -> None:
    if "system_variables" not in {
        row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        return
    rows = db.execute(
        "SELECT id, agent_type, name, description, status FROM system_variables"
    ).fetchall()
    for row in rows:
        agent_type = _replace_image_route_naming(row["agent_type"])
        description = _replace_image_route_naming(row["description"])
        agent_type = canonical_system_variable_agent_type(agent_type)
        if agent_type == row["agent_type"] and description == row["description"]:
            continue
        existing = db.execute(
            "SELECT id, description, status FROM system_variables WHERE agent_type = ? AND name = ? AND id != ?",
            (agent_type, row["name"], row["id"]),
        ).fetchone()
        if existing:
            next_status = "active" if existing["status"] == "active" or row["status"] == "active" else "disabled"
            next_description = existing["description"] or description
            db.execute(
                """UPDATE system_variables
                   SET description = ?, status = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (next_description, next_status, existing["id"]),
            )
            db.execute("DELETE FROM system_variables WHERE id = ?", (row["id"],))
        else:
            db.execute(
                """UPDATE system_variables
                   SET agent_type = ?, description = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (agent_type, description, row["id"]),
            )


def _migrate_image_route_naming(db: sqlite3.Connection) -> None:
    """Convert stored image-route identifiers to the final Image naming scheme."""
    _migrate_model_profile_naming(db)
    _migrate_prompt_naming(db)
    _migrate_system_variable_naming(db)
    internal_columns = {
        "configs": ("name", "designer", "html_agent", "route_model_bindings"),
        "batches": ("generation_mode", "error_message"),
        "runs": (
            "error_message",
            "engine",
            "strategy",
            "route_metadata",
            "stage_artifacts",
            "model_call_metadata",
        ),
        "run_slides": (
            "stage_artifacts",
            "seed_dependency",
            "error_message",
        ),
        "generation_history": ("action", "scope", "force_mode", "summary", "error_message", "metadata"),
        "deck_split_drafts": ("mode", "model", "error_message"),
    }
    tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for table, columns in internal_columns.items():
        if table in tables:
            _replace_internal_text_columns(db, table, columns)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT DEFAULT 'html',
    designer TEXT NOT NULL,
    html_agent TEXT NOT NULL,
    designer_profile_id INTEGER REFERENCES model_profiles(id),
    html_agent_profile_id INTEGER REFERENCES model_profiles(id),
    route_model_bindings TEXT,
    is_default INTEGER DEFAULT 0,
    timeout_minutes INTEGER DEFAULT 30,
    max_concurrent_runs INTEGER DEFAULT 2,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS model_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    api_type TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    api_key TEXT NOT NULL,
    temperature REAL DEFAULT 0.7,
    thinking TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(role, name)
);
CREATE TABLE IF NOT EXISTS auto_split_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    model_profile_id INTEGER NOT NULL REFERENCES model_profiles(id),
    thinking_effort TEXT NOT NULL CHECK (thinking_effort IN ('low', 'medium', 'high')),
    content_mode TEXT NOT NULL DEFAULT 'faithful'
        CHECK (content_mode IN ('faithful', 'editorial')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    lifecycle_status TEXT DEFAULT 'active',
    archived_at TEXT,
    deleted_at TEXT,
    purged_at TEXT,
    previous_lifecycle_status TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    lifecycle_status TEXT DEFAULT 'active',
    archived_at TEXT,
    deleted_at TEXT,
    purged_at TEXT,
    previous_lifecycle_status TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS colors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT DEFAULT 'manual',
    source_image_path TEXT,
    source_metadata TEXT,
    lifecycle_status TEXT DEFAULT 'active',
    archived_at TEXT,
    deleted_at TEXT,
    purged_at TEXT,
    previous_lifecycle_status TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    split_mode TEXT DEFAULT 'manual',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id),
    config_id INTEGER NOT NULL REFERENCES configs(id),
    designer_prompt_id INTEGER REFERENCES prompts(id),
    html_prompt_id INTEGER REFERENCES prompts(id),
    generation_mode TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'queued',
    total_runs INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS batch_requirements (
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    requirement_id INTEGER NOT NULL REFERENCES requirements(id),
    PRIMARY KEY (batch_id, requirement_id)
);
CREATE TABLE IF NOT EXISTS batch_colors (
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    color_id INTEGER NOT NULL REFERENCES colors(id),
    PRIMARY KEY (batch_id, color_id)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER REFERENCES batches(id),
    deck_id INTEGER NOT NULL REFERENCES decks(id),
    requirement_id INTEGER NOT NULL REFERENCES requirements(id),
    color_id INTEGER NOT NULL REFERENCES colors(id),
    config_id INTEGER NOT NULL REFERENCES configs(id),
    auto_candidate_index INTEGER,
    status TEXT DEFAULT 'pending',
    output_dir TEXT,
    design_principle_raw TEXT,
    design_principle_json TEXT,
    error_message TEXT,
    engine TEXT DEFAULT 'html',
    strategy TEXT DEFAULT 'html_default',
    route_metadata TEXT,
    stage_artifacts TEXT,
    model_call_metadata TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS run_slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    slide_id INTEGER NOT NULL REFERENCES slides(id),
    position INTEGER NOT NULL,
    slide_title_snapshot TEXT,
    slide_content_snapshot TEXT,
    raw_response TEXT,
    clean_html TEXT,
    html_path TEXT,
    screenshot_path TEXT,
    slide_type TEXT DEFAULT 'content',
    xml_raw TEXT,
    xml_clean TEXT,
    final_image_path TEXT,
    stage_artifacts TEXT,
    seed_dependency TEXT,
    conversation_id TEXT,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS codex_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
    run_slide_id INTEGER REFERENCES run_slides(id) ON DELETE CASCADE,
    stage_id TEXT,
    role TEXT,
    attempt INTEGER DEFAULT 1,
    status TEXT,
    command_json TEXT,
    cwd TEXT,
    sandbox TEXT,
    model TEXT,
    reasoning_effort TEXT,
    prompt_sha256 TEXT,
    raw_jsonl_path TEXT,
    observed_jsonl_path TEXT,
    output_path TEXT,
    raw_jsonl_sha256 TEXT,
    observed_jsonl_sha256 TEXT,
    output_sha256 TEXT,
    event_count INTEGER DEFAULT 0,
    error_event_count INTEGER DEFAULT 0,
    usage_json TEXT,
    exit_code INTEGER,
    error_message TEXT,
    metadata TEXT,
    started_at TEXT,
    ended_at TEXT,
    elapsed_ms INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS codex_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id INTEGER NOT NULL REFERENCES codex_invocations(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type TEXT,
    observed_at TEXT NOT NULL,
    event_timestamp TEXT,
    item_id TEXT,
    item_type TEXT,
    is_error INTEGER DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(invocation_id, sequence)
);
CREATE TABLE IF NOT EXISTS artifact_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_run_slide_id INTEGER NOT NULL REFERENCES run_slides(id) ON DELETE CASCADE,
    artifact_run_slide_id INTEGER NOT NULL REFERENCES run_slides(id) ON DELETE CASCADE,
    source_run_id INTEGER REFERENCES runs(id),
    source_batch_id INTEGER REFERENCES batches(id),
    slide_id INTEGER REFERENCES slides(id),
    position INTEGER,
    version_number INTEGER NOT NULL,
    status TEXT DEFAULT 'available',
    html_path TEXT,
    screenshot_path TEXT,
    final_image_path TEXT,
    clean_html TEXT,
    xml_raw TEXT,
    xml_clean TEXT,
    raw_response TEXT,
    evidence_snapshot TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS active_artifact_versions (
    target_run_slide_id INTEGER PRIMARY KEY REFERENCES run_slides(id) ON DELETE CASCADE,
    version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS generation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    scope TEXT NOT NULL,
    target_id INTEGER,
    target_run_slide_id INTEGER REFERENCES run_slides(id) ON DELETE CASCADE,
    artifact_run_slide_id INTEGER REFERENCES run_slides(id) ON DELETE SET NULL,
    source_run_id INTEGER REFERENCES runs(id),
    source_batch_id INTEGER REFERENCES batches(id),
    created_run_id INTEGER REFERENCES runs(id),
    created_batch_id INTEGER REFERENCES batches(id),
    version_id INTEGER REFERENCES artifact_versions(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    force_mode TEXT,
    summary TEXT,
    error_message TEXT,
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_type TEXT NOT NULL,
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    description TEXT,
    lifecycle_status TEXT DEFAULT 'active',
    archived_at TEXT,
    is_default INTEGER DEFAULT 0,
    publish_baseline_content TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_type, version)
);
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS system_variables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_type, name)
);
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES folders(id),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(scope, parent_id, name)
);
CREATE TABLE IF NOT EXISTS folder_memberships (
    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (folder_id, entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS deck_split_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'pending',
    mode TEXT NOT NULL,
    model TEXT,
    model_profile_id INTEGER REFERENCES model_profiles(id),
    thinking_effort TEXT CHECK (thinking_effort IN ('low', 'medium', 'high')),
    content_mode TEXT CHECK (content_mode IN ('faithful', 'editorial')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT CHECK (
        last_error_code IN ('configuration', 'timeout', 'resource_unavailable', 'executable_identity_unavailable', 'provider_rejected', 'parse', 'integrity')
    ),
    slides_json TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_slides_deck ON slides(deck_id);
CREATE INDEX IF NOT EXISTS idx_run_slides_run ON run_slides(run_id);
CREATE INDEX IF NOT EXISTS idx_codex_invocations_run ON codex_invocations(run_id);
CREATE INDEX IF NOT EXISTS idx_codex_invocations_slide ON codex_invocations(run_slide_id);
CREATE INDEX IF NOT EXISTS idx_codex_events_invocation ON codex_events(invocation_id, sequence);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_target ON artifact_versions(target_run_slide_id);
CREATE INDEX IF NOT EXISTS idx_generation_history_target ON generation_history(target_run_slide_id);
CREATE INDEX IF NOT EXISTS idx_generation_history_scope ON generation_history(scope, target_id);
CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at);
CREATE INDEX IF NOT EXISTS idx_deck_split_drafts_deck ON deck_split_drafts(deck_id);
CREATE INDEX IF NOT EXISTS idx_folders_scope ON folders(scope);
CREATE INDEX IF NOT EXISTS idx_folder_memberships_entity ON folder_memberships(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_model_profiles_role ON model_profiles(role);
"""


MIGRATION_SQL = """
-- Add prompt references to runs table (safe to run multiple times)
"""


EVALUATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id),
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    export_config TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evaluation_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    goal TEXT NOT NULL,
    comparison_variable TEXT,
    generation_plan_snapshot TEXT,
    representative_attempt_id INTEGER,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evaluation_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES evaluation_variants(id) ON DELETE CASCADE,
    run_id INTEGER,
    batch_id INTEGER,
    label TEXT NOT NULL,
    attempt_index INTEGER DEFAULT 1,
    status TEXT,
    snapshot TEXT,
    run_missing INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evaluation_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    variant_id INTEGER REFERENCES evaluation_variants(id) ON DELETE CASCADE,
    attempt_id INTEGER REFERENCES evaluation_attempts(id) ON DELETE CASCADE,
    slide_position INTEGER,
    note TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evaluation_issue_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    source TEXT DEFAULT 'human',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(evaluation_id, label, source)
);
CREATE TABLE IF NOT EXISTS evaluation_slide_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    attempt_id INTEGER REFERENCES evaluation_attempts(id) ON DELETE CASCADE,
    run_slide_id INTEGER,
    slide_position INTEGER NOT NULL,
    tag_id INTEGER REFERENCES evaluation_issue_tags(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(evaluation_id, attempt_id, slide_position, tag_id)
);
CREATE TABLE IF NOT EXISTS evaluation_machine_qa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    attempt_id INTEGER REFERENCES evaluation_attempts(id) ON DELETE CASCADE,
    run_slide_id INTEGER,
    slide_position INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    issues_json TEXT,
    model_profile_id INTEGER,
    prompt_id INTEGER,
    raw_response TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evaluation_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    export_type TEXT NOT NULL,
    metadata_config TEXT,
    file_path TEXT,
    manifest_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_evaluations_deck ON evaluations(deck_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_variants_eval ON evaluation_variants(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_attempts_eval ON evaluation_attempts(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_attempts_variant ON evaluation_attempts(variant_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_attempts_run ON evaluation_attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_notes_eval ON evaluation_notes(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_slide_tags_eval ON evaluation_slide_tags(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_machine_qa_eval ON evaluation_machine_qa(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_machine_qa_run_slide ON evaluation_machine_qa(run_slide_id);
"""


R3_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS codex_work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
    run_slide_id INTEGER REFERENCES run_slides(id) ON DELETE CASCADE,
    stage_id TEXT NOT NULL,
    role TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    lease_owner TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    recovery_count INTEGER NOT NULL DEFAULT 0,
    max_recoveries INTEGER NOT NULL DEFAULT 2,
    pid INTEGER,
    session_id TEXT,
    last_event_sequence INTEGER NOT NULL DEFAULT 0,
    last_raw_offset INTEGER NOT NULL DEFAULT 0,
    error_class TEXT,
    terminal_reason TEXT,
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS codex_work_item_invocations (
    work_item_id INTEGER NOT NULL REFERENCES codex_work_items(id) ON DELETE CASCADE,
    invocation_id INTEGER NOT NULL REFERENCES codex_invocations(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (work_item_id, attempt_number),
    UNIQUE(invocation_id)
);
CREATE TABLE IF NOT EXISTS codex_event_raw_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    raw_bytes BLOB NOT NULL,
    raw_sha256 TEXT NOT NULL,
    byte_offset_start INTEGER NOT NULL,
    byte_offset_end INTEGER NOT NULL,
    file_identity TEXT,
    projection_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(invocation_id, sequence),
    FOREIGN KEY (invocation_id, sequence)
        REFERENCES codex_events(invocation_id, sequence) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS codex_observed_publish_intents (
    invocation_id INTEGER PRIMARY KEY REFERENCES codex_invocations(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('PREPARING', 'PREPARED')),
    generation_id TEXT NOT NULL,
    observed_path TEXT NOT NULL,
    temp_path TEXT NOT NULL,
    old_observed_sha256 TEXT NOT NULL,
    temp_file_identity TEXT,
    new_observed_sha256 TEXT,
    expected_event_count INTEGER NOT NULL DEFAULT 0,
    intended_terminal_state TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_codex_work_items_run ON codex_work_items(run_id);
CREATE INDEX IF NOT EXISTS idx_codex_work_items_slide ON codex_work_items(run_slide_id);
CREATE INDEX IF NOT EXISTS idx_codex_work_items_lease ON codex_work_items(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_codex_event_raw_lines_offset
    ON codex_event_raw_lines(invocation_id, byte_offset_start);
"""


def get_db() -> sqlite3.Connection:
    """Open a connection to the SQLite database with WAL mode and FK support."""
    db_path = _DB_PATH_OVERRIDE.get() or Path(DB_PATH)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_schema_version() -> int:
    db = get_db()
    try:
        return int(db.execute("PRAGMA user_version").fetchone()[0])
    finally:
        db.close()


def get_pre_migration_backup_path(
    db_path: str | Path | None = None,
    *,
    target_version: int = CURRENT_SCHEMA_VERSION,
) -> Path:
    path = Path(db_path) if db_path is not None else Path(DB_PATH)
    return path.with_name(f"{path.name}.pre-v{target_version}.backup.sqlite3")


def get_pre_migration_backup_manifest_path(
    db_path: str | Path | None = None,
    *,
    target_version: int = CURRENT_SCHEMA_VERSION,
) -> Path:
    backup_path = get_pre_migration_backup_path(db_path, target_version=target_version)
    return backup_path.with_suffix(f"{backup_path.suffix}.manifest.json")


def _database_logical_sha256(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        payload = "\n".join(conn.iterdump()).encode("utf-8")
    finally:
        conn.close()
    return hashlib.sha256(payload).hexdigest()


def _database_user_version(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _database_integrity(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Persist a POSIX rename; Windows has no portable directory fsync."""

    # ``os.fsync`` maps to the Windows ``_commit`` file operation.  The
    # staged file is committed before ``os.replace`` above, but the Python
    # runtime has no corresponding portable directory-entry flush.  Do not
    # attempt to open a directory as an ordinary file descriptor on Windows:
    # that fails before ``fsync`` under a normal user token.  POSIX failures
    # deliberately continue to surface to preserve the atomic-write contract.
    if os.name == "nt":
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_database(source: Path | None, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    if source is None or not source.exists() or source.stat().st_size == 0:
        conn = sqlite3.connect(str(destination))
        conn.close()
    else:
        source_conn = sqlite3.connect(str(source))
        destination_conn = sqlite3.connect(str(destination))
        try:
            source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            source_conn.backup(destination_conn)
            destination_conn.commit()
        finally:
            destination_conn.close()
            source_conn.close()
    os.chmod(destination, 0o600)
    _fsync_file(destination)


def _ensure_pre_migration_backup(source: Path) -> Path:
    backup_path = get_pre_migration_backup_path(source)
    manifest_path = get_pre_migration_backup_manifest_path(source)
    source_logical_sha256 = _database_logical_sha256(source)
    if backup_path.exists():
        if _database_integrity(backup_path) != "ok":
            raise RuntimeError(f"pre-migration backup failed integrity check: {backup_path}")
        if _database_logical_sha256(backup_path) != source_logical_sha256:
            raise RuntimeError(f"pre-migration backup does not match source database: {backup_path}")
    else:
        temporary = backup_path.with_name(f".{backup_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            _copy_database(source, temporary)
            if _database_integrity(temporary) != "ok":
                raise RuntimeError(f"pre-migration backup failed integrity check: {temporary}")
            if _database_logical_sha256(temporary) != source_logical_sha256:
                raise RuntimeError("pre-migration backup logical content mismatch")
            os.replace(temporary, backup_path)
            _fsync_directory(backup_path.parent)
        finally:
            temporary.unlink(missing_ok=True)
    _write_json_atomic(
        manifest_path,
        {
            "backup_path": str(backup_path),
            "backup_sha256": _file_sha256(backup_path),
            "integrity_check": "ok",
            "source_logical_sha256": source_logical_sha256,
            "source_path": str(source),
            "source_schema_version": _database_user_version(source),
            "target_schema_version": CURRENT_SCHEMA_VERSION,
        },
    )
    return backup_path


@contextlib.contextmanager
def _migration_file_lock(target: Path):
    with exclusive_file_lock(
        target.with_name(f".{target.name}.migration.lock"),
        timeout_seconds=None,
    ):
        yield


def _migration_stage_pattern(target: Path) -> str:
    return f".{target.name}.migration-v{CURRENT_SCHEMA_VERSION}-*.sqlite3"


def _cleanup_stale_migration_files(target: Path) -> None:
    for stale in target.parent.glob(_migration_stage_pattern(target)):
        stale.unlink(missing_ok=True)
        Path(f"{stale}-wal").unlink(missing_ok=True)
        Path(f"{stale}-shm").unlink(missing_ok=True)


def _prepare_database_for_replace(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        conn.close()
    _fsync_file(path)


def _replace_database_atomically(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(source, 0o600)
    os.replace(source, target)
    Path(f"{target}-wal").unlink(missing_ok=True)
    Path(f"{target}-shm").unlink(missing_ok=True)
    _fsync_file(target)
    _fsync_directory(target.parent)


def _validate_current_schema(path: Path) -> None:
    if _database_integrity(path) != "ok":
        raise RuntimeError(f"migrated database failed integrity check: {path}")
    conn = sqlite3.connect(str(path))
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        migration = conn.execute(
            "SELECT name FROM schema_migrations WHERE version = ?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchone()
    finally:
        conn.close()
    if version != CURRENT_SCHEMA_VERSION or migration is None:
        raise RuntimeError(
            f"migrated database did not reach schema version {CURRENT_SCHEMA_VERSION}"
        )


def _drop_auto_spill_profile_id(db: sqlite3.Connection) -> None:
    if "auto_spill_profile_id" in _table_columns(db, "configs"):
        db.execute("ALTER TABLE configs DROP COLUMN auto_spill_profile_id")


def _config_migration_snapshot(db: sqlite3.Connection) -> dict:
    columns = [
        dict(row)
        for row in db.execute("PRAGMA table_info(configs)").fetchall()
        if row["name"] != "auto_spill_profile_id"
    ]
    column_names = [row["name"] for row in columns]
    rows = [
        tuple(row[name] for name in column_names)
        for row in db.execute("SELECT * FROM configs ORDER BY id").fetchall()
    ]
    schema_objects = [
        (row["type"], row["name"], row["sql"])
        for row in db.execute(
            """SELECT type, name, sql FROM sqlite_master
               WHERE tbl_name = 'configs'
                 AND type IN ('index', 'trigger')
                 AND sql IS NOT NULL
               ORDER BY type, name"""
        ).fetchall()
    ]
    foreign_keys = sorted(
        (
            row["table"],
            row["from"],
            row["to"],
            row["on_update"],
            row["on_delete"],
            row["match"],
        )
        for row in db.execute("PRAGMA foreign_key_list(configs)").fetchall()
        if row["from"] != "auto_spill_profile_id"
    )
    return {
        "columns": [
            (row["name"], row["type"], row["notnull"], row["dflt_value"], row["pk"])
            for row in columns
        ],
        "rows": rows,
        "schema_objects": schema_objects,
        "foreign_keys": foreign_keys,
    }


def _upgrade_deck_split_draft_error_codes(db: sqlite3.Connection) -> None:
    """Expand the persisted split-error enum without discarding existing drafts."""

    if "last_error_code" not in _table_columns(db, "deck_split_drafts"):
        return
    definition = db.execute(
        """SELECT sql FROM sqlite_master
           WHERE type = 'table' AND name = 'deck_split_drafts'"""
    ).fetchone()
    if definition is None or "executable_identity_unavailable" in str(definition[0] or ""):
        return

    legacy_table = "deck_split_drafts_r34_error_codes_legacy"
    db.execute("SAVEPOINT r34_error_codes")
    try:
        db.execute(f"ALTER TABLE deck_split_drafts RENAME TO {legacy_table}")
        db.execute(
            """CREATE TABLE deck_split_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'pending',
            mode TEXT NOT NULL,
            model TEXT,
            model_profile_id INTEGER REFERENCES model_profiles(id),
            thinking_effort TEXT CHECK (thinking_effort IN ('low', 'medium', 'high')),
            content_mode TEXT CHECK (content_mode IN ('faithful', 'editorial')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT CHECK (
                last_error_code IN (
                    'configuration', 'timeout', 'resource_unavailable',
                    'executable_identity_unavailable',
                    'provider_rejected', 'parse', 'integrity'
                )
            ),
            slides_json TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            confirmed_at TEXT
            )"""
        )
        db.execute(
            f"""INSERT INTO deck_split_drafts
               (id, deck_id, status, mode, model, model_profile_id,
                thinking_effort, content_mode, attempt_count, last_error_code,
                slides_json, error_message, created_at, confirmed_at)
           SELECT id, deck_id, status, mode, model, model_profile_id,
                  thinking_effort, content_mode, attempt_count, last_error_code,
                  slides_json, error_message, created_at, confirmed_at
           FROM {legacy_table}"""
        )
        db.execute(f"DROP TABLE {legacy_table}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_deck_split_drafts_deck ON deck_split_drafts(deck_id)")
        db.execute("RELEASE r34_error_codes")
    except Exception:
        db.execute("ROLLBACK TO r34_error_codes")
        db.execute("RELEASE r34_error_codes")
        raise


def _migrate_auto_split_decoupling(db: sqlite3.Connection) -> None:
    """Create the standalone AutoSplit catalogue and remove its Combination binding."""
    db.execute("SAVEPOINT autosplit_decoupling")
    try:
        config_snapshot = _config_migration_snapshot(db)
        foreign_key_violations = [tuple(row) for row in db.execute("PRAGMA foreign_key_check")]
        credential_row = db.execute(
            """SELECT api_key FROM model_profiles
               WHERE api_type = 'gemini'
                 AND status = 'active'
                 AND TRIM(api_key) != ''
               ORDER BY id
               LIMIT 1"""
        ).fetchone()
        gemini_api_key = (
            str(credential_row["api_key"]).strip()
            if credential_row
            else os.environ.get("GEMINI_API_KEY", "").strip()
        )

        db.execute(
            """CREATE TABLE IF NOT EXISTS auto_split_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                model_profile_id INTEGER NOT NULL REFERENCES model_profiles(id),
                thinking_effort TEXT NOT NULL CHECK (thinking_effort IN ('low', 'medium', 'high')),
                content_mode TEXT NOT NULL DEFAULT 'faithful'
                    CHECK (content_mode IN ('faithful', 'editorial')),
                updated_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        setting_columns = _table_columns(db, "auto_split_settings")
        if "content_mode" not in setting_columns:
            db.execute(
                """ALTER TABLE auto_split_settings
                   ADD COLUMN content_mode TEXT NOT NULL DEFAULT 'faithful'
                   CHECK (content_mode IN ('faithful', 'editorial'))"""
            )
        db.execute(
            """UPDATE auto_split_settings
               SET content_mode = 'faithful'
               WHERE content_mode IS NULL
                  OR content_mode NOT IN ('faithful', 'editorial')"""
        )
        canonical_ids: dict[str, int] = {}
        canonical_names = tuple(item.profile_name for item in AUTO_SPLIT_MODELS)
        for item in AUTO_SPLIT_MODELS:
            api_key = gemini_api_key if item.api_type == "gemini" else ""
            existing = db.execute(
                "SELECT id FROM model_profiles WHERE role = 'auto_spill' AND name = ?",
                (item.profile_name,),
            ).fetchone()
            if existing:
                profile_id = int(existing["id"])
                db.execute(
                    """UPDATE model_profiles
                       SET api_type = ?, endpoint = ?, model = ?, api_key = ?,
                           temperature = 1, thinking = NULL, status = 'active',
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (item.api_type, item.endpoint, item.model, api_key, profile_id),
                )
            else:
                cur = db.execute(
                    """INSERT INTO model_profiles
                       (role, name, api_type, endpoint, model, api_key,
                        temperature, thinking, status)
                       VALUES ('auto_spill', ?, ?, ?, ?, ?, 1, NULL, 'active')""",
                    (item.profile_name, item.api_type, item.endpoint, item.model, api_key),
                )
                profile_id = int(cur.lastrowid)
            canonical_ids[item.model] = profile_id

        placeholders = ", ".join("?" for _ in canonical_names)
        db.execute(
            f"""UPDATE model_profiles
                SET status = 'inactive', updated_at = datetime('now')
                WHERE role = 'auto_spill' AND name NOT IN ({placeholders})""",
            canonical_names,
        )

        setting = db.execute(
            """SELECT s.model_profile_id, s.thinking_effort, s.content_mode,
                      p.model, p.status
               FROM auto_split_settings s
               LEFT JOIN model_profiles p ON p.id = s.model_profile_id
               WHERE s.id = 1"""
        ).fetchone()
        valid_setting = bool(
            setting
            and setting["model"] in canonical_ids
            and setting["status"] == "active"
            and setting["thinking_effort"] in {"low", "medium", "high"}
            and setting["content_mode"] in {"faithful", "editorial"}
        )
        if not valid_setting:
            db.execute(
                """INSERT INTO auto_split_settings
                   (id, model_profile_id, thinking_effort, content_mode, updated_at)
                   VALUES (1, ?, 'high', 'faithful', datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET
                     model_profile_id = excluded.model_profile_id,
                     thinking_effort = excluded.thinking_effort,
                     content_mode = excluded.content_mode,
                     updated_at = excluded.updated_at""",
                (canonical_ids[DEFAULT_AUTO_SPLIT_MODEL],),
            )

        draft_columns = _table_columns(db, "deck_split_drafts")
        draft_migrations = (
            (
                "model_profile_id",
                "ALTER TABLE deck_split_drafts ADD COLUMN model_profile_id INTEGER REFERENCES model_profiles(id)",
            ),
            (
                "thinking_effort",
                "ALTER TABLE deck_split_drafts ADD COLUMN thinking_effort TEXT CHECK (thinking_effort IN ('low', 'medium', 'high'))",
            ),
            (
                "content_mode",
                "ALTER TABLE deck_split_drafts ADD COLUMN content_mode TEXT CHECK (content_mode IN ('faithful', 'editorial'))",
            ),
            (
                "attempt_count",
                "ALTER TABLE deck_split_drafts ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "last_error_code",
                "ALTER TABLE deck_split_drafts ADD COLUMN last_error_code TEXT CHECK (last_error_code IN ('configuration', 'timeout', 'resource_unavailable', 'executable_identity_unavailable', 'provider_rejected', 'parse', 'integrity'))",
            ),
        )
        for column, sql in draft_migrations:
            if column not in draft_columns:
                db.execute(sql)
        _upgrade_deck_split_draft_error_codes(db)
        db.execute(
            """UPDATE deck_split_drafts
               SET content_mode = 'faithful'
               WHERE mode = 'llm_auto' AND content_mode IS NULL"""
        )

        _drop_auto_spill_profile_id(db)

        if _config_migration_snapshot(db) != config_snapshot:
            raise RuntimeError("AutoSplit migration changed unrelated Combination data or schema")
        if [tuple(row) for row in db.execute("PRAGMA foreign_key_check")] != foreign_key_violations:
            raise RuntimeError("AutoSplit migration changed foreign-key integrity")
        active_count = db.execute(
            """SELECT COUNT(*) FROM model_profiles
               WHERE role = 'auto_spill' AND status = 'active'"""
        ).fetchone()[0]
        if active_count != len(AUTO_SPLIT_MODELS):
            raise RuntimeError("AutoSplit canonical catalogue is incomplete")
        db.execute("RELEASE autosplit_decoupling")
    except Exception:
        db.execute("ROLLBACK TO autosplit_decoupling")
        db.execute("RELEASE autosplit_decoupling")
        raise


def _migrate_db_in_place():
    """Add new columns to existing tables (idempotent)."""
    db = get_db()
    had_auto_split_settings = bool(
        db.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'auto_split_settings'"""
        ).fetchone()
    )
    db.executescript(SCHEMA_SQL)
    existing_config_columns = set()
    try:
        existing_config_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(configs)").fetchall()
        }
    except sqlite3.OperationalError:
        existing_config_columns = set()
    had_config_type_column = "type" in existing_config_columns
    migrations = [
        "ALTER TABLE configs ADD COLUMN timeout_minutes INTEGER DEFAULT 30;",
        "ALTER TABLE configs ADD COLUMN max_concurrent_runs INTEGER DEFAULT 2;",
        "ALTER TABLE configs ADD COLUMN type TEXT DEFAULT 'html';",
        "ALTER TABLE configs ADD COLUMN designer_profile_id INTEGER REFERENCES model_profiles(id);",
        "ALTER TABLE configs ADD COLUMN html_agent_profile_id INTEGER REFERENCES model_profiles(id);",
        "ALTER TABLE configs ADD COLUMN route_model_bindings TEXT;",
        "ALTER TABLE configs ADD COLUMN is_default INTEGER DEFAULT 0;",
        "ALTER TABLE runs ADD COLUMN batch_id INTEGER REFERENCES batches(id);",
        "ALTER TABLE runs ADD COLUMN designer_prompt_id INTEGER REFERENCES prompts(id);",
        "ALTER TABLE runs ADD COLUMN html_prompt_id INTEGER REFERENCES prompts(id);",
        "ALTER TABLE runs ADD COLUMN auto_candidate_index INTEGER;",
        "ALTER TABLE runs ADD COLUMN engine TEXT DEFAULT 'html';",
        "ALTER TABLE runs ADD COLUMN strategy TEXT DEFAULT 'html_default';",
        "ALTER TABLE runs ADD COLUMN route_metadata TEXT;",
        "ALTER TABLE runs ADD COLUMN stage_artifacts TEXT;",
        "ALTER TABLE runs ADD COLUMN model_call_metadata TEXT;",
        "ALTER TABLE run_slides ADD COLUMN slide_type TEXT DEFAULT 'content';",
        "ALTER TABLE run_slides ADD COLUMN xml_raw TEXT;",
        "ALTER TABLE run_slides ADD COLUMN xml_clean TEXT;",
        "ALTER TABLE run_slides ADD COLUMN final_image_path TEXT;",
        "ALTER TABLE run_slides ADD COLUMN stage_artifacts TEXT;",
        "ALTER TABLE run_slides ADD COLUMN seed_dependency TEXT;",
        "ALTER TABLE run_slides ADD COLUMN conversation_id TEXT;",
        "ALTER TABLE run_slides ADD COLUMN slide_title_snapshot TEXT;",
        "ALTER TABLE run_slides ADD COLUMN slide_content_snapshot TEXT;",
        "ALTER TABLE batches ADD COLUMN generation_mode TEXT DEFAULT 'manual';",
        "ALTER TABLE colors ADD COLUMN source_type TEXT DEFAULT 'manual';",
        "ALTER TABLE colors ADD COLUMN source_image_path TEXT;",
        "ALTER TABLE colors ADD COLUMN source_metadata TEXT;",
    ]
    for table in ("decks", "requirements", "colors"):
        migrations.extend(
            [
                f"ALTER TABLE {table} ADD COLUMN lifecycle_status TEXT DEFAULT 'active';",
                f"ALTER TABLE {table} ADD COLUMN archived_at TEXT;",
                f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT;",
                f"ALTER TABLE {table} ADD COLUMN purged_at TEXT;",
                f"ALTER TABLE {table} ADD COLUMN previous_lifecycle_status TEXT;",
            ]
        )
    migrations.extend(
        [
            "ALTER TABLE prompts ADD COLUMN lifecycle_status TEXT DEFAULT 'active';",
            "ALTER TABLE prompts ADD COLUMN archived_at TEXT;",
            "ALTER TABLE prompts ADD COLUMN is_default INTEGER DEFAULT 0;",
            "ALTER TABLE prompts ADD COLUMN publish_baseline_content TEXT;",
        ]
    )
    for sql in migrations:
        try:
            db.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                pass  # Column already exists — safe to ignore
            else:
                raise
    db.commit()
    db.execute("CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS artifact_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_run_slide_id INTEGER NOT NULL REFERENCES run_slides(id) ON DELETE CASCADE,
            artifact_run_slide_id INTEGER NOT NULL REFERENCES run_slides(id) ON DELETE CASCADE,
            source_run_id INTEGER REFERENCES runs(id),
            source_batch_id INTEGER REFERENCES batches(id),
            slide_id INTEGER REFERENCES slides(id),
            position INTEGER,
            version_number INTEGER NOT NULL,
            status TEXT DEFAULT 'available',
            html_path TEXT,
            screenshot_path TEXT,
            final_image_path TEXT,
            clean_html TEXT,
            xml_raw TEXT,
            xml_clean TEXT,
            raw_response TEXT,
            evidence_snapshot TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS active_artifact_versions (
            target_run_slide_id INTEGER PRIMARY KEY REFERENCES run_slides(id) ON DELETE CASCADE,
            version_id INTEGER NOT NULL REFERENCES artifact_versions(id) ON DELETE CASCADE,
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS generation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            scope TEXT NOT NULL,
            target_id INTEGER,
            target_run_slide_id INTEGER REFERENCES run_slides(id) ON DELETE CASCADE,
            artifact_run_slide_id INTEGER REFERENCES run_slides(id) ON DELETE SET NULL,
            source_run_id INTEGER REFERENCES runs(id),
            source_batch_id INTEGER REFERENCES batches(id),
            created_run_id INTEGER REFERENCES runs(id),
            created_batch_id INTEGER REFERENCES batches(id),
            version_id INTEGER REFERENCES artifact_versions(id) ON DELETE SET NULL,
            status TEXT NOT NULL,
            force_mode TEXT,
            summary TEXT,
            error_message TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS codex_invocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
            run_slide_id INTEGER REFERENCES run_slides(id) ON DELETE CASCADE,
            stage_id TEXT,
            role TEXT,
            attempt INTEGER DEFAULT 1,
            status TEXT,
            command_json TEXT,
            cwd TEXT,
            sandbox TEXT,
            model TEXT,
            reasoning_effort TEXT,
            prompt_sha256 TEXT,
            raw_jsonl_path TEXT,
            observed_jsonl_path TEXT,
            output_path TEXT,
            raw_jsonl_sha256 TEXT,
            observed_jsonl_sha256 TEXT,
            output_sha256 TEXT,
            event_count INTEGER DEFAULT 0,
            error_event_count INTEGER DEFAULT 0,
            usage_json TEXT,
            exit_code INTEGER,
            error_message TEXT,
            metadata TEXT,
            started_at TEXT,
            ended_at TEXT,
            elapsed_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS codex_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invocation_id INTEGER NOT NULL REFERENCES codex_invocations(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            event_type TEXT,
            observed_at TEXT NOT NULL,
            event_timestamp TEXT,
            item_id TEXT,
            item_type TEXT,
            is_error INTEGER DEFAULT 0,
            payload_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(invocation_id, sequence)
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_artifact_versions_target ON artifact_versions(target_run_slide_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_generation_history_target ON generation_history(target_run_slide_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_generation_history_scope ON generation_history(scope, target_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_codex_invocations_run ON codex_invocations(run_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_codex_invocations_slide ON codex_invocations(run_slide_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_codex_events_invocation ON codex_events(invocation_id, sequence)")
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_machine_qa_run_slide ON evaluation_machine_qa(run_slide_id)")
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e).lower():
            raise
    db.execute(
        """CREATE TABLE IF NOT EXISTS deck_split_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'pending',
            mode TEXT NOT NULL,
            model TEXT,
            model_profile_id INTEGER REFERENCES model_profiles(id),
            thinking_effort TEXT CHECK (thinking_effort IN ('low', 'medium', 'high')),
            content_mode TEXT CHECK (content_mode IN ('faithful', 'editorial')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT CHECK (
                last_error_code IN ('configuration', 'timeout', 'resource_unavailable', 'executable_identity_unavailable', 'provider_rejected', 'parse', 'integrity')
            ),
            slides_json TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            confirmed_at TEXT
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_deck_split_drafts_deck ON deck_split_drafts(deck_id)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS system_variables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(agent_type, name)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_id INTEGER REFERENCES folders(id),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(scope, parent_id, name)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS folder_memberships (
            folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (folder_id, entity_type, entity_id)
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_folders_scope ON folders(scope)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_folder_memberships_entity ON folder_memberships(entity_type, entity_id)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS model_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            name TEXT NOT NULL,
            api_type TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            model TEXT NOT NULL,
            api_key TEXT NOT NULL,
            temperature REAL DEFAULT 0.7,
            thinking TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(role, name)
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_model_profiles_role ON model_profiles(role)")
    try:
        _migrate_auto_split_decoupling(db)
    except Exception:
        if not had_auto_split_settings:
            db.execute("DROP TABLE IF EXISTS auto_split_settings")
            db.commit()
        db.close()
        raise
    db.executescript(EVALUATION_SCHEMA_SQL)
    _migrate_image_route_naming(db)
    _normalize_config_types(db, classify_default_html=not had_config_type_column)
    db.execute(
        "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
        ("provider_concurrency.openai:zenmux.ai", "100"),
    )
    db.execute(
        "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
        ("provider_concurrency.gemini:generativelanguage.googleapis.com", "10"),
    )
    db.execute(
        "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
        ("provider_concurrency.codex_exec:exec", "6"),
    )
    db.execute(
        "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
        ("provider_concurrency.codex_native_image:exec", "6"),
    )
    db.execute(
        "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
        ("run_queue_concurrency", "6"),
    )
    for agent_type, variables in SHARED_SYSTEM_VARIABLES.items():
        for variable in variables:
            db.execute(
                "INSERT OR IGNORE INTO system_variables (agent_type, name, status) VALUES (?, ?, 'active')",
                (agent_type, variable),
            )
    _normalize_system_variable_scopes(db)
    _prune_stale_system_variables(db)
    repaired_child_slide_ids = _repair_generation_action_child_lineage(db)
    image_default_prompts = {
        agent_type: (IMAGE_PROMPT_SOURCE_NAMES[agent_type], get_image_prompt_source_content(agent_type))
        for agent_type in IMAGE_PROMPT_SOURCE_NAMES
    }
    missing_required = [
        agent_type
        for agent_type, (_name, content) in image_default_prompts.items()
        if content is None
    ]
    if missing_required:
        raise FileNotFoundError(
            "Required public Image prompt source is missing for: "
            + ", ".join(missing_required)
        )
    image_default_prompts.update(
        {
            "image_generator": (
                "Image Generator",
                "Generate the final image from the provided visual XML. Preserve the XML layout and return provider response metadata."
            ),
            "xml_cleanup": (
                "Image XML Cleanup",
                "Remove only the Quality_Checklist block from Image visual XML and preserve all other XML."
            ),
            "evaluation_visual_qa": (
                "Evaluation Visual QA",
                DEFAULT_EVALUATION_VISUAL_QA_PROMPT,
            ),
        }
    )
    for agent_type, (name, content) in image_default_prompts.items():
        if content is None:
            continue
        existing = db.execute(
            """SELECT id FROM prompts
               WHERE agent_type = ?
                 AND status = 'active'
                 AND COALESCE(lifecycle_status, 'active') = 'active'
               LIMIT 1""",
            (agent_type,),
        ).fetchone()
        if not existing:
            db.execute(
                """INSERT INTO prompts
                   (agent_type, version, name, content, description, is_default)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (agent_type, "image-l4-default", name, content, "Seeded Image route prompt managed by Prompt System."),
            )
        elif agent_type in IMAGE_PROMPT_SOURCE_NAMES:
            active = db.execute(
                """SELECT id, version, content, is_default FROM prompts
                   WHERE agent_type = ?
                     AND status = 'active'
                     AND COALESCE(lifecycle_status, 'active') = 'active'
                   ORDER BY COALESCE(is_default, 0) DESC, id DESC LIMIT 1""",
                (agent_type,),
            ).fetchone()
            active_content = active["content"] or "" if active else ""
            managed_default = active and active["version"] == "image-l4-default"
            if agent_type == "image_cover_3_1":
                required_markers = (
                    "Given 「article-context」 is below:",
                    "{{Deck-Full-Content}}",
                    "Color-reference:",
                    "## Image prompt",
                )
            else:
                required_markers = tuple(f"{{{{{variable}}}}}" for variable in get_image_prompt_required_variables(agent_type) or [])
            marker_missing = any(marker not in active_content for marker in required_markers)
            needs_repair = bool(
                active
                and (
                    (
                        agent_type == "image_cover_3_1"
                        and ((managed_default and active_content != content) or marker_missing)
                    )
                    or (
                        agent_type != "image_cover_3_1"
                        and managed_default
                        and (active_content != content or marker_missing)
                    )
                )
            )
            if needs_repair:
                db.execute(
                    """UPDATE prompts
                       SET content = ?, name = ?, description = ?, is_default = 1
                       WHERE id = ?""",
                    (
                        content,
                        name,
                        "Repaired Image route prompt from source-of-truth files.",
                        active["id"],
                    ),
                )
    db.executescript(R3_SCHEMA_SQL)
    try:
        db.execute("ALTER TABLE codex_observed_publish_intents ADD COLUMN temp_file_identity TEXT;")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise
    db.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
        (CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_MIGRATION_NAME),
    )
    db.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
    db.commit()
    db.close()
    for run_slide_id in repaired_child_slide_ids:
        sync_run_slide_artifact_state(run_slide_id)
    backfill_artifact_versions()


def migrate_db():
    """Migrate through an isolated same-filesystem copy before atomic replacement."""
    target = Path(DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _MIGRATION_LOCK, _migration_file_lock(target):
        current_version = _database_user_version(target)
        if current_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {current_version} is newer than supported "
                f"version {CURRENT_SCHEMA_VERSION}"
            )
        if current_version == CURRENT_SCHEMA_VERSION:
            token = _DB_PATH_OVERRIDE.set(target)
            try:
                _migrate_db_in_place()
                _validate_current_schema(target)
            finally:
                _DB_PATH_OVERRIDE.reset(token)
            return

        if target.exists() and target.stat().st_size > 0:
            _ensure_pre_migration_backup(target)
        _cleanup_stale_migration_files(target)
        stage = target.with_name(
            f".{target.name}.migration-v{CURRENT_SCHEMA_VERSION}-{os.getpid()}-{time.time_ns()}.sqlite3"
        )
        _copy_database(target if target.exists() else None, stage)
        token = _DB_PATH_OVERRIDE.set(stage)
        try:
            _migrate_db_in_place()
            _validate_current_schema(stage)
            _prepare_database_for_replace(stage)
            _replace_database_atomically(stage, target)
        finally:
            _DB_PATH_OVERRIDE.reset(token)
            stage.unlink(missing_ok=True)
            Path(f"{stage}-wal").unlink(missing_ok=True)
            Path(f"{stage}-shm").unlink(missing_ok=True)


def restore_database_backup(
    backup_path: str | Path,
    *,
    target_path: str | Path | None = None,
) -> Path:
    """Restore a validated migration backup by atomic same-filesystem replacement."""
    backup = Path(backup_path)
    target = Path(target_path) if target_path is not None else Path(DB_PATH)
    manifest_path = backup.with_suffix(f"{backup.suffix}.manifest.json")
    if not backup.exists() or not manifest_path.exists():
        raise RuntimeError("migration backup and manifest are both required for restore")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("backup_sha256") != _file_sha256(backup):
        raise RuntimeError("migration backup file hash does not match its manifest")
    if _database_integrity(backup) != "ok":
        raise RuntimeError("migration backup failed integrity check")
    expected_logical_sha256 = manifest.get("source_logical_sha256")
    if not expected_logical_sha256 or _database_logical_sha256(backup) != expected_logical_sha256:
        raise RuntimeError("migration backup logical content does not match its manifest")

    with _MIGRATION_LOCK, _migration_file_lock(target):
        stage = target.with_name(
            f".{target.name}.restore-{os.getpid()}-{time.time_ns()}.sqlite3"
        )
        try:
            _copy_database(backup, stage)
            if _database_integrity(stage) != "ok":
                raise RuntimeError("restored database failed integrity check")
            if _database_logical_sha256(stage) != expected_logical_sha256:
                raise RuntimeError("restored database logical content mismatch")
            _prepare_database_for_replace(stage)
            _replace_database_atomically(stage, target)
        finally:
            stage.unlink(missing_ok=True)
            Path(f"{stage}-wal").unlink(missing_ok=True)
            Path(f"{stage}-shm").unlink(missing_ok=True)
    return target


def init_db():
    """Create all tables and indexes if they do not exist."""
    migrate_db()


def row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict, or return None."""
    return dict(row) if row else None


def rows_to_list(rows):
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# evaluations CRUD
# ---------------------------------------------------------------------------

def _json_text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_object(value: object | None) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: object | None) -> object:
    if not value:
        return [] if value == "[]" else {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}


def _normalize_evaluation_status(status: str | None) -> str:
    normalized = (status or "draft").strip().lower()
    if normalized not in VALID_EVALUATION_STATUSES:
        raise ValueError(f"evaluation status must be one of: {', '.join(sorted(VALID_EVALUATION_STATUSES))}")
    return normalized


def create_evaluation(
    *,
    deck_id: int,
    title: str,
    goal: str,
    status: str = "draft",
    export_config: dict | str | None = None,
) -> int:
    if not title or not str(title).strip():
        raise ValueError("Evaluation title is required")
    if not goal or not str(goal).strip():
        raise ValueError("Evaluation goal is required")
    if not get_deck(deck_id):
        raise ValueError("Deck not found")
    status = _normalize_evaluation_status(status)
    db = get_db()
    cur = db.execute(
        """INSERT INTO evaluations (deck_id, title, goal, status, export_config)
           VALUES (?, ?, ?, ?, ?)""",
        (deck_id, str(title).strip(), str(goal).strip(), status, _json_text(export_config)),
    )
    db.commit()
    evaluation_id = int(cur.lastrowid)
    db.close()
    return evaluation_id


def update_evaluation(evaluation_id: int, **fields) -> dict | None:
    allowed = {"title", "goal", "status", "export_config"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return get_evaluation_detail(evaluation_id)
    if "status" in updates:
        updates["status"] = _normalize_evaluation_status(updates["status"])
    if "export_config" in updates:
        updates["export_config"] = _json_text(updates["export_config"])
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    values = [*updates.values(), evaluation_id]
    db = get_db()
    db.execute(
        f"""UPDATE evaluations
            SET {set_clause}, updated_at = datetime('now')
            WHERE id = ?""",
        values,
    )
    db.commit()
    db.close()
    return get_evaluation_detail(evaluation_id)


def create_evaluation_variant(
    evaluation_id: int,
    *,
    label: str,
    goal: str,
    comparison_variable: str | None = None,
    generation_plan_snapshot: dict | str | None = None,
    sort_order: int | None = None,
) -> int:
    if not label or not str(label).strip():
        raise ValueError("Variant label is required")
    if not goal or not str(goal).strip():
        raise ValueError("Variant goal is required")
    if not get_evaluation_detail(evaluation_id):
        raise ValueError("Evaluation not found")
    db = get_db()
    if sort_order is None:
        row = db.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM evaluation_variants WHERE evaluation_id = ?",
            (evaluation_id,),
        ).fetchone()
        sort_order = int(row["next_order"] if row else 1)
    cur = db.execute(
        """INSERT INTO evaluation_variants
           (evaluation_id, label, goal, comparison_variable, generation_plan_snapshot, sort_order)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            evaluation_id,
            str(label).strip(),
            str(goal).strip(),
            comparison_variable,
            _json_text(generation_plan_snapshot),
            sort_order,
        ),
    )
    db.commit()
    variant_id = int(cur.lastrowid)
    db.close()
    return variant_id


def create_evaluation_attempt(
    evaluation_id: int,
    variant_id: int,
    *,
    run_id: int | None,
    batch_id: int | None = None,
    label: str,
    attempt_index: int = 1,
    snapshot: dict | str | None = None,
    status: str | None = None,
) -> int:
    if not label or not str(label).strip():
        raise ValueError("Attempt label is required")
    db = get_db()
    variant = db.execute(
        "SELECT id FROM evaluation_variants WHERE id = ? AND evaluation_id = ?",
        (variant_id, evaluation_id),
    ).fetchone()
    db.close()
    if not variant:
        raise ValueError("Variant not found for evaluation")
    run = get_run(run_id) if run_id is not None else None
    if status is None and run:
        status = run.get("status")
    cur_db = get_db()
    cur = cur_db.execute(
        """INSERT INTO evaluation_attempts
           (evaluation_id, variant_id, run_id, batch_id, label, attempt_index, status, snapshot, run_missing)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            evaluation_id,
            variant_id,
            run_id,
            batch_id if batch_id is not None else (run.get("batch_id") if run else None),
            str(label).strip(),
            int(attempt_index),
            status,
            _json_text(snapshot),
            0 if run_id is None or run else 1,
        ),
    )
    cur_db.commit()
    attempt_id = int(cur.lastrowid)
    cur_db.close()
    return attempt_id


def set_evaluation_variant_representative(variant_id: int, attempt_id: int | None) -> dict | None:
    db = get_db()
    if attempt_id is not None:
        attempt = db.execute(
            "SELECT id FROM evaluation_attempts WHERE id = ? AND variant_id = ?",
            (attempt_id, variant_id),
        ).fetchone()
        if not attempt:
            db.close()
            raise ValueError("Representative attempt must belong to the Variant")
    db.execute(
        """UPDATE evaluation_variants
           SET representative_attempt_id = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (attempt_id, variant_id),
    )
    db.commit()
    row = db.execute("SELECT evaluation_id FROM evaluation_variants WHERE id = ?", (variant_id,)).fetchone()
    db.close()
    return get_evaluation_detail(row["evaluation_id"]) if row else None


def update_evaluation_variant(variant_id: int, **fields) -> dict | None:
    allowed = {"label", "goal", "comparison_variable", "generation_plan_snapshot"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        db = get_db()
        row = db.execute("SELECT evaluation_id FROM evaluation_variants WHERE id = ?", (variant_id,)).fetchone()
        db.close()
        return get_evaluation_detail(row["evaluation_id"]) if row else None
    if "generation_plan_snapshot" in updates:
        updates["generation_plan_snapshot"] = _json_text(updates["generation_plan_snapshot"])
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    values = [*updates.values(), variant_id]
    db = get_db()
    db.execute(
        f"""UPDATE evaluation_variants
            SET {set_clause}, updated_at = datetime('now')
            WHERE id = ?""",
        values,
    )
    db.commit()
    row = db.execute("SELECT evaluation_id FROM evaluation_variants WHERE id = ?", (variant_id,)).fetchone()
    db.close()
    return get_evaluation_detail(row["evaluation_id"]) if row else None


def list_evaluations() -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT e.*, d.title AS deck_title,
                  COUNT(DISTINCT v.id) AS variant_count,
                  COUNT(DISTINCT a.id) AS attempt_count
           FROM evaluations e
           JOIN decks d ON d.id = e.deck_id
           LEFT JOIN evaluation_variants v ON v.evaluation_id = e.id
           LEFT JOIN evaluation_attempts a ON a.evaluation_id = e.id
           GROUP BY e.id
           ORDER BY e.created_at DESC, e.id DESC"""
    ).fetchall()
    db.close()
    items = rows_to_list(rows)
    for item in items:
        item["export_config"] = _json_object(item.get("export_config"))
    return items


def _evaluation_attempt_with_slides(attempt: dict) -> dict:
    run = get_run(attempt["run_id"]) if attempt.get("run_id") else None
    attempt["snapshot"] = _json_object(attempt.get("snapshot"))
    attempt["run_missing"] = bool(attempt.get("run_id") and not run)
    attempt["slides"] = list_run_slides(attempt["run_id"]) if run else []
    if run:
        attempt["run_status"] = run.get("status")
        attempt["deck_id"] = run.get("deck_id")
        attempt["deck_title"] = run.get("deck_title")
        attempt["engine"] = run.get("engine")
        attempt["strategy"] = run.get("strategy")
    return attempt


def get_evaluation_detail(evaluation_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        """SELECT e.*, d.title AS deck_title
           FROM evaluations e
           JOIN decks d ON d.id = e.deck_id
           WHERE e.id = ?""",
        (evaluation_id,),
    ).fetchone()
    if not row:
        db.close()
        return None
    evaluation = row_to_dict(row)
    evaluation["export_config"] = _json_object(evaluation.get("export_config"))
    variant_rows = db.execute(
        """SELECT *
           FROM evaluation_variants
           WHERE evaluation_id = ?
           ORDER BY sort_order, id""",
        (evaluation_id,),
    ).fetchall()
    variants: list[dict] = []
    representative_attempts: list[dict] = []
    for variant_row in variant_rows:
        variant = row_to_dict(variant_row)
        variant["generation_plan_snapshot"] = _json_object(variant.get("generation_plan_snapshot"))
        attempt_rows = db.execute(
            """SELECT *
               FROM evaluation_attempts
               WHERE variant_id = ?
               ORDER BY attempt_index, id""",
            (variant["id"],),
        ).fetchall()
        attempts = [_evaluation_attempt_with_slides(row_to_dict(attempt_row)) for attempt_row in attempt_rows]
        variant["attempts"] = attempts
        representative_id = variant.get("representative_attempt_id")
        representative = next((attempt for attempt in attempts if attempt["id"] == representative_id), None)
        if representative:
            representative_attempts.append(representative)
        variants.append(variant)
    db.close()
    evaluation["variants"] = variants
    evaluation["representative_attempts"] = representative_attempts
    evaluation["notes"] = _list_evaluation_notes(evaluation_id)
    evaluation["issue_tags"] = _list_evaluation_issue_tags(evaluation_id)
    evaluation["slide_tags"] = _list_evaluation_slide_tags(evaluation_id)
    evaluation["machine_qa"] = _list_evaluation_machine_qa(evaluation_id)
    return evaluation


def _list_evaluation_notes(evaluation_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT *
           FROM evaluation_notes
           WHERE evaluation_id = ?
           ORDER BY slide_position, id""",
        (evaluation_id,),
    ).fetchall()
    db.close()
    return rows_to_list(rows)


def _list_evaluation_issue_tags(evaluation_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT *
           FROM evaluation_issue_tags
           WHERE evaluation_id = ?
           ORDER BY source, label""",
        (evaluation_id,),
    ).fetchall()
    db.close()
    return rows_to_list(rows)


def _list_evaluation_slide_tags(evaluation_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT st.*, it.label, it.source
           FROM evaluation_slide_tags st
           JOIN evaluation_issue_tags it ON it.id = st.tag_id
           WHERE st.evaluation_id = ?
           ORDER BY st.slide_position, st.id""",
        (evaluation_id,),
    ).fetchall()
    db.close()
    return rows_to_list(rows)


def _list_evaluation_machine_qa(evaluation_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT *
           FROM evaluation_machine_qa
           WHERE evaluation_id = ?
           ORDER BY slide_position, id""",
        (evaluation_id,),
    ).fetchall()
    db.close()
    items = rows_to_list(rows)
    for item in items:
        item["issues"] = _json_value(item.pop("issues_json", None))
    return items


def list_machine_qa_for_run(run_id: int) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT qa.*,
                      e.title AS evaluation_title,
                      e.status AS evaluation_status,
                      a.run_id AS attempt_run_id,
                      a.batch_id AS attempt_batch_id,
                      a.label AS attempt_label,
                      a.status AS attempt_status,
                      v.label AS variant_label
               FROM evaluation_machine_qa qa
               JOIN evaluations e ON e.id = qa.evaluation_id
               LEFT JOIN evaluation_attempts a ON a.id = qa.attempt_id
               LEFT JOIN evaluation_variants v ON v.id = a.variant_id
               WHERE qa.run_slide_id IN (
                        SELECT id FROM run_slides WHERE run_id = ?
                     )
               ORDER BY qa.slide_position, qa.id""",
            (run_id,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        db.close()
        if "no such table" in str(e).lower():
            return []
        raise
    db.close()
    items = rows_to_list(rows)
    for item in items:
        item["issues"] = _json_value(item.pop("issues_json", None))
        item["run_id"] = item.pop("attempt_run_id", None) or run_id
    return items


# ---------------------------------------------------------------------------
# configs CRUD
# ---------------------------------------------------------------------------

def list_configs() -> list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM configs ORDER BY id").fetchall()
    db.close()
    return rows_to_list(rows)


def get_config(config_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    db.close()
    return row_to_dict(row)


def get_config_by_name(name: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM configs WHERE name = ?", (name,)).fetchone()
    db.close()
    return row_to_dict(row)


def create_config(
    name: str,
    designer: dict,
    html_agent: dict,
    timeout_minutes: int = 30,
    max_concurrent_runs: int = 2,
    designer_profile_id: int | None = None,
    html_agent_profile_id: int | None = None,
    route_model_bindings: dict | str | None = None,
    is_default: bool = False,
    config_type: str = "html",
) -> int:
    db = get_db()
    config_type = normalize_config_type(config_type)
    if isinstance(route_model_bindings, dict):
        route_model_bindings = json.dumps(route_model_bindings, ensure_ascii=False)
    cur = db.execute(
        """INSERT INTO configs
           (name, type, designer, html_agent, timeout_minutes, max_concurrent_runs,
            designer_profile_id, html_agent_profile_id, route_model_bindings, is_default)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            config_type,
            json.dumps(designer, ensure_ascii=False),
            json.dumps(html_agent, ensure_ascii=False),
            timeout_minutes,
            max_concurrent_runs,
            designer_profile_id,
            html_agent_profile_id,
            route_model_bindings,
            1 if is_default else 0,
        ),
    )
    db.commit()
    config_id = cur.lastrowid
    db.close()
    return config_id


def update_config(config_id: int, **fields) -> bool:
    if not fields:
        return False
    if "auto_spill_profile_id" in fields:
        raise ValueError("auto_spill_profile_id is no longer a Combination field")
    allowed_fields = {
        "name",
        "type",
        "designer",
        "html_agent",
        "designer_profile_id",
        "html_agent_profile_id",
        "route_model_bindings",
        "is_default",
        "timeout_minutes",
        "max_concurrent_runs",
    }
    unknown_fields = set(fields) - allowed_fields
    if unknown_fields:
        raise ValueError(f"Unsupported Config field: {sorted(unknown_fields)[0]}")
    if "type" in fields:
        fields["type"] = normalize_config_type(fields["type"])
    # Serialize dicts to JSON strings for structured config fields.
    for key in ("designer", "html_agent", "route_model_bindings"):
        if key in fields and isinstance(fields[key], dict):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [config_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE configs SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_config(config_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM configs WHERE id = ?", (config_id,))
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


# ---------------------------------------------------------------------------
# decks CRUD
# ---------------------------------------------------------------------------

def list_decks() -> list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM decks ORDER BY id").fetchall()
    db.close()
    return rows_to_list(rows)


def get_deck(deck_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
    db.close()
    return row_to_dict(row)


def create_deck(title: str, content: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO decks (title, content) VALUES (?, ?)",
        (title, content),
    )
    db.commit()
    deck_id = cur.lastrowid
    db.close()
    return deck_id


def update_deck(deck_id: int, **fields) -> bool:
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [deck_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE decks SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_deck(deck_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


# ---------------------------------------------------------------------------
# requirements CRUD
# ---------------------------------------------------------------------------

def list_requirements() -> list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM requirements ORDER BY id").fetchall()
    db.close()
    return rows_to_list(rows)


def get_requirement(requirement_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM requirements WHERE id = ?", (requirement_id,)).fetchone()
    db.close()
    return row_to_dict(row)


def create_requirement(title: str, content: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO requirements (title, content) VALUES (?, ?)",
        (title, content),
    )
    db.commit()
    req_id = cur.lastrowid
    db.close()
    return req_id


def update_requirement(requirement_id: int, **fields) -> bool:
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [requirement_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE requirements SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_requirement(requirement_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM requirements WHERE id = ?", (requirement_id,))
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


# ---------------------------------------------------------------------------
# colors CRUD
# ---------------------------------------------------------------------------

def list_colors() -> list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM colors ORDER BY id").fetchall()
    db.close()
    return rows_to_list(rows)


def get_color(color_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM colors WHERE id = ?", (color_id,)).fetchone()
    db.close()
    return row_to_dict(row)


def create_color(title: str, content: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO colors (title, content) VALUES (?, ?)",
        (title, content),
    )
    db.commit()
    color_id = cur.lastrowid
    db.close()
    return color_id


def update_color(color_id: int, **fields) -> bool:
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [color_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE colors SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_color(color_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM colors WHERE id = ?", (color_id,))
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


# ---------------------------------------------------------------------------
# slides CRUD
# ---------------------------------------------------------------------------

def list_slides(deck_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM slides WHERE deck_id = ? ORDER BY position",
        (deck_id,),
    ).fetchall()
    db.close()
    return rows_to_list(rows)


def get_slide(slide_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM slides WHERE id = ?", (slide_id,)).fetchone()
    db.close()
    return row_to_dict(row)


def create_slide(deck_id: int, position: int, title: str, content: str, split_mode: str = "manual") -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO slides (deck_id, position, title, content, split_mode) VALUES (?, ?, ?, ?, ?)",
        (deck_id, position, title, content, split_mode),
    )
    db.commit()
    slide_id = cur.lastrowid
    db.close()
    return slide_id


def update_slide(slide_id: int, **fields) -> bool:
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [slide_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE slides SET {set_clause} WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_slide(slide_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM slides WHERE id = ?", (slide_id,))
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_slides_for_deck(deck_id: int) -> int:
    """Delete all slides belonging to a deck. Returns count deleted."""
    db = get_db()
    cur = db.execute("DELETE FROM slides WHERE deck_id = ?", (deck_id,))
    db.commit()
    count = cur.rowcount
    db.close()
    return count


def bulk_create_slides(deck_id: int, slides_data: list[dict]) -> list[int]:
    """Insert multiple slides at once. Each dict: {title, content, split_mode}.
    Position is auto-assigned sequentially starting from 1."""
    db = get_db()
    ids = []
    for i, s in enumerate(slides_data, start=1):
        cur = db.execute(
            "INSERT INTO slides (deck_id, position, title, content, split_mode) VALUES (?, ?, ?, ?, ?)",
            (deck_id, i, s["title"], s["content"], s.get("split_mode", "manual")),
        )
        ids.append(cur.lastrowid)
    db.commit()
    db.close()
    return ids


# ---------------------------------------------------------------------------
# batches CRUD and summaries
# ---------------------------------------------------------------------------

def create_batch(
    deck_id: int,
    config_id: int,
    requirement_ids: list[int],
    color_ids: list[int],
    designer_prompt_id: int | None = None,
    html_prompt_id: int | None = None,
    total_runs: int = 0,
) -> int:
    db = get_db()
    cur = db.execute(
        """INSERT INTO batches
           (deck_id, config_id, designer_prompt_id, html_prompt_id, total_runs)
           VALUES (?, ?, ?, ?, ?)""",
        (deck_id, config_id, designer_prompt_id, html_prompt_id, total_runs),
    )
    batch_id = cur.lastrowid
    for requirement_id in requirement_ids:
        db.execute(
            "INSERT OR IGNORE INTO batch_requirements (batch_id, requirement_id) VALUES (?, ?)",
            (batch_id, requirement_id),
        )
    for color_id in color_ids:
        db.execute(
            "INSERT OR IGNORE INTO batch_colors (batch_id, color_id) VALUES (?, ?)",
            (batch_id, color_id),
        )
    db.commit()
    db.close()
    return batch_id


def get_batch(batch_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        """SELECT b.*,
                  d.title AS deck_title,
                  cfg.name AS config_name,
                  dp.version AS designer_prompt_version,
                  hp.version AS html_prompt_version
           FROM batches b
           JOIN decks d ON b.deck_id = d.id
           JOIN configs cfg ON b.config_id = cfg.id
           LEFT JOIN prompts dp ON b.designer_prompt_id = dp.id
           LEFT JOIN prompts hp ON b.html_prompt_id = hp.id
           WHERE b.id = ?""",
        (batch_id,),
    ).fetchone()
    db.close()
    return row_to_dict(row)


def get_batch_summary(batch_id: int) -> dict | None:
    batch = get_batch(batch_id)
    if not batch:
        return None
    db = get_db()
    rows = db.execute(
        "SELECT status, COUNT(*) AS cnt FROM runs WHERE batch_id = ? GROUP BY status",
        (batch_id,),
    ).fetchall()
    route_row = db.execute(
        "SELECT id, engine, strategy, error_message FROM runs WHERE batch_id = ? ORDER BY id LIMIT 1",
        (batch_id,),
    ).fetchone()
    error_row = db.execute(
        """SELECT error_message FROM runs
           WHERE batch_id = ? AND error_message IS NOT NULL AND error_message != ''
           ORDER BY id DESC LIMIT 1""",
        (batch_id,),
    ).fetchone()
    requirement_rows = db.execute(
        """SELECT req.id, req.title
           FROM batch_requirements br
           JOIN requirements req ON br.requirement_id = req.id
           WHERE br.batch_id = ?
           ORDER BY req.id""",
        (batch_id,),
    ).fetchall()
    color_rows = db.execute(
        """SELECT c.id, c.title
           FROM batch_colors bc
           JOIN colors c ON bc.color_id = c.id
           WHERE bc.batch_id = ?
           ORDER BY c.id""",
        (batch_id,),
    ).fetchall()
    db.close()
    counts = {row["status"]: row["cnt"] for row in rows}
    total_runs = batch.get("total_runs") or sum(counts.values())
    completed_with_failures_runs = counts.get("completed_with_failures", 0)
    failed_runs = counts.get("failed", 0)
    timed_out_runs = counts.get("timed_out", 0)
    batch.update(
        {
            "total_runs": total_runs,
            "queued_runs": counts.get("queued", 0) + counts.get("pending", 0),
            "running_runs": counts.get("running", 0),
            "completed_runs": counts.get("completed", 0),
            "completed_with_failures_runs": completed_with_failures_runs,
            "failed_runs": failed_runs,
            "timed_out_runs": timed_out_runs,
            "failure_rate": (failed_runs + timed_out_runs) / total_runs if total_runs else 0,
            "engine": route_row["engine"] if route_row else "html",
            "strategy": route_row["strategy"] if route_row else "html_default",
            "representative_run_id": route_row["id"] if route_row else None,
            "error_message": error_row["error_message"] if error_row else batch.get("error_message"),
            "requirements": [{"id": row["id"], "title": row["title"]} for row in requirement_rows],
            "colors": [{"id": row["id"], "title": row["title"]} for row in color_rows],
        }
    )
    return batch


def list_batch_summaries() -> list[dict]:
    backfill_legacy_batches()
    db = get_db()
    rows = db.execute("SELECT id FROM batches ORDER BY id DESC").fetchall()
    db.close()
    return [summary for row in rows if (summary := get_batch_summary(row["id"]))]


def list_runs_for_batch(batch_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT r.*,
                  d.title AS deck_title,
                  req.title AS requirement_title,
                  c.title AS color_title,
                  cfg.name AS config_name
           FROM runs r
           JOIN decks d ON r.deck_id = d.id
           JOIN requirements req ON r.requirement_id = req.id
           JOIN colors c ON r.color_id = c.id
           JOIN configs cfg ON r.config_id = cfg.id
           WHERE r.batch_id = ?
           ORDER BY r.id""",
        (batch_id,),
    ).fetchall()
    db.close()
    runs = rows_to_list(rows)
    for run in runs:
        run["progress"] = get_run_progress(run["id"])
    return runs


def get_batch_detail(batch_id: int) -> dict | None:
    batch = get_batch_summary(batch_id)
    if not batch:
        return None
    batch["runs"] = list_runs_for_batch(batch_id)
    batch_history = list_generation_history(scope="batch", target_id=batch_id)
    for item in batch_history:
        created_batch_id = item.get("created_batch_id")
        created_batch = get_batch_summary(created_batch_id) if created_batch_id else None
        item["created_batch_status"] = created_batch.get("status") if created_batch else None
    batch["generation_history"] = batch_history
    return batch


def get_batch_config(batch_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        """SELECT cfg.*
           FROM batches b
           JOIN configs cfg ON b.config_id = cfg.id
           WHERE b.id = ?""",
        (batch_id,),
    ).fetchone()
    db.close()
    return row_to_dict(row)


def count_running_runs_for_batch(batch_id: int) -> int:
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS count FROM runs WHERE batch_id = ? AND status = 'running'",
        (batch_id,),
    ).fetchone()
    db.close()
    return int(row["count"] if row else 0)


def count_running_runs_for_config(config_id: int) -> int:
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS count FROM runs WHERE config_id = ? AND status = 'running'",
        (config_id,),
    ).fetchone()
    db.close()
    return int(row["count"] if row else 0)


def count_running_runs() -> int:
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS count FROM runs WHERE status = 'running'",
    ).fetchone()
    db.close()
    return int(row["count"] if row else 0)


def list_pending_runs_for_batch(batch_id: int, limit: int | None = None) -> list[dict]:
    db = get_db()
    query = (
        "SELECT * FROM runs "
        "WHERE batch_id = ? AND status IN ('pending', 'queued') "
        "ORDER BY id"
    )
    params: list[object] = [batch_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(max(0, int(limit)))
    rows = db.execute(query, params).fetchall()
    db.close()
    return rows_to_list(rows)


def claim_pending_runs_for_batch(
    batch_id: int,
    limit: int,
    *,
    config_id: int | None = None,
    max_running_for_config: int | None = None,
    max_running_global: int | None = None,
) -> list[dict]:
    """Atomically claim pending runs for launch by moving them to running."""
    limit = max(0, int(limit or 0))
    if limit == 0:
        return []
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        if max_running_global is not None:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM runs WHERE status = 'running'",
            ).fetchone()
            running_global = int(row["count"] if row else 0)
            limit = min(limit, max(0, int(max_running_global) - running_global))
            if limit == 0:
                db.commit()
                return []
        if config_id is not None and max_running_for_config is not None:
            row = db.execute(
                """SELECT COUNT(*) AS count
                   FROM runs
                   WHERE config_id = ? AND status = 'running'""",
                (config_id,),
            ).fetchone()
            running_for_config = int(row["count"] if row else 0)
            limit = min(limit, max(0, int(max_running_for_config) - running_for_config))
            if limit == 0:
                db.commit()
                return []
        rows = db.execute(
            """SELECT * FROM runs
               WHERE batch_id = ? AND status IN ('pending', 'queued')
               ORDER BY id
               LIMIT ?""",
            (batch_id, limit),
        ).fetchall()
        run_ids = [row["id"] for row in rows]
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            db.execute(
                f"""UPDATE runs
                    SET status = 'running', started_at = COALESCE(started_at, datetime('now'))
                    WHERE id IN ({placeholders}) AND status IN ('pending', 'queued')""",
                run_ids,
            )
        db.commit()
        if not run_ids:
            return []
        claimed = db.execute(
            f"SELECT * FROM runs WHERE id IN ({placeholders}) ORDER BY id",
            run_ids,
        ).fetchall()
        return rows_to_list(claimed)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_active_batch_summary() -> dict | None:
    backfill_legacy_batches()
    db = get_db()
    row = db.execute(
        """SELECT id FROM batches
           WHERE status IN ('queued', 'running')
           ORDER BY id DESC
           LIMIT 1"""
    ).fetchone()
    db.close()
    return get_batch_summary(row["id"]) if row else None


def get_default_timeout_minutes() -> int:
    db = get_db()
    row = db.execute("SELECT timeout_minutes FROM configs ORDER BY id LIMIT 1").fetchone()
    db.close()
    return int(row["timeout_minutes"]) if row and row["timeout_minutes"] else 30


def reconcile_run_statuses(timeout_minutes: int = 30) -> None:
    """Reconcile stale and aggregate run states before status reads."""
    timeout_minutes = max(1, int(timeout_minutes or 30))
    db = get_db()
    db.execute(
        """UPDATE runs
           SET status = 'timed_out',
               error_message = COALESCE(error_message, 'Run exceeded configured timeout'),
               completed_at = COALESCE(completed_at, datetime('now'))
           WHERE status IN ('queued', 'pending', 'running')
             AND COALESCE(started_at, created_at) <= datetime('now', ?)""",
        (f"-{timeout_minutes} minutes",),
    )
    db.commit()
    candidate_rows = db.execute(
        """SELECT id, engine, strategy
           FROM runs
           WHERE status IN ('queued', 'pending', 'running')"""
    ).fetchall()
    db.close()

    for row in candidate_rows:
        progress = get_run_progress(row["id"])
        if progress["total"] <= 0:
            continue
        if progress["pending"] > 0 or progress["running"] > 0:
            continue
        if progress["completed"] == progress["total"]:
            update_run(row["id"], status="completed", completed_at=current_timestamp())
        elif progress["completed"] > 0 and progress["failed"] > 0:
            terminal_status = (
                "completed_with_failures"
                if row["engine"] == "image" and row["strategy"] == "image_3_0"
                else "failed"
            )
            update_run(
                row["id"],
                status=terminal_status,
                completed_at=current_timestamp(),
            )
        elif progress["failed"] == progress["total"]:
            update_run(row["id"], status="failed", completed_at=current_timestamp())

    update_batch_statuses()


def current_timestamp() -> str:
    db = get_db()
    row = db.execute("SELECT datetime('now') AS now").fetchone()
    db.close()
    return row["now"]


def update_batch_statuses() -> None:
    db = get_db()
    batch_rows = db.execute("SELECT id FROM batches").fetchall()
    for batch in batch_rows:
        rows = db.execute(
            "SELECT status, COUNT(*) AS cnt FROM runs WHERE batch_id = ? GROUP BY status",
            (batch["id"],),
        ).fetchall()
        counts = {row["status"]: row["cnt"] for row in rows}
        total = sum(counts.values())
        if total == 0:
            status = "queued"
        elif counts.get("running", 0) > 0:
            status = "running"
        elif counts.get("queued", 0) > 0 or counts.get("pending", 0) > 0:
            status = "queued"
        elif counts.get("failed", 0) > 0:
            status = "failed"
        elif counts.get("timed_out", 0) > 0:
            status = "timed_out"
        elif counts.get("completed_with_failures", 0) > 0:
            status = "completed_with_failures"
        elif counts.get("completed", 0) == total:
            status = "completed"
        else:
            status = "running"
        completed_at_clause = (
            ", completed_at = COALESCE(completed_at, datetime('now'))"
            if status in {"completed", "completed_with_failures", "failed", "timed_out"}
            else ""
        )
        db.execute(
            f"UPDATE batches SET status = ?{completed_at_clause} WHERE id = ?",
            (status, batch["id"]),
        )
    db.commit()
    db.close()


def backfill_legacy_batches() -> int:
    """Create one legacy batch per unbatched run so existing history survives."""
    db = get_db()
    rows = db.execute(
        """SELECT id, deck_id, config_id, designer_prompt_id, html_prompt_id, status, created_at
           FROM runs
           WHERE batch_id IS NULL
           ORDER BY id"""
    ).fetchall()
    count = 0
    for row in rows:
        cur = db.execute(
            """INSERT INTO batches
               (deck_id, config_id, designer_prompt_id, html_prompt_id, status, total_runs, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (
                row["deck_id"],
                row["config_id"],
                row["designer_prompt_id"],
                row["html_prompt_id"],
                row["status"],
                row["created_at"],
            ),
        )
        db.execute("UPDATE runs SET batch_id = ? WHERE id = ?", (cur.lastrowid, row["id"]))
        count += 1
    db.commit()
    db.close()
    return count


# ---------------------------------------------------------------------------
# runs CRUD
# ---------------------------------------------------------------------------

def list_runs() -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT r.*,
                  d.title AS deck_title,
                  req.title AS requirement_title,
                  c.title AS color_title,
                  cfg.name AS config_name
           FROM runs r
           JOIN decks d ON r.deck_id = d.id
           JOIN requirements req ON r.requirement_id = req.id
           JOIN colors c ON r.color_id = c.id
           JOIN configs cfg ON r.config_id = cfg.id
           ORDER BY r.id DESC"""
    ).fetchall()
    db.close()
    return rows_to_list(rows)


def get_run(run_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        """SELECT r.*,
                  d.title AS deck_title,
                  req.title AS requirement_title,
                  c.title AS color_title,
                  cfg.name AS config_name
           FROM runs r
           JOIN decks d ON r.deck_id = d.id
           JOIN requirements req ON r.requirement_id = req.id
           JOIN colors c ON r.color_id = c.id
           JOIN configs cfg ON r.config_id = cfg.id
           WHERE r.id = ?""",
        (run_id,),
    ).fetchone()
    db.close()
    return row_to_dict(row)


def create_run(
    deck_id: int,
    requirement_id: int,
    color_id: int,
    config_id: int,
    batch_id: int | None = None,
    engine: str = "html",
    strategy: str = "html_default",
    route_metadata: dict | str | None = None,
    stage_artifacts: dict | str | None = None,
    model_call_metadata: dict | str | None = None,
) -> int:
    db = get_db()
    if isinstance(route_metadata, dict):
        route_metadata = json.dumps(route_metadata, ensure_ascii=False)
    if isinstance(stage_artifacts, dict):
        stage_artifacts = json.dumps(stage_artifacts, ensure_ascii=False)
    if isinstance(model_call_metadata, dict):
        model_call_metadata = json.dumps(model_call_metadata, ensure_ascii=False)
    cur = db.execute(
        """INSERT INTO runs
           (batch_id, deck_id, requirement_id, color_id, config_id,
            engine, strategy, route_metadata, stage_artifacts, model_call_metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            batch_id,
            deck_id,
            requirement_id,
            color_id,
            config_id,
            engine,
            strategy,
            route_metadata,
            stage_artifacts,
            model_call_metadata,
        ),
    )
    db.commit()
    run_id = cur.lastrowid
    db.close()
    return run_id


def _merge_run_action_evidence(run_id: int, fields: dict) -> dict:
    stage_value = fields.get("stage_artifacts")
    model_value = fields.get("model_call_metadata")
    if stage_value is None and model_value is None:
        return fields
    db = get_db()
    row = db.execute(
        "SELECT stage_artifacts, model_call_metadata FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    db.close()
    if not row:
        return fields

    merged_fields = dict(fields)
    if stage_value is not None:
        next_stage = _parse_json_dict(stage_value)
        existing_stage = _parse_json_dict(row["stage_artifacts"])
        existing_lineage = existing_stage.get("lineage")
        if (
            next_stage
            and not isinstance(next_stage.get("lineage"), dict)
            and isinstance(existing_lineage, dict)
        ):
            next_stage["lineage"] = existing_lineage
            merged_fields["stage_artifacts"] = json.dumps(
                next_stage,
                ensure_ascii=False,
            )

    if model_value is not None:
        next_model = _parse_json_dict(model_value)
        existing_model = _parse_json_dict(row["model_call_metadata"])
        changed = False
        for key in ("action", "source_run_id"):
            if key not in next_model and key in existing_model:
                next_model[key] = existing_model[key]
                changed = True
        if changed:
            merged_fields["model_call_metadata"] = json.dumps(
                next_model,
                ensure_ascii=False,
            )
    return merged_fields


def update_run(run_id: int, **fields) -> bool:
    if not fields:
        return False
    fields = _merge_run_action_evidence(run_id, fields)
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [run_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE runs SET {set_clause} WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_run(run_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_batch(batch_id: int) -> bool:
    db = get_db()
    run_rows = db.execute("SELECT id FROM runs WHERE batch_id = ?", (batch_id,)).fetchall()
    for run in run_rows:
        db.execute("DELETE FROM run_slides WHERE run_id = ?", (run["id"],))
    db.execute("DELETE FROM runs WHERE batch_id = ?", (batch_id,))
    db.execute("DELETE FROM batch_requirements WHERE batch_id = ?", (batch_id,))
    db.execute("DELETE FROM batch_colors WHERE batch_id = ?", (batch_id,))
    cur = db.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


# ---------------------------------------------------------------------------
# run_slides CRUD
# ---------------------------------------------------------------------------

def list_run_slides(run_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT rs.*, s.title AS slide_title, s.content AS slide_content
           FROM run_slides rs
           JOIN slides s ON rs.slide_id = s.id
           WHERE rs.run_id = ?
           ORDER BY rs.position""",
        (run_id,),
    ).fetchall()
    db.close()
    slides = rows_to_list(rows)
    for slide in slides:
        active_version = get_active_artifact_version(slide["id"])
        if active_version:
            for key in ("html_path", "screenshot_path", "final_image_path", "clean_html", "xml_raw", "xml_clean", "raw_response"):
                if active_version.get(key):
                    slide[key] = active_version[key]
        slide["active_version"] = active_version
        _apply_sibling_screenshot_fallback(slide)
        slide["versions"] = list_artifact_versions(slide["id"])
        slide["generation_history"] = list_generation_history(target_run_slide_id=slide["id"])
        slide["has_displayable_artifact"] = bool(active_version) or _has_displayable_artifact(slide)
    return slides


def create_run_slide(run_id: int, slide_id: int, position: int, slide_type: str = "content") -> int:
    db = get_db()
    slide = db.execute("SELECT title, content FROM slides WHERE id = ?", (slide_id,)).fetchone()
    cur = db.execute(
        """INSERT INTO run_slides
           (run_id, slide_id, position, slide_type, slide_title_snapshot, slide_content_snapshot)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            slide_id,
            position,
            slide_type,
            slide["title"] if slide else None,
            slide["content"] if slide else None,
        ),
    )
    db.commit()
    rs_id = cur.lastrowid
    db.close()
    return rs_id


def update_run_slide(run_slide_id: int, **fields) -> bool:
    if not fields:
        return False
    fields = dict(fields)
    if "stage_artifacts" in fields:
        fields["stage_artifacts"] = _merge_stage_artifacts_with_existing_lineage(
            run_slide_id,
            fields["stage_artifacts"],
        )
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [run_slide_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE run_slides SET {set_clause} WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    if changed:
        sync_run_slide_artifact_state(run_slide_id)
    return changed


def _parse_json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validated_generation_action_lineage(
    lineage: dict[str, Any],
) -> dict[str, Any]:
    if set(lineage) != {
        "action",
        "scope",
        "force_mode",
        "source_target_id",
        "source_run_id",
        "source_batch_id",
        "source_run_slide_ids",
        "version_index",
        "retention_policy",
    }:
        return {}
    if lineage.get("action") not in {"retry", "auto_retry", "force_regenerate"}:
        return {}
    if lineage.get("scope") not in {"batch", "run", "slide", "image"}:
        return {}
    if lineage.get("force_mode") not in {
        None,
        "overwrite_current",
        "new_run",
        "new_batch",
    }:
        return {}
    source_batch_id = lineage.get("source_batch_id")
    source_slide_ids = lineage.get("source_run_slide_ids")
    if (
        (source_batch_id is not None and type(source_batch_id) is not int)
        or not isinstance(source_slide_ids, list)
        or not source_slide_ids
        or any(type(source_slide_id) is not int for source_slide_id in source_slide_ids)
        or type(lineage.get("source_target_id")) is not int
        or type(lineage.get("source_run_id")) is not int
        or type(lineage.get("version_index")) is not int
        or lineage.get("retention_policy")
        != {"mode": "preserve_source", "cleanup": "manual_future_task"}
    ):
        return {}
    return lineage


def _json_dumps_or_none(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Codex audit CRUD
# ---------------------------------------------------------------------------

def create_codex_invocation(
    *,
    run_id: int | None = None,
    run_slide_id: int | None = None,
    stage_id: str | None = None,
    role: str | None = None,
    attempt: int = 1,
    status: str | None = None,
    command: object | None = None,
    cwd: str | None = None,
    sandbox: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    prompt_sha256: str | None = None,
    raw_jsonl_path: str | None = None,
    observed_jsonl_path: str | None = None,
    output_path: str | None = None,
    raw_jsonl_sha256: str | None = None,
    observed_jsonl_sha256: str | None = None,
    output_sha256: str | None = None,
    event_count: int = 0,
    error_event_count: int = 0,
    usage: object | None = None,
    exit_code: int | None = None,
    error_message: str | None = None,
    metadata: object | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    elapsed_ms: int | None = None,
) -> int:
    db = get_db()
    cur = db.execute(
        """INSERT INTO codex_invocations
           (run_id, run_slide_id, stage_id, role, attempt, status, command_json,
            cwd, sandbox, model, reasoning_effort, prompt_sha256, raw_jsonl_path,
            observed_jsonl_path, output_path, raw_jsonl_sha256, observed_jsonl_sha256,
            output_sha256, event_count, error_event_count, usage_json, exit_code,
            error_message, metadata, started_at, ended_at, elapsed_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            run_slide_id,
            stage_id,
            role,
            attempt,
            status,
            _json_dumps_or_none(command),
            cwd,
            sandbox,
            model,
            reasoning_effort,
            prompt_sha256,
            raw_jsonl_path,
            observed_jsonl_path,
            output_path,
            raw_jsonl_sha256,
            observed_jsonl_sha256,
            output_sha256,
            event_count,
            error_event_count,
            _json_dumps_or_none(usage),
            exit_code,
            error_message,
            _json_dumps_or_none(metadata),
            started_at,
            ended_at,
            elapsed_ms,
        ),
    )
    invocation_id = cur.lastrowid
    db.commit()
    db.close()
    return invocation_id


def append_codex_event(
    *,
    invocation_id: int,
    sequence: int,
    payload: dict,
    observed_at: str,
    event_timestamp: str | None = None,
    event_type: str | None = None,
    item_id: str | None = None,
    item_type: str | None = None,
    is_error: bool = False,
) -> int:
    db = get_db()
    cur = db.execute(
        """INSERT INTO codex_events
           (invocation_id, sequence, event_type, observed_at, event_timestamp,
            item_id, item_type, is_error, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            invocation_id,
            sequence,
            event_type,
            observed_at,
            event_timestamp,
            item_id,
            item_type,
            1 if is_error else 0,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    event_id = cur.lastrowid
    db.commit()
    db.close()
    return event_id


def append_codex_event_with_raw_line(
    *,
    invocation_id: int,
    sequence: int,
    payload: dict,
    observed_at: str,
    raw_bytes: bytes,
    raw_sha256: str,
    byte_offset_start: int,
    byte_offset_end: int,
    file_identity: str | None,
    projection: dict,
    event_timestamp: str | None = None,
    event_type: str | None = None,
    item_id: str | None = None,
    item_type: str | None = None,
    is_error: bool = False,
) -> int:
    """Persist one projected Codex event and its exact source line atomically."""
    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO codex_events
               (invocation_id, sequence, event_type, observed_at, event_timestamp,
                item_id, item_type, is_error, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invocation_id,
                sequence,
                event_type,
                observed_at,
                event_timestamp,
                item_id,
                item_type,
                1 if is_error else 0,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        event_id = int(cur.lastrowid)
        db.execute(
            """INSERT INTO codex_event_raw_lines
               (invocation_id, sequence, raw_bytes, raw_sha256,
                byte_offset_start, byte_offset_end, file_identity, projection_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invocation_id,
                sequence,
                sqlite3.Binary(raw_bytes),
                raw_sha256,
                byte_offset_start,
                byte_offset_end,
                file_identity,
                json.dumps(projection, ensure_ascii=False),
            ),
        )
        db.execute(
            """UPDATE codex_invocations
               SET event_count = event_count + 1,
                   error_event_count = error_event_count + ?
               WHERE id = ?""",
            (1 if is_error else 0, invocation_id),
        )
        db.execute(
            """UPDATE codex_work_items
               SET last_event_sequence = ?, last_raw_offset = ?, updated_at = datetime('now')
               WHERE id IN (
                   SELECT work_item_id FROM codex_work_item_invocations
                   WHERE invocation_id = ?
               )""",
            (sequence, byte_offset_end, invocation_id),
        )
        db.commit()
        return event_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def inspect_codex_event_persistence_outcome(
    *,
    invocation_id: int,
    sequence: int,
    payload: dict,
    observed_at: str,
    raw_bytes: bytes,
    raw_sha256: str,
    byte_offset_start: int,
    byte_offset_end: int,
    file_identity: str | None,
    projection: dict,
) -> str:
    """Inspect a raised append through a fresh connection, never exception type.

    ``COMMITTED`` means both rows exactly match the caller's bounded
    projection.  ``NOT_COMMITTED`` is safe to compensate in the observed file;
    all partial or conflicting state is deliberately fail-closed.
    """
    try:
        db = get_db()
        try:
            event_row = db.execute(
                """SELECT observed_at, payload_json FROM codex_events
                   WHERE invocation_id = ? AND sequence = ?""",
                (invocation_id, sequence),
            ).fetchone()
            raw_row = db.execute(
                """SELECT raw_bytes, raw_sha256, byte_offset_start, byte_offset_end,
                          file_identity, projection_json
                   FROM codex_event_raw_lines
                   WHERE invocation_id = ? AND sequence = ?""",
                (invocation_id, sequence),
            ).fetchone()
        finally:
            db.close()
    except Exception:
        return "UNKNOWN"
    if event_row is None and raw_row is None:
        return "NOT_COMMITTED"
    if event_row is None or raw_row is None:
        return "CONFLICT"
    try:
        matches = (
            event_row["observed_at"] == observed_at
            and json.loads(event_row["payload_json"]) == payload
            and bytes(raw_row["raw_bytes"]) == raw_bytes
            and raw_row["raw_sha256"] == raw_sha256
            and int(raw_row["byte_offset_start"]) == byte_offset_start
            and int(raw_row["byte_offset_end"]) == byte_offset_end
            and raw_row["file_identity"] == file_identity
            and json.loads(raw_row["projection_json"]) == projection
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return "CONFLICT"
    return "COMMITTED" if matches else "CONFLICT"


def begin_codex_observed_publish_intent(
    *,
    invocation_id: int,
    generation_id: str,
    observed_path: str,
    temp_path: str,
    old_observed_sha256: str,
    intended_terminal_state: str,
) -> None:
    """Durably record the old observed identity before creating its replacement."""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """INSERT INTO codex_observed_publish_intents
               (invocation_id, state, generation_id, observed_path, temp_path,
                old_observed_sha256, intended_terminal_state)
               VALUES (?, 'PREPARING', ?, ?, ?, ?, ?)""",
            (
                invocation_id,
                generation_id,
                observed_path,
                temp_path,
                old_observed_sha256,
                intended_terminal_state,
            ),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def update_codex_observed_publish_intent_preparing(
    *,
    invocation_id: int,
    new_observed_sha256: str,
    expected_event_count: int,
) -> None:
    db = get_db()
    try:
        cursor = db.execute(
            """UPDATE codex_observed_publish_intents
               SET new_observed_sha256 = ?, expected_event_count = ?, updated_at = datetime('now')
               WHERE invocation_id = ? AND state = 'PREPARING'""",
            (new_observed_sha256, expected_event_count, invocation_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("observed publish intent is not PREPARING")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def record_codex_observed_publish_temp_identity(
    *,
    invocation_id: int,
    generation_id: str,
    temp_file_identity: str,
) -> None:
    """Bind a PREPARING reservation to the descriptor that created its temp."""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """UPDATE codex_observed_publish_intents
               SET temp_file_identity = ?, updated_at = datetime('now')
               WHERE invocation_id = ? AND generation_id = ? AND state = 'PREPARING'
                 AND temp_file_identity IS NULL""",
            (temp_file_identity, invocation_id, generation_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("observed publish intent cannot record temp identity")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _insert_codex_observed_publish_event(
    db: sqlite3.Connection,
    *,
    invocation_id: int,
    sequence: int,
    payload: dict,
    observed_at: str,
    raw_bytes: bytes,
    raw_sha256: str,
    byte_offset_start: int,
    byte_offset_end: int,
    file_identity: str | None,
    projection: dict,
    event_timestamp: str | None,
    event_type: str | None,
    item_id: str | None,
    item_type: str | None,
    is_error: bool,
) -> None:
    db.execute(
        """INSERT INTO codex_events
           (invocation_id, sequence, event_type, observed_at, event_timestamp,
            item_id, item_type, is_error, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            invocation_id,
            sequence,
            event_type,
            observed_at,
            event_timestamp,
            item_id,
            item_type,
            1 if is_error else 0,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    db.execute(
        """INSERT INTO codex_event_raw_lines
           (invocation_id, sequence, raw_bytes, raw_sha256,
            byte_offset_start, byte_offset_end, file_identity, projection_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            invocation_id,
            sequence,
            sqlite3.Binary(raw_bytes),
            raw_sha256,
            byte_offset_start,
            byte_offset_end,
            file_identity,
            json.dumps(projection, ensure_ascii=False),
        ),
    )


def commit_codex_observed_publish_batch(
    *,
    invocation_id: int,
    records: Iterable[dict],
) -> str:
    """Commit every legacy event/raw/counter/cursor row and PREPARED together."""
    db = get_db()
    record_count = 0
    error_count = 0
    last_sequence = 0
    last_offset = 0
    try:
        db.execute("BEGIN IMMEDIATE")
        intent = db.execute(
            """SELECT expected_event_count FROM codex_observed_publish_intents
               WHERE invocation_id = ? AND state = 'PREPARING'""",
            (invocation_id,),
        ).fetchone()
        if intent is None:
            raise RuntimeError("observed publish intent is not PREPARING")
        for record in records:
            _insert_codex_observed_publish_event(db, invocation_id=invocation_id, **record)
            record_count += 1
            error_count += 1 if record["is_error"] else 0
            last_sequence = int(record["sequence"])
            last_offset = int(record["byte_offset_end"])
        if record_count != int(intent["expected_event_count"]):
            raise RuntimeError("observed publish intent event count changed during batch")
        db.execute(
            """UPDATE codex_invocations
               SET event_count = event_count + ?, error_event_count = error_event_count + ?
               WHERE id = ?""",
            (record_count, error_count, invocation_id),
        )
        if record_count:
            db.execute(
                """UPDATE codex_work_items
                   SET last_event_sequence = ?, last_raw_offset = ?, updated_at = datetime('now')
                   WHERE id IN (
                       SELECT work_item_id FROM codex_work_item_invocations
                       WHERE invocation_id = ?
                   )""",
                (last_sequence, last_offset, invocation_id),
            )
        db.execute(
            """UPDATE codex_observed_publish_intents
               SET state = 'PREPARED', updated_at = datetime('now')
               WHERE invocation_id = ? AND state = 'PREPARING'""",
            (invocation_id,),
        )
        db.commit()
        return "COMMITTED"
    except BaseException:
        db.rollback()
        outcome = inspect_codex_observed_publish_intent_outcome(invocation_id)
        if outcome == "COMMITTED":
            return outcome
        raise
    finally:
        db.close()


def get_codex_observed_publish_intent(invocation_id: int) -> dict | None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM codex_observed_publish_intents WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        db.close()


def inspect_codex_observed_publish_intent_outcome(invocation_id: int) -> str:
    """Classify the batch via fresh rows after an uncertain commit exception."""
    try:
        db = get_db()
        try:
            intent = db.execute(
                """SELECT state, expected_event_count FROM codex_observed_publish_intents
                   WHERE invocation_id = ?""",
                (invocation_id,),
            ).fetchone()
            if intent is None:
                return "NOT_COMMITTED"
            counts = db.execute(
                """SELECT COUNT(*) AS event_count FROM codex_events
                   WHERE invocation_id = ?""",
                (invocation_id,),
            ).fetchone()
            raw_counts = db.execute(
                """SELECT COUNT(*) AS raw_count FROM codex_event_raw_lines
                   WHERE invocation_id = ?""",
                (invocation_id,),
            ).fetchone()
        finally:
            db.close()
    except Exception:
        return "UNKNOWN"
    if intent["state"] == "PREPARING" and int(counts["event_count"]) == 0 and int(raw_counts["raw_count"]) == 0:
        return "NOT_COMMITTED"
    if (
        intent["state"] == "PREPARED"
        and int(counts["event_count"]) == int(intent["expected_event_count"])
        and int(raw_counts["raw_count"]) == int(intent["expected_event_count"])
    ):
        return "COMMITTED"
    return "CONFLICT"


def complete_codex_observed_publish_intent(invocation_id: int) -> str:
    """Record terminal intent state and clear it in one durable transaction."""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        intent = db.execute(
            """SELECT intended_terminal_state FROM codex_observed_publish_intents
               WHERE invocation_id = ? AND state = 'PREPARED'""",
            (invocation_id,),
        ).fetchone()
        if intent is None:
            raise RuntimeError("observed publish intent is not PREPARED")
        db.execute(
            "UPDATE codex_invocations SET status = ? WHERE id = ?",
            (intent["intended_terminal_state"], invocation_id),
        )
        db.execute("DELETE FROM codex_observed_publish_intents WHERE invocation_id = ?", (invocation_id,))
        db.commit()
        return "COMMITTED"
    except BaseException:
        db.rollback()
        intent = get_codex_observed_publish_intent(invocation_id)
        if intent is None:
            return "COMMITTED"
        raise
    finally:
        db.close()


def clear_codex_observed_publish_intent(invocation_id: int) -> None:
    """Discard a PREPARING intent only when no batch rows were committed."""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        intent = db.execute(
            """SELECT state FROM codex_observed_publish_intents
               WHERE invocation_id = ?""",
            (invocation_id,),
        ).fetchone()
        counts = db.execute(
            "SELECT COUNT(*) AS count FROM codex_events WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        raw_counts = db.execute(
            "SELECT COUNT(*) AS count FROM codex_event_raw_lines WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if (
            intent is None
            or intent["state"] != "PREPARING"
            or int(counts["count"]) != 0
            or int(raw_counts["count"]) != 0
        ):
            raise RuntimeError("cannot clear a nonempty observed publish intent")
        db.execute("DELETE FROM codex_observed_publish_intents WHERE invocation_id = ?", (invocation_id,))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def get_codex_event_cursor(invocation_id: int) -> dict:
    """Return the durable cursor for the latest complete raw event line."""
    db = get_db()
    try:
        row = db.execute(
            """SELECT sequence, byte_offset_end, file_identity
               FROM codex_event_raw_lines
               WHERE invocation_id = ?
               ORDER BY sequence DESC
               LIMIT 1""",
            (invocation_id,),
        ).fetchone()
    finally:
        db.close()
    if row is None:
        return {"sequence": 0, "byte_offset_end": 0, "file_identity": None}
    return dict(row)


def mark_codex_invocation_recovery_blocked(invocation_id: int, reason: str) -> bool:
    db = get_db()
    try:
        cur = db.execute(
            """UPDATE codex_invocations
               SET status = 'recovery_blocked', error_message = ?
               WHERE id = ?""",
            (reason, invocation_id),
        )
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def list_codex_event_raw_lines_for_diagnostics(invocation_id: int) -> list[dict]:
    """Internal-only raw evidence read used by terminal reconciliation."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT invocation_id, sequence, raw_bytes, raw_sha256,
                      byte_offset_start, byte_offset_end, file_identity, projection_json
               FROM codex_event_raw_lines
               WHERE invocation_id = ?
               ORDER BY sequence""",
            (invocation_id,),
        ).fetchall()
        return rows_to_list(rows)
    finally:
        db.close()


def mark_codex_invocation_reconciliation_failed(invocation_id: int, reason: str) -> bool:
    db = get_db()
    try:
        cur = db.execute(
            """UPDATE codex_invocations
               SET status = 'reconciliation_failed', error_message = ?
               WHERE id = ?""",
            (reason, invocation_id),
        )
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def finalize_codex_invocation(
    invocation_id: int,
    *,
    status: str,
    output_path: str | None,
    raw_jsonl_sha256: str | None,
    observed_jsonl_sha256: str | None,
    output_sha256: str | None,
    event_count: int,
    error_event_count: int,
    usage: object | None,
    exit_code: int | None,
    error_message: str | None,
    metadata: object | None,
    ended_at: str | None,
    elapsed_ms: int | None,
) -> bool:
    """Finalize an already-streaming invocation without re-importing its events."""
    db = get_db()
    try:
        cur = db.execute(
            """UPDATE codex_invocations
               SET status = ?, output_path = ?, raw_jsonl_sha256 = ?,
                   observed_jsonl_sha256 = ?, output_sha256 = ?, event_count = ?,
                   error_event_count = ?, usage_json = ?, exit_code = ?,
                   error_message = ?, metadata = COALESCE(?, metadata), ended_at = ?, elapsed_ms = ?
               WHERE id = ?""",
            (
                status,
                output_path,
                raw_jsonl_sha256,
                observed_jsonl_sha256,
                output_sha256,
                event_count,
                error_event_count,
                _json_dumps_or_none(usage),
                exit_code,
                error_message,
                _json_dumps_or_none(metadata),
                ended_at,
                elapsed_ms,
                invocation_id,
            ),
        )
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def list_codex_events(invocation_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM codex_events WHERE invocation_id = ? ORDER BY sequence",
        (invocation_id,),
    ).fetchall()
    db.close()
    events = rows_to_list(rows)
    for event in events:
        payload = _json_value(event.get("payload_json"))
        event["payload"] = payload
        if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
            event["usage"] = payload["usage"]
    return events


def _codex_invocation_from_row(row, *, include_events: bool = True) -> dict:
    invocation = row_to_dict(row)
    invocation["command"] = _json_value(invocation.get("command_json"))
    invocation["usage"] = _json_value(invocation.get("usage_json"))
    invocation["metadata"] = _json_value(invocation.get("metadata"))
    if include_events:
        invocation["events"] = list_codex_events(int(invocation["id"]))
    return invocation


def get_codex_invocation(invocation_id: int, *, include_events: bool = True) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM codex_invocations WHERE id = ?", (invocation_id,)).fetchone()
    db.close()
    if not row:
        return None
    return _codex_invocation_from_row(row, include_events=include_events)


def list_codex_invocations(
    *,
    run_id: int | None = None,
    run_slide_id: int | None = None,
    include_events: bool = True,
) -> list[dict]:
    if run_id is None and run_slide_id is None:
        raise ValueError("run_id or run_slide_id is required")
    clauses: list[str] = []
    params: list[int] = []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if run_slide_id is not None:
        clauses.append("run_slide_id = ?")
        params.append(run_slide_id)
    db = get_db()
    rows = db.execute(
        f"SELECT * FROM codex_invocations WHERE {' AND '.join(clauses)} ORDER BY id",
        tuple(params),
    ).fetchall()
    db.close()
    return [_codex_invocation_from_row(row, include_events=include_events) for row in rows]


def _merge_stage_artifacts_with_existing_lineage(run_slide_id: int, stage_artifacts) -> str | None:
    next_artifacts = _parse_json_dict(stage_artifacts)
    if not next_artifacts:
        return _json_dumps_or_none(stage_artifacts)
    if isinstance(next_artifacts.get("lineage"), dict):
        return _json_dumps_or_none(next_artifacts) if not isinstance(stage_artifacts, str) else stage_artifacts
    db = get_db()
    row = db.execute("SELECT stage_artifacts FROM run_slides WHERE id = ?", (run_slide_id,)).fetchone()
    db.close()
    existing_artifacts = _parse_json_dict(row["stage_artifacts"] if row else None)
    lineage = existing_artifacts.get("lineage")
    if not isinstance(lineage, dict):
        return _json_dumps_or_none(next_artifacts) if not isinstance(stage_artifacts, str) else stage_artifacts
    merged = dict(next_artifacts)
    merged["lineage"] = lineage
    return json.dumps(merged, ensure_ascii=False)


def _get_run_slide_with_context(run_slide_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        """SELECT rs.*,
                  r.batch_id AS run_batch_id,
                  r.deck_id AS run_deck_id,
                  r.requirement_id AS run_requirement_id,
                  r.color_id AS run_color_id,
                  r.config_id AS run_config_id,
                  r.engine AS run_engine,
                  r.strategy AS run_strategy,
                  r.route_metadata AS run_route_metadata,
                  r.stage_artifacts AS run_stage_artifacts,
                  r.model_call_metadata AS run_model_call_metadata,
                  r.status AS run_status
           FROM run_slides rs
           JOIN runs r ON rs.run_id = r.id
           WHERE rs.id = ?""",
        (run_slide_id,),
    ).fetchone()
    db.close()
    return row_to_dict(row)


def _lineage_for_artifact_slide(slide: dict) -> dict:
    dedicated: dict = {}
    for value in (slide.get("run_stage_artifacts"), slide.get("stage_artifacts")):
        parsed = _parse_json_dict(value)
        candidate = parsed.get("lineage")
        if isinstance(candidate, dict):
            dedicated.update(candidate)
    if dedicated:
        return dedicated

    for value in (
        slide.get("run_route_metadata"),
        slide.get("run_stage_artifacts"),
        slide.get("stage_artifacts"),
    ):
        legacy = _validated_generation_action_lineage(_parse_json_dict(value))
        if legacy:
            return legacy
    return {}


def _has_displayable_artifact(slide: dict) -> bool:
    return bool(
        slide.get("status") == "completed"
        and (
            slide.get("final_image_path")
            or slide.get("screenshot_path")
            or slide.get("html_path")
            or slide.get("clean_html")
        )
    )


def _html_sibling_screenshot_path(html_path: str | None) -> str | None:
    if not html_path:
        return None
    try:
        candidate = Path(str(html_path)).with_suffix(".png")
    except (TypeError, ValueError):
        return None
    if candidate.exists() and candidate.is_file():
        return str(candidate)
    return None


def _apply_sibling_screenshot_fallback(slide: dict) -> None:
    if slide.get("screenshot_path"):
        return
    fallback = _html_sibling_screenshot_path(slide.get("html_path"))
    if not fallback:
        return
    slide["screenshot_path"] = fallback
    slide["screenshot_path_source"] = "html_sibling_png"
    active_version = slide.get("active_version")
    if isinstance(active_version, dict) and not active_version.get("screenshot_path"):
        updated_active = dict(active_version)
        updated_active["screenshot_path"] = fallback
        updated_active["screenshot_path_source"] = "html_sibling_png"
        slide["active_version"] = updated_active


def _version_target_run_slide_id(slide: dict, lineage: dict) -> int:
    explicit_target = lineage.get("version_target_run_slide_id")
    if explicit_target:
        try:
            return int(explicit_target)
        except (TypeError, ValueError):
            pass
    action = str(lineage.get("action") or "")
    force_mode = str(lineage.get("force_mode") or "")
    source_slide = lineage.get("source_run_slide_id")
    source_slides = lineage.get("source_run_slide_ids")
    if not source_slide and isinstance(source_slides, list) and len(source_slides) == 1:
        source_slide = source_slides[0]
    if source_slide and (action in {"retry", "auto_retry"} or force_mode == "overwrite_current"):
        try:
            return int(source_slide)
        except (TypeError, ValueError):
            pass
    return int(slide["id"])


def _find_prompt_path(value, ancestors: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            next_ancestors = (*ancestors, str(key).lower())
            if isinstance(item, str) and (str(key).lower().endswith("path") or str(key).lower() == "path"):
                if any("prompt" in part for part in next_ancestors):
                    return item
            found = _find_prompt_path(item, next_ancestors)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_prompt_path(item, ancestors)
            if found:
                return found
    return None


def _parse_json_or_text(value):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return {}
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw_response": value}


def _native_public_projection_from_slide(slide: dict) -> dict | None:
    stage_artifacts = _parse_json_dict(slide.get("stage_artifacts"))
    image = stage_artifacts.get("image")
    if not isinstance(image, dict):
        return None
    projection = image.get("native_public")
    return projection if isinstance(projection, dict) else None


def native_public_audit_marker_for_run_slide(run_slide_id: int) -> dict | None:
    """Return one verified, path-free Native marker for a rendered Run Slide.

    The marker is deliberately derived from the durable invocation identity and
    the already-persisted public stage projection.  A file hash supplements
    that identity binding; it never substitutes for it.
    """
    if type(run_slide_id) is not int or run_slide_id <= 0:
        return None
    slide = _get_run_slide_with_context(run_slide_id)
    if (
        not slide
        or type(slide.get("run_id")) is not int
        or slide.get("run_engine") != "image"
        or slide.get("run_strategy") != "image_3_0"
    ):
        return None
    stage_projection = _native_public_projection_from_slide(slide)
    if stage_projection is None:
        return None
    from backend.services import codex_audit

    candidates: list[dict] = []
    for invocation in list_codex_invocations(
        run_id=int(slide["run_id"]), run_slide_id=run_slide_id, include_events=False
    ):
        marker = codex_audit.native_public_audit_marker(invocation)
        if marker is None:
            continue
        binding = codex_audit.native_public_audit_binding(marker)
        if binding is None or binding["public_projection"] != stage_projection:
            continue
        image = binding["public_projection"].get("business_image")
        if not isinstance(image, dict) or not isinstance(image.get("sha256"), str):
            continue
        try:
            artifact_path = Path(str(slide.get("final_image_path") or ""))
            if (
                not artifact_path.is_file()
                or hashlib.sha256(artifact_path.read_bytes()).hexdigest() != image["sha256"]
            ):
                continue
        except OSError:
            continue
        candidates.append(marker)
    return candidates[0] if len(candidates) == 1 else None


def _evidence_snapshot_for_slide(slide: dict) -> dict:
    run_route_metadata = _parse_json_dict(slide.get("run_route_metadata"))
    run_stage_artifacts = _parse_json_dict(slide.get("run_stage_artifacts"))
    run_model_metadata = _parse_json_dict(slide.get("run_model_call_metadata"))
    slide_stage_artifacts = _parse_json_dict(slide.get("stage_artifacts"))
    seed_dependency = _parse_json_dict(slide.get("seed_dependency"))
    prompt_path = _find_prompt_path(slide_stage_artifacts) or _find_prompt_path(run_stage_artifacts)
    response = _parse_json_or_text(slide.get("raw_response"))
    if not isinstance(response, dict):
        response = {"value": response}
    response = {
        **response,
        "html_path": slide.get("html_path"),
        "screenshot_path": slide.get("screenshot_path"),
        "final_image_path": slide.get("final_image_path"),
        "error_message": slide.get("error_message"),
    }
    snapshot = {
        "prompt": {"path": prompt_path} if prompt_path else {},
        "config": {
            "engine": slide.get("run_engine"),
            "strategy": slide.get("run_strategy"),
            "route_metadata": run_route_metadata,
            "model_call_metadata": run_model_metadata,
        },
        "response": response,
        "blueprint_xml": slide.get("xml_clean") or slide.get("xml_raw") or "",
        "run_stage_artifacts": run_stage_artifacts,
        "slide_stage_artifacts": slide_stage_artifacts,
        "seed_dependency": seed_dependency,
        "route": {"engine": slide.get("run_engine"), "strategy": slide.get("run_strategy")},
    }
    marker = native_public_audit_marker_for_run_slide(int(slide["id"]))
    if marker is not None:
        snapshot["native_audit"] = marker
    return snapshot


def create_artifact_version(
    *,
    target_run_slide_id: int,
    artifact_run_slide_id: int,
    status: str = "available",
    html_path: str | None = None,
    screenshot_path: str | None = None,
    final_image_path: str | None = None,
    clean_html: str | None = None,
    xml_raw: str | None = None,
    xml_clean: str | None = None,
    raw_response: str | None = None,
    evidence_snapshot: dict | str | None = None,
    make_active: bool = False,
) -> int:
    artifact = _get_run_slide_with_context(artifact_run_slide_id)
    if not artifact:
        raise ValueError("artifact_run_slide_id not found")
    html_path = html_path if html_path is not None else artifact.get("html_path")
    screenshot_path = screenshot_path if screenshot_path is not None else artifact.get("screenshot_path")
    final_image_path = final_image_path if final_image_path is not None else artifact.get("final_image_path")
    clean_html = clean_html if clean_html is not None else artifact.get("clean_html")
    xml_raw = xml_raw if xml_raw is not None else artifact.get("xml_raw")
    xml_clean = xml_clean if xml_clean is not None else artifact.get("xml_clean")
    raw_response = raw_response if raw_response is not None else artifact.get("raw_response")
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM artifact_versions WHERE target_run_slide_id = ?",
        (target_run_slide_id,),
    ).fetchone()
    version_number = int(row["next_version"] if row else 1)
    cur = db.execute(
        """INSERT INTO artifact_versions
           (target_run_slide_id, artifact_run_slide_id, source_run_id, source_batch_id,
            slide_id, position, version_number, status, html_path, screenshot_path,
            final_image_path, clean_html, xml_raw, xml_clean, raw_response, evidence_snapshot)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            target_run_slide_id,
            artifact_run_slide_id,
            artifact.get("run_id"),
            artifact.get("run_batch_id"),
            artifact.get("slide_id"),
            artifact.get("position"),
            version_number,
            status,
            html_path,
            screenshot_path,
            final_image_path,
            clean_html,
            xml_raw,
            xml_clean,
            raw_response,
            _json_dumps_or_none(evidence_snapshot),
        ),
    )
    version_id = cur.lastrowid
    db.commit()
    db.close()
    if make_active:
        set_active_artifact_version(version_id)
    return version_id


def update_artifact_version_evidence_snapshot(version_id: int, evidence_snapshot: dict) -> bool:
    """Persist a rebuilt safe snapshot for one existing artifact version."""
    if type(version_id) is not int or version_id <= 0:
        return False
    db = get_db()
    try:
        cur = db.execute(
            "UPDATE artifact_versions SET evidence_snapshot = ? WHERE id = ?",
            (_json_dumps_or_none(evidence_snapshot), version_id),
        )
        db.commit()
        return cur.rowcount == 1
    finally:
        db.close()


def _artifact_version_by_artifact(target_run_slide_id: int, artifact_run_slide_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        """SELECT * FROM artifact_versions
           WHERE target_run_slide_id = ? AND artifact_run_slide_id = ?
           ORDER BY id DESC LIMIT 1""",
        (target_run_slide_id, artifact_run_slide_id),
    ).fetchone()
    db.close()
    return row_to_dict(row)


def _update_artifact_version_from_slide(version_id: int, slide: dict, evidence_snapshot: dict) -> None:
    db = get_db()
    db.execute(
        """UPDATE artifact_versions
           SET html_path = ?,
               screenshot_path = ?,
               final_image_path = ?,
               clean_html = ?,
               xml_raw = ?,
               xml_clean = ?,
               raw_response = ?,
               evidence_snapshot = ?
           WHERE id = ?""",
        (
            slide.get("html_path"),
            slide.get("screenshot_path"),
            slide.get("final_image_path"),
            slide.get("clean_html"),
            slide.get("xml_raw"),
            slide.get("xml_clean"),
            slide.get("raw_response"),
            _json_dumps_or_none(evidence_snapshot),
            version_id,
        ),
    )
    db.commit()
    db.close()


def set_active_artifact_version(version_id: int) -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM artifact_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        db.close()
        raise ValueError("Artifact version not found")
    version = row_to_dict(row)
    db.execute(
        """INSERT INTO active_artifact_versions (target_run_slide_id, version_id, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(target_run_slide_id)
           DO UPDATE SET version_id = excluded.version_id, updated_at = datetime('now')""",
        (version["target_run_slide_id"], version_id),
    )
    db.commit()
    db.close()
    return get_active_artifact_version(version["target_run_slide_id"])


def get_active_artifact_version(target_run_slide_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        """SELECT av.*
           FROM active_artifact_versions aav
           JOIN artifact_versions av ON av.id = aav.version_id
           WHERE aav.target_run_slide_id = ?""",
        (target_run_slide_id,),
    ).fetchone()
    db.close()
    version = row_to_dict(row)
    if version:
        version["status"] = "active"
        version["evidence_snapshot"] = _parse_json_dict(version.get("evidence_snapshot"))
    return version


def list_artifact_versions(target_run_slide_id: int) -> list[dict]:
    active = get_active_artifact_version(target_run_slide_id)
    active_id = active["id"] if active else None
    db = get_db()
    rows = db.execute(
        "SELECT * FROM artifact_versions WHERE target_run_slide_id = ? ORDER BY version_number DESC, id DESC",
        (target_run_slide_id,),
    ).fetchall()
    db.close()
    versions = rows_to_list(rows)
    for version in versions:
        version["status"] = "active" if version["id"] == active_id else version.get("status") or "available"
        version["evidence_snapshot"] = _parse_json_dict(version.get("evidence_snapshot"))
        version["is_active"] = version["id"] == active_id
    return versions


def record_generation_history(
    *,
    action: str,
    scope: str,
    status: str,
    target_id: int | None = None,
    target_run_slide_id: int | None = None,
    artifact_run_slide_id: int | None = None,
    source_run_id: int | None = None,
    source_batch_id: int | None = None,
    created_run_id: int | None = None,
    created_batch_id: int | None = None,
    version_id: int | None = None,
    force_mode: str | None = None,
    summary: str | None = None,
    error_message: str | None = None,
    metadata: dict | str | None = None,
) -> int:
    db = get_db()
    cur = db.execute(
        """INSERT INTO generation_history
           (action, scope, target_id, target_run_slide_id, artifact_run_slide_id,
            source_run_id, source_batch_id, created_run_id, created_batch_id,
            version_id, status, force_mode, summary, error_message, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            action,
            scope,
            target_id,
            target_run_slide_id,
            artifact_run_slide_id,
            source_run_id,
            source_batch_id,
            created_run_id,
            created_batch_id,
            version_id,
            status,
            force_mode,
            summary,
            error_message,
            _json_dumps_or_none(metadata),
        ),
    )
    history_id = cur.lastrowid
    db.commit()
    db.close()
    return history_id


def _generation_history_exists(
    *,
    target_run_slide_id: int,
    artifact_run_slide_id: int,
    action: str,
    status: str,
) -> bool:
    db = get_db()
    row = db.execute(
        """SELECT id FROM generation_history
           WHERE target_run_slide_id = ?
             AND artifact_run_slide_id = ?
             AND action = ?
             AND status = ?
           LIMIT 1""",
        (target_run_slide_id, artifact_run_slide_id, action, status),
    ).fetchone()
    db.close()
    return bool(row)


def list_generation_history(
    *,
    target_run_slide_id: int | None = None,
    scope: str | None = None,
    target_id: int | None = None,
) -> list[dict]:
    clauses = []
    values: list = []
    if target_run_slide_id is not None:
        clauses.append("target_run_slide_id = ?")
        values.append(target_run_slide_id)
    if scope is not None:
        clauses.append("scope = ?")
        values.append(scope)
    if target_id is not None:
        clauses.append("target_id = ?")
        values.append(target_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    db = get_db()
    rows = db.execute(f"SELECT * FROM generation_history {where} ORDER BY id", values).fetchall()
    db.close()
    history = rows_to_list(rows)
    for item in history:
        item["metadata"] = _parse_json_dict(item.get("metadata"))
    return history


def mark_created_run_generation_history_running(created_run_id: int | None) -> int:
    if created_run_id is None:
        return 0
    db = get_db()
    cur = db.execute(
        """UPDATE generation_history
           SET status = 'running',
               summary = REPLACE(COALESCE(summary, 'Generation queued.'), 'queued', 'running')
           WHERE created_run_id = ?
             AND status = 'queued'""",
        (created_run_id,),
    )
    db.commit()
    changed = cur.rowcount
    db.close()
    return changed


def update_generation_history_for_created_run(
    *,
    created_run_id: int | None,
    status: str,
    target_run_slide_id: int | None = None,
    artifact_run_slide_id: int | None = None,
    version_id: int | None = None,
    summary: str | None = None,
    error_message: str | None = None,
    metadata: dict | str | None = None,
) -> int:
    if created_run_id is None:
        return 0
    fields: dict[str, object | None] = {"status": status}
    if artifact_run_slide_id is not None:
        fields["artifact_run_slide_id"] = artifact_run_slide_id
    if version_id is not None:
        fields["version_id"] = version_id
    if summary is not None:
        fields["summary"] = summary
    if error_message is not None:
        fields["error_message"] = error_message
    if metadata is not None:
        fields["metadata"] = _json_dumps_or_none(metadata)
    clauses = ["created_run_id = ?", "status IN ('queued', 'running')"]
    values = list(fields.values())
    where_values: list[object] = [created_run_id]
    if target_run_slide_id is not None:
        clauses.append("target_run_slide_id = ?")
        where_values.append(target_run_slide_id)
    db = get_db()
    cur = db.execute(
        f"UPDATE generation_history SET {', '.join(f'{key} = ?' for key in fields)} WHERE {' AND '.join(clauses)}",
        values + where_values,
    )
    db.commit()
    changed = cur.rowcount
    db.close()
    return changed


def restore_artifact_version(version_id: int) -> dict:
    active = set_active_artifact_version(version_id)
    record_generation_history(
        action="version_restored",
        scope="slide",
        target_id=active["target_run_slide_id"],
        target_run_slide_id=active["target_run_slide_id"],
        artifact_run_slide_id=active["artifact_run_slide_id"],
        source_run_id=active.get("source_run_id"),
        source_batch_id=active.get("source_batch_id"),
        version_id=active["id"],
        status="success",
        summary="Set artifact version as current without duplicating the artifact.",
    )
    return active


def _record_source_new_run_history(
    *,
    slide: dict,
    lineage: dict,
    version_id: int | None,
    status: str,
    summary: str,
    error_message: str | None = None,
) -> None:
    source_slide_id = lineage.get("source_run_slide_id")
    if not source_slide_id or str(lineage.get("force_mode") or "") != "new_run":
        return
    try:
        source_slide_id = int(source_slide_id)
    except (TypeError, ValueError):
        return
    if source_slide_id == int(slide["id"]):
        return
    action = str(lineage.get("action") or "force_regenerate")
    updated_history = update_generation_history_for_created_run(
        created_run_id=slide.get("run_id"),
        status=status,
        target_run_slide_id=source_slide_id,
        artifact_run_slide_id=slide["id"],
        version_id=version_id,
        summary=summary,
        error_message=error_message,
        metadata=lineage,
    )
    if updated_history:
        return
    if _generation_history_exists(
        target_run_slide_id=source_slide_id,
        artifact_run_slide_id=slide["id"],
        action=action,
        status=status,
    ):
        return
    record_generation_history(
        action=action,
        scope=str(lineage.get("scope") or "slide"),
        target_id=int(lineage.get("source_target_id") or source_slide_id),
        target_run_slide_id=source_slide_id,
        artifact_run_slide_id=slide["id"],
        source_run_id=slide.get("run_id"),
        source_batch_id=slide.get("run_batch_id"),
        created_run_id=slide.get("run_id"),
        version_id=version_id,
        status=status,
        force_mode="new_run",
        summary=summary,
        error_message=error_message,
        metadata=lineage,
    )


def sync_run_slide_artifact_state(run_slide_id: int) -> None:
    slide = _get_run_slide_with_context(run_slide_id)
    if not slide:
        return
    lineage = _lineage_for_artifact_slide(slide)
    target_run_slide_id = _version_target_run_slide_id(slide, lineage)
    action = str(lineage.get("action") or "initial_generation")
    scope = str(lineage.get("scope") or "slide")
    target_id = int(lineage.get("source_target_id") or target_run_slide_id)
    force_mode = str(lineage.get("force_mode") or "") or None
    created_run_id = slide.get("run_id") if action != "initial_generation" else None
    if _has_displayable_artifact(slide):
        evidence_snapshot = _evidence_snapshot_for_slide(slide)
        existing = _artifact_version_by_artifact(target_run_slide_id, run_slide_id)
        if existing:
            version_id = existing["id"]
            _update_artifact_version_from_slide(version_id, slide, evidence_snapshot)
        else:
            version_id = create_artifact_version(
                target_run_slide_id=target_run_slide_id,
                artifact_run_slide_id=run_slide_id,
                evidence_snapshot=evidence_snapshot,
            )
        active = get_active_artifact_version(target_run_slide_id)
        should_promote = (
            not active
            or action in {"retry", "auto_retry"}
            or force_mode == "overwrite_current"
            or target_run_slide_id == run_slide_id
        )
        if should_promote:
            set_active_artifact_version(version_id)
        updated_history = update_generation_history_for_created_run(
            created_run_id=created_run_id,
            status="success",
            target_run_slide_id=target_run_slide_id,
            artifact_run_slide_id=run_slide_id,
            version_id=version_id,
            summary="Successful artifact version created.",
            metadata=lineage,
        )
        if not updated_history and not _generation_history_exists(
            target_run_slide_id=target_run_slide_id,
            artifact_run_slide_id=run_slide_id,
            action=action,
            status="success",
        ):
            record_generation_history(
                action=action,
                scope=scope,
                target_id=target_id,
                target_run_slide_id=target_run_slide_id,
                artifact_run_slide_id=run_slide_id,
                source_run_id=slide.get("run_id"),
                source_batch_id=slide.get("run_batch_id"),
                created_run_id=created_run_id,
                version_id=version_id,
                status="success",
                force_mode=force_mode,
                summary="Successful artifact version created.",
                metadata=lineage,
            )
        _record_source_new_run_history(
            slide=slide,
            lineage=lineage,
            version_id=version_id,
            status="success",
            summary="Force new run completed and created an independent artifact version.",
        )
        return
    if slide.get("status") in {"failed", "timed_out"}:
        updated_history = update_generation_history_for_created_run(
            created_run_id=created_run_id,
            status="failed",
            target_run_slide_id=target_run_slide_id,
            artifact_run_slide_id=run_slide_id,
            summary="Artifact generation attempt failed.",
            error_message=slide.get("error_message"),
            metadata=lineage,
        )
        if not updated_history and not _generation_history_exists(
            target_run_slide_id=target_run_slide_id,
            artifact_run_slide_id=run_slide_id,
            action=action,
            status="failed",
        ):
            record_generation_history(
                action=action,
                scope=scope,
                target_id=target_id,
                target_run_slide_id=target_run_slide_id,
                artifact_run_slide_id=run_slide_id,
                source_run_id=slide.get("run_id"),
                source_batch_id=slide.get("run_batch_id"),
                created_run_id=created_run_id,
                status="failed",
                force_mode=force_mode,
                summary="Artifact generation attempt failed.",
                error_message=slide.get("error_message"),
                metadata=lineage,
            )
        _record_source_new_run_history(
            slide=slide,
            lineage=lineage,
            version_id=None,
            status="failed",
            summary="Force new run attempt failed.",
            error_message=slide.get("error_message"),
        )


def backfill_artifact_versions() -> int:
    db = get_db()
    rows = db.execute(
        """SELECT rs.id
           FROM run_slides rs
           WHERE rs.status = 'completed'
             AND (
               COALESCE(rs.final_image_path, '') != ''
               OR COALESCE(rs.screenshot_path, '') != ''
               OR COALESCE(rs.html_path, '') != ''
               OR COALESCE(rs.clean_html, '') != ''
             )
             AND NOT EXISTS (
               SELECT 1 FROM artifact_versions av
               WHERE av.artifact_run_slide_id = rs.id
             )
           ORDER BY rs.id"""
    ).fetchall()
    orphan_targets = db.execute(
        """SELECT av.target_run_slide_id, av.id
           FROM artifact_versions av
           LEFT JOIN active_artifact_versions aav ON aav.target_run_slide_id = av.target_run_slide_id
           WHERE aav.target_run_slide_id IS NULL
           ORDER BY av.target_run_slide_id, av.version_number DESC, av.id DESC"""
    ).fetchall()
    db.close()
    changed = 0
    for row in rows:
        sync_run_slide_artifact_state(row["id"])
        changed += 1
    activated: set[int] = set()
    for row in orphan_targets:
        target_id = int(row["target_run_slide_id"])
        if target_id in activated:
            continue
        set_active_artifact_version(int(row["id"]))
        activated.add(target_id)
        changed += 1
    return changed


def get_run_progress(run_id: int) -> dict:
    """Return a progress summary: total slides, completed, failed, pending."""
    db = get_db()
    rows = db.execute(
        "SELECT status, COUNT(*) as cnt FROM run_slides WHERE run_id = ? GROUP BY status",
        (run_id,),
    ).fetchall()
    displayable_row = db.execute(
        """SELECT COUNT(*) AS count
           FROM run_slides rs
           LEFT JOIN active_artifact_versions aav ON aav.target_run_slide_id = rs.id
           LEFT JOIN artifact_versions av ON av.id = aav.version_id
           WHERE rs.run_id = ?
             AND (
               av.id IS NOT NULL
               OR (
                 rs.status = 'completed'
                 AND (
                   COALESCE(rs.final_image_path, '') != ''
                   OR COALESCE(rs.screenshot_path, '') != ''
                   OR COALESCE(rs.html_path, '') != ''
                   OR COALESCE(rs.clean_html, '') != ''
                 )
               )
             )""",
        (run_id,),
    ).fetchone()
    db.close()
    counts = {r["status"]: r["cnt"] for r in rows}
    total = sum(counts.values())
    displayable = int(displayable_row["count"] if displayable_row else 0)
    return {
        "total": total,
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "displayable": displayable,
        "missing_displayable": max(0, total - displayable),
    }


# ---------------------------------------------------------------------------
# prompts CRUD
# ---------------------------------------------------------------------------

def list_prompts(agent_type=None, status: str | None = "active", folder_id: int | None = None) -> list[dict]:
    db = get_db()
    clauses = []
    params: list[object] = []
    if agent_type:
        clauses.append("agent_type = ?")
        params.append(agent_type)
    if status and status != "all":
        clauses.append("COALESCE(lifecycle_status, 'active') = ?")
        params.append(status)
    if folder_id:
        clauses.append(
            """id IN (
                SELECT entity_id FROM folder_memberships
                WHERE entity_type = 'prompt' AND folder_id = ?
            )"""
        )
        params.append(folder_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(f"SELECT * FROM prompts {where} ORDER BY agent_type, id DESC", params).fetchall()
    db.close()
    return rows_to_list(rows)


def get_prompt(prompt_id: int) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
    db.close()
    return row_to_dict(row)


def get_active_prompt(agent_type: str) -> dict | None:
    """Get the default active prompt for a role, falling back to latest active."""
    db = get_db()
    row = db.execute(
        """SELECT * FROM prompts
           WHERE agent_type = ?
             AND status = 'active'
             AND COALESCE(lifecycle_status, 'active') = 'active'
           ORDER BY COALESCE(is_default, 0) DESC, id DESC LIMIT 1""",
        (agent_type,),
    ).fetchone()
    db.close()
    return row_to_dict(row)


def create_prompt(
    agent_type: str,
    version: str,
    name: str,
    content: str,
    description: str = None,
    is_default: bool = False,
    status: str = "active",
    publish_baseline_content: str | None = None,
) -> int:
    db = get_db()
    cur = db.execute(
        """INSERT INTO prompts
           (agent_type, version, name, content, status, description, is_default, publish_baseline_content)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_type,
            version,
            name,
            content,
            status,
            description,
            1 if is_default else 0,
            publish_baseline_content,
        ),
    )
    db.commit()
    prompt_id = cur.lastrowid
    db.close()
    return prompt_id


def update_prompt(prompt_id: int, **kwargs) -> bool:
    if not kwargs:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [prompt_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE prompts SET {set_clause} WHERE id = ?",
        values,
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def delete_prompt(prompt_id: int) -> bool:
    """Archive a prompt without removing historical references."""
    db = get_db()
    cur = db.execute(
        """UPDATE prompts
           SET lifecycle_status = 'archived', archived_at = datetime('now')
           WHERE id = ?""",
        (prompt_id,),
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed
