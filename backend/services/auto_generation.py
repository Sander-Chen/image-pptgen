"""Auto generation mode helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import db as dbmod

AUTO_MAX_CANDIDATES = 10
AUTO_SKILL_PATH = Path(__file__).resolve().parents[2] / "example" / "auto-skill-5.3.20.md"
AUTO_REQUIREMENT_TITLE = "AutoSkill System Requirement"
EMPTY_COLOR_TITLE = "System Empty Color"
VALID_ENGINES = {"html", "image"}
VALID_STRATEGIES = {"html_default", "codex_html", "image_1_0", "image_3_0", "image_3_2", "image_5_0"}

BatchLauncher = Callable[[list[int], str, int], object]
PromptResolver = Callable[[str, int | None], int | None]


@dataclass
class AutoGenerationRequestError(Exception):
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def _candidate_count(value: object) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise AutoGenerationRequestError("auto_candidate_count must be between 1 and 10", 422) from exc
    if count < 1 or count > AUTO_MAX_CANDIDATES:
        raise AutoGenerationRequestError("auto_candidate_count must be between 1 and 10", 422)
    return count


def _find_row_by_title_and_content(rows: list[dict], title: str, content: str) -> dict | None:
    for row in rows:
        if row["title"] == title and row["content"] == content:
            return row
    return None


def get_or_create_auto_requirement() -> int:
    content = AUTO_SKILL_PATH.read_text(encoding="utf-8")
    existing = _find_row_by_title_and_content(dbmod.list_requirements(), AUTO_REQUIREMENT_TITLE, content)
    if existing:
        return int(existing["id"])
    return dbmod.create_requirement(AUTO_REQUIREMENT_TITLE, content)


def get_or_create_empty_color() -> int:
    existing = _find_row_by_title_and_content(dbmod.list_colors(), EMPTY_COLOR_TITLE, "")
    if existing:
        if existing.get("lifecycle_status") != "archived":
            dbmod.update_color(int(existing["id"]), lifecycle_status="archived")
        return int(existing["id"])
    color_id = dbmod.create_color(EMPTY_COLOR_TITLE, "")
    dbmod.update_color(color_id, lifecycle_status="archived")
    return color_id


def _mark_batch_auto(batch_id: int) -> None:
    conn = dbmod.get_db()
    conn.execute("UPDATE batches SET generation_mode = 'auto' WHERE id = ?", (batch_id,))
    conn.commit()
    conn.close()


def _resolve_route(data: dict) -> tuple[str, str, dict]:
    engine = data.get("engine") or "html"
    strategy = data.get("strategy") or ("html_default" if engine == "html" else "image_5_0")
    route_metadata = data.get("route_metadata") or {}
    if engine not in VALID_ENGINES or strategy not in VALID_STRATEGIES:
        raise AutoGenerationRequestError("Invalid generation route", 400)
    if engine == "html" and strategy not in {"html_default", "codex_html"}:
        raise AutoGenerationRequestError("HTML engine only supports HTML strategies", 400)
    if engine == "image" and strategy != "image_5_0":
        raise AutoGenerationRequestError("Image Auto requires Image 5.0", 422)
    if not isinstance(route_metadata, dict):
        raise AutoGenerationRequestError("route_metadata must be an object", 400)
    return engine, strategy, route_metadata


def _reject_manual_image_auto_dimensions(data: dict) -> None:
    if data.get("engine") != "image":
        return
    for field in ("requirement_id", "requirement_ids"):
        if field in data and data.get(field) not in (None, "", []):
            raise AutoGenerationRequestError(
                "Image Auto must not include manual requirement fields",
                422,
            )


def _resolve_auto_color_ids(data: dict) -> list[int]:
    raw_color_ids = data.get("auto_color_ids")
    if raw_color_ids is None:
        raw_color_id = data.get("auto_color_id")
        raw_color_ids = [] if raw_color_id is None else [raw_color_id]
    if not isinstance(raw_color_ids, list):
        raise AutoGenerationRequestError("auto_color_ids must be an array", 422)
    if not raw_color_ids:
        return [get_or_create_empty_color()]

    color_ids: list[int] = []
    for raw_color_id in raw_color_ids:
        try:
            color_id = int(raw_color_id)
        except (TypeError, ValueError) as exc:
            raise AutoGenerationRequestError("auto_color_ids must contain valid color ids", 422) from exc
        if not dbmod.get_color(color_id):
            raise AutoGenerationRequestError(f"Color {color_id} not found", 404)
        if color_id not in color_ids:
            color_ids.append(color_id)
    return color_ids


def create_auto_generation_batch(
    data: dict,
    db_path: str,
    launch_batch_runs: BatchLauncher,
    resolve_prompt_id: PromptResolver,
    launch_immediately: bool = True,
) -> dict:
    deck_id = data.get("deck_id")
    config_id = data.get("config_id")
    if deck_id is None or config_id is None:
        raise AutoGenerationRequestError("Missing required fields: deck_id, config_id", 400)

    candidate_count = _candidate_count(data.get("auto_candidate_count", 1))
    engine, strategy, route_metadata = _resolve_route(data)
    _reject_manual_image_auto_dimensions(data)

    deck = dbmod.get_deck(deck_id)
    if not deck:
        raise AutoGenerationRequestError("Deck not found", 404)

    config = dbmod.get_config(config_id)
    if not config:
        raise AutoGenerationRequestError("Config not found", 404)
    expected_type = "html" if engine == "html" else "image"
    if dbmod.normalize_config_type(config.get("type")) != expected_type:
        raise AutoGenerationRequestError(
            f"{expected_type.upper()} Auto generation requires a {expected_type} config",
            422,
        )
    if engine == "html":
        from backend.services.generation import GenerationRequestError, _effective_html_strategy, _html_route_metadata

        try:
            strategy = _effective_html_strategy(engine, strategy, config)
        except GenerationRequestError as exc:
            raise AutoGenerationRequestError(exc.message, exc.status_code) from exc
        route_metadata = _html_route_metadata(strategy, route_metadata)
    if engine == "image":
        from backend.services.generation import normalize_image_route_metadata

        route_metadata = normalize_image_route_metadata(engine, strategy, route_metadata, config)

    slides = dbmod.list_slides(deck_id)
    if not slides:
        raise AutoGenerationRequestError("Deck has no slides. Split the deck first.", 422)

    requirement_id = get_or_create_auto_requirement()
    color_ids = _resolve_auto_color_ids(data)
    total_runs = candidate_count * len(color_ids)
    if total_runs > AUTO_MAX_CANDIDATES:
        raise AutoGenerationRequestError("auto_candidate_count across selected colors must be between 1 and 10", 422)

    designer_prompt_id = resolve_prompt_id("designer", data.get("designer_prompt_id"))
    html_prompt_id = resolve_prompt_id("html_agent", data.get("html_prompt_id"))

    batch_id = dbmod.create_batch(
        deck_id=deck_id,
        config_id=config_id,
        requirement_ids=[requirement_id],
        color_ids=color_ids,
        designer_prompt_id=designer_prompt_id,
        html_prompt_id=html_prompt_id,
        total_runs=total_runs,
    )
    _mark_batch_auto(batch_id)

    run_ids = []
    for color_id in color_ids:
        for candidate_index in range(1, candidate_count + 1):
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
            update_fields = {"auto_candidate_index": candidate_index}
            if designer_prompt_id:
                update_fields["designer_prompt_id"] = designer_prompt_id
            if html_prompt_id:
                update_fields["html_prompt_id"] = html_prompt_id
            dbmod.update_run(run_id, **update_fields)
            for index, slide in enumerate(slides):
                dbmod.create_run_slide(
                    run_id,
                    slide["id"],
                    slide["position"],
                    slide_type="cover" if index == 0 else "content",
                )
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
