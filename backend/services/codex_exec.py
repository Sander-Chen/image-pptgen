"""Audited Codex exec runner for backend-owned generation routes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
from typing import Any, Coroutine, TypeVar

from backend.services.codex_html_cleanup import clean_codex_html_output
from backend.services import codex_audit, codex_platform_gate, codex_supervisor
from backend.services.codex_executable import (
    CodexExecutableIdentity,
    CodexExecutableUnavailable,
    resolve_codex_executable,
    resolve_codex_executable_identity,
    verify_codex_executable_identity,
)
from backend.services.codex_jsonl_stream import (
    BoundedJsonlFramer,
    CodexStreamSummary,
    CodexStreamSummaryBuilder,
    FinalTextCapture,
    JsonlRecord,
    SOURCE_WINDOW_BYTES,
    SemanticJsonlError,
    SemanticJsonlRecord,
    StreamingJsonlSemanticProjector,
    TranscriptProjection,
    iter_bounded_jsonl_file_records,
    iter_semantic_jsonl_file_records,
)
from backend.services.private_file_permissions import restrict_owner_only_fd


@dataclass(frozen=True)
class CodexAuditContext:
    run_id: int | None = None
    run_slide_id: int | None = None
    attempt: int = 1
    metadata: dict[str, Any] | None = None
    work_item_id: int | None = None
    lease_owner: str | None = None
    fencing_token: int | None = None
    lease_seconds: int = 30


class CodexStreamContinuityError(RuntimeError):
    """Raised when persisted JSONL cursor continuity cannot be trusted."""


class CodexChildReaderSettlementTimeout(RuntimeError):
    """The direct child ended but this runner's pipe readers did not settle."""

    code = "codex_child_reader_settlement_timeout"

    def __init__(self, *, phase: str) -> None:
        self.phase = phase
        super().__init__(f"{self.code}:{phase}")


T = TypeVar("T")


@dataclass(frozen=True)
class CodexExecResult:
    stage_id: str
    role: str
    command: list[str]
    cwd: Path
    prompt_path: Path
    raw_jsonl_path: Path
    observed_jsonl_path: Path
    stderr_path: Path
    command_path: Path
    final_response_path: Path
    transcript_path: Path
    started_at: str
    ended_at: str
    elapsed_ms: int
    exit_code: int
    prompt_sha256: str
    final_text: str
    peak_rss_kb: int | None
    stream_summary: CodexStreamSummary | None = None
    timed_out: bool = False
    invocation_id: int | None = None
    executable_identity_path: Path | None = None
    result_receipt_path: Path | None = None


def final_text_capture_for_result(result: Any) -> FinalTextCapture | None:
    """Return an attached final-text capture without materializing its content."""
    summary = getattr(result, "stream_summary", None)
    capture = getattr(summary, "final_capture", None)
    return capture if isinstance(capture, FinalTextCapture) else None


def has_codex_result_final_content(result: Any) -> bool:
    """Check the result-content contract without expanding a capture into RAM."""
    inline_text = getattr(result, "final_text", "")
    if isinstance(inline_text, str) and bool(inline_text):
        return True
    capture = final_text_capture_for_result(result)
    return capture is not None and capture.text_length > 0


def materialize_codex_result_final_text(result: Any) -> str:
    """Materialize final text only for a concrete business-consumption boundary."""
    inline_text = getattr(result, "final_text", "")
    if isinstance(inline_text, str) and inline_text:
        return inline_text
    capture = final_text_capture_for_result(result)
    if capture is None:
        return inline_text if isinstance(inline_text, str) else ""
    return "".join(capture.iter_text_chunks())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def run_codex_async_from_sync(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run a coroutine factory from a worker thread without nesting event loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    raise RuntimeError("cannot run Codex async work from an active event loop")


def build_codex_command(
    *,
    work_dir: Path,
    model: str,
    reasoning_effort: str,
    sandbox: str = "read-only",
    extra_config: list[str] | None = None,
    ephemeral: bool = True,
    image_paths: list[Path] | None = None,
    executable_identity: CodexExecutableIdentity | None = None,
) -> list[str]:
    command = [
        executable_identity.path if executable_identity is not None else resolve_codex_executable(),
        "--ask-for-approval",
        "never",
        "exec",
        "--json",
    ]
    if ephemeral:
        command.append("--ephemeral")
    if os.environ.get("PPTGEN_CODEX_INHERIT_USER_CONFIG") != "1":
        command.append("--ignore-user-config")
    command.extend([
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        sandbox,
        "--cd",
        str(work_dir),
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
    ])
    for image_path in image_paths or []:
        command.extend(["--image", str(image_path)])
    for item in extra_config or []:
        command.extend(["-c", item])
    # The complete prompt is supplied through stdin. Keep that contract
    # explicit instead of relying on an omitted positional prompt.
    command.append("-")
    return command


def _resolve_runner_identity() -> CodexExecutableIdentity:
    """Resolve the identity once so the command and receipt share one path."""
    if sys.platform in {"win32", "darwin"}:
        return resolve_codex_executable_identity()
    path = resolve_codex_executable()
    return CodexExecutableIdentity(path=path, source="legacy_system_path")


def _identity_receipt(identity: CodexExecutableIdentity) -> dict[str, Any]:
    """Return the private, schema-versioned executable selection receipt."""
    return {
        "schema": "codex-executable-identity-v1",
        "selected_path": identity.path,
        "selected_version": identity.version or "unknown",
        "selected_size": identity.size,
        "selected_sha256": identity.sha256,
        "selected_signature_status": identity.signature_status,
        "selected_publisher": identity.publisher,
        "publisher": identity.publisher,
        "selected_provenance_root": identity.provenance_root,
        "provenance_root": identity.provenance_root,
        "package_family_name": identity.package_family_name,
        "appx_package_full_name": identity.appx_package_full_name,
        "appx_resource_path": identity.appx_resource_path,
        "appx_resource_version": identity.appx_resource_version,
        "appx_resource_size": identity.appx_resource_size,
        "appx_resource_sha256": identity.appx_resource_sha256,
    }


_PUBLIC_WINDOWS_IDENTITY_ERROR = "codex_desktop_executable_unavailable"
_PRIVATE_IDENTITY_REASON_MAX_LENGTH = 128
_PRIVATE_IDENTITY_PATH_MAX_LENGTH = 512
_PRIVATE_IDENTITY_SUBJECT_MAX_LENGTH = 512


def _bounded_private_identity_reason(exc: CodexExecutableUnavailable) -> str:
    """Keep diagnostic receipts useful without persisting arbitrary exception text."""
    reason = getattr(exc, "reason", "")
    if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,128}", reason):
        return reason[:_PRIVATE_IDENTITY_REASON_MAX_LENGTH]
    return "identity_check_failed"


def _bounded_private_identity_path(exc: CodexExecutableUnavailable) -> str | None:
    """Persist one normalized local path only when it fits the private receipt."""
    candidate = getattr(exc, "path", None)
    if not isinstance(candidate, Path):
        return None
    try:
        normalized = os.path.normcase(str(candidate.resolve(strict=False)))
    except (OSError, RuntimeError):
        return None
    if not os.path.isabs(normalized) or len(normalized) > _PRIVATE_IDENTITY_PATH_MAX_LENGTH:
        return None
    return normalized


def _bounded_private_hresult(exc: CodexExecutableUnavailable) -> str | None:
    """Persist one stable 32-bit HRESULT without exposing arbitrary exception text."""
    value = getattr(exc, "_hresult", None)
    if not isinstance(value, int) or not -(1 << 31) <= value <= (1 << 32) - 1:
        return None
    return f"0x{value & 0xFFFFFFFF:08X}"


def _bounded_private_wintrust_status(exc: CodexExecutableUnavailable) -> int | None:
    """Persist only a signed 32-bit WinVerifyTrust LONG, never an HRESULT."""
    value = getattr(exc, "_wintrust_status", None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not -(1 << 31) <= value <= (1 << 31) - 1:
        return None
    return value


def _bounded_private_authenticode_subject(
    exc: CodexExecutableUnavailable,
) -> dict[str, str] | None:
    """Persist bounded native X.500 subject forms for private diagnostics only."""
    exact = getattr(exc, "_authenticode_subject_exact", None)
    normalized = getattr(exc, "_authenticode_subject_normalized", None)
    if (
        not isinstance(exact, str)
        or not isinstance(normalized, str)
        or not exact
        or not normalized
        or len(exact) > _PRIVATE_IDENTITY_SUBJECT_MAX_LENGTH
        or len(normalized) > _PRIVATE_IDENTITY_SUBJECT_MAX_LENGTH
        or re.sub(r"\s+", " ", exact.strip()).casefold() != normalized
    ):
        return None
    return {"exact": exact, "normalized": normalized}


def _write_windows_identity_failure_receipt(
    path: Path,
    *,
    phase: str,
    exc: CodexExecutableUnavailable,
) -> None:
    receipt: dict[str, Any] = {
        "schema": "codex-executable-identity-failure-v1",
        "phase": phase,
        "private_reason": _bounded_private_identity_reason(exc),
        "private_path": _bounded_private_identity_path(exc),
    }
    private_hresult = _bounded_private_hresult(exc)
    if private_hresult is not None:
        receipt["private_hresult"] = private_hresult
    private_wintrust_status = _bounded_private_wintrust_status(exc)
    if private_wintrust_status is not None:
        receipt["private_wintrust_status"] = private_wintrust_status
    private_subject = _bounded_private_authenticode_subject(exc)
    if private_subject is not None:
        receipt["private_authenticode_subject"] = private_subject
    path.write_text(
        json.dumps(
            receipt,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _stable_windows_identity_error() -> CodexExecutableUnavailable:
    return CodexExecutableUnavailable(_PUBLIC_WINDOWS_IDENTITY_ERROR)


def write_observed_jsonl(
    raw_jsonl_path: Path,
    observed_jsonl_path: Path,
    *,
    now: Callable[[], str] = utc_now_iso,
) -> Path:
    observed_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with observed_jsonl_path.open("w", encoding="utf-8") as target:
        for line_number, record in enumerate(iter_bounded_jsonl_file_records(raw_jsonl_path), start=1):
            event = _record_event(record)
            if event is None:
                raise ValueError(f"invalid Codex JSONL line {line_number}")
            target.write(json.dumps({"observed_at": now(), "event": event}, ensure_ascii=False) + "\n")
    return observed_jsonl_path


def extract_final_agent_text(raw_jsonl_path: Path) -> str:
    final_text = ""
    for line_number, record in enumerate(iter_bounded_jsonl_file_records(raw_jsonl_path), start=1):
        event = _record_event(record)
        if event is None:
            raise ValueError(f"invalid Codex JSONL line {line_number}")
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            final_text = item["text"]
    if not final_text:
        raise ValueError("Codex JSONL did not contain a final agent_message")
    return final_text


def transcript_from_jsonl(prompt: str, raw_jsonl_path: Path) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "user", "text": prompt}]
    for record in iter_bounded_jsonl_file_records(raw_jsonl_path):
        message = _transcript_message(record)
        if message is not None:
            messages.append(message)
    return {"messages": messages}


class _ProjectedTranscriptWriter:
    """Private transcript sink fed by validated producer-order projections only."""

    def __init__(self, prompt: str, transcript_path: Path) -> None:
        self._path = transcript_path
        self._target = transcript_path.open("w", encoding="utf-8")
        self._target.write('{"messages":[')
        self._target.write(json.dumps({"role": "user", "text": prompt}, ensure_ascii=False))
        self._closed = False

    def write(self, projection: TranscriptProjection | dict[str, str]) -> None:
        _write_transcript_projection(self._target, projection)
        self._target.flush()

    def finish(self) -> None:
        if not self._closed:
            self._target.write("]}")
            self._target.close()
            self._closed = True

    def abort(self) -> None:
        if not self._closed:
            self._target.close()
            self._closed = True
        self._path.unlink(missing_ok=True)


def write_bounded_transcript(
    prompt: str,
    projections,
    transcript_path: Path,
) -> None:
    """Write producer-order projections without reopening private raw JSONL."""
    writer = _ProjectedTranscriptWriter(prompt, transcript_path)
    try:
        for projection in projections:
            writer.write(projection)
        writer.finish()
    except BaseException:
        writer.abort()
        raise


def _write_transcript_projection(target, projection: TranscriptProjection | dict[str, str]) -> None:
    """Emit a single projection before the producer can advance to the next record."""
    target.write(",")
    if isinstance(projection, dict):
        target.write(json.dumps(projection, ensure_ascii=False))
        return
    if projection.capture is None or projection.capture.inline_text is not None:
        target.write(
            json.dumps(
                {"role": projection.role, "text": projection.inline_text or ""},
                ensure_ascii=False,
            )
        )
        return
    target.write(json.dumps({"role": projection.role}, ensure_ascii=False)[:-1])
    target.write(', "text": "')
    for chunk in projection.capture.iter_text_chunks():
        _write_json_string_chunk(target, chunk)
    target.write('"}')


def _write_json_string_chunk(target, value: str) -> None:
    """JSON-escape a capture chunk without allocating a second large string."""
    escapes = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for char in value:
        escaped = escapes.get(char)
        if escaped is not None:
            target.write(escaped)
        elif ord(char) < 0x20:
            target.write(f"\\u{ord(char):04x}")
        else:
            target.write(char)


def _write_transcript_record(target, record: JsonlRecord) -> None:
    """Emit one projected transcript message without retaining earlier records."""
    message = _transcript_message(record)
    if message is not None:
        target.write(",")
        target.write(json.dumps(message, ensure_ascii=False))


def _record_event(record: JsonlRecord) -> dict[str, Any] | None:
    if record.oversized:
        return record.sqlite_placeholder_event()
    return record.event


def _transcript_message(record: JsonlRecord) -> dict[str, str] | None:
    event = _record_event(record)
    if event is None:
        raise ValueError("invalid Codex JSONL record")
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
        return {"role": "assistant", "text": item["text"]}
    if item.get("type") == "error":
        return {"role": "codex_error", "text": json.dumps(item, ensure_ascii=False)}
    return None


def _extract_fenced(text: str, lang: str) -> str | None:
    matches = re.findall(rf"```(?:{re.escape(lang)})?\s*(.*?)```", text, flags=re.I | re.S)
    if not matches:
        return None
    return max((match.strip() for match in matches), key=len)


def clean_json_output(text: str) -> tuple[str, Any]:
    candidate = _extract_fenced(text, "json")
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("No JSON object found in Codex output")
        candidate = text[start : end + 1]
    parsed = json.loads(candidate)
    return json.dumps(parsed, ensure_ascii=False, indent=2), parsed


def clean_html_output(text: str) -> str:
    return clean_codex_html_output(text).html


def _recover_persisted_jsonl_tail(
    *,
    raw_path: Path,
    observed_path: Path,
    invocation_id: int,
    sequence: int,
    byte_offset: int,
    file_identity: str,
) -> tuple[int, int]:
    """Project complete raw-file lines that were flushed after the durable cursor."""
    try:
        records = iter_semantic_jsonl_file_records(raw_path, start_offset=byte_offset)
        with observed_path.open("a", encoding="utf-8") as observed:
            for record in records:
                try:
                    if not isinstance(record.event, dict):
                        raise ValueError("invalid JSONL record")
                    sequence += 1
                    _persist_stdout_record(
                        record=record,
                        observed=observed,
                        invocation_id=invocation_id,
                        sequence=sequence,
                        file_identity=file_identity,
                        observed_at=utc_now_iso(),
                    )
                    byte_offset = record.raw_range.end
                finally:
                    record.discard_final_capture()
    except ValueError as exc:
        reason = "raw JSONL has an invalid or incomplete trailing record after the durable cursor"
        codex_audit.mark_codex_stream_continuity_error(invocation_id, reason)
        raise CodexStreamContinuityError(reason) from exc
    return sequence, byte_offset


async def _read_stdout_jsonl(
    stream: asyncio.StreamReader,
    raw_path: Path,
    observed_path: Path,
    *,
    invocation_id: int | None = None,
    resume: bool = False,
    transcript_writer: _ProjectedTranscriptWriter | None = None,
    thread_started_hook: Callable[[str], None] | None = None,
) -> CodexStreamSummary:
    """Persist stdout while optionally binding one newly announced thread.

    The hook is deliberately synchronous and opt-in.  It is for a caller that
    must create an invocation baseline before Codex can emit later tool output;
    it is not a general-purpose event observer.  A resumed stream cannot
    establish such a baseline safely because its thread-start record may
    precede the durable cursor.
    """
    if resume and thread_started_hook is not None:
        raise ValueError("thread_started_hook cannot be used while resuming a Codex stream")
    sequence = 0
    byte_offset = 0
    expected_file_identity: str | None = None
    hooked_thread_id: str | None = None
    if resume:
        if invocation_id is None:
            raise ValueError("resume requires an invocation_id")
        cursor = codex_audit.get_codex_stream_cursor(invocation_id)
        sequence = int(cursor["sequence"])
        byte_offset = int(cursor["byte_offset_end"])
        expected_file_identity = cursor.get("file_identity")
        if raw_path.exists():
            stat = raw_path.stat()
            actual_identity = f"{stat.st_dev}:{stat.st_ino}"
            if stat.st_size < byte_offset:
                reason = f"raw JSONL truncated below durable offset {byte_offset}"
                codex_audit.mark_codex_stream_continuity_error(invocation_id, reason)
                raise CodexStreamContinuityError(reason)
            if expected_file_identity and actual_identity != expected_file_identity:
                reason = "raw JSONL rotated since the durable cursor was recorded"
                codex_audit.mark_codex_stream_continuity_error(invocation_id, reason)
                raise CodexStreamContinuityError(reason)
            if stat.st_size > byte_offset:
                sequence, byte_offset = _recover_persisted_jsonl_tail(
                    raw_path=raw_path,
                    observed_path=observed_path,
                    invocation_id=invocation_id,
                    sequence=sequence,
                    byte_offset=byte_offset,
                    file_identity=actual_identity,
                )
                expected_file_identity = actual_identity
        elif byte_offset:
            reason = "raw JSONL rotated or removed since the durable cursor was recorded"
            codex_audit.mark_codex_stream_continuity_error(invocation_id, reason)
            raise CodexStreamContinuityError(reason)

    raw_mode = "ab" if resume else "wb"
    observed_mode = "a" if resume else "w"
    projector = StreamingJsonlSemanticProjector(spool_dir=raw_path.parent, start_offset=byte_offset)
    summary_builder = CodexStreamSummaryBuilder()
    try:
        with raw_path.open(raw_mode) as raw, observed_path.open(observed_mode, encoding="utf-8") as observed:
            restrict_owner_only_fd(raw.fileno())
            stat = os.fstat(raw.fileno())
            file_identity = f"{stat.st_dev}:{stat.st_ino}"
            if expected_file_identity and file_identity != expected_file_identity:
                reason = "raw JSONL rotated while reopening at the durable cursor"
                assert invocation_id is not None
                codex_audit.mark_codex_stream_continuity_error(invocation_id, reason)
                raise CodexStreamContinuityError(reason)
            while True:
                chunk = await stream.read(SOURCE_WINDOW_BYTES)
                if not chunk:
                    break
                if len(chunk) > SOURCE_WINDOW_BYTES:
                    raise ValueError("stdout source exceeded the fixed read window")
                raw.write(chunk)
                raw.flush()
                summary_builder.observe_chunk(chunk)
                for record in projector.feed(chunk):
                    sequence += 1
                    observed_at = utc_now_iso()
                    if thread_started_hook is not None:
                        event = record.event
                        if event.get("type") == "thread.started":
                            thread_id = event.get("thread_id")
                            if not isinstance(thread_id, str) or not thread_id:
                                raise ValueError("Codex thread.started event has no valid thread id")
                            if hooked_thread_id is not None:
                                raise ValueError("Codex stdout contains more than one thread.started event")
                            thread_started_hook(thread_id)
                            hooked_thread_id = thread_id
                    # Transfer any published capture before a persistence or
                    # transcript failure can strand it outside cleanup ownership.
                    summary_builder.observe_semantic_record(record, observed_at=observed_at)
                    _persist_stdout_record(
                        record=record,
                        observed=observed,
                        invocation_id=invocation_id,
                        sequence=sequence,
                        file_identity=file_identity,
                        observed_at=observed_at,
                    )
                    if transcript_writer is not None and record.transcript_projection is not None:
                        transcript_writer.write(record.transcript_projection)
                    byte_offset = record.raw_range.end
            projector.finish()
        if thread_started_hook is not None and hooked_thread_id is None:
            raise ValueError("Codex stdout has no valid thread.started event")
    except SemanticJsonlError as exc:
        projector.abort()
        summary_builder.discard_captures()
        if transcript_writer is not None:
            transcript_writer.abort()
        reason = f"raw JSONL contains an invalid semantic record: {exc}"
        if invocation_id is not None:
            codex_audit.mark_codex_stream_continuity_error(invocation_id, reason)
        raise CodexStreamContinuityError(reason) from exc
    except BaseException:
        projector.abort()
        summary_builder.discard_captures()
        if transcript_writer is not None:
            transcript_writer.abort()
        raise
    return summary_builder.build()


def _persist_stdout_record(
    *,
    record: SemanticJsonlRecord | JsonlRecord,
    observed,
    invocation_id: int | None,
    sequence: int,
    file_identity: str,
    observed_at: str,
) -> None:
    event = record.event if isinstance(record, SemanticJsonlRecord) else _record_event(record)
    if event is None:
        raise ValueError("invalid JSONL record")
    observed_offset = observed.tell()
    try:
        observed.write(json.dumps({"observed_at": observed_at, "event": event}, ensure_ascii=False) + "\n")
        observed.flush()
        if invocation_id is not None:
            codex_audit.append_live_codex_event(
                invocation_id=invocation_id,
                sequence=sequence,
                event=event,
                observed_at=observed_at,
                raw_bytes=record.raw_bytes,
                raw_sha256=record.raw_range.sha256,
                byte_offset_start=record.raw_range.start,
                byte_offset_end=record.raw_range.end,
                file_identity=file_identity,
            )
    except BaseException:
        outcome = "NOT_COMMITTED"
        if invocation_id is not None:
            outcome = codex_audit.inspect_live_codex_event_persistence(
                invocation_id=invocation_id,
                sequence=sequence,
                event=event,
                observed_at=observed_at,
                raw_bytes=record.raw_bytes,
                raw_sha256=record.raw_range.sha256,
                byte_offset_start=record.raw_range.start,
                byte_offset_end=record.raw_range.end,
                file_identity=file_identity,
            )
        if outcome == "COMMITTED":
            return
        if outcome == "NOT_COMMITTED":
            observed.seek(observed_offset)
            observed.truncate()
            observed.flush()
        raise


async def _read_stderr(stream: asyncio.StreamReader, stderr_path: Path) -> None:
    with stderr_path.open("wb") as handle:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            handle.write(chunk)
            handle.flush()


def _read_rss_kb(pid: int) -> int | None:
    status_path = Path("/proc") / str(pid) / "status"
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                return int(parts[1]) if len(parts) >= 2 else None
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return None
    return None


async def _sample_peak_rss(pid: int, done: asyncio.Event) -> int | None:
    peak: int | None = None
    while not done.is_set():
        rss = _read_rss_kb(pid)
        if rss is not None:
            peak = rss if peak is None else max(peak, rss)
        await asyncio.sleep(0.2)
    rss = _read_rss_kb(pid)
    if rss is not None:
        peak = rss if peak is None else max(peak, rss)
    return peak


async def _heartbeat_supervised_child(context: CodexAuditContext, done: asyncio.Event) -> None:
    if context.work_item_id is None or context.lease_owner is None or context.fencing_token is None:
        return
    interval = max(0.1, min(5.0, context.lease_seconds / 3))
    while not done.is_set():
        try:
            await asyncio.wait_for(done.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            if not codex_supervisor.heartbeat(
                work_item_id=context.work_item_id,
                owner=context.lease_owner,
                fencing_token=context.fencing_token,
                now=datetime.now(timezone.utc),
                lease_seconds=context.lease_seconds,
            ):
                return


async def _send_process_stdin(writer: asyncio.StreamWriter, payload: bytes) -> None:
    try:
        writer.write(payload)
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        writer.close()


_PROCESS_TREE_TERMINATION_GRACE_SECONDS = 5.0
_PIPE_READER_SETTLEMENT_SECONDS = 5.0


def _owned_process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group is still present even if the signal probe is not permitted.
        return True
    return True


async def _wait_for_owned_process_group_exit(
    process_group_id: int, *, deadline: float
) -> bool:
    while _owned_process_group_exists(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.05, remaining))
    return True


async def _stop_owned_process_tree(proc: asyncio.subprocess.Process) -> None:
    """End the session owned by *proc* and reap its direct child.

    The runner creates each POSIX child in a new session, so its PID is also the
    process-group ID.  Killing only ``proc`` leaves descendants holding inherited
    stdout/stderr pipes; that in turn makes the readers wait past the caller's
    deadline.  TERM first gives the CLI a bounded cleanup opportunity, then KILL
    closes any remaining members before this coroutine returns.
    """
    if os.name == "posix":
        process_group_id = proc.pid
        if _owned_process_group_exists(process_group_id):
            with suppress(ProcessLookupError):
                os.killpg(process_group_id, signal.SIGTERM)
            deadline = time.monotonic() + _PROCESS_TREE_TERMINATION_GRACE_SECONDS
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(
                        proc.wait(), timeout=max(0, deadline - time.monotonic())
                    )
                except asyncio.TimeoutError:
                    pass
            if not await _wait_for_owned_process_group_exit(
                process_group_id, deadline=deadline
            ):
                with suppress(ProcessLookupError):
                    os.killpg(process_group_id, signal.SIGKILL)
            await proc.wait()
            return

        # A test double or an already-reaped child can have no group.  The
        # direct PID fallback is still safe because this runner owns it.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    else:
        # Windows has no POSIX process groups.  Preserve the existing exact-PID
        # cleanup behavior there rather than broadening this Public split repair.
        with suppress(ProcessLookupError):
            proc.kill()

    await proc.wait()


def _reader_task_states(tasks: tuple[asyncio.Task[Any], ...]) -> dict[str, str]:
    states: dict[str, str] = {}
    for name, task in zip(("stdout", "stderr", "rss"), tasks):
        if task.cancelled():
            states[name] = "cancelled"
        elif task.done():
            states[name] = "done"
        else:
            states[name] = "pending"
    return states


def _close_owned_local_pipe_transports(proc: asyncio.subprocess.Process) -> dict[str, str]:
    """Close only the runner's local pipe ends for this exact child.

    ``asyncio.subprocess.Process`` deliberately exposes stdout and stderr as
    readers rather than closable public handles.  A descendant that inherited a
    Windows pipe can therefore keep the reader pending after the direct child
    has exited.  The guarded transport lookup releases only the parent-side
    handles created for *proc*; it neither enumerates nor terminates a process.
    """
    outcomes: dict[str, str] = {}
    stdin = getattr(proc, "stdin", None)
    close_stdin = getattr(stdin, "close", None)
    if callable(close_stdin):
        try:
            close_stdin()
            outcomes["stdin"] = "closed"
        except Exception as exc:  # Diagnostic-only cleanup must keep settling.
            outcomes["stdin"] = f"close_error:{type(exc).__name__}"
    else:
        outcomes["stdin"] = "unavailable"

    transport = getattr(proc, "_transport", None)
    get_pipe_transport = getattr(transport, "get_pipe_transport", None)
    for fd, name in ((1, "stdout"), (2, "stderr")):
        pipe_transport = get_pipe_transport(fd) if callable(get_pipe_transport) else None
        close_pipe = getattr(pipe_transport, "close", None)
        if callable(close_pipe):
            try:
                close_pipe()
                outcomes[name] = "closed"
            except Exception as exc:  # Diagnostic-only cleanup must keep settling.
                outcomes[name] = f"close_error:{type(exc).__name__}"
        else:
            outcomes[name] = "unavailable"
        stream = getattr(proc, name, None)
        feed_eof = getattr(stream, "feed_eof", None)
        if callable(feed_eof):
            try:
                feed_eof()
            except Exception as exc:  # A completed reader can reject a second EOF.
                outcomes[name] = f"{outcomes[name]};feed_eof_error:{type(exc).__name__}"
    return outcomes


def _write_child_settlement_diagnostic(
    diagnostic_path: Path,
    *,
    phase: str,
    proc: asyncio.subprocess.Process,
    deadline_seconds: float,
    start_monotonic: float,
    reader_states_before_close: dict[str, str],
    pipe_close_outcomes: dict[str, str],
    code: str = CodexChildReaderSettlementTimeout.code,
) -> None:
    diagnostic_path.write_text(
        json.dumps(
            {
                "schema": "codex-child-settlement-diagnostic-v1",
                "code": code,
                "phase": phase,
                "direct_pid": proc.pid,
                "return_code": proc.returncode,
                "deadline_seconds": deadline_seconds,
                "elapsed_ms": int((time.monotonic() - start_monotonic) * 1000),
                "reader_states_before_close": reader_states_before_close,
                "local_pipe_close": pipe_close_outcomes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def _settle_reader_tasks(
    *,
    proc: asyncio.subprocess.Process,
    tasks: tuple[asyncio.Task[Any], ...],
    diagnostic_path: Path,
    phase: str,
    deadline_seconds: float,
    start_monotonic: float,
) -> None:
    """Await readers for a bounded interval, then release only local handles."""
    if not tasks:
        return
    reader_group = asyncio.gather(*tasks, return_exceptions=True)
    try:
        await asyncio.wait_for(
            asyncio.shield(reader_group),
            timeout=_PIPE_READER_SETTLEMENT_SECONDS,
        )
        return
    except asyncio.TimeoutError:
        pass

    states_before_close = _reader_task_states(tasks)
    pipe_close_outcomes = _close_owned_local_pipe_transports(proc)
    for task in tasks:
        if not task.done():
            task.cancel()
    await reader_group
    _write_child_settlement_diagnostic(
        diagnostic_path,
        phase=phase,
        proc=proc,
        deadline_seconds=deadline_seconds,
        start_monotonic=start_monotonic,
        reader_states_before_close=states_before_close,
        pipe_close_outcomes=pipe_close_outcomes,
    )
    raise CodexChildReaderSettlementTimeout(phase=phase)


async def _stop_process_and_settle_tasks(
    proc: asyncio.subprocess.Process,
    done: asyncio.Event,
    tasks: tuple[asyncio.Task[Any], ...],
    *,
    diagnostic_path: Path,
    phase: str,
    deadline_seconds: float,
    start_monotonic: float,
) -> None:
    try:
        await _stop_owned_process_tree(proc)
    finally:
        done.set()
        await _settle_reader_tasks(
            proc=proc,
            tasks=tasks,
            diagnostic_path=diagnostic_path,
            phase=phase,
            deadline_seconds=deadline_seconds,
            start_monotonic=start_monotonic,
        )


async def run_codex_exec_json(
    *,
    stage_id: str,
    role: str,
    prompt: str,
    work_dir: Path,
    artifact_dir: Path,
    model: str,
    reasoning_effort: str,
    sandbox: str = "read-only",
    extra_config: list[str] | None = None,
    ephemeral: bool = True,
    image_paths: list[Path] | None = None,
    timeout_seconds: int = 900,
    admission_timeout_seconds: float | None = None,
    audit_context: CodexAuditContext | None = None,
    thread_started_hook: Callable[[str], None] | None = None,
) -> CodexExecResult:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = artifact_dir / "prompt.md"
    raw_path = artifact_dir / "codex.raw.jsonl"
    observed_path = artifact_dir / "codex.observed.jsonl"
    stderr_path = artifact_dir / "codex.stderr.txt"
    final_response_path = artifact_dir / "final_response.txt"
    transcript_path = artifact_dir / "codex.transcript.json"
    private_artifact_dir = artifact_dir / ".codex-private"
    private_artifact_dir.mkdir(parents=True, exist_ok=True)
    command_path = private_artifact_dir / "codex.command.json"
    executable_identity_path = private_artifact_dir / "codex.executable-identity.json"
    result_receipt_path = private_artifact_dir / "codex.result.json"
    settlement_diagnostic_path = private_artifact_dir / "codex.settlement-diagnostic.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    try:
        executable_identity = _resolve_runner_identity()
    except CodexExecutableUnavailable as exc:
        # A Windows resolver failure happens before a command exists, so the
        # private receipt is the only durable diagnostic surface.  The public
        # exception deliberately never exposes its precise trust failure.
        if sys.platform == "win32" or exc.reason.startswith("windows_"):
            _write_windows_identity_failure_receipt(
                executable_identity_path,
                phase="resolution",
                exc=exc,
            )
            raise _stable_windows_identity_error() from exc
        raise
    executable_identity_path.write_text(
        json.dumps(_identity_receipt(executable_identity), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    command = build_codex_command(
        work_dir=work_dir,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox=sandbox,
        extra_config=extra_config,
        ephemeral=ephemeral,
        image_paths=image_paths,
        executable_identity=executable_identity,
    )
    command_path.write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")

    started_at = utc_now_iso()
    start_monotonic = time.monotonic()
    invocation_id = None
    if audit_context is not None:
        invocation_id = codex_audit.start_codex_invocation(
            run_id=audit_context.run_id,
            run_slide_id=audit_context.run_slide_id,
            stage_id=stage_id,
            role=role,
            attempt=audit_context.attempt,
            command=command,
            cwd=str(work_dir),
            sandbox=sandbox,
            model=model,
            reasoning_effort=reasoning_effort,
            prompt_sha256=sha256_text(prompt),
            raw_jsonl_path=str(raw_path),
            observed_jsonl_path=str(observed_path),
            metadata=audit_context.metadata,
            started_at=started_at,
        )
        if audit_context.work_item_id is not None:
            codex_audit.link_codex_invocation_to_work_item(
                work_item_id=audit_context.work_item_id,
                invocation_id=invocation_id,
                attempt_number=audit_context.attempt,
            )
    gate = codex_platform_gate.get_platform_codex_child_gate()
    gate_lease = await gate.acquire_async(
        timeout_seconds=(
            timeout_seconds
            if admission_timeout_seconds is None
            else admission_timeout_seconds
        )
    )
    proc: asyncio.subprocess.Process | None = None
    done: asyncio.Event | None = None
    child_started_monotonic: float | None = None
    reader_tasks: tuple[asyncio.Task[Any], ...] = ()
    heartbeat_task: asyncio.Task[Any] | None = None
    transcript_writer: _ProjectedTranscriptWriter | None = None
    settled = False
    release_reason = "completed"

    async def settle_owned_child() -> None:
        """Reap only this lease-bound process, then settle its three readers."""
        nonlocal settled
        if settled or proc is None:
            return
        settlement_start = child_started_monotonic or start_monotonic
        if done is None:
            await _stop_owned_process_tree(proc)
        else:
            await _stop_process_and_settle_tasks(
                proc,
                done,
                reader_tasks,
                diagnostic_path=settlement_diagnostic_path,
                phase="exception_cleanup",
                deadline_seconds=float(timeout_seconds),
                start_monotonic=settlement_start,
            )
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        settled = True

    try:
        # This is deliberately immediately adjacent to the sole Platform child
        # creation seam.  Nothing outside this function receives a PID lease.
        spawn_kwargs: dict[str, Any] = {
            "cwd": str(work_dir),
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "posix":
            spawn_kwargs["start_new_session"] = True
        # The admission wait can be long enough for a managed cache update to
        # race the initial resolver. Recheck the exact selected identity after
        # admission and immediately before the sole spawn call.
        try:
            verify_codex_executable_identity(executable_identity)
        except CodexExecutableUnavailable as exc:
            if executable_identity.source == "windows_desktop_managed_cache":
                _write_windows_identity_failure_receipt(
                    executable_identity_path,
                    phase="pre_spawn",
                    exc=exc,
                )
                raise _stable_windows_identity_error() from exc
            raise
        proc = await asyncio.create_subprocess_exec(*command, **spawn_kwargs)
        child_started_monotonic = time.monotonic()
        gate.record_spawned_child(gate_lease, pid=proc.pid)
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        if (
            audit_context is not None
            and audit_context.work_item_id is not None
            and audit_context.lease_owner is not None
            and audit_context.fencing_token is not None
            and not codex_supervisor.bind_process(
                work_item_id=audit_context.work_item_id,
                owner=audit_context.lease_owner,
                fencing_token=audit_context.fencing_token,
                pid=proc.pid,
            )
        ):
            release_reason = "supervisor_bind_rejected"
            raise RuntimeError("lost supervised child ownership before process bind")
        done = asyncio.Event()
        transcript_writer = _ProjectedTranscriptWriter(prompt, transcript_path)
        reader_kwargs: dict[str, object] = {"transcript_writer": transcript_writer}
        if thread_started_hook is not None:
            reader_kwargs["thread_started_hook"] = thread_started_hook
        try:
            if invocation_id is None:
                stdout_reader = _read_stdout_jsonl(
                    proc.stdout,
                    raw_path,
                    observed_path,
                    **reader_kwargs,
                )
            else:
                stdout_reader = _read_stdout_jsonl(
                    proc.stdout,
                    raw_path,
                    observed_path,
                    invocation_id=invocation_id,
                    **reader_kwargs,
                )
        except TypeError as exc:
            # Legacy tests and recovery seams may still provide the historical
            # three/four-argument reader double. Production always uses the
            # projection-aware path above.
            if "transcript_writer" not in str(exc):
                raise
            fallback_kwargs: dict[str, object] = {}
            if thread_started_hook is not None:
                fallback_kwargs["thread_started_hook"] = thread_started_hook
            if invocation_id is None:
                stdout_reader = _read_stdout_jsonl(
                    proc.stdout,
                    raw_path,
                    observed_path,
                    **fallback_kwargs,
                )
            else:
                stdout_reader = _read_stdout_jsonl(
                    proc.stdout,
                    raw_path,
                    observed_path,
                    invocation_id=invocation_id,
                    **fallback_kwargs,
                )
        stdout_task = asyncio.create_task(stdout_reader)
        stderr_task = asyncio.create_task(_read_stderr(proc.stderr, stderr_path))
        rss_task = asyncio.create_task(_sample_peak_rss(proc.pid, done))
        reader_tasks = (stdout_task, stderr_task, rss_task)
        heartbeat_task = (
            asyncio.create_task(_heartbeat_supervised_child(audit_context, done)) if audit_context else None
        )
        timed_out = False
        try:
            try:
                child_deadline = child_started_monotonic + timeout_seconds
                await asyncio.wait_for(
                    _send_process_stdin(proc.stdin, prompt.encode("utf-8")),
                    timeout=max(0.0, child_deadline - time.monotonic()),
                )
                exit_code = await asyncio.wait_for(
                    proc.wait(), timeout=max(0.0, child_deadline - time.monotonic())
                )
                if exit_code != 0:
                    release_reason = "child_exit_nonzero"
            except asyncio.TimeoutError:
                timed_out = True
                release_reason = "timeout_process_tree"
                await _stop_owned_process_tree(proc)
                exit_code = proc.returncode
                assert exit_code is not None
        finally:
            done.set()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
        await _settle_reader_tasks(
            proc=proc,
            tasks=reader_tasks,
            diagnostic_path=settlement_diagnostic_path,
            phase="post_exit_reader_settlement",
            deadline_seconds=float(timeout_seconds),
            start_monotonic=child_started_monotonic,
        )
        if timed_out:
            _write_child_settlement_diagnostic(
                settlement_diagnostic_path,
                phase="deadline_process_tree_settled",
                proc=proc,
                deadline_seconds=float(timeout_seconds),
                start_monotonic=child_started_monotonic,
                reader_states_before_close=_reader_task_states(reader_tasks),
                pipe_close_outcomes=_close_owned_local_pipe_transports(proc),
                code="codex_child_deadline_elapsed",
            )
        stream_summary = await stdout_task
        transcript_writer.finish()
        if stream_summary is None:
            # Test doubles and historical internal callers may still return None.
            stream_summary = CodexStreamSummary()
        await stderr_task
        peak_rss_kb = await rss_task
        settled = True
        ended_at = utc_now_iso()
        elapsed_ms = int((time.monotonic() - start_monotonic) * 1000)
        final_text = stream_summary.final_text
        if stream_summary.final_capture is not None:
            stream_summary.final_capture.copy_to(final_response_path)
        else:
            final_response_path.write_text(final_text, encoding="utf-8")
        result_receipt_path.write_text(
            json.dumps(
                {
                    "schema": "codex-process-result-v1",
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "elapsed_ms": elapsed_ms,
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "stdout_jsonl_bytes": raw_path.stat().st_size,
                    "stderr_bytes": stderr_path.stat().st_size,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return CodexExecResult(
            stage_id=stage_id,
            role=role,
            command=command,
            cwd=work_dir,
            prompt_path=prompt_path,
            raw_jsonl_path=raw_path,
            observed_jsonl_path=observed_path,
            stderr_path=stderr_path,
            command_path=command_path,
            final_response_path=final_response_path,
            transcript_path=transcript_path,
            started_at=started_at,
            ended_at=ended_at,
            elapsed_ms=elapsed_ms,
            exit_code=exit_code,
            prompt_sha256=sha256_text(prompt),
            final_text=final_text,
            peak_rss_kb=peak_rss_kb,
            stream_summary=stream_summary,
            timed_out=timed_out,
            invocation_id=invocation_id,
            executable_identity_path=executable_identity_path,
            result_receipt_path=result_receipt_path,
        )
    except asyncio.CancelledError:
        release_reason = "cancelled"
        if transcript_writer is not None:
            transcript_writer.abort()
        await settle_owned_child()
        raise
    except BaseException:
        if proc is None:
            release_reason = "spawn_failed"
        elif release_reason == "completed":
            release_reason = "failed"
        if transcript_writer is not None:
            transcript_writer.abort()
        await settle_owned_child()
        raise
    finally:
        # A release is safety-significant: retain the reservation rather than
        # admitting another child if a process/reader settlement itself fails.
        if proc is not None and not settled:
            await settle_owned_child()
        if proc is None or settled:
            gate.release(gate_lease, reason=release_reason)
