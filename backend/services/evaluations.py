"""Evaluation workflow service for multi-run comparison."""

from __future__ import annotations

import json
import base64
import html
import hashlib
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import db as dbmod
import pipeline
from backend.services.generation import (
    BatchLauncher,
    GenerationRequestError,
    NO_GENERIC_DIMENSION_IMAGE_STRATEGIES,
    _parse_json_dict,
    _prompt_snapshot,
    _redact_secret_fields,
    _safe_config_snapshot,
    build_generation_plan,
    create_generation_batch,
)

MIN_VARIANTS = 2
MAX_VARIANTS = 4
MAX_REPEAT_COUNT = 5
PRESERVED_EVALUATION_STATUSES = {"reviewed", "archived"}
RUN_ACTIVE_STATUSES = {"pending", "queued", "running"}
RUN_FAILED_STATUSES = {"failed", "timed_out", "missing"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
EXPORT_VIEWPORT_WIDTH = 3200
EXPORT_VIEWPORT_HEIGHT = 1800


@dataclass
class EvaluationRequestError(Exception):
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


@dataclass
class EvaluationExportArchive:
    data: BytesIO
    filename: str
    mimetype: str = "application/zip"


def list_evaluations() -> list[dict]:
    for item in dbmod.list_evaluations():
        _reconcile_evaluation_status(item["id"])
    return dbmod.list_evaluations()


def get_evaluation(evaluation_id: int) -> dict | None:
    _reconcile_evaluation_status(evaluation_id)
    return dbmod.get_evaluation_detail(evaluation_id)


def list_history_run_candidates() -> list[dict]:
    """Return slim run records for the Evaluation history picker."""
    db = dbmod.get_db()
    try:
        rows = db.execute(
            """SELECT r.id, r.deck_id, r.requirement_id, r.color_id, r.config_id,
                      r.status, r.started_at, r.completed_at, r.created_at,
                      r.batch_id, r.auto_candidate_index, r.engine, r.strategy, r.route_metadata,
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
        runs = dbmod.rows_to_list(rows)
        run_ids = [int(run["id"]) for run in runs]
        progress_by_run = _bulk_run_progress(db, run_ids)
        snapshots_by_run = _bulk_run_deck_snapshots(db, run_ids)
    finally:
        db.close()

    for run in runs:
        run_id = int(run["id"])
        run["progress"] = progress_by_run.get(run_id, _empty_progress())
        snapshot = snapshots_by_run.get(run_id)
        if snapshot:
            run["deck_snapshot_fingerprint"] = snapshot["fingerprint"]
            run["deck_snapshot_label"] = snapshot["label"]
            run["deck_snapshot_slide_count"] = snapshot["slide_count"]
        run["route_metadata"] = _redact_secret_fields(_parse_json_dict(run.get("route_metadata")))
    return runs


def _empty_progress() -> dict:
    return {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "pending": 0,
        "displayable": 0,
        "missing_displayable": 0,
    }


def _bulk_run_progress(db, run_ids: list[int]) -> dict[int, dict]:
    progress = {run_id: _empty_progress() for run_id in run_ids}
    if not run_ids:
        return progress
    placeholders = ",".join("?" for _ in run_ids)
    rows = db.execute(
        f"""SELECT run_id, status, COUNT(*) AS cnt
            FROM run_slides
            WHERE run_id IN ({placeholders})
            GROUP BY run_id, status""",
        run_ids,
    ).fetchall()
    for row in rows:
        item = progress[int(row["run_id"])]
        count = int(row["cnt"])
        status = str(row["status"] or "")
        item["total"] += count
        if status == "completed":
            item["completed"] += count
        elif status in {"failed", "timed_out"}:
            item["failed"] += count
        else:
            item["pending"] += count
    displayable_rows = db.execute(
        f"""SELECT rs.run_id, COUNT(*) AS cnt
            FROM run_slides rs
            LEFT JOIN active_artifact_versions aav ON aav.target_run_slide_id = rs.id
            LEFT JOIN artifact_versions av ON av.id = aav.version_id
            WHERE rs.run_id IN ({placeholders})
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
              )
            GROUP BY rs.run_id""",
        run_ids,
    ).fetchall()
    for row in displayable_rows:
        progress[int(row["run_id"])]["displayable"] = int(row["cnt"])
    for item in progress.values():
        item["missing_displayable"] = max(item["total"] - item["displayable"], 0)
    return progress


def _bulk_run_deck_snapshots(db, run_ids: list[int]) -> dict[int, dict]:
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    rows = db.execute(
        f"""SELECT rs.run_id,
                   rs.position,
                   COALESCE(rs.slide_title_snapshot, s.title, '') AS title,
                   COALESCE(rs.slide_content_snapshot, s.content, '') AS content,
                   d.title AS deck_title
            FROM run_slides rs
            LEFT JOIN slides s ON s.id = rs.slide_id
            LEFT JOIN runs r ON r.id = rs.run_id
            LEFT JOIN decks d ON d.id = r.deck_id
            WHERE rs.run_id IN ({placeholders})
            ORDER BY rs.run_id, rs.position, rs.id""",
        run_ids,
    ).fetchall()
    slides_by_run: dict[int, list[dict[str, Any]]] = {}
    deck_title_by_run: dict[int, str] = {}
    for row in rows:
        run_id = int(row["run_id"])
        deck_title_by_run.setdefault(run_id, row["deck_title"])
        slides_by_run.setdefault(run_id, []).append(
            {
                "position": int(row["position"] or 0),
                "title": row["title"] or "",
                "content": row["content"] or "",
            }
        )
    snapshots: dict[int, dict] = {}
    for run_id, slides in slides_by_run.items():
        fingerprint = _deck_snapshot_fingerprint(slides)
        snapshots[run_id] = {
            "fingerprint": fingerprint,
            "slide_count": len(slides),
            "label": _deck_snapshot_label(deck_title_by_run.get(run_id), len(slides), fingerprint),
        }
    return snapshots


def update_variant(evaluation_id: int, variant_id: int, data: dict) -> dict:
    _require_evaluation(evaluation_id)
    _require_variant(evaluation_id, variant_id)
    fields: dict[str, object] = {}
    if "label" in data:
        fields["label"] = _required_text(data, "label", "Variant label is required")
    if "goal" in data:
        fields["goal"] = _required_text(data, "goal", "Variant goal is required")
    if "comparison_variable" in data:
        fields["comparison_variable"] = data.get("comparison_variable")
    detail = dbmod.update_evaluation_variant(variant_id, **fields)
    return detail or _require_evaluation(evaluation_id)


def update_representative(evaluation_id: int, variant_id: int, attempt_id: int | None) -> dict:
    _require_evaluation(evaluation_id)
    _require_variant(evaluation_id, variant_id)
    try:
        detail = dbmod.set_evaluation_variant_representative(variant_id, attempt_id)
    except ValueError as exc:
        raise EvaluationRequestError(str(exc), 422) from exc
    return detail or _require_evaluation(evaluation_id)


def add_note(evaluation_id: int, data: dict) -> dict:
    _require_evaluation(evaluation_id)
    note = _required_text(data, "note", "Note is required")
    variant_id = data.get("variant_id")
    attempt_id = data.get("attempt_id")
    if variant_id is not None:
        _require_variant(evaluation_id, int(variant_id))
    if attempt_id is not None:
        _require_attempt(evaluation_id, int(attempt_id))
    db = dbmod.get_db()
    cur = db.execute(
        """INSERT INTO evaluation_notes
           (evaluation_id, variant_id, attempt_id, slide_position, note)
           VALUES (?, ?, ?, ?, ?)""",
        (
            evaluation_id,
            variant_id,
            attempt_id,
            data.get("slide_position"),
            note,
        ),
    )
    db.commit()
    note_id = int(cur.lastrowid)
    db.close()
    return {"id": note_id, **(get_evaluation(evaluation_id) or {})}


def add_slide_tag(evaluation_id: int, data: dict) -> dict:
    _require_evaluation(evaluation_id)
    label = _required_text(data, "label", "Issue tag label is required")
    source = str(data.get("source") or "human").strip().lower()
    if source not in {"human", "machine"}:
        raise EvaluationRequestError("Issue tag source must be human or machine.", 422)
    attempt_id = data.get("attempt_id")
    if attempt_id is not None:
        _require_attempt(evaluation_id, int(attempt_id))
    slide_position = data.get("slide_position")
    if slide_position is None:
        raise EvaluationRequestError("slide_position is required.", 400)
    db = dbmod.get_db()
    db.execute(
        """INSERT OR IGNORE INTO evaluation_issue_tags (evaluation_id, label, source)
           VALUES (?, ?, ?)""",
        (evaluation_id, label, source),
    )
    tag = db.execute(
        """SELECT id FROM evaluation_issue_tags
           WHERE evaluation_id = ? AND label = ? AND source = ?""",
        (evaluation_id, label, source),
    ).fetchone()
    db.execute(
        """INSERT OR IGNORE INTO evaluation_slide_tags
           (evaluation_id, attempt_id, run_slide_id, slide_position, tag_id)
           VALUES (?, ?, ?, ?, ?)""",
        (
            evaluation_id,
            attempt_id,
            data.get("run_slide_id"),
            int(slide_position),
            int(tag["id"]),
        ),
    )
    db.commit()
    db.close()
    return get_evaluation(evaluation_id) or {}


def record_machine_qa(evaluation_id: int, data: dict) -> dict:
    _require_evaluation(evaluation_id)
    attempt_id = data.get("attempt_id")
    attempt = None
    if attempt_id is not None:
        attempt = _require_attempt(evaluation_id, int(attempt_id))
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in {"pass", "fail", "skipped"}:
        raise EvaluationRequestError("Machine QA verdict must be pass, fail, or skipped.", 422)
    issues = data.get("issues", [])
    if not isinstance(issues, list):
        raise EvaluationRequestError("Machine QA issues must be an array.", 422)
    for issue in issues:
        if not isinstance(issue, dict):
            raise EvaluationRequestError("Each Machine QA issue must be an object.", 422)
    slide_position = data.get("slide_position")
    if slide_position is None:
        raise EvaluationRequestError("slide_position is required.", 400)
    slide_position = int(slide_position)
    run_slide_id = data.get("run_slide_id")
    if attempt:
        matching_slide = next(
            (
                slide for slide in attempt.get("slides", [])
                if int(slide.get("position") or 0) == slide_position
            ),
            None,
        )
        if not matching_slide:
            raise EvaluationRequestError("Machine QA slide_position does not belong to this Attempt.", 422)
        if run_slide_id is not None and int(run_slide_id) != int(matching_slide["id"]):
            raise EvaluationRequestError("Machine QA run_slide_id does not belong to this Attempt.", 422)
    safe_raw_response = _redact_secret_fields(data.get("raw_response"))
    db = dbmod.get_db()
    if data.get("replace_existing"):
        db.execute(
            """DELETE FROM evaluation_machine_qa
               WHERE evaluation_id = ?
                 AND attempt_id IS ?
                 AND slide_position = ?""",
            (evaluation_id, attempt_id, slide_position),
        )
    cur = db.execute(
        """INSERT INTO evaluation_machine_qa
           (evaluation_id, attempt_id, run_slide_id, slide_position, verdict,
            issues_json, model_profile_id, prompt_id, raw_response)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            evaluation_id,
            attempt_id,
            run_slide_id,
            slide_position,
            verdict,
            json.dumps(_redact_secret_fields(issues), ensure_ascii=False),
            data.get("model_profile_id"),
            data.get("prompt_id"),
            safe_raw_response if isinstance(safe_raw_response, str) else json.dumps(safe_raw_response, ensure_ascii=False),
        ),
    )
    db.commit()
    qa_id = int(cur.lastrowid)
    db.close()
    return {"id": qa_id, **(get_evaluation(evaluation_id) or {})}


def build_export_archive(evaluation_id: int, data: dict) -> EvaluationExportArchive:
    detail = get_evaluation(evaluation_id)
    if not detail:
        raise EvaluationRequestError("Evaluation not found.", 404)
    scope = str(data.get("scope") or "current_slide")
    if scope not in {"current_slide", "all_slides"}:
        raise EvaluationRequestError("Export scope must be current_slide or all_slides.", 422)
    metadata_fields = data.get("metadata_fields") or ["column_label", "prompt", "model", "strategy", "page_number"]
    if not isinstance(metadata_fields, list):
        raise EvaluationRequestError("metadata_fields must be an array.", 422)
    slide_positions = _export_slide_positions(detail, scope, data.get("slide_position"))
    slides = [_export_slide_payload(detail, position, metadata_fields) for position in slide_positions]
    manifest = {
        "evaluation_id": evaluation_id,
        "title": detail["title"],
        "deck": {"id": detail["deck_id"], "title": detail["deck_title"]},
        "scope": scope,
        "metadata_fields": metadata_fields,
        "slide_positions": slide_positions,
        "generated_artifact": "comparison_zip_v1",
        "render_errors": [],
        "included": [],
    }
    safe_manifest = _redact_secret_fields(manifest)
    safe_slides = _redact_secret_fields(slides)
    buffer = BytesIO()
    with tempfile.TemporaryDirectory(prefix="evaluation-export-") as temp_dir:
        temp_root = Path(temp_dir)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for slide in safe_slides:
                page_number = slide["page_number"]
                archive.writestr(
                    f"slides/slide-{page_number}.json",
                    json.dumps(slide, ensure_ascii=False, indent=2),
                )
                report_html = _render_export_report_html(detail, slide, metadata_fields)
                report_path = temp_root / f"slide-{page_number}-comparison.html"
                report_path.write_text(report_html, encoding="utf-8")
                archive.write(report_path, f"reports/slide-{page_number}.html")
                safe_manifest["included"].append(f"reports/slide-{page_number}.html")
                _add_export_assets(archive, safe_manifest, slide)
                try:
                    screenshot_path = Path(
                        pipeline.screenshot_html_file(
                            str(report_path),
                            viewport_w=EXPORT_VIEWPORT_WIDTH,
                            viewport_h=EXPORT_VIEWPORT_HEIGHT,
                        )
                    )
                    if screenshot_path.exists():
                        archive.write(screenshot_path, f"slides/slide-{page_number}-comparison.png")
                        safe_manifest["included"].append(f"slides/slide-{page_number}-comparison.png")
                except Exception as exc:
                    safe_manifest["render_errors"].append(
                        {"slide_position": page_number, "error": str(exc)[:500]}
                    )
            archive.writestr("manifest.json", json.dumps(safe_manifest, ensure_ascii=False, indent=2))
    buffer.seek(0)
    db = dbmod.get_db()
    db.execute(
        """INSERT INTO evaluation_exports
           (evaluation_id, export_type, metadata_config, file_path, manifest_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            evaluation_id,
            scope,
            json.dumps({"metadata_fields": metadata_fields}, ensure_ascii=False),
            None,
            json.dumps(safe_manifest, ensure_ascii=False),
        ),
    )
    db.commit()
    db.close()
    return EvaluationExportArchive(
        data=buffer,
        filename=f"evaluation-{evaluation_id}-{scope}.zip",
    )


def create_history_evaluation(data: dict) -> dict:
    title = _required_text(data, "title", "Evaluation title is required")
    goal = _required_text(data, "goal", "Evaluation goal is required")
    variants = _variant_specs(data.get("variants"))
    resolved = [_resolve_history_variant(spec, index) for index, spec in enumerate(variants)]
    deck_ids = {item["run"]["deck_id"] for item in resolved}
    if len(deck_ids) != 1:
        raise EvaluationRequestError("History Evaluations must compare Runs from the same Deck.", 422)
    seen_run_ids: set[int] = set()
    for item in resolved:
        run_id = item["run"]["id"]
        if run_id in seen_run_ids:
            raise EvaluationRequestError("History Evaluation Runs must be unique.", 422)
        seen_run_ids.add(run_id)
        _require_completed_displayable_run(item["run"])
    fingerprints = {item["snapshot"]["deck"]["snapshot_fingerprint"] for item in resolved}
    if len(fingerprints) != 1:
        raise EvaluationRequestError("History Evaluations must compare Runs from the same Deck snapshot.", 422)

    evaluation_id = dbmod.create_evaluation(
        deck_id=next(iter(deck_ids)),
        title=title,
        goal=goal,
        status="reviewing",
    )
    for index, item in enumerate(resolved):
        spec = item["spec"]
        run = item["run"]
        label = _required_text(spec, "label", "Variant label is required")
        variant_id = dbmod.create_evaluation_variant(
            evaluation_id,
            label=label,
            goal=_required_text(spec, "goal", "Variant goal is required"),
            comparison_variable=spec.get("comparison_variable"),
            generation_plan_snapshot=item["snapshot"],
            sort_order=index + 1,
        )
        attempt_id = dbmod.create_evaluation_attempt(
            evaluation_id,
            variant_id,
            run_id=run["id"],
            batch_id=run.get("batch_id"),
            label=f"{_variant_letter(index)}1 · Run",
            attempt_index=1,
            snapshot=item["snapshot"],
            status=run.get("status"),
        )
        dbmod.set_evaluation_variant_representative(variant_id, attempt_id)
    return dbmod.get_evaluation_detail(evaluation_id)


def create_blank_evaluation(
    data: dict,
    *,
    db_path: str,
    launch_batch_runs: BatchLauncher,
) -> dict:
    title = _required_text(data, "title", "Evaluation title is required")
    goal = _required_text(data, "goal", "Evaluation goal is required")
    deck_id = data.get("deck_id")
    if deck_id is None:
        raise EvaluationRequestError("Deck is required", 400)
    if not dbmod.get_deck(deck_id):
        raise EvaluationRequestError("Deck not found", 404)
    repeat_count = _repeat_count(data.get("repeat_count", 1))
    variants = _variant_specs(data.get("variants"))

    plans = []
    for index, spec in enumerate(variants):
        if spec.get("deck_id") not in (None, deck_id):
            raise EvaluationRequestError("Blank Evaluation Variants must use the shared Deck.", 422)
        _validate_blank_generation_spec(spec)
        try:
            plan = build_generation_plan({**spec, "deck_id": deck_id})
        except GenerationRequestError as exc:
            raise EvaluationRequestError(exc.message, exc.status_code) from exc
        plans.append({"spec": spec, "plan": plan, "index": index})

    evaluation_id = dbmod.create_evaluation(deck_id=deck_id, title=title, goal=goal, status="running")
    run_ids: list[int] = []
    batch_ids: list[int] = []
    launch_specs: list[tuple[list[int], int]] = []
    total_attempts = 0
    for item in plans:
        spec = item["spec"]
        plan = item["plan"]
        index = item["index"]
        variant_id = dbmod.create_evaluation_variant(
            evaluation_id,
            label=_required_text(spec, "label", "Variant label is required"),
            goal=_required_text(spec, "goal", "Variant goal is required"),
            comparison_variable=spec.get("comparison_variable"),
            generation_plan_snapshot=plan["snapshot"],
            sort_order=index + 1,
        )
        representative_attempt_id: int | None = None
        for attempt_index in range(1, repeat_count + 1):
            payload = _generation_payload_from_plan(plan)
            try:
                generated = create_generation_batch(
                    payload,
                    db_path=db_path,
                    launch_batch_runs=launch_batch_runs,
                    batch_run_limit=1,
                    launch_immediately=False,
                )
            except GenerationRequestError as exc:
                dbmod.update_evaluation(evaluation_id, status="partial")
                raise EvaluationRequestError(exc.message, exc.status_code) from exc
            run_id = generated["run_ids"][0]
            run_ids.append(run_id)
            batch_ids.append(generated["batch_id"])
            launch_specs.append((generated["run_ids"], generated["max_concurrent_runs"]))
            attempt_id = dbmod.create_evaluation_attempt(
                evaluation_id,
                variant_id,
                run_id=run_id,
                batch_id=generated["batch_id"],
                label=f"{_variant_letter(index)}{attempt_index} · Run",
                attempt_index=attempt_index,
                snapshot=plan["snapshot"],
            )
            representative_attempt_id = representative_attempt_id or attempt_id
            total_attempts += 1
        dbmod.set_evaluation_variant_representative(variant_id, representative_attempt_id)

    try:
        for launch_run_ids, max_concurrent_runs in launch_specs:
            launch_batch_runs(launch_run_ids, db_path, max_concurrent_runs)
    except Exception as exc:
        dbmod.update_evaluation(evaluation_id, status="partial")
        raise EvaluationRequestError(str(exc), 503) from exc

    detail = dbmod.get_evaluation_detail(evaluation_id)
    detail["run_ids"] = run_ids
    detail["batch_ids"] = batch_ids
    detail["total_attempts"] = total_attempts
    return detail


def _variant_specs(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise EvaluationRequestError("Evaluation variants must be an array.", 400)
    if len(value) < MIN_VARIANTS or len(value) > MAX_VARIANTS:
        raise EvaluationRequestError(f"Evaluation requires {MIN_VARIANTS}-{MAX_VARIANTS} Variants.", 422)
    for variant in value:
        if not isinstance(variant, dict):
            raise EvaluationRequestError("Each Evaluation Variant must be an object.", 400)
        _required_text(variant, "label", "Variant label is required")
        _required_text(variant, "goal", "Variant goal is required")
    return value


def _required_text(data: dict, key: str, message: str) -> str:
    value = data.get(key)
    if value is None or not str(value).strip():
        raise EvaluationRequestError(message, 400)
    return str(value).strip()


def _repeat_count(value: object) -> int:
    if isinstance(value, bool) or isinstance(value, float):
        raise EvaluationRequestError(f"repeat_count must be 1-{MAX_REPEAT_COUNT}.", 422)
    try:
        repeat_count = int(value)
    except (TypeError, ValueError):
        raise EvaluationRequestError(f"repeat_count must be 1-{MAX_REPEAT_COUNT}.", 422) from None
    if repeat_count < 1 or repeat_count > MAX_REPEAT_COUNT:
        raise EvaluationRequestError(f"repeat_count must be 1-{MAX_REPEAT_COUNT}.", 422)
    return repeat_count


def _validate_blank_generation_spec(spec: dict) -> None:
    if spec.get("mode") != "auto":
        return
    engine = str(spec.get("engine") or "html")
    strategy = str(spec.get("strategy") or ("html_default" if engine == "html" else "image_5_0"))
    if engine == "image" and strategy != "image_5_0":
        raise EvaluationRequestError("Image Auto requires Image 5.0", 422)
    forbidden_fields = ("requirement_id", "requirement_ids")
    if engine == "html":
        forbidden_fields = (
            "requirement_id",
            "requirement_ids",
            "color_id",
            "color_ids",
            "auto_color_id",
            "auto_color_ids",
        )
    elif engine == "image":
        forbidden_fields = (
            "requirement_id",
            "requirement_ids",
            "color_id",
            "color_ids",
        )
    for field in forbidden_fields:
        if field in spec and spec.get(field) not in (None, "", []):
            label = "HTML Auto" if engine == "html" else "Image Auto"
            raise EvaluationRequestError(
                f"{label} Evaluation variants must not include {field}; inputs are generated at run time.",
                422,
            )


def _resolve_history_variant(spec: dict, index: int) -> dict:
    run_id = spec.get("run_id")
    if run_id is None:
        raise EvaluationRequestError("History Variant run_id is required.", 400)
    run = dbmod.get_run(run_id)
    if not run:
        raise EvaluationRequestError(f"Run {run_id} not found.", 404)
    return {
        "spec": spec,
        "run": run,
        "snapshot": _history_run_snapshot(run, index),
    }


def deck_snapshot_for_run(run_id: int) -> dict[str, Any] | None:
    run = dbmod.get_run(run_id)
    if not run:
        return None
    deck = dbmod.get_deck(run["deck_id"])
    slides = _run_deck_snapshot_slides(run_id)
    fingerprint = _deck_snapshot_fingerprint(slides)
    return {
        "deck_id": run["deck_id"],
        "deck_title": deck["title"] if deck else run.get("deck_title"),
        "slide_count": len(slides),
        "fingerprint": fingerprint,
        "label": _deck_snapshot_label(deck["title"] if deck else run.get("deck_title"), len(slides), fingerprint),
        "slides": slides,
    }


def enrich_run_with_deck_snapshot(run: dict) -> dict:
    snapshot = deck_snapshot_for_run(run["id"])
    if not snapshot:
        return run
    run["deck_snapshot_fingerprint"] = snapshot["fingerprint"]
    run["deck_snapshot_label"] = snapshot["label"]
    run["deck_snapshot_slide_count"] = snapshot["slide_count"]
    return run


def _require_completed_displayable_run(run: dict) -> None:
    if run.get("status") != "completed":
        raise EvaluationRequestError("History Evaluations can only use completed Runs.", 422)
    slides = dbmod.list_run_slides(run["id"])
    slide_positions = {slide.get("position") for slide in slides if slide.get("has_displayable_artifact")}
    snapshot_positions = {slide["position"] for slide in _run_deck_snapshot_slides(run["id"])}
    if not slides or not all(slide.get("has_displayable_artifact") for slide in slides):
        raise EvaluationRequestError("History Evaluations can only use displayable Runs.", 422)
    if not snapshot_positions or slide_positions != snapshot_positions:
        raise EvaluationRequestError(
            "History Evaluations require a displayable result for every slide in the historical Deck snapshot.",
            422,
        )


def _reconcile_evaluation_status(evaluation_id: int) -> None:
    detail = dbmod.get_evaluation_detail(evaluation_id)
    if not detail:
        return
    statuses: list[str] = []
    all_completed_displayable = True
    for variant in detail["variants"]:
        for attempt in variant["attempts"]:
            run = dbmod.get_run(attempt["run_id"]) if attempt.get("run_id") else None
            status = run.get("status") if run else "missing"
            statuses.append(status)
            if status != attempt.get("status"):
                _update_attempt_status(attempt["id"], status)
            if status != "completed" or not run or not _run_has_displayable_for_every_slide(run):
                all_completed_displayable = False
    if not statuses or detail.get("status") in PRESERVED_EVALUATION_STATUSES:
        return
    if any(status in RUN_ACTIVE_STATUSES for status in statuses):
        next_status = "running"
    elif all_completed_displayable:
        next_status = "reviewing"
    elif all(status in RUN_FAILED_STATUSES for status in statuses):
        next_status = "failed"
    else:
        next_status = "partial"
    if next_status != detail.get("status"):
        dbmod.update_evaluation(evaluation_id, status=next_status)


def _update_attempt_status(attempt_id: int, status: str) -> None:
    db = dbmod.get_db()
    db.execute(
        """UPDATE evaluation_attempts
           SET status = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (status, attempt_id),
    )
    db.commit()
    db.close()


def _run_has_displayable_for_every_slide(run: dict) -> bool:
    deck_positions = {slide["position"] for slide in dbmod.list_slides(run["deck_id"])}
    displayable_positions = {
        slide["position"]
        for slide in dbmod.list_run_slides(run["id"])
        if slide.get("has_displayable_artifact")
    }
    return bool(deck_positions) and deck_positions == displayable_positions


def _require_evaluation(evaluation_id: int) -> dict:
    detail = dbmod.get_evaluation_detail(evaluation_id)
    if not detail:
        raise EvaluationRequestError("Evaluation not found.", 404)
    return detail


def _require_variant(evaluation_id: int, variant_id: int) -> dict:
    detail = _require_evaluation(evaluation_id)
    variant = next((item for item in detail["variants"] if item["id"] == variant_id), None)
    if not variant:
        raise EvaluationRequestError("Variant not found for Evaluation.", 404)
    return variant


def _require_attempt(evaluation_id: int, attempt_id: int) -> dict:
    detail = _require_evaluation(evaluation_id)
    for variant in detail["variants"]:
        for attempt in variant["attempts"]:
            if attempt["id"] == attempt_id:
                return attempt
    raise EvaluationRequestError("Attempt not found for Evaluation.", 404)


def _export_slide_positions(detail: dict, scope: str, slide_position: object | None) -> list[int]:
    positions = sorted({
        slide["position"]
        for variant in detail["variants"]
        for attempt in variant["attempts"]
        for slide in attempt.get("slides", [])
    })
    if scope == "all_slides":
        return positions
    if slide_position is None:
        return [positions[0]] if positions else []
    position = int(slide_position)
    if position not in positions:
        raise EvaluationRequestError(f"Slide {position} is not available for this Evaluation.", 404)
    return [position]


def _export_slide_payload(detail: dict, position: int, metadata_fields: list) -> dict:
    columns = []
    for variant in detail["variants"]:
        attempt = _representative_or_first_attempt(variant)
        if not attempt:
            continue
        slide = next((item for item in attempt.get("slides", []) if item.get("position") == position), None)
        columns.append(_export_column_payload(variant, attempt, slide, position, metadata_fields))
    return {
        "page_number": position,
        "columns": columns,
    }


def _representative_or_first_attempt(variant: dict) -> dict | None:
    representative_id = variant.get("representative_attempt_id")
    attempts = variant.get("attempts") or []
    return next((attempt for attempt in attempts if attempt["id"] == representative_id), None) or (attempts[0] if attempts else None)


def _export_column_payload(variant: dict, attempt: dict, slide: dict | None, position: int, metadata_fields: list) -> dict:
    snapshot = attempt.get("snapshot") if isinstance(attempt.get("snapshot"), dict) else {}
    payload = {
        "variant_id": variant["id"],
        "attempt_id": attempt["id"],
        "run_id": attempt.get("run_id"),
        "column_label": variant["label"],
        "attempt_label": attempt["label"],
        "page_number": position,
        "prompt": _prompt_display(snapshot),
        "model": _model_display(snapshot),
        "strategy": attempt.get("strategy") or snapshot.get("strategy"),
        "artifact": {
            "screenshot_path": slide.get("screenshot_path") if slide else None,
            "final_image_path": slide.get("final_image_path") if slide else None,
            "html_path": slide.get("html_path") if slide else None,
        },
    }
    allowed = {
        "variant_id",
        "attempt_id",
        "run_id",
        "column_label",
        "attempt_label",
        "page_number",
        "prompt",
        "model",
        "strategy",
        "artifact",
    }
    requested = {field for field in metadata_fields if field in allowed}
    requested.update({"variant_id", "attempt_id", "run_id", "column_label", "attempt_label", "artifact"})
    return {key: value for key, value in payload.items() if key in requested}


def _render_export_report_html(detail: dict, slide_payload: dict, metadata_fields: list) -> str:
    title = html.escape(str(detail.get("title") or "Evaluation"))
    deck_title = html.escape(str(detail.get("deck_title") or "Deck"))
    page_number = slide_payload["page_number"]
    columns = slide_payload.get("columns") or []
    column_count = max(1, min(4, len(columns)))
    cards = "\n".join(_render_export_column(column, metadata_fields) for column in columns)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #f5f7fb;
      color: #0f172a;
      font-family: Arial, "Microsoft YaHei", sans-serif;
    }}
    .report {{
      width: {EXPORT_VIEWPORT_WIDTH}px;
      min-height: {EXPORT_VIEWPORT_HEIGHT}px;
      padding: 48px;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-start;
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 32px;
      line-height: 1.15;
    }}
    .subtle {{
      color: #64748b;
      font-size: 18px;
      line-height: 1.35;
    }}
    .badge {{
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      padding: 8px 12px;
      background: #ffffff;
      font-size: 18px;
      white-space: nowrap;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat({column_count}, minmax(0, 1fr));
      gap: 16px;
      align-items: stretch;
    }}
    .card {{
      min-width: 0;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }}
    .card-head {{
      padding: 14px 16px;
      border-bottom: 1px solid #e2e8f0;
    }}
    .label {{
      font-weight: 700;
      font-size: 22px;
      line-height: 1.15;
    }}
    .attempt {{
      color: #64748b;
      font-size: 16px;
      margin-top: 4px;
    }}
    .meta {{
      display: grid;
      gap: 4px;
      padding: 10px 16px;
      border-bottom: 1px solid #e2e8f0;
      color: #334155;
      font-size: 14px;
      line-height: 1.25;
      min-height: 72px;
    }}
    .visual {{
      height: 1120px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 12px;
      background: #f8fafc;
    }}
    .visual img {{
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      border: 1px solid #e2e8f0;
      background: #ffffff;
    }}
    .missing {{
      width: 100%;
      height: 100%;
      border: 1px dashed #94a3b8;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #64748b;
      font-size: 18px;
      text-align: center;
      padding: 24px;
    }}
  </style>
</head>
<body>
  <main class="report">
    <header class="header">
      <div>
        <h1>{title}</h1>
        <div class="subtle">Deck: {deck_title}</div>
      </div>
      <div class="badge">Slide {page_number}</div>
    </header>
    <section class="grid">
      {cards}
    </section>
  </main>
</body>
</html>"""


def _render_export_column(column: dict, metadata_fields: list) -> str:
    label = html.escape(str(column.get("column_label") or "Variant"))
    attempt_label = html.escape(str(column.get("attempt_label") or "Attempt"))
    meta_rows = []
    labels = {
        "page_number": "Page",
        "prompt": "Prompt",
        "model": "Model",
        "strategy": "Strategy",
    }
    for field in metadata_fields:
        if field not in labels or field not in column:
            continue
        meta_rows.append(
            f"<div><strong>{html.escape(labels[field])}:</strong> {html.escape(str(column.get(field) or ''))}</div>"
        )
    metadata = "\n".join(meta_rows) or "<div>No metadata selected</div>"
    image_data = _artifact_image_data_url(column.get("artifact") or {})
    if image_data:
        visual = f'<img src="{image_data}" alt="{label} slide visual" />'
    else:
        visual = '<div class="missing">No readable image artifact for this column</div>'
    return f"""<article class="card">
  <div class="card-head">
    <div class="label">{label}</div>
    <div class="attempt">{attempt_label}</div>
  </div>
  <div class="meta">{metadata}</div>
  <div class="visual">{visual}</div>
</article>"""


def _artifact_image_data_url(artifact: dict) -> str | None:
    path = _resolve_export_artifact(
        artifact.get("screenshot_path") or artifact.get("final_image_path")
    )
    if not path:
        return None
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    if path.suffix.lower() == ".webp":
        mime = "image/webp"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _add_export_assets(archive: zipfile.ZipFile, manifest: dict[str, Any], slide_payload: dict) -> None:
    page_number = slide_payload["page_number"]
    for index, column in enumerate(slide_payload.get("columns") or [], start=1):
        artifact = column.get("artifact") or {}
        path = _resolve_export_artifact(
            artifact.get("screenshot_path") or artifact.get("final_image_path")
        )
        if not path:
            continue
        zip_name = f"assets/slide-{page_number}/variant-{index}{path.suffix.lower()}"
        archive.write(path, zip_name)
        manifest["included"].append(zip_name)


def _resolve_export_artifact(value: object | None) -> Path | None:
    if not value:
        return None
    try:
        path = Path(str(value))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved.suffix.lower() not in IMAGE_SUFFIXES or not resolved.is_file():
        return None
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return resolved


def _prompt_display(snapshot: dict) -> str | None:
    prompts = snapshot.get("prompts") if isinstance(snapshot.get("prompts"), dict) else {}
    parts = []
    for prompt in prompts.values():
        if not isinstance(prompt, dict):
            continue
        label = " ".join(str(prompt.get(key) or "").strip() for key in ("name", "version")).strip()
        if label:
            parts.append(label)
    return " · ".join(parts) if parts else None


def _model_display(snapshot: dict) -> str | None:
    config = snapshot.get("config") if isinstance(snapshot.get("config"), dict) else {}
    models = []
    for key in ("designer", "html_agent"):
        agent_config = config.get(key)
        if isinstance(agent_config, dict) and agent_config.get("model"):
            models.append(str(agent_config["model"]))
    return " · ".join(models) if models else None


def _history_run_snapshot(run: dict, index: int) -> dict:
    config = dbmod.get_config(run["config_id"])
    requirement = dbmod.get_requirement(run["requirement_id"])
    color = dbmod.get_color(run["color_id"])
    deck = dbmod.get_deck(run["deck_id"])
    deck_snapshot = deck_snapshot_for_run(run["id"]) or {
        "fingerprint": "",
        "slide_count": 0,
        "slides": [],
        "label": str(run.get("deck_title") or run["deck_id"]),
    }
    return {
        "run": {
            "id": run["id"],
            "batch_id": run.get("batch_id"),
            "status": run.get("status"),
            "created_at": run.get("created_at"),
        },
        "deck": {
            "id": deck["id"] if deck else run["deck_id"],
            "title": deck["title"] if deck else run.get("deck_title"),
            "snapshot_fingerprint": deck_snapshot["fingerprint"],
            "snapshot_slide_count": deck_snapshot["slide_count"],
            "snapshot_label": deck_snapshot["label"],
        },
        "config": _safe_config_snapshot(config) if config else {"id": run["config_id"]},
        "requirement": {
            "id": requirement["id"],
            "title": requirement["title"],
            "content": requirement["content"],
        } if requirement else {"id": run["requirement_id"]},
        "color": {
            "id": color["id"],
            "title": color["title"],
            "content": color["content"],
        } if color else {"id": run["color_id"]},
        "engine": run.get("engine") or "html",
        "strategy": run.get("strategy") or "html_default",
        "route_metadata": _parse_json_dict(run.get("route_metadata")),
        "model_call_metadata": _redact_secret_fields(_parse_json_dict(run.get("model_call_metadata"))),
        "prompts": {
            "designer": _prompt_snapshot(run.get("designer_prompt_id")),
            "html_agent": _prompt_snapshot(run.get("html_prompt_id")),
        },
        "variant_index": index + 1,
    }


def _run_deck_snapshot_slides(run_id: int) -> list[dict[str, Any]]:
    slides = dbmod.list_run_slides(run_id)
    return [
        {
            "position": int(slide.get("position") or 0),
            "title": slide.get("slide_title_snapshot") or slide.get("slide_title") or "",
            "content": slide.get("slide_content_snapshot") or slide.get("slide_content") or "",
        }
        for slide in sorted(slides, key=lambda item: (int(item.get("position") or 0), int(item.get("id") or 0)))
    ]


def _deck_snapshot_fingerprint(slides: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "position": slide.get("position"),
            "title": slide.get("title") or "",
            "content": slide.get("content") or "",
        }
        for slide in slides
    ]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _deck_snapshot_label(title: object, slide_count: int, fingerprint: str) -> str:
    safe_title = str(title or "Historical Deck").strip() or "Historical Deck"
    short_hash = fingerprint[:8] if fingerprint else "unknown"
    return f"{safe_title} · {slide_count} slides · {short_hash}"


def _generation_payload_from_plan(plan: dict) -> dict:
    if plan.get("mode") == "auto":
        payload = {
            "mode": "auto",
            "deck_id": plan["deck_id"],
            "config_id": plan["config_id"],
            "engine": plan["engine"],
            "strategy": plan["strategy"],
            "route_metadata": json.loads(json.dumps(plan["route_metadata"], ensure_ascii=False)),
            "auto_candidate_count": plan.get("auto_candidate_count", 1),
            "designer_prompt_id": plan.get("designer_prompt_id"),
            "html_prompt_id": plan.get("html_prompt_id"),
        }
        if plan["engine"] == "image":
            payload["auto_color_id"] = plan["color_id"]
        return payload
    uses_system_managed_dimensions = (
        plan["engine"] == "image"
        and plan["strategy"] in NO_GENERIC_DIMENSION_IMAGE_STRATEGIES
    )
    return {
        "deck_id": plan["deck_id"],
        "requirement_ids": [] if uses_system_managed_dimensions else [plan["requirement_id"]],
        "color_ids": [] if uses_system_managed_dimensions else [plan["color_id"]],
        "config_id": plan["config_id"],
        "engine": plan["engine"],
        "strategy": plan["strategy"],
        "route_metadata": json.loads(json.dumps(plan["route_metadata"], ensure_ascii=False)),
        "designer_prompt_id": plan.get("designer_prompt_id"),
        "html_prompt_id": plan.get("html_prompt_id"),
    }


def _variant_letter(index: int) -> str:
    return "ABCD"[index]
