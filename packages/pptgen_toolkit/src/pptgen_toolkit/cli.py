"""Command-line entrypoint for the PPTGen control plane."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from . import main_session_supervisor
from .client import PlatformError, PlatformUnavailable, PptgenClient


DEFAULT_BASE_URL = "http://127.0.0.1:3100"
IMAGE_SUFFIXES = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}
IMAGE_MAGIC_PREFIXES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"BM",
    b"II*\x00",
    b"MM\x00*",
    b"\x00\x00\x01\x00",
)
SVG_PREFIX = re.compile(
    rb"^(?:\xef\xbb\xbf)?\s*(?:<\?xml\b[^>]*\?>\s*)?<svg(?:\s|>)",
    re.IGNORECASE | re.DOTALL,
)
PUBLIC_ACTIVITY_PATH = re.compile(
    r"(?:^|[\s'\"`(])(?:/(?:home|root|tmp|var|etc|Users|mnt)/[^\s'\"`)]*|[A-Za-z]:\\[^\s'\"`)]*)"
)
PUBLIC_ACTIVITY_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization:",
    "bearer ",
    "password",
    "secret",
    "token=",
)


def _emit(payload: dict[str, Any], *, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=stream,
        flush=True,
    )


def _looks_like_image(payload: bytes) -> bool:
    if payload.startswith(IMAGE_MAGIC_PREFIXES):
        return True
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return True
    if len(payload) >= 12 and payload[4:8] == b"ftyp":
        brand = payload[8:12].lower()
        if brand in {b"avif", b"avis", b"heic", b"heix", b"hevc", b"hevx", b"mif1"}:
            return True
    return SVG_PREFIX.match(payload[:8192]) is not None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pptgen")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PPTGEN_BASE_URL", DEFAULT_BASE_URL),
        help="PPTGen Platform base URL",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser("doctor", help="Check platform connectivity")
    doctor.add_argument("--json", action="store_true", required=True)

    material = subcommands.add_parser("material", help="Manage source material")
    material_commands = material.add_subparsers(dest="material_command", required=True)
    submit = material_commands.add_parser("submit", help="Submit text material")
    submit.add_argument("--title", required=True)
    submit.add_argument("--text-file", required=True, type=Path)
    submit.add_argument("--json", action="store_true", required=True)

    split = subcommands.add_parser("split", help="Review page splitting")
    split_commands = split.add_subparsers(dest="split_command", required=True)
    propose = split_commands.add_parser("propose", help="Create a pending split")
    propose.add_argument("--deck-id", required=True, type=int)
    propose.add_argument(
        "--mode",
        required=True,
        choices=("deterministic", "llm"),
    )
    propose.add_argument("--json", action="store_true", required=True)
    revise = split_commands.add_parser("revise", help="Revise a pending split")
    revise.add_argument("--draft-id", required=True, type=int)
    revise.add_argument("--instruction", required=True)
    revise.add_argument("--json", action="store_true", required=True)
    confirm = split_commands.add_parser("confirm", help="Confirm a pending split")
    confirm.add_argument("--draft-id", required=True, type=int)
    confirm.add_argument("--json", action="store_true", required=True)

    generate = subcommands.add_parser("generate", help="Start presentation generation")
    generate.add_argument("--deck-id", required=True, type=int)
    generate.add_argument("--intent", required=True, choices=("auto", "preference"))
    generate.add_argument("--preference")
    generate.add_argument("--json", action="store_true", required=True)

    status = subcommands.add_parser("status", help="Follow generation progress")
    status.add_argument("--run-id", required=True, type=int)
    status.add_argument("--follow", action="store_true", required=True)
    status.add_argument("--jsonl", action="store_true", required=True)
    status.add_argument(
        "--after-activity-cursor",
        help="Resume safe activity after an invocation:sequence cursor",
    )

    session = subcommands.add_parser("session", help="Supervise the current Codex session")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    supervise = session_commands.add_parser(
        "supervise",
        help="Register exact-session recovery for one existing Run",
    )
    supervise.add_argument("--session-id")
    supervise.add_argument("--run-id", required=True, type=int)
    supervise.add_argument("--main-pid", type=int)
    supervise.add_argument("--json", action="store_true", required=True)

    result = subcommands.add_parser("result", help="Show generation results")
    result.add_argument("--run-id", required=True, type=int)
    result.add_argument("--json", action="store_true", required=True)
    return parser


def _split_projection(draft: dict[str, Any]) -> dict[str, Any]:
    slides = draft["slides"]
    markdown = "\n\n".join(
        f"## Page {index}: {slide.get('title') or f'Page {index}'}\n\n"
        f"{str(slide.get('content') or '').strip()}"
        for index, slide in enumerate(slides, start=1)
    )
    projection = {
        "deck_id": draft["deck_id"],
        "draft_id": draft["id"],
        "markdown": markdown,
        "mode": draft["mode"],
        "page_count": len(slides),
        "status": draft["status"],
    }
    if draft.get("content_mode") is not None:
        projection["content_mode"] = draft["content_mode"]
    return projection


def _integer_fact(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _progress_projection(
    *,
    run_id: int,
    status: dict[str, Any],
    detail: dict[str, Any],
    event: str,
    follow_elapsed_seconds: float,
) -> dict[str, Any]:
    run_status = status["status"]
    progress = status["progress"]
    completed = _integer_fact(progress.get("completed"))
    failed = _integer_fact(progress.get("failed"))
    running = _integer_fact(progress.get("running"))
    pending = _integer_fact(progress.get("pending"))
    total = _integer_fact(progress.get("total"))
    design_ready = bool(
        detail.get("design_principle_raw") or detail.get("design_principle_json")
    )
    active = run_status in {"queued", "pending", "running"}

    if design_ready:
        design_status = "completed"
    elif active:
        design_status = "running" if run_status == "running" else "pending"
    else:
        design_status = "failed"

    if total > 0 and completed == total and failed == 0:
        page_status = "completed"
    elif active and design_ready:
        page_status = "running"
    elif failed > 0:
        page_status = "failed"
    else:
        page_status = "pending"

    if run_status == "completed" and failed == 0:
        finish_status = "completed"
    elif active:
        finish_status = "pending"
    else:
        finish_status = "failed"

    if run_status in {"queued", "pending"}:
        current_activity = "Waiting for the existing task queue to start generation"
    elif run_status == "running" and not design_ready:
        current_activity = "Generating the overall design"
    elif run_status == "running":
        current_activity = (
            f"Design is ready; generating pages, {completed}/{total} complete"
        )
    elif run_status == "completed" and failed == 0:
        current_activity = f"All {completed} pages have been generated"
    elif failed > 0:
        current_activity = (
            f"Page generation ended: {completed} succeeded, {failed} failed"
        )
    else:
        current_activity = f"The task ended with status {run_status}"

    source_facts = {
        "completed_slides": completed,
        "design_ready": design_ready,
        "failed_slides": failed,
        "pending_slides": pending,
        "run_status": run_status,
        "running_slides": running,
        "total_slides": total,
    }
    projection = {
        "current_activity": current_activity,
        "event": event,
        "follow_elapsed_seconds": round(follow_elapsed_seconds, 3),
        "run_id": run_id,
        "source_facts": source_facts,
        "task_progress": [
            {"step": "Design", "status": design_status},
            {"step": "Page generation", "status": page_status},
            {"step": "Done", "status": finish_status},
        ],
    }
    if event == "heartbeat":
        projection.update(
            {
                "current_activity": "The task is still running; no new business milestone yet.",
                "kind": "heartbeat",
                "milestone": False,
            }
        )
    else:
        projection.update({"kind": "progress_update", "milestone": True})
    return projection


def _safe_activity_events(
    status: dict[str, Any],
    *,
    run_id: int,
) -> tuple[list[dict[str, Any]], str | None]:
    activity = status.get("activity")
    if not isinstance(activity, dict):
        return [], None
    raw_events = activity.get("events")
    if not isinstance(raw_events, list):
        return [], None
    allowed_kinds = {"agent_message", "authorization_wait", "business_activity"}
    event_names = {
        "agent_message": "agent_message",
        "authorization_wait": "authorization_wait",
        "business_activity": "activity",
    }
    projected: list[dict[str, Any]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        message = raw.get("message")
        cursor = raw.get("cursor")
        if (
            kind not in allowed_kinds
            or not isinstance(message, str)
            or not message.strip()
            or len(message.strip()) > 240
            or "\n" in message
            or "\r" in message
            or PUBLIC_ACTIVITY_PATH.search(message) is not None
            or any(
                marker in message.lower()
                for marker in PUBLIC_ACTIVITY_SECRET_MARKERS
            )
            or not isinstance(cursor, str)
            or not cursor
            or raw.get("milestone") is not False
        ):
            continue
        public = {
            "cursor": cursor,
            "event": event_names[kind],
            "kind": kind,
            "message": message,
            "milestone": False,
            "run_id": run_id,
        }
        if isinstance(raw.get("observed_at"), str):
            public["observed_at"] = raw["observed_at"]
        projected.append(public)
    next_cursor = activity.get("next_cursor")
    return projected, next_cursor if isinstance(next_cursor, str) and next_cursor else None


def _result_projection(detail: dict[str, Any]) -> dict[str, Any]:
    raw_slides = detail.get("slides")
    if not isinstance(raw_slides, list):
        raise PlatformError("PPTGen Platform returned invalid result slides")

    image_run = detail.get("engine") == "image"
    missing_artifact_message = (
        "Expected final image artifact is missing"
        if image_run
        else "Expected HTML/PNG artifact is missing"
    )
    slides: list[dict[str, Any]] = []
    successful = 0
    cover_png: str | None = None
    for raw_slide in sorted(
        (item for item in raw_slides if isinstance(item, dict)),
        key=lambda item: _integer_fact(item.get("position")),
    ):
        html_path = raw_slide.get("html_path")
        png_path = (
            raw_slide.get("final_image_path")
            if image_run
            else raw_slide.get("screenshot_path") or raw_slide.get("final_image_path")
        )
        artifact_complete = (
            raw_slide.get("status") == "completed"
            and raw_slide.get("has_displayable_artifact") is True
            and isinstance(png_path, str)
            and bool(png_path)
            and (
                image_run
                or (isinstance(html_path, str) and bool(html_path))
            )
        )
        if artifact_complete:
            projected_status = "completed"
            successful += 1
            if cover_png is None:
                cover_png = png_path
        elif raw_slide.get("status") == "completed":
            projected_status = "missing_artifact"
        else:
            projected_status = str(raw_slide.get("status") or "incomplete")
        slides.append(
            {
                "error_message": raw_slide.get("error_message")
                or (
                    missing_artifact_message
                    if projected_status == "missing_artifact"
                    else None
                ),
                "html_path": html_path if isinstance(html_path, str) else None,
                "png_path": png_path if isinstance(png_path, str) else None,
                "position": _integer_fact(raw_slide.get("position")),
                "run_slide_id": raw_slide.get("id"),
                "status": projected_status,
                "title": str(raw_slide.get("slide_title") or ""),
            }
        )

    platform_status = str(detail.get("status") or "unknown")
    design_raw = detail.get("design_principle_raw")
    design_ready = isinstance(design_raw, str) and bool(design_raw.strip())
    failed = len(slides) - successful
    if platform_status in {"queued", "pending", "running"}:
        result_status = "in_progress"
    elif (
        platform_status == "completed"
        and (image_run or design_ready)
        and slides
        and failed == 0
    ):
        result_status = "completed"
    elif successful > 0:
        result_status = "partially_completed"
    else:
        result_status = "failed"

    run_detail_keys = (
        "batch_id",
        "completed_at",
        "config_id",
        "config_name",
        "created_at",
        "deck_id",
        "engine",
        "error_message",
        "output_dir",
        "requirement_title",
        "started_at",
        "strategy",
    )
    return {
        "cover_png": cover_png,
        "design_director_raw": design_raw if isinstance(design_raw, str) else None,
        "failed_slide_count": failed,
        "platform_status": platform_status,
        "run_detail": {key: detail.get(key) for key in run_detail_keys},
        "run_id": detail["id"],
        "slide_count": len(slides),
        "slides": slides,
        "status": result_status,
        "successful_slide_count": successful,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    client = PptgenClient(args.base_url)
    try:
        if args.command == "session" and args.session_command == "supervise":
            session_id = main_session_supervisor.resolve_registration_session_id(
                args.session_id
            )
            main_pid = (
                args.main_pid
                if args.main_pid is not None
                else main_session_supervisor.find_codex_main_pid()
            )
            _emit(
                main_session_supervisor.start_supervisor(
                    base_url=args.base_url,
                    main_pid=main_pid,
                    run_id=args.run_id,
                    session_id=session_id,
                )
            )
            return 0

        if args.command == "doctor":
            _emit(client.health())
            return 0

        if args.command == "material" and args.material_command == "submit":
            if args.text_file.suffix.lower() in IMAGE_SUFFIXES:
                _emit(
                    {
                        "error": "unsupported_image_input",
                        "message": (
                            "Image and OCR input are not supported; provide a text "
                            "or Markdown file"
                        ),
                    },
                    stream=sys.stderr,
                )
                return 2
            try:
                payload = args.text_file.read_bytes()
                if _looks_like_image(payload):
                    _emit(
                        {
                            "error": "unsupported_image_input",
                            "message": (
                                "Image and OCR input are not supported; provide a text "
                                "or Markdown file"
                            ),
                        },
                        stream=sys.stderr,
                    )
                    return 2
                content = payload.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                _emit(
                    {"error": "material_unreadable", "message": str(exc)},
                    stream=sys.stderr,
                )
                return 2
            deck_id = client.create_deck(title=args.title, content=content)
            _emit(
                {
                    "deck_id": deck_id,
                    "status": "material_accepted",
                    "title": args.title,
                }
            )
            return 0

        if args.command == "split" and args.split_command == "propose":
            draft = client.create_split_draft(deck_id=args.deck_id, mode=args.mode)
            _emit(_split_projection(draft))
            return 0


        if args.command == "split" and args.split_command == "revise":
            draft = client.revise_split_draft(
                draft_id=args.draft_id,
                instruction=args.instruction,
            )
            _emit(_split_projection(draft))
            return 0
        if args.command == "split" and args.split_command == "confirm":
            _emit(client.confirm_split_draft(draft_id=args.draft_id))
            return 0

        if args.command == "generate":
            preference = (args.preference or "").strip()
            if args.intent == "preference" and not preference:
                _emit(
                    {
                        "error": "preference_required",
                        "message": "Preference intent requires --preference",
                    },
                    stream=sys.stderr,
                )
                return 2
            if args.intent == "auto" and preference:
                _emit(
                    {
                        "error": "auto_preference_conflict",
                        "message": "Auto intent does not accept --preference",
                    },
                    stream=sys.stderr,
                )
                return 2
            _emit(
                client.start_generation(
                    deck_id=args.deck_id,
                    intent=args.intent,
                    preference=preference or None,
                )
            )
            return 0

        if args.command == "status":
            try:
                interval = float(
                    os.environ.get("PPTGEN_STATUS_INTERVAL_SECONDS", "3")
                )
            except ValueError:
                interval = 3.0
            interval = max(0.01, interval)
            started = time.monotonic()
            previous_facts: dict[str, Any] | None = None
            activity_cursor: str | None = args.after_activity_cursor
            while True:
                run_status = client.get_run_status(
                    run_id=args.run_id,
                    activity_after=activity_cursor,
                )
                detail = client.get_run_detail(run_id=args.run_id)
                activities, next_cursor = _safe_activity_events(
                    run_status,
                    run_id=args.run_id,
                )
                for activity in activities:
                    _emit(activity)
                if next_cursor is not None:
                    activity_cursor = next_cursor
                provisional = _progress_projection(
                    run_id=args.run_id,
                    status=run_status,
                    detail=detail,
                    event="update",
                    follow_elapsed_seconds=time.monotonic() - started,
                )
                facts = provisional["source_facts"]
                provisional["event"] = (
                    "heartbeat" if facts == previous_facts else "update"
                )
                if provisional["event"] == "heartbeat":
                    provisional.update(
                        {
                            "current_activity": "The task is still running; no new business milestone yet.",
                            "kind": "heartbeat",
                            "milestone": False,
                        }
                    )
                _emit(provisional)
                previous_facts = facts
                if run_status["status"] not in {"queued", "pending", "running"}:
                    return 0
                time.sleep(interval)

        if args.command == "result":
            _emit(_result_projection(client.get_run_detail(run_id=args.run_id)))
            return 0
    except PlatformUnavailable as exc:
        _emit(
            {"error": "platform_unavailable", "message": str(exc)},
            stream=sys.stderr,
        )
        return 3
    except PlatformError as exc:
        _emit({"error": "platform_error", "message": str(exc)}, stream=sys.stderr)
        return 4
    except main_session_supervisor.MainSessionSupervisorError as exc:
        _emit(
            {"error": "main_session_supervisor_error", "message": str(exc)},
            stream=sys.stderr,
        )
        return 5

    _emit({"error": "unsupported_command"}, stream=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
