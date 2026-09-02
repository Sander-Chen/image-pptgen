"""Machine visual QA runner for Evaluation attempts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import db as dbmod
import pipeline
from backend.services import evaluations, model_profiles

QA_ROLE = "evaluation_visual_qa"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
HTML_SUFFIXES = {".html", ".htm"}
AUTO_CODEX_QA_SOURCE = "codex_html_auto_machine_qa"


def run_machine_qa(evaluation_id: int, data: dict[str, Any]) -> dict:
    detail = evaluations.get_evaluation(evaluation_id)
    if not detail:
        raise evaluations.EvaluationRequestError("Evaluation not found.", 404)
    targets = _qa_targets(detail, data)
    result = detail
    for attempt, slide in targets:
        result = _run_single_machine_qa(evaluation_id, detail, attempt, slide, data)
    return result


def run_codex_html_machine_qa_for_run(run_id: int, data: dict[str, Any] | None = None) -> dict[str, Any]:
    run = dbmod.get_run(int(run_id))
    if not run:
        raise evaluations.EvaluationRequestError("Codex HTML run not found for Machine QA.", 404)
    if run.get("strategy") != "codex_html":
        raise evaluations.EvaluationRequestError("Automatic Machine QA is only supported for codex_html runs.", 422)

    payload = dict(data or {})
    snapshot = {
        "source": AUTO_CODEX_QA_SOURCE,
        "run_id": int(run_id),
        "batch_id": run.get("batch_id"),
        "strategy": run.get("strategy"),
        "engine": run.get("engine"),
        "route_metadata": _json_dict(run.get("route_metadata")),
    }
    evaluation_id = dbmod.create_evaluation(
        deck_id=int(run["deck_id"]),
        title=f"Codex HTML Machine QA · Run {run_id}",
        goal="Run screenshot-backed Machine QA for Codex HTML output.",
        status="reviewing",
        export_config={"source": AUTO_CODEX_QA_SOURCE, "run_id": int(run_id)},
    )
    variant_id = dbmod.create_evaluation_variant(
        evaluation_id,
        label="A · Codex HTML",
        goal="Review the Codex HTML screenshots generated for this run.",
        comparison_variable="codex_html_machine_qa",
        generation_plan_snapshot=snapshot,
        sort_order=1,
    )
    attempt_id = dbmod.create_evaluation_attempt(
        evaluation_id,
        variant_id,
        run_id=int(run_id),
        batch_id=run.get("batch_id"),
        label="A1 · Codex HTML Run",
        attempt_index=1,
        snapshot=snapshot,
        status=run.get("status"),
    )
    dbmod.set_evaluation_variant_representative(variant_id, attempt_id)

    qa_payload = {
        **payload,
        "scope": "all_slides",
        "attempt_ids": [attempt_id],
    }
    try:
        run_machine_qa(evaluation_id, qa_payload)
        status = "complete"
        error = None
    except evaluations.EvaluationRequestError as exc:
        if exc.status_code != 409 or "gemini api key" not in str(exc).lower():
            raise
        _record_skipped_machine_qa_for_run(
            evaluation_id,
            attempt_id,
            reason_code="missing_gemini_key",
            evidence=str(exc),
        )
        status = "skipped_missing_gemini_key"
        error = str(exc)

    rows = dbmod.list_machine_qa_for_run(int(run_id))
    summary = _summarize_machine_qa_rows(rows)
    return {
        "source": AUTO_CODEX_QA_SOURCE,
        "status": status,
        "error": error,
        "evaluation_id": evaluation_id,
        "variant_id": variant_id,
        "attempt_id": attempt_id,
        "total": summary["total"],
        "pass_count": summary["pass_count"],
        "fail_count": summary["fail_count"],
        "skipped_count": summary["skipped_count"],
        "verdict_status": summary["status"],
        "run_slide_ids": [row.get("run_slide_id") for row in rows if row.get("run_slide_id") is not None],
    }


def _run_single_machine_qa(
    evaluation_id: int,
    detail: dict[str, Any],
    attempt: dict[str, Any],
    slide: dict[str, Any],
    data: dict[str, Any],
) -> dict:
    slide_position = int(slide.get("position") or 0)
    image_path = _resolve_image_artifact(slide)
    if not image_path:
        return evaluations.record_machine_qa(
            evaluation_id,
            {
                "attempt_id": attempt["id"],
                "run_slide_id": slide["id"],
                "slide_position": slide_position,
                "verdict": "skipped",
                "issues": [
                    {
                        "severity": "medium",
                        "dimension": "missing_asset",
                        "evidence": "No readable screenshot or final image artifact is available for Machine QA.",
                    }
                ],
                "replace_existing": True,
                "raw_response": "Machine QA skipped: missing readable visual artifact.",
            },
        )

    profile_row, agent_config = _resolve_qa_profile(data.get("model_profile_id"))
    prompt = _resolve_qa_prompt(data.get("prompt_id"))
    prompt_text = _build_prompt(prompt["content"], detail, attempt, slide, image_path)
    timeout_seconds = _timeout_seconds(data.get("timeout_seconds"))
    try:
        raw_response, attempts = pipeline.call_llm_with_metadata(
            agent_config,
            prompt_text,
            timeout_seconds=timeout_seconds,
            agent_role=QA_ROLE,
            image_paths=[str(image_path)],
        )
    except Exception as exc:
        raise evaluations.EvaluationRequestError(f"Machine QA failed: {exc}", 502) from exc

    parsed = parse_machine_qa_response(raw_response)
    return evaluations.record_machine_qa(
        evaluation_id,
        {
            "attempt_id": attempt["id"],
            "run_slide_id": slide["id"],
            "slide_position": slide_position,
            "verdict": parsed["verdict"],
            "issues": parsed["issues"],
            "model_profile_id": profile_row["id"],
            "prompt_id": prompt["id"],
            "replace_existing": True,
            "raw_response": json.dumps(
                {
                    "response": raw_response,
                    "provider_attempts": attempts,
                    "image_path": str(image_path),
                },
                ensure_ascii=False,
            ),
        },
    )


def _record_skipped_machine_qa_for_run(
    evaluation_id: int,
    attempt_id: int,
    *,
    reason_code: str,
    evidence: str,
) -> None:
    detail = evaluations.get_evaluation(evaluation_id)
    if not detail:
        return
    attempt = _require_attempt(detail, attempt_id)
    for slide in attempt.get("slides") or []:
        evaluations.record_machine_qa(
            evaluation_id,
            {
                "attempt_id": attempt_id,
                "run_slide_id": slide["id"],
                "slide_position": int(slide.get("position") or 0),
                "verdict": "skipped",
                "issues": [
                    {
                        "severity": "medium",
                        "dimension": reason_code,
                        "evidence": evidence,
                    }
                ],
                "replace_existing": True,
                "raw_response": f"Machine QA skipped: {reason_code}.",
            },
        )


def _qa_targets(detail: dict[str, Any], data: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    scope = str(data.get("scope") or "").strip().lower()
    if not scope and ("attempt_ids" in data or "slide_positions" in data):
        scope = "selected_slides" if data.get("slide_positions") else "all_slides"
    if not scope:
        attempt = _require_attempt(detail, data.get("attempt_id"))
        slide_position = _required_slide_position(data)
        slide = _require_attempt_slide(attempt, slide_position)
        run_slide_id = data.get("run_slide_id")
        if run_slide_id is not None and int(run_slide_id) != int(slide["id"]):
            raise evaluations.EvaluationRequestError("run_slide_id does not belong to this Attempt slide.", 422)
        return [(attempt, slide)]
    if scope not in {"all_slides", "selected_slides"}:
        raise evaluations.EvaluationRequestError("Machine QA scope must be all_slides or selected_slides.", 422)
    attempts = _selected_attempts(detail, data.get("attempt_ids"))
    if scope == "all_slides":
        positions = sorted({
            int(slide.get("position") or 0)
            for attempt in attempts
            for slide in attempt.get("slides") or []
            if int(slide.get("position") or 0) > 0
        })
    else:
        positions = _selected_slide_positions(data.get("slide_positions"))
    if not positions:
        raise evaluations.EvaluationRequestError("No Machine QA slides selected.", 400)
    targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for attempt in attempts:
        for position in positions:
            targets.append((attempt, _require_attempt_slide(attempt, position)))
    return targets


def _selected_attempts(detail: dict[str, Any], raw_attempt_ids: Any) -> list[dict[str, Any]]:
    all_attempts = [
        attempt
        for variant in detail.get("variants") or []
        for attempt in variant.get("attempts") or []
    ]
    if raw_attempt_ids in (None, ""):
        return all_attempts
    if not isinstance(raw_attempt_ids, list):
        raise evaluations.EvaluationRequestError("attempt_ids must be an array.", 422)
    requested_ids = [int(value) for value in raw_attempt_ids]
    attempts = [attempt for attempt in all_attempts if int(attempt["id"]) in requested_ids]
    if len(attempts) != len(set(requested_ids)):
        raise evaluations.EvaluationRequestError("One or more Machine QA attempt_ids do not belong to this Evaluation.", 422)
    return attempts


def _selected_slide_positions(raw_positions: Any) -> list[int]:
    if not isinstance(raw_positions, list) or not raw_positions:
        raise evaluations.EvaluationRequestError("selected_slides scope requires slide_positions.", 400)
    positions: list[int] = []
    for value in raw_positions:
        try:
            position = int(value)
        except (TypeError, ValueError):
            raise evaluations.EvaluationRequestError("slide_positions must be numbers.", 422) from None
        if position <= 0:
            raise evaluations.EvaluationRequestError("slide_positions must be positive numbers.", 422)
        positions.append(position)
    return sorted(set(positions))


def parse_machine_qa_response(raw_response: str) -> dict[str, Any]:
    parsed = _json_from_response(raw_response)
    if parsed is not None:
        verdict = _normalize_verdict(parsed.get("verdict"))
        issues = _normalize_issues(parsed.get("issues") or parsed.get("list_of_issues") or [])
        return {"verdict": verdict, "issues": issues}

    verdict_match = re.search(r"(?im)^\s*Verdict\s*:\s*(True|False|Pass|Fail)\s*$", raw_response or "")
    if verdict_match:
        verdict = _normalize_verdict(verdict_match.group(1))
        issues = _issues_from_legacy_text(raw_response)
        return {"verdict": verdict, "issues": issues}

    return {
        "verdict": "skipped",
        "issues": [
            {
                "severity": "medium",
                "dimension": "schema",
                "evidence": "Machine QA response did not match the expected JSON or Verdict format.",
            }
        ],
    }


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _summarize_machine_qa_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pass_count = sum(1 for row in rows if row.get("verdict") == "pass")
    fail_count = sum(1 for row in rows if row.get("verdict") == "fail")
    skipped_count = sum(1 for row in rows if row.get("verdict") == "skipped")
    if fail_count:
        status = "fail"
    elif skipped_count and not pass_count:
        status = "skipped"
    elif rows:
        status = "pass"
    else:
        status = "empty"
    return {
        "status": status,
        "total": len(rows),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "skipped_count": skipped_count,
    }


def _json_from_response(raw_response: str) -> dict[str, Any] | None:
    text = raw_response or ""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    candidates.append(text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_verdict(value: Any) -> str:
    if isinstance(value, bool):
        return "pass" if value else "fail"
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "pass", "passed", "ok"}:
        return "pass"
    if normalized in {"false", "fail", "failed", "issue", "issues"}:
        return "fail"
    if normalized in {"skip", "skipped"}:
        return "skipped"
    return "skipped"


def _normalize_issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    issues: list[dict[str, Any]] = []
    for issue in value:
        if isinstance(issue, dict):
            safe_issue = {str(key): item for key, item in issue.items()}
        else:
            safe_issue = {"evidence": str(issue)}
        safe_issue.setdefault("severity", "medium")
        safe_issue.setdefault("dimension", "visual")
        issues.append(safe_issue)
    return issues


def _issues_from_legacy_text(raw_response: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for line in (raw_response or "").splitlines():
        text = line.strip(" -\t")
        if not text or text.lower().startswith("verdict"):
            continue
        if ":" in text and any(label in text.lower() for label in ("dimension", "screenshot evidence", "evidence")):
            continue
        issues.append({"severity": "medium", "dimension": "visual", "evidence": text})
    return issues


def _require_attempt(detail: dict, attempt_id: Any) -> dict:
    if attempt_id is None:
        raise evaluations.EvaluationRequestError("attempt_id is required.", 400)
    for variant in detail.get("variants") or []:
        for attempt in variant.get("attempts") or []:
            if int(attempt["id"]) == int(attempt_id):
                return attempt
    raise evaluations.EvaluationRequestError("Attempt not found for Evaluation.", 404)


def _required_slide_position(data: dict[str, Any]) -> int:
    value = data.get("slide_position")
    if value is None:
        raise evaluations.EvaluationRequestError("slide_position is required.", 400)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise evaluations.EvaluationRequestError("slide_position must be a number.", 422) from None


def _require_attempt_slide(attempt: dict, slide_position: int) -> dict:
    for slide in attempt.get("slides") or []:
        if int(slide.get("position") or 0) == slide_position:
            return slide
    raise evaluations.EvaluationRequestError("Slide not found for Attempt.", 404)


def _resolve_qa_profile(profile_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if profile_id:
        profile = model_profiles.get_profile(int(profile_id))
        if not profile:
            raise evaluations.EvaluationRequestError("Machine QA model profile not found.", 404)
    else:
        profiles = model_profiles.list_profiles(role=QA_ROLE, status="active")
        if not profiles:
            try:
                profile_id = model_profiles.ensure_evaluation_visual_qa_profile()
            except ValueError as exc:
                raise evaluations.EvaluationRequestError(str(exc), 409) from exc
            profile = model_profiles.get_profile(profile_id)
        else:
            profile = profiles[0]
    if not profile:
        raise evaluations.EvaluationRequestError("Machine QA model profile not found.", 404)
    if profile.get("role") != QA_ROLE:
        raise evaluations.EvaluationRequestError("Machine QA model profile must use evaluation_visual_qa role.", 422)
    try:
        agent_config = model_profiles.profile_to_agent_config(int(profile["id"]))
    except ValueError as exc:
        raise evaluations.EvaluationRequestError(str(exc), 422) from exc
    if not agent_config.get("api_key"):
        raise evaluations.EvaluationRequestError("No Gemini API key is configured for Machine QA.", 409)
    return profile, agent_config


def _resolve_qa_prompt(prompt_id: Any) -> dict[str, Any]:
    prompt = dbmod.get_prompt(int(prompt_id)) if prompt_id else dbmod.get_active_prompt(QA_ROLE)
    if not prompt:
        raise evaluations.EvaluationRequestError("No active Machine QA prompt is configured.", 409)
    if prompt.get("agent_type") != QA_ROLE:
        raise evaluations.EvaluationRequestError("Machine QA prompt must use evaluation_visual_qa role.", 422)
    if prompt.get("status") != "active" or prompt.get("lifecycle_status", "active") != "active":
        raise evaluations.EvaluationRequestError("Machine QA prompt must be active.", 422)
    return prompt


def _timeout_seconds(value: Any) -> int:
    if value in (None, ""):
        return 120
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        raise evaluations.EvaluationRequestError("timeout_seconds must be a number.", 422) from None
    return min(max(timeout, 10), 300)


def _build_prompt(prompt_content: str, detail: dict, attempt: dict, slide: dict, image_path: Path) -> str:
    html_context = _slide_html_context(slide)
    variables = {
        "Evaluation-Title": str(detail.get("title") or ""),
        "Evaluation-Goal": str(detail.get("goal") or ""),
        "Deck-Title": str(detail.get("deck_title") or ""),
        "Attempt-Label": str(attempt.get("label") or ""),
        "Slide-Position": str(slide.get("position") or ""),
        "Screenshot-Path": str(image_path),
        "Complete-HTML": html_context,
    }
    rendered = pipeline.render_template_string(prompt_content, variables)
    return (
        f"{rendered}\n\n"
        "Machine QA input metadata:\n"
        f"- Evaluation: {variables['Evaluation-Title']}\n"
        f"- Goal: {variables['Evaluation-Goal']}\n"
        f"- Deck: {variables['Deck-Title']}\n"
        f"- Attempt: {variables['Attempt-Label']}\n"
        f"- Slide: {variables['Slide-Position']}\n\n"
        "Complete HTML, when available:\n"
        "```html\n"
        f"{html_context[:20000]}\n"
        "```\n"
    )


def _slide_html_context(slide: dict) -> str:
    if slide.get("clean_html"):
        return str(slide["clean_html"])
    html_path = _resolve_path(slide.get("html_path"), HTML_SUFFIXES)
    if not html_path:
        return ""
    try:
        return html_path.read_text(encoding="utf-8")[:20000]
    except OSError:
        return ""


def _resolve_image_artifact(slide: dict) -> Path | None:
    for key in ("screenshot_path", "final_image_path"):
        resolved = _resolve_path(slide.get(key), IMAGE_SUFFIXES)
        if resolved:
            return resolved
    return None


def _resolve_path(value: Any, suffixes: set[str]) -> Path | None:
    if not value:
        return None
    try:
        path = Path(str(value))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved.suffix.lower() not in suffixes or not resolved.is_file():
        return None
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return resolved
