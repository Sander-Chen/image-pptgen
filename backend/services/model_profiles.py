"""Role model profile helpers for Config combinations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import db as dbmod

VALID_ROLES = {
    "designer",
    "html_agent",
    "auto_spill",
    "prompt_assistant",
    "evaluation_visual_qa",
    "image_designer",
    "image_generator",
    "shared_extraction",
    "xml_cleanup",
}
VALID_THINKING = {None, "low", "medium", "high"}
DEFAULT_AUTO_SPILL_MODEL = "gemini-3-flash-preview"
OPENAI_COMPAT_ENDPOINT = "https://zenmux.ai/api/v1/chat/completions"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
ZENMUX_IMAGES_ENDPOINT = "https://zenmux.ai/api/v1/images/generations"
CODEX_EXEC_API_TYPE = "codex_exec"
CODEX_EXEC_ENDPOINT = "codex://exec"
CODEX_PROFILE_MODEL = "gpt-5.4-mini"
CODEX_PROFILE_THINKING = "low"
CODEX_SMOKE_MODEL = "gpt-5.3-codex-spark"
CODEX_HTML_CONFIG_NAME = "Codex HTML GPT-5.4-mini Low"
NATIVE_IMAGE_ADAPTER = "codex_native"
NATIVE_IMAGE_API_TYPE = "codex_native_image"
NATIVE_IMAGE_DIRECT_ROUTE = "image_direct"
NATIVE_IMAGE_3_0_ROUTE = "image_3_0"
NATIVE_IMAGE_DIRECT_CONFIG_NAME = "Codex Native Image Direct"
NATIVE_IMAGE_3_0_CONFIG_NAME = "Codex Native Image 3.0"
NATIVE_IMAGE_3_0_LUNA_DIRECTOR_CONFIG_NAME = (
    "Codex Native Image 3.0 Luna Low Director"
)
NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME = "Codex Native Image 3.0 Terra Low E2E"
NATIVE_IMAGE_DIRECTOR_PROFILE_NAME = "Codex Native Image Director Sol Low"
NATIVE_IMAGE_LUNA_DIRECTOR_PROFILE_NAME = "Codex Native Image Director Luna Low"
NATIVE_IMAGE_LAUNCHER_PROFILE_NAME = "Codex Native Image Launcher Luna Low"
NATIVE_IMAGE_TERRA_DIRECTOR_PROFILE_NAME = "Codex Native Image Director Terra Low E2E"
NATIVE_IMAGE_TERRA_LAUNCHER_PROFILE_NAME = "Codex Native Image Launcher Terra Low E2E"
NATIVE_IMAGE_PALETTE_PROFILE_NAME = "Codex Native Image Palette Gemini Flash"
IMAGE_PPTGEN_E2E_TERRA_LOW_ENV = "IMAGE_PPTGEN_E2E_TERRA_LOW"
# Native Image 3.0's product-owned run cap. Generic Image/HTML combinations
# retain their legacy compatibility value; only managed Native routes use it.
NATIVE_IMAGE_MAX_CONCURRENT_RUNS = 6


def image_pptgen_e2e_terra_low_enabled() -> bool:
    return os.environ.get(IMAGE_PPTGEN_E2E_TERRA_LOW_ENV) == "1"

REQUESTED_DEFAULT_PROFILES = [
    ("designer", "Test", "gemini", GEMINI_ENDPOINT, "google/gemini-3.1-flash-lite-preview", 1, None),
    ("designer", "Production Mini", "openai", OPENAI_COMPAT_ENDPOINT, "openai/gpt-5.4-mini", 1, "low"),
    ("designer", "Production Pro", "openai", OPENAI_COMPAT_ENDPOINT, "openai/gpt-5.4", 1, "high"),
    ("html_agent", "Test", "gemini", GEMINI_ENDPOINT, "google/gemini-3.1-flash-lite-preview", 1, None),
    ("html_agent", "Production Mini", "gemini", GEMINI_ENDPOINT, "gemini-3-flash-preview", 1, "high"),
    ("html_agent", "Production Pro", "gemini", GEMINI_ENDPOINT, "gemini-3.1-pro-preview", 1, "high"),
    ("image_designer", "Test", "gemini", GEMINI_ENDPOINT, "google/gemini-3.1-flash-lite-preview", 1, None),
    ("image_designer", "Production Legacy", "openai", OPENAI_COMPAT_ENDPOINT, "openai/gpt-5.1", 1, "high"),
    ("image_designer", "Production", "openai", OPENAI_COMPAT_ENDPOINT, "openai/gpt-5.4", 1, "high"),
    ("image_generator", "Test", "gemini", GEMINI_ENDPOINT, "gemini-3.1-flash-image", 1, "low"),
    ("image_generator", "Production Mini", "gemini", GEMINI_ENDPOINT, "gemini-3.1-flash-image", 1, "high"),
    ("image_generator", "Production", "gemini", GEMINI_ENDPOINT, "gemini-3-pro-image", 1, "high"),
    ("evaluation_visual_qa", "Gemini 3 Flash", "gemini", GEMINI_ENDPOINT, "gemini-3-flash-preview", 0.2, None),
]
DEFAULT_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "ZENMUX_API_KEY",
    "zenmux_images": "ZENMUX_API_KEY",
}

@dataclass(frozen=True)
class HtmlTestProfileSpec:
    role: str
    name: str
    api_type: str
    endpoint: str
    model: str
    thinking: str | None
    key_source: str


@dataclass(frozen=True)
class ProductProfileSpec:
    role: str
    name: str
    api_type: str
    endpoint: str
    model: str
    temperature: float
    thinking: str | None
    key_source: str


@dataclass(frozen=True)
class ImageProductConfigSpec:
    name: str
    designer_profile_name: str
    generator_profile_name: str
    palette_profile_name: str | None = None
    legacy_aliases: tuple[str, ...] = ()


HTML_TEST_ZENMUX_PROFILES = [
    HtmlTestProfileSpec("designer", "HTML Test DS4 Pro", "openai", OPENAI_COMPAT_ENDPOINT, "deepseek/deepseek-v4-pro", "high", "zenmux"),
    HtmlTestProfileSpec("designer", "HTML Test DS4 Flash", "openai", OPENAI_COMPAT_ENDPOINT, "deepseek/deepseek-v4-flash", "high", "zenmux"),
    HtmlTestProfileSpec("html_agent", "HTML Test DS4 Pro", "openai", OPENAI_COMPAT_ENDPOINT, "deepseek/deepseek-v4-pro", "high", "zenmux"),
    HtmlTestProfileSpec("html_agent", "HTML Test DS4 Flash", "openai", OPENAI_COMPAT_ENDPOINT, "deepseek/deepseek-v4-flash", "high", "zenmux"),
    HtmlTestProfileSpec("designer", "HTML Test GPT Mini", "openai", OPENAI_COMPAT_ENDPOINT, "openai/gpt-5.4-mini", "low", "zenmux"),
    HtmlTestProfileSpec("html_agent", "HTML Test Gemini Flash", "gemini", GEMINI_ENDPOINT, "gemini-3-flash-preview", "high", "gemini"),
]

HTML_TEST_ZENMUX_COMBINATIONS = [
    ("HTML Test DS4 Flash", "HTML Test DS4 Flash", "HTML Test DS4 Flash"),
    ("HTML Test DS4 Pro + Flash", "HTML Test DS4 Pro", "HTML Test DS4 Flash"),
    ("HTML Test GPT Mini + DS4 Flash", "HTML Test GPT Mini", "HTML Test DS4 Flash"),
    ("HTML Test DS4 Pro + Gemini Flash", "HTML Test DS4 Pro", "HTML Test Gemini Flash"),
]

HTML_TEST_RETIRED_CONFIG_NAMES = (
    "HTML Test DS4 Pro",
    "HTML Test Mimo Flash",
    "HTML Test Mimo Pro + Flash",
)
RETIRED_HTML_TEST_CONFIG_TYPE = "retired_html_test"

IMAGE_PRODUCT_PROFILE_SPECS = [
    ProductProfileSpec("image_designer", "Production", "openai", OPENAI_COMPAT_ENDPOINT, "openai/gpt-5.4", 1, "high", "zenmux"),
    ProductProfileSpec("image_designer", "Production Mini", "openai", OPENAI_COMPAT_ENDPOINT, "openai/gpt-5.4-mini", 1, "low", "zenmux"),
    ProductProfileSpec("image_generator", "GPT Image 2 via ZenMux Images", "zenmux_images", ZENMUX_IMAGES_ENDPOINT, "gpt-image-2", 1, None, "zenmux"),
    ProductProfileSpec("image_generator", "Production", "gemini", GEMINI_ENDPOINT, "gemini-3-pro-image", 1, "high", "gemini"),
    ProductProfileSpec("image_generator", "Test", "gemini", GEMINI_ENDPOINT, "gemini-3.1-flash-image", 1, "low", "gemini"),
]

CODEX_HTML_PROFILE_SPECS = [
    ProductProfileSpec(
        "designer",
        "Codex HTML Designer GPT-5.4-mini Low",
        CODEX_EXEC_API_TYPE,
        CODEX_EXEC_ENDPOINT,
        CODEX_PROFILE_MODEL,
        0.7,
        CODEX_PROFILE_THINKING,
        "codex",
    ),
    ProductProfileSpec(
        "html_agent",
        "Codex HTML Agent GPT-5.4-mini Low",
        CODEX_EXEC_API_TYPE,
        CODEX_EXEC_ENDPOINT,
        CODEX_PROFILE_MODEL,
        0.7,
        CODEX_PROFILE_THINKING,
        "codex",
    ),
]

NATIVE_IMAGE_PROFILE_SPECS = [
    ProductProfileSpec(
        "image_designer",
        NATIVE_IMAGE_DIRECTOR_PROFILE_NAME,
        CODEX_EXEC_API_TYPE,
        CODEX_EXEC_ENDPOINT,
        "gpt-5.6-sol",
        1,
        "low",
        "codex",
    ),
    ProductProfileSpec(
        "image_designer",
        NATIVE_IMAGE_LUNA_DIRECTOR_PROFILE_NAME,
        CODEX_EXEC_API_TYPE,
        CODEX_EXEC_ENDPOINT,
        "gpt-5.6-luna",
        1,
        "low",
        "codex",
    ),
    ProductProfileSpec(
        "image_generator",
        NATIVE_IMAGE_LAUNCHER_PROFILE_NAME,
        NATIVE_IMAGE_API_TYPE,
        CODEX_EXEC_ENDPOINT,
        "gpt-5.6-luna",
        1,
        "low",
        "codex",
    ),
    ProductProfileSpec(
        "image_generator",
        NATIVE_IMAGE_PALETTE_PROFILE_NAME,
        "gemini",
        GEMINI_ENDPOINT,
        "gemini-3-flash-preview",
        0.2,
        None,
        "gemini",
    ),
]

NATIVE_IMAGE_TERRA_E2E_PROFILE_SPECS = (
    ProductProfileSpec(
        "image_designer",
        NATIVE_IMAGE_TERRA_DIRECTOR_PROFILE_NAME,
        CODEX_EXEC_API_TYPE,
        CODEX_EXEC_ENDPOINT,
        "gpt-5.6-terra",
        1,
        "low",
        "codex",
    ),
    ProductProfileSpec(
        "image_generator",
        NATIVE_IMAGE_TERRA_LAUNCHER_PROFILE_NAME,
        NATIVE_IMAGE_API_TYPE,
        CODEX_EXEC_ENDPOINT,
        "gpt-5.6-terra",
        1,
        "low",
        "codex",
    ),
)

GPT_IMAGE_2_CONFIG_NAME = "pro production-GPT"
GPT_IMAGE_2_PROFILE_NAME = "GPT Image 2 via ZenMux Images"
IMAGE_PRODUCT_CONFIG_SPECS = [
    ImageProductConfigSpec(
        "pro production-GPT",
        designer_profile_name="Production",
        generator_profile_name="GPT Image 2 via ZenMux Images",
        palette_profile_name="Production",
        legacy_aliases=("Production Pro GPT",),
    ),
    ImageProductConfigSpec(
        "Pro production-gpt-mini",
        designer_profile_name="Production Mini",
        generator_profile_name="GPT Image 2 via ZenMux Images",
        palette_profile_name="Production",
    ),
    ImageProductConfigSpec(
        "pro production-banana",
        designer_profile_name="Production",
        generator_profile_name="Production",
    ),
    ImageProductConfigSpec(
        "pro production-banana-mini",
        designer_profile_name="Production Mini",
        generator_profile_name="Test",
    ),
]

IMAGE_DIRECT_COMBINATIONS = [
    ("Nano Banana 2", ("nanobanana2", "gemini31flashimage")),
    ("Nano Banana Pro", ("nanobananapro", "nanobanana3t", "gemini3proimage")),
    ("GPT image2", ("gptimage2",)),
]


def _model_token(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def normalize_profile(data: dict[str, Any]) -> dict[str, Any]:
    role = data.get("role")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")

    thinking = data.get("thinking")
    if thinking == "":
        thinking = None
    if thinking not in VALID_THINKING:
        raise ValueError("thinking must be low, medium, high, or empty")

    required = ("name", "api_type", "endpoint", "model")
    missing = [field for field in required if not data.get(field)]
    if missing:
        raise ValueError(f"missing required profile fields: {', '.join(missing)}")

    return {
        "role": role,
        "name": str(data["name"]).strip(),
        "api_type": str(data["api_type"]).strip(),
        "endpoint": str(data["endpoint"]).strip(),
        "model": str(data["model"]).strip(),
        "api_key": str(data.get("api_key") or ""),
        "temperature": float(data.get("temperature", 0.7)),
        "thinking": thinking,
        "status": data.get("status") or "active",
    }


def is_codex_profile(profile: dict[str, Any] | None) -> bool:
    return bool(profile) and str(profile.get("api_type") or "").strip().lower() == CODEX_EXEC_API_TYPE


def create_profile(data: dict[str, Any]) -> int:
    profile = normalize_profile(data)
    db = dbmod.get_db()
    cur = db.execute(
        """INSERT INTO model_profiles
           (role, name, api_type, endpoint, model, api_key, temperature, thinking, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            profile["role"],
            profile["name"],
            profile["api_type"],
            profile["endpoint"],
            profile["model"],
            profile["api_key"],
            profile["temperature"],
            profile["thinking"],
            profile["status"],
        ),
    )
    db.commit()
    profile_id = cur.lastrowid
    db.close()
    return profile_id


def ensure_requested_default_profiles() -> list[int]:
    """Seed requested role model defaults without enabling any new generation route."""
    created: list[int] = []
    db = dbmod.get_db()
    try:
        for role, name, api_type, endpoint, model, temperature, thinking in REQUESTED_DEFAULT_PROFILES:
            existing = db.execute(
                "SELECT id FROM model_profiles WHERE role = ? AND name = ?",
                (role, name),
            ).fetchone()
            if existing:
                continue
            cur = db.execute(
                """INSERT INTO model_profiles
                   (role, name, api_type, endpoint, model, api_key, temperature, thinking, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                (role, name, api_type, endpoint, model, _default_api_key(db, api_type), temperature, thinking),
            )
            created.append(int(cur.lastrowid))
        db.commit()
    finally:
        db.close()
    return created


def ensure_evaluation_visual_qa_profile() -> int:
    """Ensure the auxiliary visual QA role has one active default profile."""
    role, name, api_type, endpoint, model, temperature, thinking = next(
        item for item in REQUESTED_DEFAULT_PROFILES if item[0] == "evaluation_visual_qa"
    )
    db = dbmod.get_db()
    try:
        api_key = _default_api_key(db, api_type)
        active = db.execute(
            "SELECT id, api_key FROM model_profiles WHERE role = ? AND status = 'active' ORDER BY id LIMIT 1",
            (role,),
        ).fetchone()
        if active:
            if not active["api_key"]:
                if not api_key:
                    raise ValueError("No Gemini API key is configured for Machine QA.")
                db.execute(
                    "UPDATE model_profiles SET api_key = ?, updated_at = datetime('now') WHERE id = ?",
                    (api_key, int(active["id"])),
                )
                db.commit()
            return int(active["id"])

        existing = db.execute(
            "SELECT id, api_key FROM model_profiles WHERE role = ? AND name = ?",
            (role, name),
        ).fetchone()
        if not api_key and (not existing or not existing["api_key"]):
            raise ValueError("No Gemini API key is configured for Machine QA.")
        if existing:
            db.execute(
                """UPDATE model_profiles
                   SET api_type = ?, endpoint = ?, model = ?,
                       api_key = CASE WHEN COALESCE(api_key, '') = '' THEN ? ELSE api_key END,
                       temperature = ?, thinking = ?, status = 'active', updated_at = datetime('now')
                   WHERE id = ?""",
                (api_type, endpoint, model, api_key, temperature, thinking, int(existing["id"])),
            )
            profile_id = int(existing["id"])
        else:
            cur = db.execute(
                """INSERT INTO model_profiles
                   (role, name, api_type, endpoint, model, api_key, temperature, thinking, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                (role, name, api_type, endpoint, model, api_key, temperature, thinking),
            )
            profile_id = int(cur.lastrowid)
        db.commit()
    finally:
        db.close()
    return profile_id


def _default_api_key(db, api_type: str) -> str:
    env_key = DEFAULT_API_KEY_ENV.get(api_type)
    if env_key:
        value = os.environ.get(env_key)
        if value:
            return value
    row = db.execute(
        """SELECT api_key FROM model_profiles
           WHERE api_type = ? AND status = 'active' AND COALESCE(api_key, '') != ''
           ORDER BY id
           LIMIT 1""",
        (api_type,),
    ).fetchone()
    return row["api_key"] if row else ""


def _existing_zenmux_api_key(db) -> str:
    row = db.execute(
        """SELECT api_key FROM model_profiles
           WHERE endpoint = ? AND api_key != '' AND status = 'active'
           ORDER BY
             CASE
               WHEN model IN ('openai/gpt-5.4', 'openai/gpt-5.4-mini') THEN 0
               ELSE 1
             END,
             id
           LIMIT 1""",
        (OPENAI_COMPAT_ENDPOINT,),
    ).fetchone()
    return str(row["api_key"]) if row else ""


def _existing_gemini_api_key(db) -> str:
    row = db.execute(
        """SELECT api_key FROM model_profiles
           WHERE api_type = 'gemini'
             AND endpoint = ?
             AND api_key != ''
             AND status = 'active'
           ORDER BY
             CASE
               WHEN role = 'html_agent' AND name = 'Production Mini' THEN 0
               WHEN model = 'gemini-3-flash-preview' THEN 1
               ELSE 2
             END,
             id
           LIMIT 1""",
        (GEMINI_ENDPOINT,),
    ).fetchone()
    return str(row["api_key"]) if row else ""


def _profile_config_from_row(row) -> dict[str, Any]:
    return _profile_config(dbmod.row_to_dict(row))


def _active_profile_row(db, role: str):
    return db.execute(
        """SELECT * FROM model_profiles
           WHERE role = ? AND status = 'active'
           ORDER BY
             CASE
               WHEN name LIKE '%Production Mini%' THEN 0
               WHEN name LIKE '%Production%' THEN 1
               WHEN name = 'Test' THEN 2
               ELSE 3
             END,
             id""",
        (role,),
    ).fetchone()


def _image_direct_profile_for_aliases(db, aliases: tuple[str, ...]):
    rows = db.execute(
        """SELECT * FROM model_profiles
           WHERE role = 'image_generator' AND status = 'active'
           ORDER BY
             CASE
               WHEN name LIKE '%Production Mini%' THEN 0
               WHEN name LIKE '%Production%' THEN 1
               WHEN name = 'Test' THEN 2
               ELSE 3
             END,
             id"""
    ).fetchall()
    for row in rows:
        tokens = {
            _model_token(row["name"]),
            _model_token(row["model"]),
        }
        if any(alias in tokens for alias in aliases):
            return row
    return None


def _ensure_gpt_image_2_profile(db):
    existing = _image_direct_profile_for_aliases(db, ("gptimage2",))
    if existing:
        return existing
    api_key = os.environ.get("ZENMUX_API_KEY") or _existing_zenmux_api_key(db)
    if not api_key:
        return None
    cur = db.execute(
        """INSERT INTO model_profiles
           (role, name, api_type, endpoint, model, api_key, temperature, thinking, status)
           VALUES ('image_generator', ?, 'zenmux_images', ?, 'gpt-image-2', ?, 1, NULL, 'active')""",
        (GPT_IMAGE_2_PROFILE_NAME, ZENMUX_IMAGES_ENDPOINT, api_key),
    )
    return db.execute("SELECT * FROM model_profiles WHERE id = ?", (cur.lastrowid,)).fetchone()


def ensure_image_direct_combinations() -> dict[str, list[int] | list[str]]:
    """Expose ImageDirect models as single-image-generator image configs when matching profiles exist."""
    created_config_ids: list[int] = []
    skipped: list[str] = []
    db = dbmod.get_db()
    try:
        designer = _active_profile_row(db, "designer")
        html_agent = _active_profile_row(db, "html_agent")
        if not designer or not html_agent:
            skipped.append("missing_compatibility_profile")
            return {"created_config_ids": created_config_ids, "skipped": skipped}

        for config_name, aliases in IMAGE_DIRECT_COMBINATIONS:
            existing = db.execute("SELECT id FROM configs WHERE name = ?", (config_name,)).fetchone()
            if existing:
                continue
            image_generator = _image_direct_profile_for_aliases(db, aliases)
            if not image_generator and config_name == "GPT image2":
                image_generator = _ensure_gpt_image_2_profile(db)
            if not image_generator:
                skipped.append(f"missing_image_generator_profile_for:{config_name}")
                continue
            cur = db.execute(
                """INSERT INTO configs
                   (name, type, designer, html_agent, timeout_minutes, max_concurrent_runs,
                    designer_profile_id, html_agent_profile_id, route_model_bindings, is_default)
                   VALUES (?, 'image', ?, ?, 30, 2, ?, ?, ?, 0)""",
                (
                    config_name,
                    json.dumps(_profile_config_from_row(designer), ensure_ascii=False),
                    json.dumps(_profile_config_from_row(html_agent), ensure_ascii=False),
                    int(designer["id"]),
                    int(html_agent["id"]),
                    json.dumps({"image_generator": {"profile_id": int(image_generator["id"])}}, ensure_ascii=False),
                ),
            )
            created_config_ids.append(int(cur.lastrowid))
        db.commit()
    finally:
        db.close()
    return {"created_config_ids": created_config_ids, "skipped": skipped}


def _expected_api_key(spec: HtmlTestProfileSpec, key_by_source: dict[str, str]) -> str:
    return key_by_source.get(spec.key_source, "")


def _matches_expected_html_test_profile(row, *, spec: HtmlTestProfileSpec, key_by_source: dict[str, str]) -> bool:
    return (
        row["status"] == "active"
        and row["api_type"] == spec.api_type
        and row["endpoint"] == spec.endpoint
        and row["model"] == spec.model
        and row["thinking"] == spec.thinking
        and row["api_key"] == _expected_api_key(spec, key_by_source)
    )


def _matches_product_profile(row, expected: ProductProfileSpec, api_key: str) -> bool:
    return (
        row["status"] == "active"
        and row["api_type"] == expected.api_type
        and row["endpoint"] == expected.endpoint
        and row["model"] == expected.model
        and float(row["temperature"]) == float(expected.temperature)
        and row["thinking"] == expected.thinking
        and row["api_key"] == api_key
    )


def _active_gemini_image_generator_profile(db):
    return db.execute(
        """SELECT * FROM model_profiles
           WHERE role = 'image_generator'
             AND api_type = 'gemini'
             AND endpoint = ?
             AND status = 'active'
             AND model != 'gpt-image-2'
           ORDER BY
             CASE
               WHEN name = 'Production' THEN 0
               WHEN name = 'Production Mini' THEN 1
               WHEN name = 'Test' THEN 2
               ELSE 3
             END,
             id
           LIMIT 1""",
        (GEMINI_ENDPOINT,),
    ).fetchone()


def _expected_image_product_bindings(
    designer_id: int,
    generator_id: int,
    palette_id: int | None = None,
) -> dict[str, dict[str, int]]:
    bindings = {
        "image_designer": {"profile_id": designer_id},
        "image_generator": {"profile_id": generator_id},
    }
    if palette_id is not None:
        bindings["image_palette_extractor"] = {"profile_id": palette_id}
    return bindings


def _decode_bindings(value) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}


def _matches_image_product_config(row, expected_bindings: dict[str, dict[str, int]], designer, generator) -> bool:
    return (
        row["type"] == "image"
        and int(row["is_default"] or 0) == 0
        and int(row["designer_profile_id"] or 0) == expected_bindings["image_designer"]["profile_id"]
        and int(row["html_agent_profile_id"] or 0) == expected_bindings["image_generator"]["profile_id"]
        and int(row["timeout_minutes"] or 0) == 30
        and int(row["max_concurrent_runs"] or 0) == 2
        and _decode_bindings(row["designer"]) == _profile_config_from_row(designer)
        and _decode_bindings(row["html_agent"]) == _profile_config_from_row(generator)
        and _decode_bindings(row["route_model_bindings"]) == expected_bindings
    )


def _product_profile_key(spec: ProductProfileSpec) -> tuple[str, str]:
    return (spec.role, spec.name)


def _ensure_product_profile(
    db,
    spec: ProductProfileSpec,
    *,
    api_key: str,
    created_profile_ids: list[int],
    skipped: list[str],
) -> int | None:
    existing = db.execute(
        "SELECT * FROM model_profiles WHERE role = ? AND name = ?",
        (spec.role, spec.name),
    ).fetchone()
    if existing:
        if not _matches_product_profile(existing, spec, api_key):
            skipped.append(f"profile_mismatch_for:{spec.role}:{spec.name}")
            return None
        return int(existing["id"])

    cur = db.execute(
        """INSERT INTO model_profiles
           (role, name, api_type, endpoint, model, api_key, temperature, thinking, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
        (
            spec.role,
            spec.name,
            spec.api_type,
            spec.endpoint,
            spec.model,
            api_key,
            spec.temperature,
            spec.thinking,
        ),
    )
    profile_id = int(cur.lastrowid)
    created_profile_ids.append(profile_id)
    return profile_id


def _image_product_config_row(db, spec: ImageProductConfigSpec):
    exact = db.execute("SELECT * FROM configs WHERE name = ?", (spec.name,)).fetchone()
    if exact:
        return exact, None
    for alias in spec.legacy_aliases:
        row = db.execute("SELECT * FROM configs WHERE name = ?", (alias,)).fetchone()
        if row:
            return row, alias
    return None, None


def _rename_legacy_product_config_alias(db, config_id: int, spec: ImageProductConfigSpec, alias: str) -> str | None:
    conflict = db.execute("SELECT id FROM configs WHERE name = ? AND id != ?", (spec.name, config_id)).fetchone()
    if conflict:
        return f"config_name_conflict_for:{spec.name}"
    db.execute("UPDATE configs SET name = ?, updated_at = datetime('now') WHERE id = ?", (spec.name, config_id))
    return None


def _semantically_matches_product_profile(row, expected: ProductProfileSpec, api_key: str) -> bool:
    return (
        row
        and row["status"] == "active"
        and row["role"] == expected.role
        and row["api_type"] == expected.api_type
        and row["endpoint"] == expected.endpoint
        and row["model"] == expected.model
        and float(row["temperature"]) == float(expected.temperature)
        and row["thinking"] == expected.thinking
        and row["api_key"] == api_key
    )


def _repairable_legacy_product_alias(
    db,
    row,
    designer_spec: ProductProfileSpec,
    generator_spec: ProductProfileSpec,
    *,
    zenmux_key: str,
) -> bool:
    if (
        not row
        or row["type"] != "image"
        or int(row["is_default"] or 0) != 0
        or int(row["timeout_minutes"] or 0) != 30
        or int(row["max_concurrent_runs"] or 0) != 2
    ):
        return False
    designer = db.execute("SELECT * FROM model_profiles WHERE id = ?", (row["designer_profile_id"],)).fetchone()
    generator = db.execute("SELECT * FROM model_profiles WHERE id = ?", (row["html_agent_profile_id"],)).fetchone()
    return _semantically_matches_product_profile(designer, designer_spec, zenmux_key) and _semantically_matches_product_profile(
        generator,
        generator_spec,
        zenmux_key,
    )


def _update_image_product_config_row(db, config_id: int, spec: ImageProductConfigSpec, expected_bindings, designer, generator) -> None:
    db.execute(
        """UPDATE configs
           SET name = ?, type = 'image', designer = ?, html_agent = ?,
               timeout_minutes = 30, max_concurrent_runs = 2,
               designer_profile_id = ?, html_agent_profile_id = ?,
               route_model_bindings = ?, is_default = 0,
               updated_at = datetime('now')
           WHERE id = ?""",
        (
            spec.name,
            json.dumps(_profile_config_from_row(designer), ensure_ascii=False),
            json.dumps(_profile_config_from_row(generator), ensure_ascii=False),
            expected_bindings["image_designer"]["profile_id"],
            expected_bindings["image_generator"]["profile_id"],
            json.dumps(expected_bindings, ensure_ascii=False),
            config_id,
        ),
    )


def _matches_codex_html_config(row, designer, html_agent) -> bool:
    return (
        row["type"] == "html"
        and int(row["is_default"] or 0) == 0
        and int(row["designer_profile_id"] or 0) == int(designer["id"])
        and int(row["html_agent_profile_id"] or 0) == int(html_agent["id"])
        and int(row["timeout_minutes"] or 0) == 30
        and int(row["max_concurrent_runs"] or 0) == 2
        and _decode_bindings(row["designer"]) == _profile_config_from_row(designer)
        and _decode_bindings(row["html_agent"]) == _profile_config_from_row(html_agent)
        and _decode_bindings(row["route_model_bindings"]) == {}
    )


def ensure_codex_html_combination() -> dict[str, list[int] | list[str]]:
    """Seed the non-default Codex HTML product combination."""
    created_profile_ids: list[int] = []
    created_config_ids: list[int] = []
    skipped: list[str] = []
    profile_ids: dict[str, int] = {}

    db = dbmod.get_db()
    try:
        for spec in CODEX_HTML_PROFILE_SPECS:
            profile_id = _ensure_product_profile(
                db,
                spec,
                api_key="",
                created_profile_ids=created_profile_ids,
                skipped=skipped,
            )
            if profile_id is None:
                db.commit()
                return {
                    "created_profile_ids": created_profile_ids,
                    "created_config_ids": created_config_ids,
                    "skipped": skipped,
                }
            profile_ids[spec.role] = profile_id

        designer = db.execute("SELECT * FROM model_profiles WHERE id = ?", (profile_ids["designer"],)).fetchone()
        html_agent = db.execute("SELECT * FROM model_profiles WHERE id = ?", (profile_ids["html_agent"],)).fetchone()
        existing = db.execute("SELECT * FROM configs WHERE name = ?", (CODEX_HTML_CONFIG_NAME,)).fetchone()
        if existing:
            if not _matches_codex_html_config(existing, designer, html_agent):
                skipped.append(f"config_mismatch_for:{CODEX_HTML_CONFIG_NAME}")
            db.commit()
            return {
                "created_profile_ids": created_profile_ids,
                "created_config_ids": created_config_ids,
                "skipped": skipped,
            }

        cur = db.execute(
            """INSERT INTO configs
               (name, type, designer, html_agent, timeout_minutes, max_concurrent_runs,
                designer_profile_id, html_agent_profile_id, route_model_bindings, is_default)
               VALUES (?, 'html', ?, ?, 30, 2, ?, ?, NULL, 0)""",
            (
                CODEX_HTML_CONFIG_NAME,
                json.dumps(_profile_config_from_row(designer), ensure_ascii=False),
                json.dumps(_profile_config_from_row(html_agent), ensure_ascii=False),
                profile_ids["designer"],
                profile_ids["html_agent"],
            ),
        )
        created_config_ids.append(int(cur.lastrowid))
        db.commit()
    finally:
        db.close()
    return {
        "created_profile_ids": created_profile_ids,
        "created_config_ids": created_config_ids,
        "skipped": skipped,
    }


def _ensure_native_image_profile(db, spec: ProductProfileSpec, created_profile_ids: list[int]) -> int:
    existing = db.execute(
        "SELECT * FROM model_profiles WHERE role = ? AND name = ?",
        (spec.role, spec.name),
    ).fetchone()
    api_key = ""
    if spec.key_source == "gemini":
        api_key = str(existing["api_key"] or "") if existing else ""
        api_key = api_key or _default_api_key(db, spec.api_type)
    if existing:
        db.execute(
            """UPDATE model_profiles
               SET api_type = ?, endpoint = ?, model = ?, api_key = ?, temperature = ?,
                   thinking = ?, status = 'active', updated_at = datetime('now')
               WHERE id = ?""",
            (
                spec.api_type,
                spec.endpoint,
                spec.model,
                api_key,
                spec.temperature,
                spec.thinking,
                int(existing["id"]),
            ),
        )
        return int(existing["id"])

    cur = db.execute(
        """INSERT INTO model_profiles
           (role, name, api_type, endpoint, model, api_key, temperature, thinking, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
        (
            spec.role,
            spec.name,
            spec.api_type,
            spec.endpoint,
            spec.model,
            api_key,
            spec.temperature,
            spec.thinking,
        ),
    )
    profile_id = int(cur.lastrowid)
    created_profile_ids.append(profile_id)
    return profile_id


def _native_image_config_spec(config: dict[str, Any] | None) -> tuple[str, bool] | None:
    if not config:
        return None
    name = config.get("name")
    if name == NATIVE_IMAGE_DIRECT_CONFIG_NAME:
        return NATIVE_IMAGE_DIRECT_ROUTE, False
    if name == NATIVE_IMAGE_3_0_CONFIG_NAME:
        return NATIVE_IMAGE_3_0_ROUTE, True
    if name == NATIVE_IMAGE_3_0_LUNA_DIRECTOR_CONFIG_NAME:
        return NATIVE_IMAGE_3_0_ROUTE, True
    if (
        name == NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME
        and image_pptgen_e2e_terra_low_enabled()
    ):
        return NATIVE_IMAGE_3_0_ROUTE, True
    return None


def is_system_managed_native_profile(profile: dict[str, Any] | None) -> bool:
    if not profile:
        return False
    specs = list(NATIVE_IMAGE_PROFILE_SPECS)
    if image_pptgen_e2e_terra_low_enabled():
        specs.extend(NATIVE_IMAGE_TERRA_E2E_PROFILE_SPECS)
    return any(
        profile.get("role") == spec.role and profile.get("name") == spec.name
        for spec in specs
    )


def is_system_managed_native_config(config: dict[str, Any] | None) -> bool:
    return _native_image_config_spec(config) is not None


def native_image_route_for_config(config: dict[str, Any] | None) -> str | None:
    spec = _native_image_config_spec(config)
    return spec[0] if spec else None


def has_native_image_binding(value: object) -> bool:
    return "native_image" in _decode_bindings(value)


def _ensure_native_image_config(
    db,
    *,
    name: str,
    route: str,
    director,
    launcher,
    palette,
    include_director: bool,
    created_config_ids: list[int],
) -> None:
    designer = director if include_director else launcher
    bindings: dict[str, Any] = {
        "image_generator": {"profile_id": int(launcher["id"])},
        "native_image": {"adapter": NATIVE_IMAGE_ADAPTER, "route": route},
    }
    if include_director:
        bindings = {
            "image_designer": {"profile_id": int(director["id"])},
            "image_palette_extractor": {"profile_id": int(palette["id"])},
            **bindings,
        }

    values = (
        json.dumps(_profile_config_from_row(designer), ensure_ascii=False),
        json.dumps(_profile_config_from_row(launcher), ensure_ascii=False),
        int(designer["id"]),
        int(launcher["id"]),
        json.dumps(bindings, ensure_ascii=False),
    )
    existing = db.execute("SELECT id FROM configs WHERE name = ?", (name,)).fetchone()
    if existing:
        db.execute(
            """UPDATE configs
               SET type = 'image', designer = ?, html_agent = ?, timeout_minutes = 30,
                   max_concurrent_runs = ?, designer_profile_id = ?, html_agent_profile_id = ?,
                   route_model_bindings = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (
                values[0],
                values[1],
                NATIVE_IMAGE_MAX_CONCURRENT_RUNS,
                values[2],
                values[3],
                values[4],
                int(existing["id"]),
            ),
        )
        return

    cur = db.execute(
        """INSERT INTO configs
           (name, type, designer, html_agent, timeout_minutes, max_concurrent_runs,
            designer_profile_id, html_agent_profile_id, route_model_bindings, is_default)
           VALUES (?, 'image', ?, ?, 30, ?, ?, ?, ?, 0)""",
        (
            name,
            values[0],
            values[1],
            NATIVE_IMAGE_MAX_CONCURRENT_RUNS,
            values[2],
            values[3],
            values[4],
        ),
    )
    created_config_ids.append(int(cur.lastrowid))


def ensure_native_image_combinations() -> dict[str, list[int]]:
    """Seed and repair the server-owned Native Image choices without default drift."""
    created_profile_ids: list[int] = []
    created_config_ids: list[int] = []
    db = dbmod.get_db()
    try:
        profile_specs = list(NATIVE_IMAGE_PROFILE_SPECS)
        if image_pptgen_e2e_terra_low_enabled():
            profile_specs.extend(NATIVE_IMAGE_TERRA_E2E_PROFILE_SPECS)
        profile_ids = {
            (spec.role, spec.name): _ensure_native_image_profile(
                db, spec, created_profile_ids
            )
            for spec in profile_specs
        }
        director = db.execute(
            "SELECT * FROM model_profiles WHERE id = ?",
            (
                profile_ids[
                    ("image_designer", NATIVE_IMAGE_DIRECTOR_PROFILE_NAME)
                ],
            ),
        ).fetchone()
        luna_director = db.execute(
            "SELECT * FROM model_profiles WHERE id = ?",
            (
                profile_ids[
                    ("image_designer", NATIVE_IMAGE_LUNA_DIRECTOR_PROFILE_NAME)
                ],
            ),
        ).fetchone()
        launcher = db.execute(
            "SELECT * FROM model_profiles WHERE id = ?",
            (
                profile_ids[
                    ("image_generator", NATIVE_IMAGE_LAUNCHER_PROFILE_NAME)
                ],
            ),
        ).fetchone()
        palette = db.execute(
            "SELECT * FROM model_profiles WHERE id = ?",
            (
                profile_ids[
                    ("image_generator", NATIVE_IMAGE_PALETTE_PROFILE_NAME)
                ],
            ),
        ).fetchone()
        _ensure_native_image_config(
            db,
            name=NATIVE_IMAGE_DIRECT_CONFIG_NAME,
            route=NATIVE_IMAGE_DIRECT_ROUTE,
            director=director,
            launcher=launcher,
            palette=palette,
            include_director=False,
            created_config_ids=created_config_ids,
        )
        if image_pptgen_e2e_terra_low_enabled():
            terra_director = db.execute(
                "SELECT * FROM model_profiles WHERE id = ?",
                (
                    profile_ids[
                        ("image_designer", NATIVE_IMAGE_TERRA_DIRECTOR_PROFILE_NAME)
                    ],
                ),
            ).fetchone()
            terra_launcher = db.execute(
                "SELECT * FROM model_profiles WHERE id = ?",
                (
                    profile_ids[
                        ("image_generator", NATIVE_IMAGE_TERRA_LAUNCHER_PROFILE_NAME)
                    ],
                ),
            ).fetchone()
            _ensure_native_image_config(
                db,
                name=NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME,
                route=NATIVE_IMAGE_3_0_ROUTE,
                director=terra_director,
                launcher=terra_launcher,
                palette=terra_director,
                include_director=True,
                created_config_ids=created_config_ids,
            )
        _ensure_native_image_config(
            db,
            name=NATIVE_IMAGE_3_0_CONFIG_NAME,
            route=NATIVE_IMAGE_3_0_ROUTE,
            director=director,
            launcher=launcher,
            palette=director,
            include_director=True,
            created_config_ids=created_config_ids,
        )
        _ensure_native_image_config(
            db,
            name=NATIVE_IMAGE_3_0_LUNA_DIRECTOR_CONFIG_NAME,
            route=NATIVE_IMAGE_3_0_ROUTE,
            director=luna_director,
            launcher=launcher,
            palette=luna_director,
            include_director=True,
            created_config_ids=created_config_ids,
        )
        db.commit()
    finally:
        db.close()
    return {
        "created_profile_ids": created_profile_ids,
        "created_config_ids": created_config_ids,
    }


def ensure_gpt_image_2_product_combinations() -> dict[str, list[int] | list[str]]:
    """Seed optional Image 5.0 product profiles and product-facing combinations."""
    created_profile_ids: list[int] = []
    created_config_ids: list[int] = []
    created_or_existing_config_names: list[str] = []
    renamed_config_names: list[str] = []
    skipped: list[str] = []
    profile_ids: dict[tuple[str, str], int] = {}

    db = dbmod.get_db()
    try:
        zenmux_key = _existing_zenmux_api_key(db)
        if not zenmux_key:
            return {
                "created_profile_ids": created_profile_ids,
                "created_config_ids": created_config_ids,
                "created_or_existing_config_names": created_or_existing_config_names,
                "renamed_config_names": renamed_config_names,
                "skipped": ["missing_zenmux_key"],
            }
        gemini_key = _existing_gemini_api_key(db)
        if not gemini_key:
            return {
                "created_profile_ids": created_profile_ids,
                "created_config_ids": created_config_ids,
                "created_or_existing_config_names": created_or_existing_config_names,
                "renamed_config_names": renamed_config_names,
                "skipped": ["missing_gemini_key"],
            }

        key_by_source = {"zenmux": zenmux_key, "gemini": gemini_key}
        profile_specs = {
            _product_profile_key(spec): spec
            for spec in IMAGE_PRODUCT_PROFILE_SPECS
        }
        for spec in IMAGE_PRODUCT_PROFILE_SPECS:
            profile_id = _ensure_product_profile(
                db,
                spec,
                api_key=key_by_source[spec.key_source],
                created_profile_ids=created_profile_ids,
                skipped=skipped,
            )
            if profile_id is not None:
                profile_ids[_product_profile_key(spec)] = profile_id

        for spec in IMAGE_PRODUCT_CONFIG_SPECS:
            designer_id = profile_ids.get(("image_designer", spec.designer_profile_name))
            generator_id = profile_ids.get(("image_generator", spec.generator_profile_name))
            palette_id = (
                profile_ids.get(("image_generator", spec.palette_profile_name))
                if spec.palette_profile_name
                else None
            )
            if not designer_id or not generator_id or (spec.palette_profile_name and not palette_id):
                skipped.append(f"missing_profile_for:{spec.name}")
                continue
            expected_bindings = _expected_image_product_bindings(designer_id, generator_id, palette_id)
            designer = db.execute(
                "SELECT * FROM model_profiles WHERE id = ?",
                (designer_id,),
            ).fetchone()
            generator = db.execute(
                "SELECT * FROM model_profiles WHERE id = ?",
                (generator_id,),
            ).fetchone()
            existing_config, alias = _image_product_config_row(db, spec)
            if existing_config:
                if _matches_image_product_config(existing_config, expected_bindings, designer, generator):
                    if alias:
                        conflict = _rename_legacy_product_config_alias(db, int(existing_config["id"]), spec, alias)
                        if conflict:
                            skipped.append(conflict)
                            continue
                        renamed_config_names.append(f"{alias}->{spec.name}")
                    created_or_existing_config_names.append(spec.name)
                elif alias and _repairable_legacy_product_alias(
                    db,
                    existing_config,
                    profile_specs[("image_designer", spec.designer_profile_name)],
                    profile_specs[("image_generator", spec.generator_profile_name)],
                    zenmux_key=zenmux_key,
                ):
                    conflict = db.execute("SELECT id FROM configs WHERE name = ? AND id != ?", (spec.name, int(existing_config["id"]))).fetchone()
                    if conflict:
                        skipped.append(f"config_name_conflict_for:{spec.name}")
                        continue
                    _update_image_product_config_row(db, int(existing_config["id"]), spec, expected_bindings, designer, generator)
                    renamed_config_names.append(f"{alias}->{spec.name}")
                    created_or_existing_config_names.append(spec.name)
                else:
                    skipped.append(f"config_mismatch_for:{existing_config['name']}")
            else:
                cur = db.execute(
                    """INSERT INTO configs
                       (name, type, designer, html_agent, timeout_minutes, max_concurrent_runs,
                        designer_profile_id, html_agent_profile_id, route_model_bindings, is_default)
                       VALUES (?, 'image', ?, ?, 30, 2, ?, ?, ?, 0)""",
                    (
                        spec.name,
                        json.dumps(_profile_config_from_row(designer), ensure_ascii=False),
                        json.dumps(_profile_config_from_row(generator), ensure_ascii=False),
                        designer_id,
                        generator_id,
                        json.dumps(expected_bindings, ensure_ascii=False),
                    ),
                )
                created_config_ids.append(int(cur.lastrowid))
                created_or_existing_config_names.append(spec.name)

        db.commit()
    finally:
        db.close()
    return {
        "created_profile_ids": created_profile_ids,
        "created_config_ids": created_config_ids,
        "created_or_existing_config_names": created_or_existing_config_names,
        "renamed_config_names": renamed_config_names,
        "skipped": skipped,
    }


def _retire_stale_html_test_configs(db) -> tuple[list[str], list[dict[str, int | str]]]:
    retired_names: list[str] = []
    blockers: list[dict[str, int | str]] = []
    placeholders = ", ".join("?" for _ in HTML_TEST_RETIRED_CONFIG_NAMES)
    rows = db.execute(
        f"""SELECT id, name FROM configs
            WHERE type = 'html'
              AND COALESCE(is_default, 0) = 0
              AND name IN ({placeholders})
            ORDER BY id""",
        HTML_TEST_RETIRED_CONFIG_NAMES,
    ).fetchall()
    for row in rows:
        batch_refs = db.execute("SELECT COUNT(*) AS count FROM batches WHERE config_id = ?", (row["id"],)).fetchone()["count"]
        run_refs = db.execute("SELECT COUNT(*) AS count FROM runs WHERE config_id = ?", (row["id"],)).fetchone()["count"]
        if batch_refs or run_refs:
            db.execute(
                "UPDATE configs SET type = ?, updated_at = datetime('now') WHERE id = ?",
                (RETIRED_HTML_TEST_CONFIG_TYPE, row["id"]),
            )
            blockers.append(
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "batch_references": int(batch_refs),
                    "run_references": int(run_refs),
                    "action": "marked_retired_unselectable",
                }
            )
            continue
        db.execute("DELETE FROM configs WHERE id = ?", (row["id"],))
        retired_names.append(row["name"])
    return retired_names, blockers


def ensure_html_test_zenmux_combinations() -> dict[str, list[int] | list[str] | list[dict[str, int | str]]]:
    """Seed optional HTML-route ZenMux comparison profiles and combinations."""
    created_profile_ids: list[int] = []
    created_config_ids: list[int] = []
    skipped: list[str] = []
    retired_config_names: list[str] = []
    retirement_blockers: list[dict[str, int | str]] = []
    profile_mismatches: set[tuple[str, str]] = set()
    profile_expectations = {
        (spec.role, spec.name): spec
        for spec in HTML_TEST_ZENMUX_PROFILES
    }
    db = dbmod.get_db()
    try:
        retired_config_names, retirement_blockers = _retire_stale_html_test_configs(db)
        zenmux_key = _existing_zenmux_api_key(db)
        gemini_key = _existing_gemini_api_key(db)
        key_by_source = {"zenmux": zenmux_key, "gemini": gemini_key}
        if not zenmux_key:
            skipped.append("missing_active_zenmux_api_key")
            return {
                "created_profile_ids": created_profile_ids,
                "created_config_ids": created_config_ids,
                "skipped": skipped,
                "retired_config_names": retired_config_names,
                "retirement_blockers": retirement_blockers,
            }
        if not gemini_key:
            skipped.append("missing_active_gemini_api_key")
            return {
                "created_profile_ids": created_profile_ids,
                "created_config_ids": created_config_ids,
                "skipped": skipped,
                "retired_config_names": retired_config_names,
                "retirement_blockers": retirement_blockers,
            }
        for spec in HTML_TEST_ZENMUX_PROFILES:
            existing = db.execute(
                "SELECT * FROM model_profiles WHERE role = ? AND name = ?",
                (spec.role, spec.name),
            ).fetchone()
            if existing:
                if not _matches_expected_html_test_profile(
                    existing,
                    spec=spec,
                    key_by_source=key_by_source,
                ):
                    profile_mismatches.add((spec.role, spec.name))
                    skipped.append(f"profile_mismatch_for:{spec.role}:{spec.name}")
                continue
            cur = db.execute(
                """INSERT INTO model_profiles
                   (role, name, api_type, endpoint, model, api_key, temperature, thinking, status)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'active')""",
                (
                    spec.role,
                    spec.name,
                    spec.api_type,
                    spec.endpoint,
                    spec.model,
                    _expected_api_key(spec, key_by_source),
                    spec.thinking,
                ),
            )
            created_profile_ids.append(int(cur.lastrowid))

        for config_name, designer_name, html_name in HTML_TEST_ZENMUX_COMBINATIONS:
            existing = db.execute("SELECT id FROM configs WHERE name = ?", (config_name,)).fetchone()
            if existing:
                continue
            designer = db.execute(
                "SELECT * FROM model_profiles WHERE role = 'designer' AND name = ?",
                (designer_name,),
            ).fetchone()
            html_agent = db.execute(
                "SELECT * FROM model_profiles WHERE role = 'html_agent' AND name = ?",
                (html_name,),
            ).fetchone()
            if not designer or not html_agent:
                skipped.append(f"missing_profile_for:{config_name}")
                continue
            designer_expected = profile_expectations.get(("designer", designer_name))
            html_expected = profile_expectations.get(("html_agent", html_name))
            designer_mismatch = (
                ("designer", designer_name) in profile_mismatches
                or not designer_expected
                or not _matches_expected_html_test_profile(
                    designer,
                    spec=designer_expected,
                    key_by_source=key_by_source,
                )
            )
            html_mismatch = (
                ("html_agent", html_name) in profile_mismatches
                or not html_expected
                or not _matches_expected_html_test_profile(
                    html_agent,
                    spec=html_expected,
                    key_by_source=key_by_source,
                )
            )
            if designer_mismatch or html_mismatch:
                skipped.append(f"profile_mismatch_for:{config_name}")
                continue
            cur = db.execute(
                """INSERT INTO configs
                   (name, type, designer, html_agent, timeout_minutes, max_concurrent_runs,
                    designer_profile_id, html_agent_profile_id, route_model_bindings, is_default)
                   VALUES (?, 'html', ?, ?, 30, 2, ?, ?, NULL, 0)""",
                (
                    config_name,
                    json.dumps(_profile_config_from_row(designer), ensure_ascii=False),
                    json.dumps(_profile_config_from_row(html_agent), ensure_ascii=False),
                    int(designer["id"]),
                    int(html_agent["id"]),
                ),
            )
            created_config_ids.append(int(cur.lastrowid))
        db.commit()
    finally:
        db.close()
    return {
        "created_profile_ids": created_profile_ids,
        "created_config_ids": created_config_ids,
        "skipped": skipped,
        "retired_config_names": retired_config_names,
        "retirement_blockers": retirement_blockers,
    }


def list_profiles(role: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    clauses = []
    values: list[Any] = []
    if role:
        if role not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        clauses.append("role = ?")
        values.append(role)
    if status:
        clauses.append("status = ?")
        values.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    db = dbmod.get_db()
    rows = db.execute(f"SELECT * FROM model_profiles {where} ORDER BY role, id", values).fetchall()
    db.close()
    return [_with_system_managed_metadata(profile) for profile in dbmod.rows_to_list(rows)]


def get_profile(profile_id: int | None) -> dict[str, Any] | None:
    if not profile_id:
        return None
    db = dbmod.get_db()
    row = db.execute("SELECT * FROM model_profiles WHERE id = ?", (profile_id,)).fetchone()
    db.close()
    profile = dbmod.row_to_dict(row)
    return _with_system_managed_metadata(profile) if profile else None


def _with_system_managed_metadata(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        **profile,
        "system_managed": is_system_managed_native_profile(profile),
    }


def update_profile(profile_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    current = get_profile(profile_id)
    if not current:
        return None
    merged = {**current, **data, "role": data.get("role", current["role"])}
    profile = normalize_profile(merged)
    db = dbmod.get_db()
    db.execute(
        """UPDATE model_profiles
           SET role = ?, name = ?, api_type = ?, endpoint = ?, model = ?, api_key = ?,
               temperature = ?, thinking = ?, status = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (
            profile["role"],
            profile["name"],
            profile["api_type"],
            profile["endpoint"],
            profile["model"],
            profile["api_key"],
            profile["temperature"],
            profile["thinking"],
            profile["status"],
            profile_id,
        ),
    )
    db.commit()
    db.close()
    return get_profile(profile_id)


def delete_profile(profile_id: int) -> bool:
    db = dbmod.get_db()
    cur = db.execute("DELETE FROM model_profiles WHERE id = ?", (profile_id,))
    db.commit()
    deleted = cur.rowcount > 0
    db.close()
    return deleted


def _agent_config(config_row: dict[str, Any], key: str) -> dict[str, Any]:
    value = config_row[key]
    return json.loads(value) if isinstance(value, str) else dict(value)


def _legacy_profile_data(config_row: dict[str, Any], role: str, source: dict[str, Any]) -> dict[str, Any]:
    label = {
        "designer": "Designer",
        "html_agent": "HTML Agent",
    }[role]
    return {
        "role": role,
        "name": f"{config_row['name']} {label}",
        "api_type": source.get("api_type") or "openai",
        "endpoint": source.get("endpoint") or "",
        "model": source.get("model"),
        "api_key": source.get("api_key") or "",
        "temperature": source.get("temperature", 0.7),
        "thinking": source.get("thinking"),
        "status": "active",
    }


def ensure_profiles_for_legacy_config(config_row: dict[str, Any]) -> dict[str, int]:
    profile_ids = {
        "designer_profile_id": config_row.get("designer_profile_id"),
        "html_agent_profile_id": config_row.get("html_agent_profile_id"),
    }
    if all(profile_ids.values()):
        return {key: int(value) for key, value in profile_ids.items()}

    designer = _agent_config(config_row, "designer")
    html_agent = _agent_config(config_row, "html_agent")
    created = dict(profile_ids)
    if not created["designer_profile_id"]:
        created["designer_profile_id"] = create_profile(_legacy_profile_data(config_row, "designer", designer))
    if not created["html_agent_profile_id"]:
        created["html_agent_profile_id"] = create_profile(_legacy_profile_data(config_row, "html_agent", html_agent))
    dbmod.update_config(
        int(config_row["id"]),
        designer_profile_id=created["designer_profile_id"],
        html_agent_profile_id=created["html_agent_profile_id"],
    )
    return {key: int(value) for key, value in created.items()}


def _profile_config(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_type": profile["api_type"],
        "endpoint": profile["endpoint"],
        "model": profile["model"],
        "api_key": profile["api_key"],
        "temperature": profile["temperature"],
        "thinking": profile["thinking"],
        "profile_id": profile["id"],
        "profile_name": profile["name"],
    }


def profile_to_agent_config(profile_id: int) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise ValueError("Model profile not found")
    if profile["status"] != "active":
        raise ValueError("Model profile is not active")
    return _profile_config(profile)


def active_profile_config_for_role(role: str) -> dict[str, Any] | None:
    profiles = list_profiles(role=role, status="active")
    if not profiles:
        return None
    return _profile_config(profiles[0])


def resolve_config(config_id: int) -> dict[str, Any]:
    config_row = dbmod.get_config(config_id)
    if not config_row:
        raise ValueError("Config not found")

    profile_ids = ensure_profiles_for_legacy_config(config_row)
    designer = get_profile(profile_ids["designer_profile_id"])
    html_agent = get_profile(profile_ids["html_agent_profile_id"])
    if not designer or not html_agent:
        raise ValueError("Config references missing model profiles")

    resolved = dict(config_row)
    resolved["designer"] = _profile_config(designer)
    resolved["html_agent"] = _profile_config(html_agent)
    bindings = resolved.get("route_model_bindings")
    if isinstance(bindings, str) and bindings:
        try:
            resolved["route_model_bindings"] = json.loads(bindings)
        except json.JSONDecodeError:
            resolved["route_model_bindings"] = {}
    elif not bindings:
        resolved["route_model_bindings"] = {}
    resolved["is_default"] = bool(resolved.get("is_default"))
    resolved["system_managed"] = is_system_managed_native_config(resolved)
    return resolved
