"""Global system settings for request concurrency controls."""

from __future__ import annotations

import re

import db as dbmod

PROVIDER_CONCURRENCY_PREFIX = "provider_concurrency."
RUN_QUEUE_CONCURRENCY_KEY = "run_queue_concurrency"
DEFAULT_PROVIDER_CONCURRENCY = {
    # Native Image 3.0 uses two Codex transport identities. Keep both
    # explicit so an unconfigured deployment resolves the product cap.
    "codex_exec:exec": 6,
    "codex_native_image:exec": 6,
    "gemini:generativelanguage.googleapis.com": 10,
    "openai:zenmux.ai": 100,
}
DEFAULT_RUN_QUEUE_CONCURRENCY = 6
PROVIDER_KEY_RE = re.compile(r"^[a-z0-9_-]+:[a-z0-9.-]+$")


def _coerce_positive_int(value: object, key: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{key} must be a positive integer")
    return parsed


def _coerce_provider_key(value: object) -> str:
    key = str(value or "").strip().lower()
    if not PROVIDER_KEY_RE.match(key):
        raise ValueError(f"provider_concurrency key is invalid: {value}")
    return key


def get_system_settings() -> dict[str, object]:
    db = dbmod.get_db()
    try:
        rows = db.execute(
            "SELECT key, value FROM system_settings WHERE key LIKE ? OR key = ?",
            (f"{PROVIDER_CONCURRENCY_PREFIX}%", RUN_QUEUE_CONCURRENCY_KEY),
        ).fetchall()
    finally:
        db.close()
    provider_values = dict(DEFAULT_PROVIDER_CONCURRENCY)
    run_queue_concurrency = DEFAULT_RUN_QUEUE_CONCURRENCY
    for row in rows:
        key = row["key"]
        if key.startswith(PROVIDER_CONCURRENCY_PREFIX):
            provider_values[key.removeprefix(PROVIDER_CONCURRENCY_PREFIX)] = int(row["value"])
        elif key == RUN_QUEUE_CONCURRENCY_KEY:
            run_queue_concurrency = _coerce_positive_int(row["value"], RUN_QUEUE_CONCURRENCY_KEY)
    return {
        "provider_concurrency": provider_values,
        "run_queue_concurrency": run_queue_concurrency,
    }


def get_run_queue_concurrency() -> int:
    settings = get_system_settings()
    return int(settings["run_queue_concurrency"])


def update_system_settings(data: dict[str, object]) -> dict[str, object]:
    supported_keys = {"provider_concurrency", RUN_QUEUE_CONCURRENCY_KEY}
    unsupported_keys = sorted(key for key in data if key not in supported_keys)
    if unsupported_keys:
        raise ValueError(f"Unsupported system setting keys: {', '.join(unsupported_keys)}")

    provider_updates: dict[str, int] = {}
    if "provider_concurrency" in data:
        provider_data = data["provider_concurrency"]
        if not isinstance(provider_data, dict):
            raise ValueError("provider_concurrency must be an object")
        for raw_key, raw_value in provider_data.items():
            provider_key = _coerce_provider_key(raw_key)
            provider_updates[provider_key] = _coerce_positive_int(
                raw_value,
                f"provider_concurrency.{provider_key}",
            )
    run_queue_update = None
    if RUN_QUEUE_CONCURRENCY_KEY in data:
        run_queue_update = _coerce_positive_int(data[RUN_QUEUE_CONCURRENCY_KEY], RUN_QUEUE_CONCURRENCY_KEY)

    if not provider_updates and run_queue_update is None:
        return get_system_settings()

    db = dbmod.get_db()
    try:
        for provider_key, value in provider_updates.items():
            db.execute(
                """INSERT INTO system_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value,
                     updated_at = datetime('now')""",
                (f"{PROVIDER_CONCURRENCY_PREFIX}{provider_key}", str(value)),
            )
        if run_queue_update is not None:
            db.execute(
                """INSERT INTO system_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value,
                     updated_at = datetime('now')""",
                (RUN_QUEUE_CONCURRENCY_KEY, str(run_queue_update)),
            )
        db.commit()
    finally:
        db.close()
    return get_system_settings()
