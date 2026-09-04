"""Command-line entrypoint for the fixed public Image PPT 3.0 workflow."""

from __future__ import annotations

import argparse
import base64
import html
import os
from pathlib import Path
import sys
import time
from typing import Any, Sequence
from urllib import error, parse, request

from . import cli as shared_cli
from .client import PlatformError, PlatformUnavailable
from .image_client import ImagePptgenClient
from .static_preview_bundle import BundlePage, write_static_preview_bundle


DEFAULT_BASE_URL = "http://127.0.0.1:3130"


def _emit(payload: dict[str, Any], *, stream: Any | None = None) -> None:
    shared_cli._emit(payload, stream=stream)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="image-pptgen")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("IMAGE_PPTGEN_BASE_URL", DEFAULT_BASE_URL),
        help="Public Image PPT 3.0 base URL",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser("doctor", help="Check public Image connectivity")
    doctor.add_argument("--json", action="store_true", required=True)

    material = subcommands.add_parser("material", help="Manage source material")
    material_commands = material.add_subparsers(dest="material_command", required=True)
    submit = material_commands.add_parser("submit", help="Submit text material")
    submit.add_argument("--title", required=True)
    submit.add_argument("--text-file", required=True, type=Path)
    submit.add_argument("--json", action="store_true", required=True)

    split = subcommands.add_parser("split", help="Review faithful page splitting")
    split_commands = split.add_subparsers(dest="split_command", required=True)
    propose = split_commands.add_parser("propose", help="Create a pending split")
    propose.add_argument("--deck-id", required=True, type=int)
    propose.add_argument("--json", action="store_true", required=True)
    revise = split_commands.add_parser("revise", help="Revise a pending split")
    revise.add_argument("--draft-id", required=True, type=int)
    revise_options = revise.add_mutually_exclusive_group(required=True)
    revise_options.add_argument("--instruction")
    revise_options.add_argument("--target-page-count", type=int)
    revise.add_argument("--json", action="store_true", required=True)
    confirm = split_commands.add_parser("confirm", help="Confirm a pending split")
    confirm.add_argument("--draft-id", required=True, type=int)
    confirm.add_argument("--json", action="store_true", required=True)

    generate = subcommands.add_parser(
        "generate", help="Start fixed Luna Image 3.0 generation"
    )
    generate.add_argument("--deck-id", required=True, type=int)
    generate.add_argument("--json", action="store_true", required=True)

    status = subcommands.add_parser("status", help="Follow Image generation progress")
    status.add_argument("--run-id", required=True, type=int)
    status.add_argument("--follow", action="store_true", required=True)
    status.add_argument("--jsonl", action="store_true", required=True)
    status.add_argument(
        "--after-activity-cursor",
        help="Resume safe activity after an invocation:sequence cursor",
    )

    result = subcommands.add_parser("result", help="Show Image generation results")
    result.add_argument("--run-id", required=True, type=int)
    result.add_argument(
        "--static-preview-file",
        type=Path,
        help="Write a standalone local HTML preview while the runtime is available",
    )
    result.add_argument("--json", action="store_true", required=True)
    return parser


def _split_projection(draft: dict[str, Any]) -> dict[str, Any]:
    # The public DTO deliberately omits the internal ``mode`` column.  Keep the
    # existing CLI's complete Markdown projection while supplying the one
    # server-owned mode value instead of requiring an internal field.
    slides = draft.get("slides")
    if not isinstance(slides, list):
        raise PlatformError("PPTGen Platform returned an invalid split draft")
    markdown = "\n\n".join(
        f"## Page {index}: {slide.get('title') or f'Page {index}'}\n\n"
        f"{str(slide.get('content') or '').strip()}"
        for index, slide in enumerate(slides, start=1)
        if isinstance(slide, dict)
    )
    return {
        "deck_id": draft["deck_id"],
        "draft_id": draft["id"],
        "markdown": markdown,
        "mode": "faithful",
        "model": str(draft.get("model") or ""),
        "attempt_count": int(draft.get("attempt_count") or 0),
        "page_count": len(slides),
        "status": draft.get("status"),
    }


def _read_material(path: Path) -> str:
    if path.suffix.lower() in shared_cli.IMAGE_SUFFIXES:
        raise ValueError("unsupported_image_input")
    payload = path.read_bytes()
    if shared_cli._looks_like_image(payload):
        raise ValueError("unsupported_image_input")
    return payload.decode("utf-8")


def _run_status_follow(client: ImagePptgenClient, args: argparse.Namespace) -> int:
    try:
        interval = float(os.environ.get("IMAGE_PPTGEN_STATUS_INTERVAL_SECONDS", "3"))
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
        activities, next_cursor = shared_cli._safe_activity_events(
            run_status,
            run_id=args.run_id,
        )
        for activity in activities:
            _emit(activity)
        if next_cursor is not None:
            activity_cursor = next_cursor
        provisional = shared_cli._progress_projection(
            run_id=args.run_id,
            status=run_status,
            detail=detail,
            event="update",
            follow_elapsed_seconds=time.monotonic() - started,
        )
        facts = provisional["source_facts"]
        provisional["event"] = "heartbeat" if facts == previous_facts else "update"
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


def _static_preview_os() -> str:
    return os.environ.get("IMAGE_PPTGEN_STATIC_PREVIEW_OS") or sys.platform


def _static_preview_enabled() -> bool:
    return _static_preview_os() in {"darwin", "linux", "win32"}


def _bundle_artifacts_root() -> Path:
    artifacts = os.environ.get("PPT_ARTIFACTS_DIR")
    if artifacts:
        return Path(artifacts).expanduser().resolve()
    data_root = os.environ.get("IMAGE_PPTGEN_DATA_ROOT")
    if data_root:
        return Path(data_root).expanduser().resolve() / "state" / "data" / "artifacts"
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser()
    return (data_home / "image-pptgen" / "state" / "data" / "artifacts").resolve()


def _encode_artifact_path(path: str) -> str:
    """Encode raw artifact path characters while preserving canonical ``%XX`` octets."""

    if not path.startswith("/artifacts/"):
        raise PlatformError("Image result has an invalid artifact path")
    remainder = path[len("/artifacts/") :]
    if any(part == ".." for part in remainder.split("/")):
        raise PlatformError("Image result has an invalid artifact path")
    encoded: list[str] = []
    index = 0
    while index < len(path):
        character = path[index]
        if character == "%":
            octet = path[index + 1 : index + 3]
            if len(octet) != 2 or any(
                digit not in "0123456789abcdefABCDEF" for digit in octet
            ):
                raise PlatformError("Image result has malformed percent-encoding")
            encoded.append("%" + octet.upper())
            index += 3
            continue
        encoded.append("/" if character == "/" else parse.quote(character, safe=""))
        index += 1
    return "".join(encoded)


def _fetch_png_bytes(base_url: str, png_path: str, *, timeout: float = 30.0) -> bytes:
    url = f"{base_url.rstrip('/')}{_encode_artifact_path(png_path)}"
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(
            request.Request(url, headers={"Accept": "image/png"}), timeout=timeout
        ) as response:
            image_bytes = response.read(50 * 1024 * 1024 + 1)
    except error.HTTPError as exc:
        raise PlatformError(
            f"PPTGen Platform returned HTTP {exc.code} for Image preview artifact"
        ) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise PlatformUnavailable("Cannot read an Image preview artifact") from exc
    if not image_bytes or len(image_bytes) > 50 * 1024 * 1024:
        raise PlatformError("Image preview artifact has an invalid size")
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PlatformError("Image preview artifact is not a PNG")
    return image_bytes


def _completed_run_bundle(
    result: dict[str, Any], *, base_url: str, run_id: int
) -> dict[str, Any]:
    if int(result.get("run_id") or 0) != int(run_id):
        raise PlatformError("Image result run_id does not match the requested run")
    slides = result.get("slides")
    if not isinstance(slides, list) or not slides:
        raise PlatformError("Image result has no slides for static preview")
    pages: list[BundlePage] = []
    for slide in slides:
        if not isinstance(slide, dict) or slide.get("status") != "completed":
            raise PlatformError("Image result is incomplete for static preview")
        png_path = slide.get("png_path")
        if not isinstance(png_path, str):
            raise PlatformError("Image result is missing a public PNG")
        try:
            position = int(slide.get("position"))
        except (TypeError, ValueError) as exc:
            raise PlatformError("Image result has an invalid slide position") from exc
        pages.append(
            BundlePage(
                position=position,
                image_bytes=_fetch_png_bytes(base_url, png_path),
                title=str(slide.get("title") or ""),
            )
        )
    try:
        written = write_static_preview_bundle(
            artifacts_root=_bundle_artifacts_root(),
            run_id=run_id,
            pages=pages,
        )
    except ValueError as exc:
        raise PlatformError(str(exc)) from exc
    result["preview_url"] = written.paths.viewer_path.resolve().as_uri()
    result["download_url"] = written.paths.zip_path.resolve().as_uri()
    result["static_preview"] = {
        "kind": written.manifest["kind"],
        "manifest_version": written.manifest["manifest_version"],
        "run_id": written.run_id,
        "viewer_path": str(written.paths.viewer_path.resolve()),
        "zip_path": str(written.paths.zip_path.resolve()),
        "page_count": written.manifest["page_count"],
    }
    return result


def _result_with_public_urls(
    detail: dict[str, Any], *, base_url: str, run_id: int
) -> dict[str, Any]:
    result = shared_cli._result_projection(detail)
    # The shared projection has an HTML-compatible key for the legacy client.
    # Image output must not advertise an HTML artifact, even as a null field.
    for slide in result.get("slides", []):
        if isinstance(slide, dict):
            slide.pop("html_path", None)
    if _static_preview_enabled() and result.get("status") == "completed":
        return _completed_run_bundle(result, base_url=base_url, run_id=run_id)
    root = base_url.rstrip("/")
    result["preview_url"] = f"{root}/history/run/{run_id}/preview"
    result["download_url"] = f"{root}/api/runs/{run_id}/download"
    return result


def _write_static_preview(
    result: dict[str, Any], *, base_url: str, output_path: Path
) -> Path:
    root = base_url.rstrip("/")
    slides = result.get("slides")
    if not isinstance(slides, list) or not slides:
        raise PlatformError("Image result has no slides for static preview")
    cards: list[str] = []
    opener = request.build_opener(request.ProxyHandler({}))
    for slide in slides:
        if not isinstance(slide, dict) or slide.get("status") != "completed":
            raise PlatformError("Image result is incomplete for static preview")
        png_path = slide.get("png_path")
        if not isinstance(png_path, str) or not png_path.startswith("/artifacts/"):
            raise PlatformError("Image result has an invalid artifact path")
        try:
            encoded_path = parse.quote(png_path, safe="/%")
            with opener.open(f"{root}{encoded_path}", timeout=30) as response:
                image_bytes = response.read(50 * 1024 * 1024 + 1)
        except (error.URLError, TimeoutError, OSError) as exc:
            raise PlatformUnavailable("Cannot read an Image preview artifact") from exc
        if not image_bytes or len(image_bytes) > 50 * 1024 * 1024:
            raise PlatformError("Image preview artifact has an invalid size")
        title = html.escape(str(slide.get("title") or ""))
        encoded = base64.b64encode(image_bytes).decode("ascii")
        cards.append(
            f'<figure><img src="data:image/png;base64,{encoded}" alt="{title}">'
            f"<figcaption>{title}</figcaption></figure>"
        )
    document = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Image PPTGen Preview</title><style>"
        "body{margin:0;background:#111;color:#eee;font:14px system-ui,sans-serif}"
        "main{max-width:1440px;margin:auto;padding:24px}figure{margin:0 0 28px}"
        "img{display:block;width:100%;height:auto;border-radius:8px;box-shadow:0 8px 32px #0008}"
        "figcaption{padding:8px 2px;color:#bbb}</style></head><body><main>"
        + "".join(cards)
        + "</main></body></html>"
    )
    destination = output_path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(destination)
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    client = ImagePptgenClient(args.base_url)
    try:
        if args.command == "doctor":
            _emit(client.health())
            return 0

        if args.command == "material" and args.material_command == "submit":
            try:
                content = _read_material(args.text_file)
            except ValueError as exc:
                if str(exc) == "unsupported_image_input":
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
                else:  # pragma: no cover - kept for future local validation codes
                    _emit({"error": str(exc), "message": str(exc)}, stream=sys.stderr)
                return 2
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
            _emit(_split_projection(client.create_split_draft(deck_id=args.deck_id)))
            return 0

        if args.command == "split" and args.split_command == "revise":
            _emit(
                _split_projection(
                    client.revise_split_draft(
                        draft_id=args.draft_id,
                        instruction=args.instruction,
                        target_page_count=args.target_page_count,
                    )
                )
            )
            return 0

        if args.command == "split" and args.split_command == "confirm":
            _emit(client.confirm_split_draft(draft_id=args.draft_id))
            return 0

        if args.command == "generate":
            _emit(client.start_generation(deck_id=args.deck_id))
            return 0

        if args.command == "status":
            return _run_status_follow(client, args)

        if args.command == "result":
            detail = client.get_run_detail(run_id=args.run_id)
            result = _result_with_public_urls(
                detail,
                base_url=args.base_url,
                run_id=args.run_id,
            )
            if args.static_preview_file is not None:
                preview_path = _write_static_preview(
                    result,
                    base_url=args.base_url,
                    output_path=args.static_preview_file,
                )
                result["static_preview_path"] = str(preview_path)
                result["static_preview_url"] = preview_path.as_uri()
            _emit(
                result
            )
            return 0
    except PlatformUnavailable as exc:
        _emit(
            {"error": "platform_unavailable", "message": str(exc)},
            stream=sys.stderr,
        )
        return 3
    except PlatformError as exc:
        _emit(
            {
                "error": getattr(exc, "code", None) or "platform_error",
                "message": str(exc),
            },
            stream=sys.stderr,
        )
        return 4

    _emit({"error": "unsupported_command"}, stream=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
