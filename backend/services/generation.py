"""Generation request validation and batch/run creation service."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import db as dbmod
from backend.services import model_profiles
from backend.services.auto_generation import (
    AUTO_MAX_CANDIDATES,
    AutoGenerationRequestError,
    create_auto_generation_batch,
    get_or_create_auto_requirement,
    get_or_create_empty_color,
)
from backend.services.scheduler import launch_batch_runs as default_launch_batch_runs

DEFAULT_BATCH_RUN_LIMIT = 10
VALID_ENGINES = {"html", "image"}
HTML_STRATEGIES = {"html_default", "codex_html"}
VALID_STRATEGIES = {
    "html_default",
    "codex_html",
    "image_1_0",
    "image_3_0",
    "image_3_2",
    "image_5_0",
    "image_direct",
}
IMAGE_STRATEGIES = VALID_STRATEGIES - HTML_STRATEGIES
NO_GENERIC_DIMENSION_IMAGE_STRATEGIES = {
    "image_1_0",
    "image_3_0",
    "image_3_2",
    "image_direct",
}
IMAGE_RENDERERS = {"banana", "gpt_image_2"}
GPT_IMAGE_PROVIDER_CHANNEL = "zenmux_images_api"
GPT_IMAGE_REQUEST_MODE = "blueprint_first"
IMAGE_SKILL_ROUTE_METADATA_KEY = "_image_skill"
IMAGE_SKILL_ROUTE_METADATA_VERSION = 1
BatchLauncher = Callable[[list[int], str, int], object]
EMPTY_REQUIREMENT_TITLE = "System Empty Requirement"
SECRET_KEY_PARTS = ("apikey", "token", "authorization", "secret", "password")
SECRET_VALUE_MARKERS = ("bearer ", "api-key", "apikey", "secret", "token=")


@dataclass
class GenerationRequestError(Exception):
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def _require_fields(data: dict, *fields: str) -> None:
    missing = [field for field in fields if field not in data or data[field] is None]
    if missing:
        raise GenerationRequestError(f"Missing required fields: {', '.join(missing)}", 400)


def _resolve_prompt_id(agent_type: str, explicit_prompt_id: int | None) -> int | None:
    if explicit_prompt_id is not None:
        return explicit_prompt_id
    active_prompt = dbmod.get_active_prompt(agent_type)
    return active_prompt["id"] if active_prompt else None


def _resolve_route(data: dict) -> tuple[str, str, dict]:
    engine = data.get("engine") or "html"
    strategy = data.get("strategy") or ("html_default" if engine == "html" else "image_5_0")
    if engine not in VALID_ENGINES:
        raise GenerationRequestError("engine must be html or image", 400)
    if strategy not in VALID_STRATEGIES:
        raise GenerationRequestError("Unknown route strategy", 400)
    if engine == "html" and strategy not in HTML_STRATEGIES:
        raise GenerationRequestError("HTML engine only supports HTML strategies", 400)
    if engine == "image" and strategy not in IMAGE_STRATEGIES:
        raise GenerationRequestError("Image engine requires an Image strategy", 400)
    route_metadata = data.get("route_metadata") or {}
    if not isinstance(route_metadata, dict):
        raise GenerationRequestError("route_metadata must be an object", 400)
    # This key is reserved for the server-owned Image Skill cover marker.  A
    # caller can provide arbitrary route metadata for legacy routes, but may not
    # activate or override the immutable Image Skill source snapshot.
    route_metadata = dict(route_metadata)
    route_metadata.pop(IMAGE_SKILL_ROUTE_METADATA_KEY, None)
    return engine, strategy, route_metadata


def _image_skill_cover_metadata(
    engine: str,
    route_metadata: dict,
    slides: list[dict],
) -> dict:
    """Add trusted Image Skill source metadata from a persisted cover marker."""
    normalized = dict(route_metadata)
    normalized.pop(IMAGE_SKILL_ROUTE_METADATA_KEY, None)
    if engine != "image":
        return normalized
    marker = next(
        (
            slide
            for slide in slides
            if slide.get("position") == 1
            and slide.get("split_mode") == "image_skill_cover"
        ),
        None,
    )
    if marker is None:
        return normalized
    title = marker.get("title")
    source_body = marker.get("content")
    if not isinstance(title, str) or not isinstance(source_body, str):
        raise GenerationRequestError("Image Skill cover marker is invalid", 422)
    normalized[IMAGE_SKILL_ROUTE_METADATA_KEY] = {
        "version": IMAGE_SKILL_ROUTE_METADATA_VERSION,
        "cover_title": title,
        "source_body": source_body,
    }
    return normalized


def _html_profile_route_kind(config: dict | None) -> str:
    if not config:
        return "api"
    try:
        resolved = model_profiles.resolve_config(int(config["id"]))
    except (KeyError, TypeError, ValueError):
        return "api"
    designer_codex = model_profiles.is_codex_profile(resolved.get("designer"))
    html_codex = model_profiles.is_codex_profile(resolved.get("html_agent"))
    if designer_codex and html_codex:
        return "codex"
    if designer_codex or html_codex:
        return "mixed"
    return "api"


def _effective_html_strategy(engine: str, requested_strategy: str, config: dict | None) -> str:
    if engine != "html":
        return requested_strategy
    route_kind = _html_profile_route_kind(config)
    if route_kind == "mixed":
        raise GenerationRequestError(
            "HTML Codex route requires both Designer and HTML Agent profiles to use codex_exec",
            422,
        )
    if route_kind == "codex":
        return "codex_html"
    if requested_strategy == "codex_html":
        raise GenerationRequestError("codex_html requires Codex Designer and HTML Agent profiles", 422)
    return "html_default"


def _html_route_metadata(strategy: str, route_metadata: dict) -> dict:
    if strategy != "codex_html":
        return route_metadata
    return {**route_metadata, "route": "direct_codex_exec_json"}


def _binding_profile_id(binding: object) -> int | None:
    if isinstance(binding, dict):
        binding = binding.get("profile_id")
    try:
        return int(binding) if binding not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _image_generator_profile_from_config(config: dict | None) -> dict | None:
    if not config:
        return None
    bindings = _parse_json_dict(config.get("route_model_bindings"))
    profile_id = _binding_profile_id(bindings.get("image_generator"))
    if not profile_id:
        return None
    from backend.services import model_profiles

    return model_profiles.get_profile(profile_id)


def _is_gpt_image_2_profile(profile: dict | None) -> bool:
    if not profile:
        return False
    api_type = str(profile.get("api_type") or "").strip().lower()
    model = str(profile.get("model") or "").strip().lower()
    endpoint = str(profile.get("endpoint") or "").strip().lower()
    return api_type == "zenmux_images" or model == "gpt-image-2" or "gpt-image-2" in endpoint


def _is_openai_native_images_profile(profile: dict | None) -> bool:
    if not profile:
        return False
    api_type = str(profile.get("api_type") or "").strip().lower()
    endpoint = str(profile.get("endpoint") or "").strip().lower()
    return api_type in {"openai_images", "openai_images_api"} or "api.openai.com" in endpoint


def infer_image_renderer_from_config(config: dict | None) -> str | None:
    profile = _image_generator_profile_from_config(config)
    if _is_gpt_image_2_profile(profile):
        return "gpt_image_2"
    return None


def normalize_image_route_metadata(
    engine: str,
    strategy: str,
    route_metadata: dict | None,
    config: dict | None = None,
) -> dict:
    normalized = dict(route_metadata or {})
    normalized.pop(IMAGE_SKILL_ROUTE_METADATA_KEY, None)
    if engine != "image":
        return normalized

    native_route = model_profiles.native_image_route_for_config(config)
    if native_route:
        if strategy != native_route:
            raise GenerationRequestError(
                "Native Image config requires its server-owned Image strategy", 422
            )
        normalized["image_renderer"] = model_profiles.NATIVE_IMAGE_ADAPTER
        normalized["native_image"] = {
            "adapter": model_profiles.NATIVE_IMAGE_ADAPTER,
            "route": native_route,
        }
        return normalized

    normalized.pop("native_image", None)

    provider_channel = normalized.get("provider_channel")
    if provider_channel == "openai_images_api":
        raise GenerationRequestError("OpenAI native Images API is not supported for GPT Image 2 product routes", 422)

    image_profile = _image_generator_profile_from_config(config)
    if _is_openai_native_images_profile(image_profile):
        raise GenerationRequestError("OpenAI native Images API is not supported for GPT Image 2 product routes", 422)

    renderer = normalized.get("image_renderer")
    config_renderer = "gpt_image_2" if _is_gpt_image_2_profile(image_profile) else None
    if config_renderer:
        renderer = config_renderer
        normalized["image_renderer"] = renderer
    if renderer is None:
        renderer = "banana"
        normalized["image_renderer"] = renderer
    if renderer not in IMAGE_RENDERERS:
        raise GenerationRequestError("Unknown image_renderer", 422)
    if renderer != "gpt_image_2":
        return normalized

    if strategy not in {"image_5_0", "image_direct"}:
        raise GenerationRequestError("GPT Image 2 renderer requires Image 5.0", 422)
    if provider_channel and provider_channel != GPT_IMAGE_PROVIDER_CHANNEL:
        raise GenerationRequestError("GPT Image 2 renderer requires ZenMux Images API", 422)
    request_mode = normalized.get("request_mode")
    if request_mode and request_mode != GPT_IMAGE_REQUEST_MODE:
        raise GenerationRequestError("GPT Image 2 renderer requires blueprint_first request mode", 422)
    normalized["provider_channel"] = GPT_IMAGE_PROVIDER_CHANNEL
    normalized["request_mode"] = GPT_IMAGE_REQUEST_MODE
    return normalized


def _get_or_create_empty_requirement() -> int:
    for row in dbmod.list_requirements():
        if row["title"] == EMPTY_REQUIREMENT_TITLE and row["content"] == "":
            if row.get("lifecycle_status") != "archived":
                dbmod.update_requirement(int(row["id"]), lifecycle_status="archived")
            return int(row["id"])
    requirement_id = dbmod.create_requirement(EMPTY_REQUIREMENT_TITLE, "")
    dbmod.update_requirement(requirement_id, lifecycle_status="archived")
    return requirement_id


def _config_type(config: dict) -> str:
    raw_type = config.get("type")
    if raw_type not in (None, ""):
        config_type = str(raw_type).strip().lower()
        if config_type == "retired_html_test":
            return config_type
    try:
        return dbmod.normalize_config_type(raw_type)
    except ValueError:
        return "html"


def _enforce_config_type(config: dict, engine: str) -> None:
    config_type = _config_type(config)
    if config_type != engine:
        raise GenerationRequestError(
            f"{engine.title()} generation requires a {engine} config; "
            f"config '{config.get('name')}' is {config_type}.",
            422,
        )


def _normalize_manual_dimensions(engine: str, requirement_ids: list, color_ids: list) -> tuple[list[int], list[int]]:
    if not isinstance(requirement_ids, list) or not isinstance(color_ids, list):
        raise GenerationRequestError("requirement_ids and color_ids must be arrays", 422)
    if engine == "html":
        if not requirement_ids:
            raise GenerationRequestError("Select at least one requirement.", 422)
        return requirement_ids, color_ids or [get_or_create_empty_color()]
    normalized_requirement_ids = requirement_ids or [_get_or_create_empty_requirement()]
    normalized_color_ids = color_ids or [get_or_create_empty_color()]
    return normalized_requirement_ids, normalized_color_ids


def _parse_json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _redact_secret_fields(value: object) -> object:
    if isinstance(value, dict):
        safe = {}
        for key, child in value.items():
            key_text = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if any(part in key_text for part in SECRET_KEY_PARTS):
                continue
            safe[key] = _redact_secret_fields(child)
        return safe
    if isinstance(value, list):
        return [_redact_secret_fields(child) for child in value]
    if isinstance(value, str):
        value_text = value.lower()
        if any(marker in value_text for marker in SECRET_VALUE_MARKERS):
            return "[REDACTED]"
    return value


def _safe_agent_config_snapshot(value: object) -> dict:
    config = _redact_secret_fields(_parse_json_dict(value))
    return config if isinstance(config, dict) else {}


def _safe_config_snapshot(config: dict) -> dict:
    return {
        "id": config["id"],
        "name": config["name"],
        "type": _config_type(config),
        "max_concurrent_runs": config.get("max_concurrent_runs"),
        "designer": _safe_agent_config_snapshot(config.get("designer")),
        "html_agent": _safe_agent_config_snapshot(config.get("html_agent")),
        "route_model_bindings": _redact_secret_fields(_parse_json_dict(config.get("route_model_bindings"))),
    }


def _image_direct_model_profile(config: dict) -> dict:
    bindings = _parse_json_dict(config.get("route_model_bindings"))
    binding = bindings.get("image_generator")
    profile_id = binding.get("profile_id") if isinstance(binding, dict) else binding
    if not profile_id:
        raise GenerationRequestError("ImageDirect requires a single bound image generator model.", 422)
    try:
        profile = model_profiles.get_profile(int(profile_id))
    except (TypeError, ValueError) as exc:
        raise GenerationRequestError("ImageDirect requires a single bound image generator model.", 422) from exc
    if not profile or profile.get("role") != "image_generator" or profile.get("status") != "active":
        raise GenerationRequestError("ImageDirect requires a single bound image generator model.", 422)
    return profile


def _image_direct_route_metadata(config: dict, route_metadata: dict) -> dict:
    profile = _image_direct_model_profile(config)
    enriched = dict(route_metadata)
    model_name = enriched.get("image_direct_model_name")
    if not isinstance(model_name, str) or not model_name.strip():
        model_name = config.get("name") or profile["name"]
    enriched.setdefault("route_label", "ImageDirect")
    enriched["image_direct_model_name"] = str(model_name).strip()
    enriched["image_direct_model_profile_id"] = profile["id"]
    enriched["image_direct_model_profile_name"] = profile["name"]
    enriched["image_direct_model"] = profile["model"]
    return enriched


def _image_direct_dimensions(data: dict) -> tuple[list[int], list[int]]:
    requirement_ids = data.get("requirement_ids")
    color_ids = data.get("color_ids")
    if requirement_ids is not None and not isinstance(requirement_ids, list):
        raise GenerationRequestError("requirement_ids must be an array", 422)
    if color_ids is not None and not isinstance(color_ids, list):
        raise GenerationRequestError("color_ids must be an array", 422)
    _reject_nonempty_generic_dimensions(data, "image_direct")
    return _normalize_manual_dimensions("image", [], [])


def _reject_nonempty_generic_dimensions(data: dict, strategy: str) -> None:
    if strategy not in NO_GENERIC_DIMENSION_IMAGE_STRATEGIES:
        return
    has_requirement = (
        data.get("requirement_id") not in (None, "")
        or bool(data.get("requirement_ids"))
    )
    has_color = (
        data.get("color_id") not in (None, "")
        or bool(data.get("color_ids"))
    )
    if not has_requirement and not has_color:
        return
    route_label = {
        "image_1_0": "Image 1.0",
        "image_3_0": "Image 3.0",
        "image_3_2": "Image 3.2",
        "image_direct": "ImageDirect",
    }[strategy]
    raise GenerationRequestError(
        f"{route_label} does not accept requirement or color selections.",
        422,
    )


def _prompt_snapshot(prompt_id: int | None) -> dict | None:
    if prompt_id is None:
        return None
    prompt = dbmod.get_prompt(prompt_id)
    if not prompt:
        return None
    return {
        "id": prompt["id"],
        "agent_type": prompt["agent_type"],
        "version": prompt["version"],
        "name": prompt["name"],
        "content": prompt["content"],
        "description": prompt.get("description"),
    }


def _extract_single_dimension(data: dict, singular_key: str, array_key: str) -> int | None:
    if array_key in data:
        values = data[array_key]
        if not isinstance(values, list):
            raise GenerationRequestError(f"{array_key} must be an array", 422)
        if len(values) > 1:
            label = singular_key.replace("_id", "").replace("_", " ")
            raise GenerationRequestError(f"Evaluation generation plans accept only one {label} per Variant.", 422)
        return values[0] if values else None
    return data.get(singular_key)


def build_generation_plan(data: dict) -> dict:
    """Validate one normalized generation plan without creating runs."""
    if data.get("mode") == "auto":
        return _build_auto_generation_plan(data)
    _require_fields(data, "deck_id", "config_id")

    engine, strategy, route_metadata = _resolve_route(data)
    deck_id = data["deck_id"]
    config_id = data["config_id"]
    if strategy == "image_direct":
        requirement_ids, color_ids = _image_direct_dimensions(data)
    else:
        requirement_id = _extract_single_dimension(data, "requirement_id", "requirement_ids")
        color_id = _extract_single_dimension(data, "color_id", "color_ids")
        _reject_nonempty_generic_dimensions(data, strategy)
        requirement_ids, color_ids = _normalize_manual_dimensions(
            engine,
            [requirement_id] if requirement_id is not None else [],
            [color_id] if color_id is not None else [],
        )
    requirement_id = requirement_ids[0]
    color_id = color_ids[0]

    deck = dbmod.get_deck(deck_id)
    if not deck:
        raise GenerationRequestError("Deck not found", 404)
    config = dbmod.get_config(config_id)
    if not config:
        raise GenerationRequestError("Config not found", 404)
    _enforce_config_type(config, engine)
    strategy = _effective_html_strategy(engine, strategy, config)
    route_metadata = _html_route_metadata(strategy, route_metadata)
    route_metadata = normalize_image_route_metadata(engine, strategy, route_metadata, config)
    if strategy == "image_direct":
        route_metadata = _image_direct_route_metadata(config, route_metadata)
    requirement = dbmod.get_requirement(requirement_id)
    if not requirement:
        raise GenerationRequestError(f"Requirement {requirement_id} not found", 404)
    color = dbmod.get_color(color_id)
    if not color:
        raise GenerationRequestError(f"Color {color_id} not found", 404)
    slides = dbmod.list_slides(deck_id)
    if not slides:
        raise GenerationRequestError("Deck has no slides. Split the deck first.", 422)
    route_metadata = _image_skill_cover_metadata(engine, route_metadata, slides)

    designer_prompt_id = _resolve_prompt_id("designer", data.get("designer_prompt_id"))
    html_prompt_id = _resolve_prompt_id("html_agent", data.get("html_prompt_id"))
    prompts = {
        "designer": _prompt_snapshot(designer_prompt_id),
        "html_agent": _prompt_snapshot(html_prompt_id),
    }
    snapshot = {
        "deck": {"id": deck["id"], "title": deck["title"]},
        "config": _safe_config_snapshot(config),
        "requirement": {
            "id": requirement["id"],
            "title": requirement["title"],
            "content": requirement["content"],
        },
        "color": {
            "id": color["id"],
            "title": color["title"],
            "content": color["content"],
        },
        "engine": engine,
        "strategy": strategy,
        "route_metadata": route_metadata,
        "prompts": prompts,
    }
    return {
        "deck_id": deck_id,
        "config_id": config_id,
        "requirement_id": requirement_id,
        "color_id": color_id,
        "engine": engine,
        "strategy": strategy,
        "route_metadata": route_metadata,
        "designer_prompt_id": designer_prompt_id,
        "html_prompt_id": html_prompt_id,
        "slides_per_run": len(slides),
        "snapshot": snapshot,
    }


def _single_auto_color_id(data: dict) -> int:
    color_id = data.get("color_id")
    raw_color_ids = data.get("auto_color_ids")
    if raw_color_ids is not None:
        if not isinstance(raw_color_ids, list):
            raise GenerationRequestError("auto_color_ids must be an array", 422)
        if len(raw_color_ids) > 1:
            raise GenerationRequestError("Evaluation Auto plans accept only one color per Variant.", 422)
        color_id = raw_color_ids[0] if raw_color_ids else None
    if data.get("auto_color_id") is not None:
        color_id = data.get("auto_color_id")
    return int(color_id) if color_id is not None else get_or_create_empty_color()


def _evaluation_auto_candidate_count(value: object) -> int:
    try:
        count = int(value if value is not None else 1)
    except (TypeError, ValueError) as exc:
        raise GenerationRequestError("Evaluation Auto plans require auto_candidate_count=1.", 422) from exc
    if count != 1:
        raise GenerationRequestError(
            "Evaluation Auto uses repeat_count for Attempts; auto_candidate_count must be 1.",
            422,
        )
    return count


def _build_auto_generation_plan(data: dict) -> dict:
    _require_fields(data, "deck_id", "config_id")
    engine, strategy, route_metadata = _resolve_route(data)
    if engine == "image" and strategy != "image_5_0":
        raise GenerationRequestError("Image Auto requires Image 5.0", 422)
    if engine == "image" and any(data.get(field) not in (None, "", []) for field in ("requirement_id", "requirement_ids")):
        raise GenerationRequestError("Image Auto must not include manual requirement fields", 422)
    deck_id = data["deck_id"]
    config_id = data["config_id"]
    auto_candidate_count = _evaluation_auto_candidate_count(data.get("auto_candidate_count", 1))
    requirement_id = get_or_create_auto_requirement()
    color_id = _single_auto_color_id(data)

    deck = dbmod.get_deck(deck_id)
    if not deck:
        raise GenerationRequestError("Deck not found", 404)
    config = dbmod.get_config(config_id)
    if not config:
        raise GenerationRequestError("Config not found", 404)
    _enforce_config_type(config, engine)
    strategy = _effective_html_strategy(engine, strategy, config)
    route_metadata = _html_route_metadata(strategy, route_metadata)
    route_metadata = normalize_image_route_metadata(engine, strategy, route_metadata, config)
    requirement = dbmod.get_requirement(requirement_id)
    color = dbmod.get_color(color_id)
    if not color:
        raise GenerationRequestError(f"Color {color_id} not found", 404)
    slides = dbmod.list_slides(deck_id)
    if not slides:
        raise GenerationRequestError("Deck has no slides. Split the deck first.", 422)
    route_metadata = _image_skill_cover_metadata(engine, route_metadata, slides)

    designer_prompt_id = _resolve_prompt_id("designer", data.get("designer_prompt_id"))
    html_prompt_id = _resolve_prompt_id("html_agent", data.get("html_prompt_id"))
    prompts = {
        "designer": _prompt_snapshot(designer_prompt_id),
        "html_agent": _prompt_snapshot(html_prompt_id),
    }
    snapshot = {
        "deck": {"id": deck["id"], "title": deck["title"]},
        "config": _safe_config_snapshot(config),
        "requirement": {
            "id": requirement["id"],
            "title": requirement["title"],
            "content": requirement["content"],
        } if requirement else {"id": requirement_id},
        "color": {
            "id": color["id"],
            "title": color["title"],
            "content": color["content"],
        },
        "engine": engine,
        "strategy": strategy,
        "mode": "auto",
        "auto_candidate_count": auto_candidate_count,
        "route_metadata": route_metadata,
        "prompts": prompts,
    }
    return {
        "deck_id": deck_id,
        "config_id": config_id,
        "requirement_id": requirement_id,
        "color_id": color_id,
        "engine": engine,
        "strategy": strategy,
        "mode": "auto",
        "auto_candidate_count": auto_candidate_count,
        "route_metadata": route_metadata,
        "designer_prompt_id": designer_prompt_id,
        "html_prompt_id": html_prompt_id,
        "slides_per_run": len(slides),
        "snapshot": snapshot,
    }


def create_generation_batch(
    data: dict,
    db_path: str,
    launch_batch_runs: BatchLauncher = default_launch_batch_runs,
    batch_run_limit: int = DEFAULT_BATCH_RUN_LIMIT,
    launch_immediately: bool = True,
) -> dict:
    if data.get("mode") == "auto":
        try:
            return create_auto_generation_batch(
                data,
                db_path=db_path,
                launch_batch_runs=launch_batch_runs,
                resolve_prompt_id=_resolve_prompt_id,
                launch_immediately=launch_immediately,
            )
        except AutoGenerationRequestError as exc:
            raise GenerationRequestError(exc.message, exc.status_code) from exc

    _require_fields(data, "deck_id", "config_id")

    engine, strategy, route_metadata = _resolve_route(data)
    deck_id = data["deck_id"]
    if strategy == "image_direct":
        requirement_ids, color_ids = _image_direct_dimensions(data)
    else:
        if strategy in NO_GENERIC_DIMENSION_IMAGE_STRATEGIES:
            if any(
                field in data
                and data[field] is not None
                and not isinstance(data[field], list)
                for field in ("requirement_ids", "color_ids")
            ):
                raise GenerationRequestError(
                    "requirement_ids and color_ids must be arrays",
                    422,
                )
            _reject_nonempty_generic_dimensions(data, strategy)
        _require_fields(data, "requirement_ids", "color_ids")
        requirement_ids, color_ids = _normalize_manual_dimensions(engine, data["requirement_ids"], data["color_ids"])
    config_id = data["config_id"]

    deck = dbmod.get_deck(deck_id)
    if not deck:
        raise GenerationRequestError("Deck not found", 404)

    config = dbmod.get_config(config_id)
    if not config:
        raise GenerationRequestError("Config not found", 404)
    _enforce_config_type(config, engine)
    strategy = _effective_html_strategy(engine, strategy, config)
    route_metadata = _html_route_metadata(strategy, route_metadata)
    route_metadata = normalize_image_route_metadata(engine, strategy, route_metadata, config)
    if strategy == "image_direct":
        route_metadata = _image_direct_route_metadata(config, route_metadata)

    slides = dbmod.list_slides(deck_id)
    if not slides:
        raise GenerationRequestError("Deck has no slides. Split the deck first.", 422)
    route_metadata = _image_skill_cover_metadata(engine, route_metadata, slides)

    total_runs = len(requirement_ids) * len(color_ids)
    run_limit = AUTO_MAX_CANDIDATES if data.get("mode") == "auto" else batch_run_limit
    if total_runs > run_limit:
        raise GenerationRequestError(
            f"Generate batches are limited to {run_limit} runs by default. "
            f"Current selection would create {total_runs} runs.",
            422,
        )

    for requirement_id in requirement_ids:
        if not dbmod.get_requirement(requirement_id):
            raise GenerationRequestError(f"Requirement {requirement_id} not found", 404)
    for color_id in color_ids:
        if not dbmod.get_color(color_id):
            raise GenerationRequestError(f"Color {color_id} not found", 404)

    designer_prompt_id = _resolve_prompt_id("designer", data.get("designer_prompt_id"))
    html_prompt_id = _resolve_prompt_id("html_agent", data.get("html_prompt_id"))

    batch_id = dbmod.create_batch(
        deck_id=deck_id,
        config_id=config_id,
        requirement_ids=requirement_ids,
        color_ids=color_ids,
        designer_prompt_id=designer_prompt_id,
        html_prompt_id=html_prompt_id,
        total_runs=total_runs,
    )

    run_ids = []
    for requirement_id in requirement_ids:
        for color_id in color_ids:
            run_id = dbmod.create_run(
                deck_id,
                requirement_id,
                color_id,
                config_id,
                batch_id=batch_id,
                engine=engine,
                strategy=strategy,
                route_metadata=route_metadata,
            )
            update_fields = {}
            if designer_prompt_id:
                update_fields["designer_prompt_id"] = designer_prompt_id
            if html_prompt_id:
                update_fields["html_prompt_id"] = html_prompt_id
            if update_fields:
                dbmod.update_run(run_id, **update_fields)
            for index, slide in enumerate(slides):
                is_image_skill_cover = (
                    engine == "image"
                    and index == 0
                    and slide.get("split_mode") == "image_skill_cover"
                )
                slide_type = (
                    "cover"
                    if is_image_skill_cover or (strategy != "image_direct" and index == 0)
                    else "content"
                )
                dbmod.create_run_slide(run_id, slide["id"], slide["position"], slide_type=slide_type)
            run_ids.append(run_id)

    max_concurrent_runs = config.get("max_concurrent_runs", 2)
    if launch_immediately:
        launch_batch_runs(run_ids, db_path, max_concurrent_runs)

    return {
        "batch_id": batch_id,
        "run_ids": run_ids,
        "total_runs": len(run_ids),
        "slides_per_run": len(slides),
        "max_concurrent_runs": max_concurrent_runs,
    }
