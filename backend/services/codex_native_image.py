"""Private audit, call binding, and normalization helpers for Codex Native images."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import config
import PIL
from PIL import Image, ImageFilter

from backend.services import codex_audit
from backend.services.codex_child_recovery import run_supervised_codex_child
from backend.services.codex_exec import run_codex_async_from_sync, run_codex_exec_json
from backend.services.codex_jsonl_stream import SOURCE_WINDOW_BYTES, iter_bounded_jsonl_file_records
from backend.services.private_file_permissions import restrict_owner_only_fd


NOT_APPLICABLE = "not_applicable"
NATIVE_IMAGE_ALGORITHM = "native_slide_16x9_v1"
TARGET_IMAGE_SIZE = (1920, 1080)
ASPECT_TOLERANCE = 0.02
REQUIRED_PILLOW_VERSION = "12.1.1"
_RUN_SEGMENT = re.compile(r"run-([1-9][0-9]*)$")
_SLIDE_SEGMENT = re.compile(r"slide-([1-9][0-9]*)$")
_ATTEMPT_SEGMENT = re.compile(r"attempt-([1-9][0-9]*)$")
_THREAD_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
THREAD_SCOPED_GENERATED_IMAGE_PROTOCOL = "thread_scoped_generated_image_v1"


def _native_one_shot_transport_prompt(business_prompt: str, *, has_attached_references: bool = False) -> str:
    """Keep the business prompt verbatim inside the auditable Native child input."""
    attached_reference_contract = (
        "- The approved reference images are already attached to this Codex conversation through --image; use "
        "num_last_images_to_include with the smallest count that includes every attached conversation image in the one "
        "imagegen call.\n"
        "- Do not use referenced_image_paths to reopen any already attached reference images from the local filesystem.\n"
        if has_attached_references
        else ""
    )
    return (
        "Native one-shot execution contract:\n"
        "- Make exactly one imagegen call for this page.\n"
        "- Do not regenerate, retry, or make any additional imagegen calls.\n"
        f"{attached_reference_contract}"
        "- Stop after that one imagegen call.\n\n"
        "# Original business slide prompt (verbatim)\n"
        f"{business_prompt}"
    )


def _resolve_active_image_generator_prompt() -> tuple[str, dict[str, object]]:
    """Resolve the current runtime Prompt immediately before Native child setup."""
    # Keep this import at the call boundary: db initializes the current runtime
    # connection, while this adapter is also imported by pipeline startup.
    import db as dbmod

    prompt = dbmod.get_active_prompt("image_generator")
    if not isinstance(prompt, dict):
        raise ValueError("active image_generator Prompt is required")
    prompt_id = prompt.get("id")
    prompt_role = prompt.get("agent_type")
    prompt_content = prompt.get("content")
    if (
        prompt_role != "image_generator"
        or not isinstance(prompt_id, int)
        or prompt_id <= 0
        or not isinstance(prompt_content, str)
        or not prompt_content
    ):
        raise ValueError("active image_generator Prompt is required")
    return prompt_content, {
        "role": prompt_role,
        "prompt_id": prompt_id,
        "prompt_content_sha256": hashlib.sha256(prompt_content.encode("utf-8")).hexdigest(),
    }


def _native_image_generator_transport_prompt(
    *,
    prompt_content: str,
    business_prompt: str,
    has_attached_references: bool,
) -> str:
    """Bind the selected system-managed Prompt to the existing one-shot input."""
    return (
        "# Active system-managed image_generator Prompt\n"
        f"{prompt_content}\n\n"
        f"{_native_one_shot_transport_prompt(business_prompt, has_attached_references=has_attached_references)}"
    )


class NativePrivatePathError(ValueError):
    """Raised when a Native runner path escapes its Run-private boundary."""


class CommonNativeAuditError(ValueError):
    """A common-audit failure carrying the private metadata to re-finalize."""

    def __init__(self, message: str, evidence: dict[str, object]):
        super().__init__(message)
        self.evidence = evidence


@dataclass(frozen=True)
class ThreadOutputBaseline:
    """The one exact output directory observed when Codex announces a thread."""

    codex_home: Path
    thread_id: str
    thread_directory: Path
    initial_state: str
    initial_error: str | None = None


def _as_resolved_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _native_private_root_from_dir(*, private_dir: str | Path, artifacts_root: str | Path) -> Path:
    """Validate the sole Native runner directory shape under ``artifacts_root``."""
    supplied = Path(private_dir).expanduser()
    if ".." in supplied.parts:
        raise NativePrivatePathError("Native runner artifact directory cannot contain traversal segments")
    resolved = supplied.resolve()
    root = _as_resolved_path(artifacts_root)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise NativePrivatePathError(
            "Native runner artifact directory is outside the configured artifacts root"
        ) from exc

    parts = relative.parts
    if len(parts) != 6:
        raise NativePrivatePathError("Native runner artifact directory must have the exact Run/Slide/Stage/Attempt shape")
    private_segment, native_segment, run_segment, slide_segment, stage_segment, attempt_segment = parts
    if private_segment != ".codex-private" or native_segment != "native-image":
        raise NativePrivatePathError("Native runner artifact directory is outside a Run-private native-image root")
    if _RUN_SEGMENT.fullmatch(run_segment) is None:
        raise NativePrivatePathError("Native runner artifact directory requires a positive Run id")
    if _SLIDE_SEGMENT.fullmatch(slide_segment) is None:
        raise NativePrivatePathError("Native runner artifact directory requires a positive Slide id")
    if not stage_segment or stage_segment in {".", ".."} or Path(stage_segment).name != stage_segment:
        raise NativePrivatePathError("Native runner stage id must be a single path segment")
    if _ATTEMPT_SEGMENT.fullmatch(attempt_segment) is None:
        raise NativePrivatePathError("Native runner artifact directory requires a positive attempt id")
    return root / private_segment / native_segment / run_segment


def native_runner_artifact_dir(
    *,
    artifacts_root: str | Path,
    run_id: int,
    run_slide_id: int,
    stage_id: str,
    attempt: int,
) -> Path:
    """Build the sole valid private runner directory for one attempted stage."""
    try:
        normalized_run_id = int(run_id)
        normalized_slide_id = int(run_slide_id)
        normalized_attempt = int(attempt)
    except (TypeError, ValueError) as exc:
        raise NativePrivatePathError("Native runner identity requires numeric Run, Slide, and attempt values") from exc
    if normalized_run_id <= 0 or normalized_slide_id <= 0 or normalized_attempt <= 0:
        raise NativePrivatePathError("Native runner identity requires positive Run, Slide, and attempt values")
    if not isinstance(stage_id, str) or not stage_id or Path(stage_id).name != stage_id or stage_id in {".", ".."}:
        raise NativePrivatePathError("Native runner stage id must be a single path segment")
    root = _as_resolved_path(artifacts_root)
    private_dir = (
        root
        / ".codex-private"
        / "native-image"
        / f"run-{normalized_run_id}"
        / f"slide-{normalized_slide_id}"
        / stage_id
        / f"attempt-{normalized_attempt}"
    )
    _native_private_root_from_dir(private_dir=private_dir, artifacts_root=root)
    return private_dir


def _thread_id_from_stdout(stdout_events: Iterable[object]) -> str:
    thread_ids = [
        event.get("thread_id")
        for event in stdout_events
        if isinstance(event, dict) and event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str)
    ]
    thread_ids = [thread_id for thread_id in thread_ids if thread_id]
    if len(thread_ids) != 1:
        raise ValueError("Codex stdout must contain exactly one thread.started thread id")
    return thread_ids[0]


def _stdout_events_from_stream_summary(result: object) -> list[dict[str, str]]:
    """Provide Native's identity input from the bounded exec projection, never raw JSONL."""
    summary = getattr(result, "stream_summary", None)
    projections = tuple(getattr(summary, "event_projections", ()) or ())
    if projections:
        events: list[dict[str, str]] = []
        for projection in projections:
            if not isinstance(projection, dict):
                continue
            event_type = projection.get("event_type")
            if not isinstance(event_type, str) or not event_type:
                continue
            event: dict[str, str] = {"type": event_type}
            thread_id = projection.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                event["thread_id"] = thread_id
            item_type = projection.get("item_type")
            if isinstance(item_type, str) and item_type:
                event["item_type"] = item_type
            events.append(event)
        if getattr(summary, "omitted_event_projection_count", 0):
            events.append({"type": "codex.events_omitted"})
        if events:
            return events
    thread_ids = tuple(getattr(summary, "thread_ids", ()) or ())
    if not thread_ids:
        thread_id = getattr(summary, "thread_id", None)
        thread_ids = (thread_id,) if isinstance(thread_id, str) and thread_id else ()
    return [{"type": "thread.started", "thread_id": thread_id} for thread_id in thread_ids]


def _jsonl_records(path: Path) -> Iterable[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("canonical Codex session cannot be read") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"canonical Codex session has invalid JSONL at line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"canonical Codex session line {line_number} is not an object")
        yield value


def _session_declares_thread(path: Path, thread_id: str) -> bool:
    """Validate just the bounded first session record, never its body history."""
    try:
        first = next(iter_bounded_jsonl_file_records(path), None)
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if first is None or first.event is None:
        return False
    payload = first.event.get("payload")
    return first.event.get("type") == "session_meta" and isinstance(payload, dict) and payload.get("id") == thread_id


def _uuid7_day_directories(
    *,
    sessions_root: Path,
    thread_id: str,
) -> tuple[Path, ...] | None:
    """Return the bounded UTC/local session days derivable from a UUIDv7."""
    compact = thread_id.replace("-", "")
    if (
        len(compact) != 32
        or thread_id[8:9] != "-"
        or thread_id[13:14] != "-"
        or thread_id[18:19] != "-"
        or thread_id[23:24] != "-"
        or compact[12:13].lower() != "7"
        or any(char not in "0123456789abcdefABCDEF" for char in compact)
    ):
        return None
    milliseconds = int(compact[:12], 16)
    utc_instant = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    days: list[Path] = []
    for instant in (utc_instant, utc_instant.astimezone()):
        day = (
            sessions_root
            / instant.strftime("%Y")
            / instant.strftime("%m")
            / instant.strftime("%d")
        )
        if day not in days:
            days.append(day)
    return tuple(days)


def find_canonical_session(*, codex_home: str | Path, thread_id: str) -> Path:
    """Find exactly the session whose recorded ID equals stdout's thread ID.

    This intentionally never selects a newest session file or uses an image
    directory timestamp as a proxy for conversation identity.
    """
    sessions_root = _as_resolved_path(codex_home) / "sessions"
    if not sessions_root.is_dir():
        raise ValueError("Codex sessions root is unavailable")
    day_directories = _uuid7_day_directories(
        sessions_root=sessions_root,
        thread_id=thread_id,
    )
    # Codex names session directories from host-local time while UUIDv7 carries
    # a UTC instant.  At a date boundary those are two distinct, fully derived
    # days.  The bounded fixed-depth branch preserves the pre-UUID
    # fixture/legacy contract without reintroducing recursive scans or
    # session-body searching.
    candidate_roots = (
        day_directories
        if day_directories is not None
        else (sessions_root,)
    )
    candidate_pattern = (
        f"rollout-*-{thread_id}.jsonl"
        if day_directories is not None
        else f"*/*/*/rollout-*-{thread_id}.jsonl"
    )
    candidates = [
        path
        for candidate_root in candidate_roots
        for path in candidate_root.glob(candidate_pattern)
        if path.is_file() and _session_declares_thread(path, thread_id)
    ]
    if len(candidates) != 1:
        raise ValueError("exact stdout thread id must resolve exactly one canonical Codex session")
    return candidates[0]


def _archive_canonical_session(*, source: Path, private_dir: Path) -> dict[str, object]:
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive_path = private_dir / "canonical-session.jsonl"
    temporary_path = private_dir / f".canonical-session-{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    byte_count = 0
    try:
        temporary_descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            restrict_owner_only_fd(temporary_descriptor)
            with source.open("rb") as source_handle, os.fdopen(temporary_descriptor, "wb", closefd=False) as handle:
                while chunk := source_handle.read(SOURCE_WINDOW_BYTES):
                    handle.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(temporary_descriptor)
        os.replace(temporary_path, archive_path)
        os.chmod(archive_path, 0o600)
        _fsync_directory(private_dir)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {
        "source_path": str(source),
        "archive_path": str(archive_path),
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
        "source_identity": _file_identity(source),
        "archive_identity": _file_identity(archive_path),
    }


def _fsync_directory(path: Path) -> None:
    """Persist a POSIX rename; Windows has no portable directory fsync."""

    # The canonical session file itself is flushed and fsynced before the
    # atomic replace above.  Python maps ``os.fsync`` to the Microsoft CRT
    # ``_commit`` operation on Windows, where an ordinary ``os.open`` call is
    # not a portable way to obtain a flushable directory handle.  Keep the
    # established POSIX durability step and its fail-closed error propagation.
    if os.name == "nt":
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _file_identity(path: Path) -> str | None:
    """Record the physical file identity when the owned filesystem exposes it."""
    try:
        file_stat = path.stat()
    except OSError:
        return None
    return f"{file_stat.st_dev}:{file_stat.st_ino}"


def _validated_thread_segment(thread_id: object) -> str:
    if not isinstance(thread_id, str) or _THREAD_SEGMENT.fullmatch(thread_id) is None:
        raise ValueError("Codex thread id is not a safe generated-image path segment")
    return thread_id


def _is_reparse_stat(path_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(path_stat, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _ordinary_lstat(path: Path, *, kind: str) -> os.stat_result:
    """Reject links and platform reparse objects before trusting a path member."""
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise ValueError(f"thread-scoped image {kind} cannot be inspected") from exc
    if stat.S_ISLNK(path_stat.st_mode) or _is_reparse_stat(path_stat):
        raise ValueError(f"thread-scoped image {kind} cannot be a link or reparse point")
    if kind == "directory" and not stat.S_ISDIR(path_stat.st_mode):
        raise ValueError("thread-scoped image directory is not a directory")
    if kind == "file":
        if not stat.S_ISREG(path_stat.st_mode):
            raise ValueError("thread-scoped image candidate is not a regular file")
        if path_stat.st_nlink != 1:
            raise ValueError("thread-scoped image candidate must not be hard-linked")
    return path_stat


def _thread_output_path(codex_home: str | Path, thread_id: object) -> tuple[Path, Path, Path, str]:
    safe_thread_id = _validated_thread_segment(thread_id)
    home = Path(codex_home).expanduser()
    generated_root = home / "generated_images"
    return home, generated_root, generated_root / safe_thread_id, safe_thread_id


def _capture_thread_output_baseline(*, codex_home: str | Path, thread_id: str) -> ThreadOutputBaseline:
    """Record only one exact directory before later Codex tool output can arrive.

    A legacy event binding does not consume this object, so a missing or unsafe
    generated-image directory must not broaden or replace the legacy contract.
    The fallback consumes only an absent or empty exact directory baseline.
    """
    home, _generated_root, thread_directory, safe_thread_id = _thread_output_path(codex_home, thread_id)
    try:
        thread_directory.lstat()
    except FileNotFoundError:
        return ThreadOutputBaseline(home, safe_thread_id, thread_directory, "absent")
    try:
        _ordinary_lstat(thread_directory, kind="directory")
        entries = list(thread_directory.iterdir())
    except (OSError, ValueError) as exc:
        return ThreadOutputBaseline(home, safe_thread_id, thread_directory, "invalid", str(exc))
    if entries:
        return ThreadOutputBaseline(
            home,
            safe_thread_id,
            thread_directory,
            "invalid",
            "thread-scoped generated-image directory was not empty at baseline",
        )
    return ThreadOutputBaseline(home, safe_thread_id, thread_directory, "empty")


def _validated_thread_output_directory(baseline: ThreadOutputBaseline) -> Path:
    """Return the exact, non-link directory allowed by the fallback protocol."""
    if baseline.initial_state not in {"absent", "empty"}:
        raise ValueError(baseline.initial_error or "thread-scoped output baseline is not usable")
    home, generated_root, thread_directory, thread_id = _thread_output_path(
        baseline.codex_home, baseline.thread_id
    )
    if thread_directory != baseline.thread_directory:
        raise ValueError("thread-scoped output baseline path changed")
    _ordinary_lstat(home, kind="directory")
    _ordinary_lstat(generated_root, kind="directory")
    _ordinary_lstat(thread_directory, kind="directory")
    try:
        resolved_home = home.resolve(strict=True)
        resolved_generated_root = generated_root.resolve(strict=True)
        resolved_thread_directory = thread_directory.resolve(strict=True)
        if resolved_generated_root.parent != resolved_home:
            raise ValueError("generated-images root is outside the configured Codex home")
        if resolved_thread_directory.parent != resolved_generated_root or resolved_thread_directory.name != thread_id:
            raise ValueError("thread-scoped output directory escaped its trusted root")
    except OSError as exc:
        raise ValueError("thread-scoped output paths cannot be resolved") from exc
    return thread_directory


def _stable_thread_scoped_png(path: Path) -> dict[str, object]:
    """Read one candidate twice and reject replacement, links, and mutation."""
    before = _ordinary_lstat(path, kind="file")
    first = _image_record(path)
    middle = _ordinary_lstat(path, kind="file")
    second = _image_record(path)
    after = _ordinary_lstat(path, kind="file")
    stat_identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        getattr(value, "st_mtime_ns", None),
        getattr(value, "st_ctime_ns", None),
    )
    if stat_identity(before) != stat_identity(middle) or stat_identity(middle) != stat_identity(after):
        raise ValueError("thread-scoped PNG changed while it was being validated")
    if first != second:
        raise ValueError("thread-scoped PNG content changed while it was being validated")
    return second


def _thread_scoped_image_details(
    *,
    baseline: ThreadOutputBaseline | None,
    stdout_events: Iterable[object],
    execution_exit_code: object,
    execution_timed_out: object,
) -> tuple[dict[str, object], Path]:
    """Bind a CLI PNG only through its single invocation-owned thread output."""
    if baseline is None:
        raise ValueError("canonical session requires exactly one completed image_generation_end event or a thread-scoped output baseline")
    thread_id = _thread_id_from_stdout(stdout_events)
    if thread_id != baseline.thread_id:
        raise ValueError("thread-scoped output baseline does not match the Codex thread")
    if execution_exit_code != 0 or execution_timed_out is not False:
        raise ValueError("thread-scoped image output requires a successful non-timeout Codex process")
    events = [event for event in stdout_events if isinstance(event, dict)]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if len(completed) != 1:
        raise ValueError("thread-scoped image output requires exactly one turn.completed event")
    if any(
        event.get("type") == "error" or event.get("item_type") == "error" or event.get("type") == "codex.events_omitted"
        for event in events
    ):
        raise ValueError("thread-scoped image output requires an error-free complete stdout projection")

    thread_directory = _validated_thread_output_directory(baseline)
    try:
        entries = list(thread_directory.iterdir())
    except OSError as exc:
        raise ValueError("thread-scoped output directory cannot be enumerated") from exc
    if len(entries) != 1:
        raise ValueError("thread-scoped image output requires exactly one new file")
    candidate = entries[0]
    _ordinary_lstat(candidate, kind="file")
    if candidate.suffix.lower() != ".png":
        raise ValueError("thread-scoped image output candidate is not a PNG file")
    try:
        resolved_candidate = candidate.resolve(strict=True)
        resolved_directory = thread_directory.resolve(strict=True)
        if resolved_candidate.parent != resolved_directory:
            raise ValueError("thread-scoped image candidate escaped its trusted output directory")
    except OSError as exc:
        raise ValueError("thread-scoped image candidate cannot be resolved") from exc
    image = _stable_thread_scoped_png(candidate)
    return (
        {
            "image_output_protocol": THREAD_SCOPED_GENERATED_IMAGE_PROTOCOL,
            "imagegen_call_id": NOT_APPLICABLE,
            "imagegen_call_arguments_sha256": NOT_APPLICABLE,
            "imagegen_input": NOT_APPLICABLE,
            "thread_scoped_generated_image": {
                "protocol": THREAD_SCOPED_GENERATED_IMAGE_PROTOCOL,
                "thread_id": thread_id,
                "source_directory": str(thread_directory),
                "source_path": str(candidate),
                "baseline_state": baseline.initial_state,
                "image": image,
            },
        },
        candidate,
    )


def _private_manifest(
    private_root: Path,
    canonical_session: dict[str, object],
    original_image: dict[str, object] | None = None,
    raw_path: Path | None = None,
    observed_path: Path | None = None,
) -> dict[str, object]:
    archive_path = canonical_session.get("archive_path")
    source_path = canonical_session.get("source_path")
    files = [archive_path] if isinstance(archive_path, str) else []
    original_path = original_image.get("path") if isinstance(original_image, dict) else None
    if isinstance(original_path, str):
        files.append(original_path)
    sources = [source_path] if isinstance(source_path, str) else []
    typed_files: dict[str, object] = {}
    if isinstance(archive_path, str):
        typed_files["canonical_session"] = archive_path
    if isinstance(original_path, str):
        typed_files["immutable_original"] = original_path
    if raw_path is not None:
        typed_files["raw"] = str(raw_path)
    if observed_path is not None:
        typed_files["observed"] = str(observed_path)
    identities = {
        "canonical_source": canonical_session.get("source_identity"),
        "canonical_archive": canonical_session.get("archive_identity"),
        "raw": _file_identity(raw_path) if raw_path is not None else None,
        "observed": _file_identity(observed_path) if observed_path is not None else None,
    }
    return {
        "private_root": str(private_root),
        "files": files,
        "typed_files": typed_files,
        "canonical_source_paths": sources,
        "identities": identities,
    }


def _safe_common_projection(evidence: dict[str, object]) -> dict[str, object]:
    """Expose only stable non-path, non-thread audit facts to caller projections."""
    return codex_audit.public_native_image_projection(evidence)


def _status_for_terminal_state(terminal_state: object) -> str:
    if terminal_state in {"result_received", "completed"}:
        return "result_received"
    if terminal_state == "timed_out":
        return "timed_out"
    if terminal_state == "skipped":
        return "skipped"
    return "failed"


def re_finalize_native_invocation(invocation_id: int, metadata: dict[str, object]) -> int:
    """Finalize the existing durable invocation with private common-audit metadata."""
    try:
        current = codex_audit.get_codex_invocation(invocation_id)
    except ValueError as exc:
        raise ValueError(f"cannot re-finalize missing Native Codex invocation {invocation_id}") from exc
    terminal_state = metadata.get("terminal_state")
    failure_code = metadata.get("failure_code")
    safe_error = str(failure_code) if failure_code else None
    return codex_audit.finalize_codex_invocation(
        invocation_id=invocation_id,
        output_path=current.get("output_path"),
        ended_at=current.get("ended_at"),
        elapsed_ms=current.get("elapsed_ms"),
        exit_code=current.get("exit_code"),
        status=_status_for_terminal_state(terminal_state),
        error_message=safe_error,
        metadata=metadata,
    )


def _re_finalize(
    *,
    invocation_id: int | None,
    metadata: dict[str, object],
    refinalize: Callable[[int, dict[str, object]], object] | None,
) -> None:
    if invocation_id is None:
        return
    if refinalize is not None:
        refinalize(invocation_id, metadata)
        return
    re_finalize_native_invocation(invocation_id, metadata)


def _failure_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "thread" in message or "canonical codex session" in message or "sessions root" in message:
        return "canonical_session_unavailable"
    if "private" in message or "run-private" in message or "native runner artifact directory" in message:
        return "private_evidence_path_invalid"
    return "common_audit_failed"


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Atomically replace one private or business image without partial output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the sibling temp name independent of the destination basename so a
    # legal NAME_MAX business PNG cannot make the hidden replacement too long.
    temporary_path = path.parent / f".pptgen-atomic-{uuid.uuid4().hex}.tmp"
    try:
        with temporary_path.open("xb") as handle:
            handle.write(payload)
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        os.chmod(path, mode)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_immutable_original(path: Path, payload: bytes) -> None:
    """Atomically create a Native original once without ever replacing one."""
    def _verify_existing() -> None:
        if path.is_symlink() or not path.is_file():
            raise ValueError("immutable Native original must be a regular file")
        if path.read_bytes() != payload:
            raise ValueError("immutable Native original already exists with different bytes")

    if path.exists() or path.is_symlink():
        _verify_existing()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.parent / f".{path.name}-{uuid.uuid4().hex}.tmp"
    try:
        with temporary_path.open("xb") as handle:
            handle.write(payload)
        os.chmod(temporary_path, 0o600)
        try:
            # link(2) creates the final name atomically and fails if a competing
            # writer has already created it; unlike replace(2), it cannot clobber
            # immutable evidence.
            os.link(temporary_path, path)
        except FileExistsError:
            _verify_existing()
        else:
            os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _image_record(path: Path) -> dict[str, object]:
    """Validate an exact PNG and return its full private content record."""
    try:
        payload = path.read_bytes()
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG":
                raise ValueError("event-selected image is not a PNG")
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.format != "PNG":
                raise ValueError("event-selected image is not a PNG")
            width, height = image.size
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("event-selected saved path does not contain a valid PNG") from exc
    if width <= 0 or height <= 0:
        raise ValueError("event-selected saved path does not contain a valid PNG")
    return {
        "path": str(path),
        "png_valid": True,
        "width": width,
        "height": height,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _legacy_image_end_events(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record["payload"]
        for record in records
        if record.get("type") == "event_msg"
        and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") == "image_generation_end"
    ]


def _completed_image_end(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    completed = [event for event in _legacy_image_end_events(records) if event.get("status") == "completed"]
    if len(completed) != 1:
        raise ValueError("canonical session requires exactly one completed image_generation_end event")
    end_event = completed[0]
    for key in ("call_id", "revised_prompt", "saved_path"):
        value = end_event.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"completed image_generation_end event is missing {key}")
    return end_event


def _is_sol_model(actual_model: object, requested_model: object) -> bool:
    values = (actual_model, requested_model)
    return any(isinstance(value, str) and "sol" in value.lower() for value in values)


def _call_bound_image_details(
    *,
    canonical_session: Path,
    requested_model: object,
    actual_model: object,
) -> tuple[dict[str, object], Path]:
    """Bind the event-selected PNG to Luna or Sol's canonical call form."""
    records = list(_jsonl_records(canonical_session))
    end_event = _completed_image_end(records)
    call_id = end_event["call_id"]
    revised_prompt = end_event["revised_prompt"]
    saved_path = Path(end_event["saved_path"]).expanduser().resolve()
    revised_prompt_sha256 = hashlib.sha256(revised_prompt.encode("utf-8")).hexdigest()

    if _is_sol_model(actual_model, requested_model):
        calls = [
            record["payload"]
            for record in records
            if record.get("type") == "response_item"
            and isinstance(record.get("payload"), dict)
            and record["payload"].get("type") == "function_call"
            and record["payload"].get("name") == "imagegen"
        ]
        if len(calls) != 1:
            raise ValueError("Sol canonical session requires exactly one imagegen function call")
        call = calls[0]
        arguments = call.get("arguments")
        if not isinstance(arguments, str):
            raise ValueError("Sol imagegen function call is missing literal arguments")
        if call.get("call_id") != call_id:
            raise ValueError("Sol imagegen function call does not match completed end-event call id")
        arguments_sha256 = hashlib.sha256(arguments.encode("utf-8")).hexdigest()
        imagegen_input = {
            "source": "function_call.arguments",
            "sha256": arguments_sha256,
            "revised_prompt_sha256": revised_prompt_sha256,
        }
    else:
        arguments_sha256 = NOT_APPLICABLE
        imagegen_input = {
            "source": "image_generation_end.revised_prompt",
            "sha256": revised_prompt_sha256,
            "revised_prompt_sha256": revised_prompt_sha256,
        }

    return (
        {
            "imagegen_call_id": call_id,
            "imagegen_call_arguments_sha256": arguments_sha256,
            "imagegen_input": imagegen_input,
        },
        saved_path,
    )


def _business_output_path(value: object, *, artifacts_root: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("business_output_path is required")
    supplied = Path(value).expanduser()
    if ".." in supplied.parts:
        raise ValueError("business_output_path cannot contain traversal segments")
    resolved = supplied.resolve()
    root = _as_resolved_path(artifacts_root)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("business_output_path must resolve inside the configured artifacts root") from exc
    if ".codex-private" in relative.parts:
        raise ValueError("business_output_path cannot resolve inside .codex-private")
    return resolved


def _remove_stale_business_output(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
    except OSError:
        # Do not turn an evidence failure into a false success; the caller will
        # retain the terminal failure fact even if a filesystem permission blocks cleanup.
        pass


def normalize_native_image(*, source_path: str | Path, output_path: str | Path) -> None:
    """Run the frozen ``native_slide_16x9_v1`` composite atomically."""
    if PIL.__version__ != REQUIRED_PILLOW_VERSION:
        raise RuntimeError(f"requires Pillow {REQUIRED_PILLOW_VERSION}, found {PIL.__version__}")
    source = Path(source_path)
    destination = Path(output_path)
    with Image.open(source) as raw:
        raw.load()
        foreground = raw.convert("RGBA")
    cover_scale = max(TARGET_IMAGE_SIZE[0] / foreground.width, TARGET_IMAGE_SIZE[1] / foreground.height)
    cover = foreground.resize(
        (round(foreground.width * cover_scale), round(foreground.height * cover_scale)),
        Image.Resampling.LANCZOS,
    )
    cover_left = (cover.width - TARGET_IMAGE_SIZE[0]) // 2
    cover_top = (cover.height - TARGET_IMAGE_SIZE[1]) // 2
    background = cover.crop(
        (cover_left, cover_top, cover_left + TARGET_IMAGE_SIZE[0], cover_top + TARGET_IMAGE_SIZE[1])
    ).filter(ImageFilter.GaussianBlur(radius=36))
    fit_scale = min(TARGET_IMAGE_SIZE[0] / foreground.width, TARGET_IMAGE_SIZE[1] / foreground.height)
    fitted = foreground.resize(
        (round(foreground.width * fit_scale), round(foreground.height * fit_scale)),
        Image.Resampling.LANCZOS,
    )
    offset = ((TARGET_IMAGE_SIZE[0] - fitted.width) // 2, (TARGET_IMAGE_SIZE[1] - fitted.height) // 2)
    background.alpha_composite(fitted, offset)
    encoded = io.BytesIO()
    background.convert("RGB").save(encoded, format="PNG")
    _atomic_write_bytes(destination, encoded.getvalue(), mode=0o600)


def _normalization_record(*, parent: dict[str, object], child: dict[str, object], normalized: bool) -> dict[str, object]:
    parent_dimensions = {"width": parent["width"], "height": parent["height"]}
    child_dimensions = {"width": child["width"], "height": child["height"]}
    derivation = {
        "parent_dimensions": parent_dimensions,
        "child_dimensions": child_dimensions,
        "parent_bytes": parent["bytes"],
        "child_bytes": child["bytes"],
        "parent_sha256": parent["sha256"],
        "child_sha256": child["sha256"],
    }
    if normalized:
        derivation.update(
            {
                "background": "blurred_cover",
                "foreground": "uncropped_centered_aspect_fit",
            }
        )
        return {
            "normalized": True,
            "algorithm": NATIVE_IMAGE_ALGORITHM,
            "operation": "blurred_cover_background_plus_uncropped_centered_aspect_fit_foreground",
            "pillow_version": PIL.__version__,
            "parent_dimensions": parent_dimensions,
            "child_dimensions": child_dimensions,
            "parent_bytes": parent["bytes"],
            "child_bytes": child["bytes"],
            "parent_sha256": parent["sha256"],
            "child_sha256": child["sha256"],
            "derivation": derivation,
        }
    return {
        "normalized": False,
        "algorithm": NATIVE_IMAGE_ALGORITHM,
        "operation": "byte_copy",
        "pillow_version": PIL.__version__,
        "parent_dimensions": parent_dimensions,
        "child_dimensions": child_dimensions,
        "parent_bytes": parent["bytes"],
        "child_bytes": child["bytes"],
        "parent_sha256": parent["sha256"],
        "child_sha256": child["sha256"],
        "derivation": derivation,
    }


def collect_common_codex_conversation_audit(
    *,
    stdout_events: Iterable[object],
    codex_home: str | Path,
    private_dir: str | Path,
    requested_model: str | None,
    requested_reasoning_effort: str | None,
    actual_model: str | None,
    actual_reasoning_effort: str | None,
    cli_binary: str | None,
    cli_version: str | None,
    binary_sha256: str | None,
    attempt: int | None,
    terminal_state: str | None,
    retry: bool | None,
    error: str | None,
    timeout: bool | None,
    skip: bool | None,
    fallback_used: bool | None,
    invocation_id: int | None = None,
    refinalize: Callable[[int, dict[str, object]], object] | None = None,
    finalize: bool = True,
) -> dict[str, object]:
    """Collect and archive only common private conversation facts.

    All raw path-bearing values stay in this private metadata object.  A caller
    may use ``public_projection`` only after the later image-specific evidence
    layer has been added.
    """
    evidence: dict[str, object] = {
        codex_audit.NATIVE_PRIVATE_EVIDENCE_KEY: codex_audit.NATIVE_PRIVATE_EVIDENCE_VALUE,
        "invocation_id": invocation_id,
        "requested_model": requested_model,
        "actual_model": actual_model,
        "requested_reasoning_effort": requested_reasoning_effort,
        "actual_reasoning_effort": actual_reasoning_effort,
        "cli_binary": cli_binary,
        "cli_version": cli_version,
        "binary_sha256": binary_sha256,
        "thread_id": None,
        "attempt": attempt,
        "terminal_state": terminal_state,
        "retry": retry,
        "error": error,
        "timeout": timeout,
        "skip": skip,
        "fallback_used": fallback_used,
        "audit_complete": False,
        "canonical_session": {
            "source_path": None,
            "archive_path": None,
            "bytes": None,
            "sha256": None,
        },
    }
    collection_error: CommonNativeAuditError | None = None
    collection_cause: Exception | None = None
    try:
        resolved_private_dir = _as_resolved_path(private_dir)
        private_root = _native_private_root_from_dir(
            private_dir=private_dir,
            artifacts_root=config.ARTIFACTS_DIR,
        )
        thread_id = _thread_id_from_stdout(stdout_events)
        evidence["thread_id"] = thread_id
        source_session = find_canonical_session(codex_home=codex_home, thread_id=thread_id)
        canonical_session = _archive_canonical_session(source=source_session, private_dir=resolved_private_dir)
        canonical_session["thread_id"] = thread_id
        evidence["canonical_session"] = canonical_session
        evidence["native_private_manifest"] = _private_manifest(
            private_root,
            canonical_session,
            raw_path=resolved_private_dir / "codex.raw.jsonl",
            observed_path=resolved_private_dir / "codex.observed.jsonl",
        )
        evidence["audit_complete"] = True
        if error:
            evidence["terminal_state"] = "failed"
            evidence["failure_code"] = "native_runner_error"
    except Exception as exc:
        evidence["terminal_state"] = "failed"
        evidence["error"] = error or str(exc)
        evidence["failure_code"] = _failure_code(exc)
        collection_error = CommonNativeAuditError(str(exc), evidence)
        collection_cause = exc

    if finalize:
        _re_finalize(invocation_id=invocation_id, metadata=evidence, refinalize=refinalize)
    if collection_error is not None:
        raise collection_error from collection_cause
    return evidence


def collect_native_image_evidence(
    *,
    director_audit: bool = False,
    **kwargs: object,
) -> dict[str, object]:
    """Collect exact private image evidence and a whitelisted public projection."""
    invocation_id = kwargs.get("invocation_id")
    refinalize = kwargs.get("refinalize")
    if invocation_id is not None and not isinstance(invocation_id, int):
        raise ValueError("invocation_id must be an integer when supplied")
    if refinalize is not None and not callable(refinalize):
        raise ValueError("refinalize must be callable when supplied")
    common_keys = {
        "stdout_events",
        "codex_home",
        "private_dir",
        "requested_model",
        "requested_reasoning_effort",
        "actual_model",
        "actual_reasoning_effort",
        "cli_binary",
        "cli_version",
        "binary_sha256",
        "attempt",
        "terminal_state",
        "retry",
        "error",
        "timeout",
        "skip",
        "fallback_used",
    }
    missing = sorted(key for key in common_keys if key not in kwargs)
    if missing:
        raise ValueError(f"common Native audit is missing required fields: {', '.join(missing)}")
    supplied_stdout_events = kwargs["stdout_events"]
    if isinstance(supplied_stdout_events, (str, bytes)) or not isinstance(supplied_stdout_events, Iterable):
        raise ValueError("common Native audit stdout_events must be an iterable of event objects")
    kwargs["stdout_events"] = tuple(supplied_stdout_events)
    try:
        common = collect_common_codex_conversation_audit(
            **{key: kwargs[key] for key in common_keys},  # type: ignore[arg-type]
            invocation_id=invocation_id,
            refinalize=refinalize,  # type: ignore[arg-type]
            finalize=False,
        )
    except CommonNativeAuditError as exc:
        common = exc.evidence
        if director_audit:
            for field in (
                "imagegen_call_id",
                "imagegen_call_arguments_sha256",
                "imagegen_input",
                "default_image",
                "original_image",
                "business_image",
                "normalization",
            ):
                common[field] = NOT_APPLICABLE
        common["public_projection"] = _safe_common_projection(common)
        _re_finalize(invocation_id=invocation_id, metadata=common, refinalize=refinalize)  # type: ignore[arg-type]
        raise ValueError(str(exc)) from exc
    if director_audit:
        for field in (
            "imagegen_call_id",
            "imagegen_call_arguments_sha256",
            "imagegen_input",
            "default_image",
            "original_image",
            "business_image",
            "normalization",
        ):
            common[field] = NOT_APPLICABLE
        common["public_projection"] = _safe_common_projection(common)
        _re_finalize(invocation_id=invocation_id, metadata=common, refinalize=refinalize)  # type: ignore[arg-type]
        return common

    business_path: Path | None = None
    try:
        business_path = _business_output_path(
            kwargs.get("business_output_path"),
            artifacts_root=config.ARTIFACTS_DIR,
        )
    except Exception as exc:
        common["terminal_state"] = "failed"
        common["failure_code"] = "business_output_path_invalid"
        common["public_projection"] = _safe_common_projection(common)
        _re_finalize(invocation_id=invocation_id, metadata=common, refinalize=refinalize)  # type: ignore[arg-type]
        raise ValueError(str(exc)) from exc

    try:
        if common.get("error"):
            raise ValueError("exactly one canonical image binding is required after a Native runner failure")
        canonical = common.get("canonical_session")
        if not isinstance(canonical, dict) or not isinstance(canonical.get("archive_path"), str):
            raise ValueError("canonical Codex session archive is unavailable")
        canonical_path = Path(canonical["archive_path"])
        canonical_records = list(_jsonl_records(canonical_path))
        if _legacy_image_end_events(canonical_records):
            binding, default_path = _call_bound_image_details(
                canonical_session=canonical_path,
                requested_model=common.get("requested_model"),
                actual_model=common.get("actual_model"),
            )
        else:
            binding, default_path = _thread_scoped_image_details(
                baseline=kwargs.get("thread_output_baseline")
                if isinstance(kwargs.get("thread_output_baseline"), ThreadOutputBaseline)
                else None,
                stdout_events=kwargs["stdout_events"],
                execution_exit_code=kwargs.get("execution_exit_code"),
                execution_timed_out=kwargs.get("execution_timed_out"),
            )
        default_image = _image_record(default_path)
        original_path = _as_resolved_path(kwargs["private_dir"]) / "native-original.png"
        _write_immutable_original(original_path, default_path.read_bytes())
        original_image = _image_record(original_path)
        if default_image["sha256"] != original_image["sha256"]:
            raise ValueError("immutable Native original must be byte-identical to the event-selected PNG")

        common.update(binding)
        common["default_image"] = default_image
        common["original_image"] = original_image
        private_manifest_root = _native_private_root_from_dir(
            private_dir=kwargs["private_dir"], artifacts_root=config.ARTIFACTS_DIR
        )
        final_private_dir = _as_resolved_path(kwargs["private_dir"])
        common["native_private_manifest"] = _private_manifest(
            private_manifest_root,
            canonical,
            original_image,
            raw_path=final_private_dir / "codex.raw.jsonl",
            observed_path=final_private_dir / "codex.observed.jsonl",
        )

        width = int(default_image["width"])
        height = int(default_image["height"])
        aspect_delta = abs(width / height - 16 / 9)
        normalized = aspect_delta > ASPECT_TOLERANCE
        if normalized:
            try:
                normalize_native_image(source_path=original_path, output_path=business_path)
            except Exception as exc:
                raise RuntimeError("normalization_failed") from exc
        else:
            _atomic_write_bytes(business_path, original_path.read_bytes(), mode=0o600)
        business_image = _image_record(business_path)
        common["business_image"] = business_image
        common["normalization"] = _normalization_record(
            parent=original_image,
            child=business_image,
            normalized=normalized,
        )
        common["terminal_state"] = common.get("terminal_state") or "result_received"
        common.pop("failure_code", None)
        common["public_projection"] = _safe_common_projection(common)
        _re_finalize(invocation_id=invocation_id, metadata=common, refinalize=refinalize)  # type: ignore[arg-type]
        return common
    except RuntimeError as exc:
        if str(exc) != "normalization_failed":
            raise
        _remove_stale_business_output(business_path)
        common["terminal_state"] = "normalization_failed"
        common["failure_code"] = "normalization_failed"
        common["public_projection"] = _safe_common_projection(common)
        _re_finalize(invocation_id=invocation_id, metadata=common, refinalize=refinalize)  # type: ignore[arg-type]
        raise RuntimeError("normalization_failed") from exc
    except Exception as exc:
        _remove_stale_business_output(business_path)
        common["terminal_state"] = "failed"
        common["failure_code"] = "native_runner_error" if common.get("error") else "image_call_binding_failed"
        common["public_projection"] = _safe_common_projection(common)
        _re_finalize(invocation_id=invocation_id, metadata=common, refinalize=refinalize)  # type: ignore[arg-type]
        raise ValueError(str(exc)) from exc


def _native_cli_identity(command: list[str], agent_config: dict[str, object]) -> tuple[str, str, str | None]:
    """Return the launcher identity without creating a second Codex invocation."""
    command_name = command[0] if command else "codex"
    resolved = shutil.which(command_name) or command_name
    binary_path = Path(resolved).expanduser().resolve()
    try:
        if not binary_path.is_file():
            return str(binary_path), "unknown", None
        binary_sha256 = hashlib.sha256(binary_path.read_bytes()).hexdigest()
    except OSError:
        return str(binary_path), "unknown", None

    # A config value cannot establish the version of this exact executable. The
    # bounded no-shell identity probe has no prompt or session arguments, so it
    # cannot start a second provider conversation or alter the provider command.
    del agent_config
    try:
        completed = subprocess.run(
            [str(binary_path), "--version"],
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=5,
        )
    except OSError:
        return str(binary_path), "unknown", binary_sha256
    except subprocess.TimeoutExpired:
        return str(binary_path), "unknown", binary_sha256
    if completed.returncode != 0:
        return str(binary_path), "unknown", binary_sha256
    for output in (completed.stdout, completed.stderr):
        if not isinstance(output, str):
            continue
        for line in output.splitlines():
            version = line.strip()
            if version:
                return str(binary_path), version, binary_sha256
    return str(binary_path), "unknown", binary_sha256


def _native_failure_evidence(
    *,
    result: object,
    agent_config: dict[str, object],
    private_dir: Path,
    timeout: bool,
) -> None:
    """Best-effort same-invocation failure finalization without exposing raw errors."""
    stdout_events = _stdout_events_from_stream_summary(result)
    if not stdout_events:
        return
    command = list(getattr(result, "command", []) or [])
    cli_binary, cli_version, binary_sha256 = _native_cli_identity(command, agent_config)
    model = str(agent_config.get("model") or "")
    reasoning_effort = str(agent_config.get("thinking") or agent_config.get("reasoning_effort") or "low")
    try:
        collect_common_codex_conversation_audit(
            stdout_events=stdout_events,
            codex_home=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
            private_dir=private_dir,
            requested_model=model,
            requested_reasoning_effort=reasoning_effort,
            actual_model=model,
            actual_reasoning_effort=reasoning_effort,
            cli_binary=cli_binary,
            cli_version=cli_version,
            binary_sha256=binary_sha256,
            attempt=getattr(result, "attempt", 1),
            terminal_state="timed_out" if timeout else "failed",
            retry=False,
            error="native_runner_error",
            timeout=timeout,
            skip=False,
            fallback_used=False,
            invocation_id=getattr(result, "invocation_id", None),
        )
    except Exception:
        # The collector records its own terminal code when it can reach the
        # durable invocation. The caller must still fail closed either way.
        pass


def generate_codex_native_image(
    agent_config: dict,
    prompt: str,
    output_path: str,
    *,
    run_id: int,
    run_slide_id: int,
    stage_id: str,
    output_dir: str | Path,
    timeout_seconds: int,
    reference_image_paths: list[str | Path] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Run one audited Native imagegen call through the existing provider seam.

    The shared Direct/3.0 orchestrator owns stage ordering and persistence. This
    adapter owns only the Codex child, its Run-private evidence, exact
    session/image binding, normalization, and the safe public result shape.
    """
    if agent_config.get("api_type") != "codex_native_image":
        raise ValueError("Codex Native image adapter requires a Native image generator profile")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("Codex Native image prompt is required")
    if not isinstance(stage_id, str) or not stage_id:
        raise ValueError("Codex Native image stage id is required")

    business_output = _business_output_path(output_path, artifacts_root=config.ARTIFACTS_DIR)
    private_dir = native_runner_artifact_dir(
        artifacts_root=config.ARTIFACTS_DIR,
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=stage_id,
        attempt=1,
    )
    model = str(agent_config.get("model") or "").strip()
    reasoning_effort = str(agent_config.get("thinking") or agent_config.get("reasoning_effort") or "low").strip()
    if not model:
        raise ValueError("Codex Native image launcher model is required")
    if not reasoning_effort:
        raise ValueError("Codex Native image reasoning effort is required")
    references = [Path(path) for path in (reference_image_paths or []) if path]
    business_prompt = prompt
    active_prompt_content, native_prompt_lineage = _resolve_active_image_generator_prompt()
    transport_prompt = _native_image_generator_transport_prompt(
        prompt_content=active_prompt_content,
        business_prompt=business_prompt,
        has_attached_references=bool(references),
    )
    native_prompt_lineage["rendered_prompt_sha256"] = hashlib.sha256(
        transport_prompt.encode("utf-8")
    ).hexdigest()
    if os.environ.get("IMAGE_PPTGEN_E2E_STOP_BEFORE_IMAGE_PROVIDER") == "1":
        private_dir.mkdir(parents=True, exist_ok=True)
        handoff_path = private_dir / "pre-image-provider-handoff.json"
        handoff_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "ready",
                    "run_id": run_id,
                    "run_slide_id": run_slide_id,
                    "stage_id": stage_id,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "business_prompt_sha256": hashlib.sha256(
                        business_prompt.encode("utf-8")
                    ).hexdigest(),
                    "transport_prompt_sha256": native_prompt_lineage[
                        "rendered_prompt_sha256"
                    ],
                    "provider_invoked": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise RuntimeError("image_pptgen_e2e_pre_image_provider_handoff")
    safe_metadata = {
        codex_audit.NATIVE_PRIVATE_EVIDENCE_KEY: codex_audit.NATIVE_PRIVATE_EVIDENCE_VALUE,
        "route": (metadata or {}).get("strategy"),
        "stage": stage_id,
    }
    observed_result: object | None = None
    execution = None
    codex_home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    thread_output_baseline: ThreadOutputBaseline | None = None

    def capture_thread_output_baseline(thread_id: str) -> None:
        nonlocal thread_output_baseline
        if thread_output_baseline is not None:
            raise ValueError("Native image invocation received more than one thread output baseline")
        thread_output_baseline = _capture_thread_output_baseline(
            codex_home=codex_home,
            thread_id=thread_id,
        )

    async def invoke(audit_context):
        nonlocal observed_result
        attempt_dir = native_runner_artifact_dir(
            artifacts_root=config.ARTIFACTS_DIR,
            run_id=run_id,
            run_slide_id=run_slide_id,
            stage_id=stage_id,
            attempt=audit_context.attempt,
        )
        result = await run_codex_exec_json(
            stage_id=stage_id,
            role="image_generator",
            prompt=transport_prompt,
            work_dir=attempt_dir / "work",
            artifact_dir=attempt_dir,
            model=model,
            reasoning_effort=reasoning_effort,
            sandbox="read-only",
            ephemeral=False,
            image_paths=references,
            timeout_seconds=timeout_seconds,
            audit_context=audit_context,
            thread_started_hook=capture_thread_output_baseline,
        )
        observed_result = result
        return result

    try:
        execution = run_codex_async_from_sync(
            lambda: run_supervised_codex_child(
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                role="image_generator",
                idempotency_key=f"codex-native-image:run:{run_id}:slide:{run_slide_id}:stage:{stage_id}",
                invoke=invoke,
                metadata=safe_metadata,
                max_recoveries=0,
                require_final_text=False,
            )
        )
        result = execution.result
        observed_result = result
        if result.exit_code != 0 or result.timed_out:
            raise RuntimeError("native_image_generation_failed")
        if result.prompt_sha256 != native_prompt_lineage["rendered_prompt_sha256"]:
            raise RuntimeError("native_image_prompt_lineage_failed")
        cli_binary, cli_version, binary_sha256 = _native_cli_identity(list(result.command), agent_config)
        evidence = collect_native_image_evidence(
            stdout_events=_stdout_events_from_stream_summary(result),
            codex_home=codex_home,
            private_dir=native_runner_artifact_dir(
                artifacts_root=config.ARTIFACTS_DIR,
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                attempt=execution.attempt_count,
            ),
            requested_model=model,
            requested_reasoning_effort=reasoning_effort,
            actual_model=model,
            actual_reasoning_effort=reasoning_effort,
            cli_binary=cli_binary,
            cli_version=cli_version,
            binary_sha256=binary_sha256,
            attempt=execution.attempt_count,
            terminal_state="result_received",
            retry=False,
            error=None,
            timeout=False,
            skip=False,
            fallback_used=False,
            invocation_id=result.invocation_id,
            business_output_path=business_output,
            thread_output_baseline=thread_output_baseline,
            execution_exit_code=result.exit_code,
            execution_timed_out=result.timed_out,
        )
        if not execution.complete_projection():
            raise RuntimeError("native_image_projection_failed")
        return {
            "response": {"image_path": str(business_output)},
            "native_public": evidence["public_projection"],
            "native_prompt_lineage": native_prompt_lineage,
        }
    except Exception as exc:
        _remove_stale_business_output(business_output)
        if observed_result is not None:
            _native_failure_evidence(
                result=observed_result,
                agent_config=agent_config,
                private_dir=private_dir,
                timeout=bool(getattr(observed_result, "timed_out", False)),
            )
        if execution is not None:
            execution.fail_unrecoverable("native_image_generation_failed")
        raise RuntimeError("native_image_generation_failed") from exc
