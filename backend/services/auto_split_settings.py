"""Standalone AutoSplit model settings and execution resolution."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

import db as dbmod
from backend.domain.auto_split import AUTO_SPLIT_MODELS, THINKING_EFFORTS

AUTO_SPLIT_CONTENT_MODES = {"faithful", "editorial"}


class AutoSplitSettingsError(ValueError):
    def __init__(self, message: str, *, code: str = "configuration") -> None:
        super().__init__(message)
        self.code = code


def _codex_readiness() -> tuple[bool, str]:
    command = shutil.which("codex")
    if not command:
        return False, "Local Codex command is not available"
    try:
        result = subprocess.run(
            [command, "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "Local Codex login status timed out"
    except OSError:
        return False, "Local Codex login status is unavailable"
    if result.returncode == 0:
        return True, "Local Codex login is ready"
    return False, "Local Codex login is not ready"


def _catalogue_by_model() -> dict[str, Any]:
    return {item.model: item for item in AUTO_SPLIT_MODELS}


def _normalize_content_mode(content_mode: str) -> str:
    mode = str(content_mode or "").strip().lower()
    if mode not in AUTO_SPLIT_CONTENT_MODES:
        raise AutoSplitSettingsError(
            "Content Mode must be faithful or editorial"
        )
    return mode


def _canonical_profile(db, profile_id: int):
    row = db.execute(
        "SELECT * FROM model_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    if not row:
        raise AutoSplitSettingsError("AutoSplit model profile was not found")
    spec = _catalogue_by_model().get(row["model"])
    if (
        not spec
        or row["role"] != "auto_spill"
        or row["name"] != spec.profile_name
        or row["api_type"] != spec.api_type
        or row["endpoint"] != spec.endpoint
    ):
        raise AutoSplitSettingsError("AutoSplit model profile is not canonical")
    if row["status"] != "active":
        raise AutoSplitSettingsError("AutoSplit model profile is inactive")
    if spec.api_type == "codex_exec" and row["api_key"]:
        raise AutoSplitSettingsError("Local Codex Exec profile must not contain an API key")
    return row, spec


def _readiness(row) -> tuple[bool, str]:
    if row["api_type"] == "gemini":
        if str(row["api_key"] or "").strip():
            return True, "Gemini API key is configured"
        return False, "Gemini API key is not configured"
    return _codex_readiness()


def _safe_profile(row, *, readiness: tuple[bool, str] | None = None) -> dict[str, Any]:
    ready, message = readiness or _readiness(row)
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "model": row["model"],
        "api_type": row["api_type"],
        "provider": "Gemini Native" if row["api_type"] == "gemini" else "Local Codex Exec",
        "ready": ready,
        "readiness_message": message,
    }


def get_auto_split_settings() -> dict[str, Any]:
    db = dbmod.get_db()
    try:
        setting = db.execute(
            "SELECT * FROM auto_split_settings WHERE id = 1"
        ).fetchone()
        if not setting:
            raise AutoSplitSettingsError("AutoSplit settings are not initialized")
        selected, _spec = _canonical_profile(db, int(setting["model_profile_id"]))
        rows = db.execute(
            """SELECT * FROM model_profiles
               WHERE role = 'auto_spill' AND status = 'active'
               ORDER BY id"""
        ).fetchall()
        canonical_rows = []
        for row in rows:
            try:
                _canonical_profile(db, int(row["id"]))
            except AutoSplitSettingsError:
                continue
            canonical_rows.append(row)
        codex_readiness = _codex_readiness() if any(
            row["api_type"] == "codex_exec" for row in canonical_rows
        ) else None
        available_profiles = [
            _safe_profile(
                row,
                readiness=codex_readiness if row["api_type"] == "codex_exec" else None,
            )
            for row in canonical_rows
        ]
        selected_readiness = (
            codex_readiness if selected["api_type"] == "codex_exec" else None
        )
        return {
            "model_profile_id": int(selected["id"]),
            "thinking_effort": setting["thinking_effort"],
            "content_mode": setting["content_mode"],
            "selected_profile": _safe_profile(
                selected, readiness=selected_readiness
            ),
            "available_profiles": available_profiles,
            "updated_at": setting["updated_at"],
        }
    finally:
        db.close()


def update_auto_split_settings(
    model_profile_id: int,
    thinking_effort: str,
    content_mode: str | None = None,
) -> dict[str, Any]:
    effort = str(thinking_effort or "").strip().lower()
    if effort not in THINKING_EFFORTS:
        raise AutoSplitSettingsError("Thinking Effort must be low, medium, or high")
    db = dbmod.get_db()
    try:
        _canonical_profile(db, int(model_profile_id))
        if content_mode is None:
            setting = db.execute(
                "SELECT content_mode FROM auto_split_settings WHERE id = 1"
            ).fetchone()
            if not setting:
                raise AutoSplitSettingsError("AutoSplit settings are not initialized")
            mode = _normalize_content_mode(setting["content_mode"])
        else:
            mode = _normalize_content_mode(content_mode)
        db.execute(
            """UPDATE auto_split_settings
               SET model_profile_id = ?, thinking_effort = ?, content_mode = ?,
                   updated_at = datetime('now')
               WHERE id = 1""",
            (int(model_profile_id), effort, mode),
        )
        db.commit()
    finally:
        db.close()
    return get_auto_split_settings()


def resolve_auto_split_execution(
    profile_id: int | None = None,
    thinking_effort: str | None = None,
    content_mode: str | None = None,
) -> dict[str, Any]:
    db = dbmod.get_db()
    try:
        setting = db.execute(
            "SELECT * FROM auto_split_settings WHERE id = 1"
        ).fetchone()
        if not setting:
            raise AutoSplitSettingsError("AutoSplit settings are not initialized")
        selected_id = int(profile_id or setting["model_profile_id"])
        effort = str(thinking_effort or setting["thinking_effort"]).strip().lower()
        if effort not in THINKING_EFFORTS:
            raise AutoSplitSettingsError("Thinking Effort must be low, medium, or high")
        mode = _normalize_content_mode(
            setting["content_mode"] if content_mode is None else content_mode
        )
        profile, _spec = _canonical_profile(db, selected_id)
        ready, message = _readiness(profile)
        if not ready:
            raise AutoSplitSettingsError(message)
        return {
            "id": int(profile["id"]),
            "profile_id": int(profile["id"]),
            "profile_name": profile["name"],
            "role": profile["role"],
            "api_type": profile["api_type"],
            "endpoint": profile["endpoint"],
            "model": profile["model"],
            "api_key": profile["api_key"],
            "temperature": profile["temperature"],
            "thinking": effort,
            "content_mode": mode,
        }
    finally:
        db.close()
