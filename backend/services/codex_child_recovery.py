"""Bounded, idempotent recovery for backend-owned Codex child work."""

from __future__ import annotations

import codecs
from contextlib import contextmanager
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Any, Awaitable, Callable
from uuid import uuid4

import db as dbmod
from backend.domain.status import COMPLETED, FAILED, TERMINAL_STATUSES
from backend.services import codex_audit, codex_supervisor
from backend.services.platform_runtime import exclusive_file_lock
from backend.services.codex_exec import (
    CodexAuditContext,
    CodexExecResult,
    has_codex_result_final_content,
    materialize_codex_result_final_text,
)
from backend.services.codex_jsonl_stream import (
    SOURCE_WINDOW_BYTES,
    CodexStreamSummary,
    FinalTextCapture,
    SemanticJsonlRecord,
    iter_semantic_jsonl_file_records,
)
from backend.services.private_file_permissions import restrict_owner_only_fd


UTC = timezone.utc


class ChildRecoveryBlocked(RuntimeError):
    """Raised when supervised child work cannot safely continue."""


def _now() -> datetime:
    return datetime.now(UTC)


def _work_item_for_key(idempotency_key: str) -> dict | None:
    db = dbmod.get_db()
    try:
        row = db.execute(
            "SELECT * FROM codex_work_items WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _get_or_create_work_item(
    *,
    run_id: int | None,
    run_slide_id: int | None,
    stage_id: str,
    role: str,
    idempotency_key: str,
    max_recoveries: int,
) -> dict:
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """INSERT OR IGNORE INTO codex_work_items
               (run_id, run_slide_id, stage_id, role, idempotency_key, max_recoveries)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, run_slide_id, stage_id, role, idempotency_key, max_recoveries),
        )
        row = db.execute(
            "SELECT * FROM codex_work_items WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("failed to create supervised work item")
        item = dict(row)
        identity = (item.get("run_id"), item.get("run_slide_id"), item["stage_id"], item["role"])
        expected = (run_id, run_slide_id, stage_id, role)
        if identity != expected:
            raise ChildRecoveryBlocked("idempotency key belongs to different business work")
        db.commit()
        return item
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _latest_invocation(work_item_id: int) -> dict | None:
    db = dbmod.get_db()
    try:
        row = db.execute(
            """SELECT ci.*, cwi.attempt_number
               FROM codex_work_item_invocations cwi
               JOIN codex_invocations ci ON ci.id = cwi.invocation_id
               WHERE cwi.work_item_id = ?
               ORDER BY cwi.attempt_number DESC
               LIMIT 1""",
            (work_item_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _has_acceptable_result(result: CodexExecResult, *, require_final_text: bool) -> bool:
    return result.exit_code == 0 and (
        not require_final_text or has_codex_result_final_content(result)
    )


def _cached_result(invocation: dict, *, require_final_text: bool) -> CodexExecResult:
    raw_path = Path(str(invocation.get("raw_jsonl_path") or ""))
    if not raw_path.exists():
        raise ChildRecoveryBlocked("cached child result is missing raw evidence")
    artifact_dir = raw_path.parent
    final_path = artifact_dir / "final_response.txt"
    if not final_path.exists():
        raise ChildRecoveryBlocked("cached child result is missing final response")
    final_text, final_capture = _cached_final_response(final_path)
    if require_final_text and not (final_text or final_capture is not None):
        raise ChildRecoveryBlocked("cached child result is empty")
    return CodexExecResult(
        stage_id=str(invocation.get("stage_id") or ""),
        role=str(invocation.get("role") or ""),
        command=_json_list(invocation.get("command_json")),
        cwd=Path(str(invocation.get("cwd") or artifact_dir)),
        prompt_path=artifact_dir / "prompt.md",
        raw_jsonl_path=raw_path,
        observed_jsonl_path=Path(str(invocation.get("observed_jsonl_path") or artifact_dir / "codex.observed.jsonl")),
        stderr_path=artifact_dir / "codex.stderr.txt",
        command_path=artifact_dir / "codex.command.json",
        final_response_path=final_path,
        transcript_path=artifact_dir / "codex.transcript.json",
        started_at=str(invocation.get("started_at") or ""),
        ended_at=str(invocation.get("ended_at") or ""),
        elapsed_ms=int(invocation.get("elapsed_ms") or 0),
        exit_code=int(invocation.get("exit_code") or 0),
        prompt_sha256=str(invocation.get("prompt_sha256") or ""),
        final_text=final_text,
        peak_rss_kb=None,
        stream_summary=CodexStreamSummary(final_capture=final_capture) if final_capture is not None else None,
        invocation_id=int(invocation["id"]),
    )


def _cached_final_response(final_path: Path) -> tuple[str, FinalTextCapture | None]:
    """Read a cached final response in fixed windows without owning its durable path."""
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    text_length = 0
    inline_chunks: list[str] = []
    inline_bytes = 0
    inline_compatible = True
    try:
        with final_path.open("rb") as source:
            while chunk := source.read(SOURCE_WINDOW_BYTES):
                digest.update(chunk)
                inline_bytes += len(chunk)
                if inline_bytes > SOURCE_WINDOW_BYTES:
                    inline_compatible = False
                    inline_chunks.clear()
                decoded = decoder.decode(chunk)
                text_length += len(decoded)
                if inline_compatible and decoded:
                    inline_chunks.append(decoded)
            decoded = decoder.decode(b"", final=True)
            text_length += len(decoded)
            if inline_compatible and decoded:
                inline_chunks.append(decoded)
    except UnicodeDecodeError as exc:
        raise ChildRecoveryBlocked("cached child final response is not valid UTF-8") from exc
    if inline_compatible:
        return "".join(inline_chunks), None
    return "", FinalTextCapture(
        inline_text=None,
        spool_path=final_path,
        text_length=text_length,
        text_sha256=digest.hexdigest(),
        owns_spool=False,
    )


def _record_legacy_result(
    result: CodexExecResult,
    *,
    context: CodexAuditContext,
    metadata: dict[str, Any] | None,
    require_final_text: bool,
) -> int:
    """Give older test/integration runners the same durable invocation identity."""
    success = _has_acceptable_result(result, require_final_text=require_final_text)
    final_response_path = getattr(result, "final_response_path", None)
    if final_response_path is None:
        final_response_path = Path(result.raw_jsonl_path).parent / "final_response.txt"
        final_response_path.write_text(
            materialize_codex_result_final_text(result), encoding="utf-8"
        )
        try:
            setattr(result, "final_response_path", final_response_path)
        except (AttributeError, TypeError):
            pass
    invocation_id = codex_audit.start_codex_invocation(
        run_id=context.run_id,
        run_slide_id=context.run_slide_id,
        stage_id=result.stage_id,
        role=result.role,
        attempt=context.attempt,
        command=result.command,
        cwd=str(result.cwd),
        sandbox="read-only",
        prompt_sha256=result.prompt_sha256,
        raw_jsonl_path=str(result.raw_jsonl_path),
        observed_jsonl_path=str(result.observed_jsonl_path),
        started_at=result.started_at,
        metadata=metadata,
    )
    codex_audit.link_codex_invocation_to_work_item(
        work_item_id=int(context.work_item_id),
        invocation_id=invocation_id,
        attempt_number=context.attempt,
    )
    _replay_legacy_evidence_compactly(
        invocation_id=invocation_id,
        raw_path=Path(result.raw_jsonl_path),
        observed_path=Path(result.observed_jsonl_path),
        intended_terminal_state="result_received" if success else FAILED,
    )
    codex_audit.finalize_codex_invocation(
        invocation_id=invocation_id,
        output_path=str(final_response_path),
        ended_at=result.ended_at,
        elapsed_ms=result.elapsed_ms,
        exit_code=result.exit_code,
        status="result_received" if success else FAILED,
        error_message=None if success else "child process exited before a final result",
        metadata=metadata,
    )
    try:
        setattr(result, "invocation_id", invocation_id)
    except (AttributeError, TypeError):
        pass
    return invocation_id


def _replay_legacy_evidence_compactly(
    *,
    invocation_id: int,
    raw_path: Path,
    observed_path: Path,
    intended_terminal_state: str = "result_received",
    _publish_lock_held: bool = False,
) -> None:
    """Publish compact legacy evidence through a recoverable PREPARING intent."""
    if not _publish_lock_held:
        with _observed_publish_lock(observed_path):
            return _replay_legacy_evidence_compactly(
                invocation_id=invocation_id,
                raw_path=raw_path,
                observed_path=observed_path,
                intended_terminal_state=intended_terminal_state,
                _publish_lock_held=True,
            )
    if dbmod.get_codex_observed_publish_intent(invocation_id) is not None:
        raise ChildRecoveryBlocked("observed publish intent already exists")
    stat = raw_path.stat()
    file_identity = f"{stat.st_dev}:{stat.st_ino}"
    generation_id = uuid4().hex
    temporary_path = observed_path.parent / f".codex.observed-{generation_id}.tmp"
    try:
        dbmod.begin_codex_observed_publish_intent(
            invocation_id=invocation_id,
            generation_id=generation_id,
            observed_path=str(observed_path),
            temp_path=str(temporary_path),
            old_observed_sha256=_sha256_file(observed_path),
            intended_terminal_state=intended_terminal_state,
        )
    except sqlite3.IntegrityError as exc:
        raise ChildRecoveryBlocked("observed publish intent already exists") from exc
    try:
        fd = os.open(str(temporary_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except BaseException:
        # The durable PREPARING reservation is intentionally retained.  A
        # recovery can clear it only after proving that no batch committed.
        raise
    try:
        restrict_owner_only_fd(fd)
    except BaseException:
        os.close(fd)
        raise
    temp_file_identity = _descriptor_file_identity(fd)
    dbmod.record_codex_observed_publish_temp_identity(
        invocation_id=invocation_id,
        generation_id=generation_id,
        temp_file_identity=temp_file_identity,
    )
    try:
        new_sha256, expected_count = _write_compact_legacy_observed_temp(
            descriptor=fd,
            temporary_path=temporary_path,
            raw_path=raw_path,
            observed_path=observed_path,
        )
        dbmod.update_codex_observed_publish_intent_preparing(
            invocation_id=invocation_id,
            new_observed_sha256=new_sha256,
            expected_event_count=expected_count,
        )
        _require_owned_observed_temp(temporary_path, temp_file_identity)
        try:
            dbmod.commit_codex_observed_publish_batch(
                invocation_id=invocation_id,
                records=_iter_legacy_publish_records(
                    raw_path=raw_path,
                    observed_path=observed_path,
                    file_identity=file_identity,
                ),
            )
        except BaseException:
            if dbmod.inspect_codex_observed_publish_intent_outcome(invocation_id) != "COMMITTED":
                raise
        _require_owned_observed_temp(temporary_path, temp_file_identity)
        _replace_observed_temp(temporary_path, observed_path)
        _fsync_directory(observed_path.parent)
        dbmod.complete_codex_observed_publish_intent(invocation_id)
    except BaseException:
        # PREPARING/PREPARED and its identity remain durable for recovery.  Do
        # not infer that a raised commit/replace means nothing reached disk.
        raise


def _iter_legacy_semantic_pairs(*, raw_path: Path, observed_path: Path):
    """Lock-step pair evidence and discard each raw capture on every next() path."""
    raw_records = iter_semantic_jsonl_file_records(raw_path)
    observed_records = iter_semantic_jsonl_file_records(observed_path, observed_wrapper=True)
    while True:
        try:
            raw_record = next(raw_records)
        except StopIteration:
            try:
                next(observed_records)
            except StopIteration:
                return
            raise ChildRecoveryBlocked("legacy child evidence line counts differ")
        try:
            try:
                observed_record = next(observed_records)
            except StopIteration as exc:
                raise ChildRecoveryBlocked("legacy child evidence line counts differ") from exc
            wrapper = observed_record.event
            observed_at = wrapper.get("observed_at") if isinstance(wrapper, dict) else None
            observed_event = wrapper.get("event") if isinstance(wrapper, dict) else None
            if not isinstance(observed_at, str) or not observed_at or not isinstance(observed_event, dict):
                raise ChildRecoveryBlocked("legacy child observed evidence is invalid")
            if not _legacy_events_match(raw_record, observed_record):
                raise ChildRecoveryBlocked("legacy child raw and observed evidence differ")
            yield raw_record, observed_at
        finally:
            raw_record.discard_final_capture()


def _write_compact_legacy_observed_temp(
    *,
    descriptor: int,
    temporary_path: Path,
    raw_path: Path,
    observed_path: Path,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    expected_count = 0
    with os.fdopen(descriptor, "wb") as compact_observed:
        for raw_record, observed_at in _iter_legacy_semantic_pairs(
            raw_path=raw_path,
            observed_path=observed_path,
        ):
            payload = (
                json.dumps({"observed_at": observed_at, "event": raw_record.event}, ensure_ascii=False)
                + "\n"
            ).encode("utf-8")
            compact_observed.write(payload)
            digest.update(payload)
            expected_count += 1
        compact_observed.flush()
        _fsync_observed_temp(compact_observed)
    _fsync_directory(temporary_path.parent)
    return digest.hexdigest(), expected_count


def _iter_legacy_publish_records(*, raw_path: Path, observed_path: Path, file_identity: str):
    for sequence, (raw_record, observed_at) in enumerate(
        _iter_legacy_semantic_pairs(raw_path=raw_path, observed_path=observed_path),
        start=1,
    ):
        event = raw_record.event
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        yield {
            "sequence": sequence,
            "payload": event,
            "observed_at": observed_at,
            "raw_bytes": raw_record.raw_bytes or b"",
            "raw_sha256": raw_record.raw_range.sha256,
            "byte_offset_start": raw_record.raw_range.start,
            "byte_offset_end": raw_record.raw_range.end,
            "file_identity": file_identity,
            "projection": {"observed_at": observed_at, "event": event},
            "event_timestamp": codex_audit._event_timestamp(event),
            "event_type": event.get("type") if isinstance(event.get("type"), str) else None,
            "item_id": item.get("id") if isinstance(item.get("id"), str) else None,
            "item_type": item.get("type") if isinstance(item.get("type"), str) else None,
            "is_error": codex_audit._is_error_event(event),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(30_720):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_observed_temp(handle) -> None:
    os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_observed_temp(temporary_path: Path, observed_path: Path) -> None:
    os.replace(temporary_path, observed_path)


@contextmanager
def _observed_publish_lock(observed_path: Path):
    """Serialize cooperating observed publication workers for one private directory."""
    with exclusive_file_lock(
        observed_path.parent / ".codex.observed.publish.lock",
        timeout_seconds=None,
    ):
        yield


def _descriptor_file_identity(descriptor: int) -> str:
    file_stat = os.fstat(descriptor)
    return f"{file_stat.st_dev}:{file_stat.st_ino}"


def _require_owned_observed_temp(path: Path, expected_identity: object) -> None:
    """Require the reserved pathname to remain the exact O_EXCL-created file."""
    if not isinstance(expected_identity, str) or not expected_identity:
        raise ChildRecoveryBlocked("publish intent temp ownership is unproven")
    try:
        file_stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ChildRecoveryBlocked("publish intent temp ownership is unproven") from exc
    if not stat.S_ISREG(file_stat.st_mode) or _stat_file_identity(file_stat) != expected_identity:
        raise ChildRecoveryBlocked("publish intent temp ownership is unproven")


def _stat_file_identity(file_stat: os.stat_result) -> str:
    return f"{file_stat.st_dev}:{file_stat.st_ino}"


def _recover_legacy_observed_publish_intent(invocation_id: int) -> None:
    """Finish one interrupted legacy observed publication without a model call."""
    intent = dbmod.get_codex_observed_publish_intent(invocation_id)
    if intent is None:
        return
    lock_observed_path = Path(str(intent["observed_path"]))
    with _observed_publish_lock(lock_observed_path):
        intent = dbmod.get_codex_observed_publish_intent(invocation_id)
        if intent is None:
            return
        observed_path = Path(str(intent["observed_path"]))
        temporary_path = Path(str(intent["temp_path"]))
        target_sha256 = _sha256_file(observed_path) if observed_path.exists() else None
        old_sha256 = str(intent["old_observed_sha256"])
        new_sha256 = intent.get("new_observed_sha256")
        temp_file_identity = intent.get("temp_file_identity")
        outcome = dbmod.inspect_codex_observed_publish_intent_outcome(invocation_id)
        if intent["state"] == "PREPARING":
            if outcome != "NOT_COMMITTED" or target_sha256 != old_sha256:
                raise ChildRecoveryBlocked("publish intent conflict while PREPARING")
            _require_owned_observed_temp(temporary_path, temp_file_identity)
            temporary_path.unlink()
            dbmod.clear_codex_observed_publish_intent(invocation_id)
            invocation = _legacy_invocation_paths(invocation_id)
            _replay_legacy_evidence_compactly(
                invocation_id=invocation_id,
                raw_path=Path(invocation["raw_jsonl_path"]),
                observed_path=observed_path,
                intended_terminal_state=str(intent["intended_terminal_state"]),
                _publish_lock_held=True,
            )
            return
        if intent["state"] != "PREPARED" or outcome != "COMMITTED" or not isinstance(new_sha256, str):
            raise ChildRecoveryBlocked("publish intent conflict")
        if target_sha256 == new_sha256:
            _fsync_directory(observed_path.parent)
        elif target_sha256 == old_sha256:
            if temporary_path.exists():
                _require_owned_observed_temp(temporary_path, temp_file_identity)
                if _sha256_file(temporary_path) != new_sha256:
                    raise ChildRecoveryBlocked("publish intent conflict")
                publish_path = temporary_path
            else:
                publish_path = _rebuild_compact_observed_temp_from_db(
                    invocation_id=invocation_id,
                    temporary_path=temporary_path,
                )
                if _sha256_file(publish_path) != new_sha256:
                    raise ChildRecoveryBlocked("publish intent conflict")
            _replace_observed_temp(publish_path, observed_path)
            _fsync_directory(observed_path.parent)
        else:
            raise ChildRecoveryBlocked("publish intent conflict")
        dbmod.complete_codex_observed_publish_intent(invocation_id)


def _legacy_invocation_paths(invocation_id: int) -> dict[str, Any]:
    db = dbmod.get_db()
    try:
        row = db.execute(
            "SELECT raw_jsonl_path, observed_jsonl_path FROM codex_invocations WHERE id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None or not row["raw_jsonl_path"] or not row["observed_jsonl_path"]:
            raise ChildRecoveryBlocked("publish intent invocation evidence is missing")
        return dict(row)
    finally:
        db.close()


def _rebuild_compact_observed_temp_from_db(*, invocation_id: int, temporary_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".codex.observed-rebuild-",
        suffix=".tmp",
        dir=temporary_path.parent,
    )
    rebuild_path = Path(name)
    restrict_owner_only_fd(descriptor)
    rebuilt = False
    try:
        with os.fdopen(descriptor, "wb") as target:
            db = dbmod.get_db()
            try:
                rows = db.execute(
                    """SELECT projection_json FROM codex_event_raw_lines
                       WHERE invocation_id = ? ORDER BY sequence""",
                    (invocation_id,),
                )
                for row in rows:
                    projection = json.loads(row["projection_json"])
                    if not isinstance(projection, dict):
                        raise ChildRecoveryBlocked("publish intent rebuild projection is invalid")
                    target.write((json.dumps(projection, ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                db.close()
            target.flush()
            _fsync_observed_temp(target)
        _fsync_directory(rebuild_path.parent)
        rebuilt = True
        return rebuild_path
    finally:
        if not rebuilt:
            rebuild_path.unlink(missing_ok=True)


def _recover_legacy_observed_publish_intents_for_work_item(work_item_id: int) -> None:
    db = dbmod.get_db()
    try:
        rows = db.execute(
            """SELECT intent.invocation_id
               FROM codex_observed_publish_intents AS intent
               JOIN codex_work_item_invocations AS link
                 ON link.invocation_id = intent.invocation_id
               WHERE link.work_item_id = ? ORDER BY intent.invocation_id""",
            (work_item_id,),
        ).fetchall()
    finally:
        db.close()
    for row in rows:
        _recover_legacy_observed_publish_intent(int(row["invocation_id"]))


def _legacy_events_match(raw_record: SemanticJsonlRecord, observed_record: SemanticJsonlRecord) -> bool:
    """Compare legacy evidence without expanding an oversized selected string."""
    if raw_record.final_text_evidence != observed_record.final_text_evidence:
        return False
    wrapper = observed_record.event
    observed_event = wrapper.get("event")
    if not isinstance(observed_event, dict):
        return False
    if not raw_record.oversized:
        return observed_event == raw_record.event
    raw_item = raw_record.event.get("item")
    observed_item = observed_event.get("item")
    raw_item = raw_item if isinstance(raw_item, dict) else {}
    observed_item = observed_item if isinstance(observed_item, dict) else {}
    return (
        observed_event.get("type") == raw_record.event.get("type")
        and observed_event.get("thread_id") == raw_record.event.get("thread_id")
        and observed_item.get("id") == raw_item.get("id")
        and observed_item.get("type") == raw_item.get("type")
    )


@dataclass
class SupervisedChildExecution:
    work_item_id: int
    owner: str
    fencing_token: int
    result: CodexExecResult
    reused_result: bool
    already_terminal: bool
    attempt_count: int
    now: Callable[[], datetime]

    def complete_projection(self) -> bool:
        if self.already_terminal:
            return True
        completed = codex_supervisor.mark_terminal(
            work_item_id=self.work_item_id,
            owner=self.owner,
            fencing_token=self.fencing_token,
            status=COMPLETED,
            reason="business projection completed",
            now=self.now(),
        )
        if completed:
            self.already_terminal = True
        return completed

    def fail_unrecoverable(self, reason: str) -> bool:
        if self.already_terminal:
            return False
        failed = codex_supervisor.mark_terminal(
            work_item_id=self.work_item_id,
            owner=self.owner,
            fencing_token=self.fencing_token,
            status=FAILED,
            reason=reason,
            now=self.now(),
        )
        if failed:
            self.already_terminal = True
        return failed


async def run_supervised_codex_child(
    *,
    run_id: int | None,
    run_slide_id: int | None,
    stage_id: str,
    role: str,
    idempotency_key: str,
    invoke: Callable[[CodexAuditContext], Awaitable[CodexExecResult]],
    lease_seconds: int = codex_supervisor.DEFAULT_LEASE_SECONDS,
    max_recoveries: int = 2,
    metadata: dict[str, Any] | None = None,
    require_final_text: bool = True,
    now: Callable[[], datetime] = _now,
) -> SupervisedChildExecution:
    """Run or recover one child call without repeating a received final result."""
    item = _get_or_create_work_item(
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=stage_id,
        role=role,
        idempotency_key=idempotency_key,
        max_recoveries=max_recoveries,
    )
    work_item_id = int(item["id"])
    if item["status"] in TERMINAL_STATUSES:
        if item["status"] != COMPLETED:
            raise ChildRecoveryBlocked(str(item.get("terminal_reason") or item["status"]))
        invocation = _latest_invocation(work_item_id)
        if invocation is None:
            raise ChildRecoveryBlocked("completed child work is missing its invocation")
        return SupervisedChildExecution(
            work_item_id=work_item_id,
            owner=str(item.get("lease_owner") or "terminal"),
            fencing_token=int(item.get("fencing_token") or 0),
            result=_cached_result(invocation, require_final_text=require_final_text),
            reused_result=True,
            already_terminal=True,
            attempt_count=int(item.get("attempt_count") or 1),
            now=now,
        )

    owner = f"child-{uuid4()}"
    has_previous_owner = bool(item.get("lease_owner"))
    while True:
        lease = codex_supervisor.acquire_lease(
            work_item_id=work_item_id,
            owner=owner,
            now=now(),
            lease_seconds=lease_seconds,
            recovery=has_previous_owner,
        )
        if not lease.acquired:
            if lease.reason == "recovery_budget_exhausted":
                latest = _work_item_for_key(idempotency_key) or item
                codex_supervisor.mark_terminal(
                    work_item_id=work_item_id,
                    owner=str(latest.get("lease_owner") or owner),
                    fencing_token=int(latest.get("fencing_token") or 0),
                    status=FAILED,
                    reason="recovery budget exhausted",
                    now=now(),
                )
                raise ChildRecoveryBlocked("recovery budget exhausted")
            raise ChildRecoveryBlocked(f"child work is not recoverable: {lease.reason}")

        token = int(lease.fencing_token or 0)
        item = _work_item_for_key(idempotency_key) or item
        _recover_legacy_observed_publish_intents_for_work_item(work_item_id)
        attempt = int(item["attempt_count"])
        cached = _latest_invocation(work_item_id)
        if cached is not None and cached.get("status") in {"result_received", COMPLETED}:
            return SupervisedChildExecution(
                work_item_id=work_item_id,
                owner=owner,
                fencing_token=token,
                result=_cached_result(cached, require_final_text=require_final_text),
                reused_result=True,
                already_terminal=False,
                attempt_count=attempt,
                now=now,
            )

        context = CodexAuditContext(
            run_id=run_id,
            run_slide_id=run_slide_id,
            attempt=attempt,
            work_item_id=work_item_id,
            lease_owner=owner,
            fencing_token=token,
            lease_seconds=lease_seconds,
            metadata=metadata,
        )
        try:
            result = await invoke(context)
        except Exception as exc:
            reason = f"child process interrupted before a result: {exc}"
            latest = _latest_invocation(work_item_id)
            if latest is not None and latest.get("status") == "running":
                codex_audit.mark_codex_invocation_interrupted(
                    int(latest["id"]),
                    reason,
                    ended_at=now().astimezone(UTC).isoformat(),
                )
            if not codex_supervisor.mark_attempt_lost(
                work_item_id=work_item_id,
                owner=owner,
                fencing_token=token,
                reason=reason,
                now=now(),
            ):
                raise ChildRecoveryBlocked("lost supervisor ownership after child interruption") from exc
            has_previous_owner = True
            continue
        invocation_id_value = getattr(result, "invocation_id", None)
        live_invocation = invocation_id_value is not None
        if invocation_id_value is None:
            invocation_id_value = _record_legacy_result(
                result,
                context=context,
                metadata=metadata,
                require_final_text=require_final_text,
            )
        if _has_acceptable_result(result, require_final_text=require_final_text):
            if live_invocation:
                codex_audit.finalize_codex_invocation(
                    invocation_id=int(invocation_id_value),
                    output_path=str(result.final_response_path),
                    ended_at=result.ended_at,
                    elapsed_ms=result.elapsed_ms,
                    exit_code=result.exit_code,
                    status="result_received",
                    error_message=None,
                )
            if not codex_supervisor.mark_result_received(
                work_item_id=work_item_id,
                owner=owner,
                fencing_token=token,
                now=now(),
                lease_seconds=lease_seconds,
            ):
                raise ChildRecoveryBlocked("lost supervisor ownership before result projection")
            return SupervisedChildExecution(
                work_item_id=work_item_id,
                owner=owner,
                fencing_token=token,
                result=result,
                reused_result=False,
                already_terminal=False,
                attempt_count=attempt,
                now=now,
            )

        reason = f"child process exited before a final result (exit_code={result.exit_code})"
        if live_invocation:
            codex_audit.finalize_codex_invocation(
                invocation_id=int(invocation_id_value),
                output_path=str(result.final_response_path),
                ended_at=result.ended_at,
                elapsed_ms=result.elapsed_ms,
                exit_code=result.exit_code,
                status=FAILED,
                error_message=reason,
            )
        if not codex_supervisor.mark_attempt_lost(
            work_item_id=work_item_id,
            owner=owner,
            fencing_token=token,
            reason=reason,
            now=now(),
        ):
            raise ChildRecoveryBlocked("lost supervisor ownership after child failure")
        has_previous_owner = True
