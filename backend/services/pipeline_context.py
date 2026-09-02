"""Load DB state required to execute one generation pipeline run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import db as dbmod
from backend.services import model_profiles


IMAGE_SKILL_ROUTE_METADATA_KEY = "_image_skill"
IMAGE_SKILL_ROUTE_METADATA_VERSION = 1


@dataclass
class PipelineRunContext:
    run: dict
    deck: dict
    requirement: dict
    color: dict
    config_row: dict
    designer_config: dict
    html_agent_config: dict
    image_designer_config: dict
    image_generator_config: dict
    image_palette_extractor_config: dict
    timeout_seconds: int
    run_slides: list[dict]
    confirmed_full_content: str
    designer_prompt_content: str | None
    html_prompt_content: str | None


def _prompt_content(prompt_id: int | None) -> str | None:
    if not prompt_id:
        return None
    prompt = dbmod.get_prompt(prompt_id)
    return prompt["content"] if prompt else None


def build_confirmed_full_content(
    run_slides: list[dict],
    *,
    image_skill_snapshot: dict | None = None,
) -> str:
    if not run_slides:
        raise ValueError("confirmed_content_unavailable: run has no slide snapshots")

    run_ids = {slide.get("run_id") for slide in run_slides}
    if (
        len(run_ids) != 1
        or not isinstance(next(iter(run_ids)), int)
        or isinstance(next(iter(run_ids)), bool)
        or next(iter(run_ids)) <= 0
    ):
        raise ValueError("confirmed_content_unavailable: snapshots do not belong to one valid run")

    normalized: list[tuple[int, str, str]] = []
    positions: list[int] = []
    for slide in run_slides:
        position = slide.get("position")
        if not isinstance(position, int) or isinstance(position, bool) or position <= 0:
            raise ValueError("confirmed_content_unavailable: invalid slide position")
        title = slide.get("slide_title_snapshot")
        content = slide.get("slide_content_snapshot")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("confirmed_content_unavailable: missing slide title snapshot")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("confirmed_content_unavailable: missing slide content snapshot")
        positions.append(position)
        normalized.append((position, title.strip(), content.strip()))

    expected_positions = list(range(1, len(normalized) + 1))
    if sorted(positions) != expected_positions:
        raise ValueError("confirmed_content_unavailable: slide positions are incomplete or duplicated")

    confirmed_content = "\n\n".join(
        f"## 第 {position} 页：{title}\n\n{content}"
        for position, title, content in sorted(normalized, key=lambda item: item[0])
    )
    if image_skill_snapshot is not None:
        return image_skill_snapshot["source_body"]
    return confirmed_content


def _image_skill_snapshot(run: dict) -> dict | None:
    value = run.get("route_metadata")
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("image_skill_snapshot_invalid: route metadata is not JSON") from exc
    if not isinstance(value, dict) or IMAGE_SKILL_ROUTE_METADATA_KEY not in value:
        return None
    snapshot = value[IMAGE_SKILL_ROUTE_METADATA_KEY]
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != {"version", "cover_title", "source_body"}
        or snapshot.get("version") != IMAGE_SKILL_ROUTE_METADATA_VERSION
        or not isinstance(snapshot.get("cover_title"), str)
        or not isinstance(snapshot.get("source_body"), str)
    ):
        raise ValueError("image_skill_snapshot_invalid: immutable source metadata is malformed")
    return snapshot


def load_run_context(run_id: int, db_path: str | None = None) -> PipelineRunContext:
    if db_path:
        dbmod.DB_PATH = Path(db_path)

    run = dbmod.get_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    deck = dbmod.get_deck(run["deck_id"])
    image_skill_snapshot = _image_skill_snapshot(run)
    if image_skill_snapshot is not None:
        # The Image Skill source is captured at confirmation/batch creation. Do
        # not let a later Deck edit change the title or full-body prompt input.
        deck = dict(deck) if deck else deck
        if deck is not None:
            deck["title"] = image_skill_snapshot["cover_title"]
            deck["content"] = image_skill_snapshot["source_body"]
    requirement = dbmod.get_requirement(run["requirement_id"])
    color = dbmod.get_color(run["color_id"])
    config_row = dbmod.get_config(run["config_id"])
    run_slides = dbmod.list_run_slides(run_id)
    if not all([deck, requirement, color, config_row]):
        raise ValueError(f"Missing linked data for run {run_id}")
    confirmed_full_content = build_confirmed_full_content(
        run_slides,
        image_skill_snapshot=image_skill_snapshot,
    )

    resolved_config = model_profiles.resolve_config(run["config_id"])
    designer_config = resolved_config["designer"]
    html_agent_config = resolved_config["html_agent"]
    bindings = resolved_config.get("route_model_bindings") or {}
    is_image_run = (run.get("engine") or "html") == "image"
    is_native_three_zero_run = (
        is_image_run
        and model_profiles.native_image_route_for_config(config_row)
        == model_profiles.NATIVE_IMAGE_3_0_ROUTE
    )

    def unbound_active_role_config(role: str) -> dict | None:
        for profile in model_profiles.list_profiles(role=role, status="active"):
            if not model_profiles.is_system_managed_native_profile(profile):
                return model_profiles.profile_to_agent_config(profile["id"])
        return None

    def bound_config(role: str, fallback: dict | None = None, *, require_active_role: bool = False) -> dict:
        binding = bindings.get(role)
        profile_id = binding.get("profile_id") if isinstance(binding, dict) else binding
        if not profile_id:
            if require_active_role:
                active_role_config = unbound_active_role_config(role)
                if active_role_config:
                    return active_role_config
                raise ValueError(f"Config {run['config_id']} has no active {role} model profile")
            if fallback is None:
                active_role_config = model_profiles.active_profile_config_for_role(role)
                if active_role_config:
                    return active_role_config
                raise ValueError(f"Config {run['config_id']} has no model profile for {role}")
            return fallback
        try:
            return model_profiles.profile_to_agent_config(int(profile_id))
        except (TypeError, ValueError):
            if require_active_role:
                active_role_config = model_profiles.active_profile_config_for_role(role)
                if active_role_config:
                    return active_role_config
                raise
            if fallback is None:
                raise
            return fallback

    def strict_native_three_zero_bound_config(
        binding_key: str,
        *,
        expected_role: str,
        expected_api_type: str,
        expected_endpoint: str | None = None,
        expected_model: str | None = None,
        expected_thinking: str | None = None,
        expected_api_key: str | None = None,
    ) -> dict:
        error = (
            f"Native Image 3.0 requires a valid bound {binding_key} profile "
            f"with role {expected_role} and api_type {expected_api_type}"
        )
        binding = bindings.get(binding_key)
        if not isinstance(binding, dict) or set(binding) != {"profile_id"}:
            raise ValueError(error)
        profile_id = binding.get("profile_id")
        if (
            not isinstance(profile_id, int)
            or isinstance(profile_id, bool)
            or profile_id <= 0
        ):
            raise ValueError(error)
        profile = model_profiles.get_profile(profile_id)
        if (
            not profile
            or profile.get("status") != "active"
            or profile.get("role") != expected_role
            or profile.get("api_type") != expected_api_type
            or (
                expected_endpoint is not None
                and profile.get("endpoint") != expected_endpoint
            )
            or (expected_model is not None and profile.get("model") != expected_model)
            or (
                expected_thinking is not None
                and profile.get("thinking") != expected_thinking
            )
            or (
                expected_api_key is not None
                and profile.get("api_key") != expected_api_key
            )
        ):
            raise ValueError(error)
        try:
            return model_profiles.profile_to_agent_config(profile_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(error) from exc

    if is_native_three_zero_run:
        expected_director_model = {
            model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME: "gpt-5.6-sol",
            model_profiles.NATIVE_IMAGE_3_0_LUNA_DIRECTOR_CONFIG_NAME: "gpt-5.6-luna",
            model_profiles.NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME: "gpt-5.6-terra",
        }.get(config_row.get("name"))
        if expected_director_model is None:
            raise ValueError(
                f"Native Image 3.0 requires a known Director config for run {run_id}"
            )
        image_designer_config = strict_native_three_zero_bound_config(
            "image_designer",
            expected_role="image_designer",
            expected_api_type=model_profiles.CODEX_EXEC_API_TYPE,
            expected_endpoint=model_profiles.CODEX_EXEC_ENDPOINT,
            expected_model=expected_director_model,
            expected_thinking="low",
            expected_api_key="",
        )
        image_generator_config = strict_native_three_zero_bound_config(
            "image_generator",
            expected_role="image_generator",
            expected_api_type=model_profiles.NATIVE_IMAGE_API_TYPE,
            expected_endpoint=model_profiles.CODEX_EXEC_ENDPOINT,
            expected_model=(
                "gpt-5.6-terra"
                if config_row.get("name")
                == model_profiles.NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME
                else "gpt-5.6-luna"
            ),
            expected_thinking="low",
            expected_api_key="",
        )
        image_palette_extractor_config = strict_native_three_zero_bound_config(
            "image_palette_extractor",
            expected_role="image_designer",
            expected_api_type=model_profiles.CODEX_EXEC_API_TYPE,
            expected_endpoint=model_profiles.CODEX_EXEC_ENDPOINT,
            expected_model=expected_director_model,
            expected_thinking="low",
            expected_api_key="",
        )
        if (
            image_palette_extractor_config["profile_id"]
            != image_designer_config["profile_id"]
        ):
            raise ValueError(
                "Native Image 3.0 requires image_palette_extractor to reuse the "
                "image_designer profile"
            )
    else:
        image_designer_config = bound_config("image_designer", designer_config)
        if is_image_run:
            image_generator_config = bound_config("image_generator", require_active_role=True)
        else:
            image_generator_config = html_agent_config
        palette_binding = bindings.get("image_palette_extractor")
        image_palette_extractor_config = bound_config(
            "image_palette_extractor", image_generator_config
        )
        if (
            is_image_run
            and palette_binding
            and image_palette_extractor_config.get("api_type") != "gemini"
        ):
            raise ValueError(
                f"Config {run['config_id']} requires a Gemini-compatible image_palette_extractor model profile"
            )
        if is_image_run and image_generator_config.get("api_type") == "zenmux_images":
            if not palette_binding:
                raise ValueError(
                    f"Config {run['config_id']} uses GPT Image 2 image generation and requires an image_palette_extractor model profile"
                )
    timeout_seconds = int(config_row.get("timeout_minutes") or 30) * 60

    return PipelineRunContext(
        run=run,
        deck=deck,
        requirement=requirement,
        color=color,
        config_row=config_row,
        designer_config=designer_config,
        html_agent_config=html_agent_config,
        image_designer_config=image_designer_config,
        image_generator_config=image_generator_config,
        image_palette_extractor_config=image_palette_extractor_config,
        timeout_seconds=timeout_seconds,
        run_slides=run_slides,
        confirmed_full_content=confirmed_full_content,
        designer_prompt_content=_prompt_content(run.get("designer_prompt_id")),
        html_prompt_content=_prompt_content(run.get("html_prompt_id")),
    )
