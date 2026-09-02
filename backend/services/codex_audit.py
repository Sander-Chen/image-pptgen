"""Codex exec audit helpers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Iterable

import config
import db as dbmod
from backend.services.codex_jsonl_stream import (
    JsonlRecord,
    SemanticJsonlRecord,
    SOURCE_WINDOW_BYTES,
    iter_bounded_jsonl_file_records,
    iter_semantic_jsonl_file_records,
)
from backend.services.private_file_permissions import restrict_owner_only_fd


TIMESTAMP_KEYS = ("event_timestamp", "timestamp", "created_at", "started_at", "completed_at")
SECRET_KEY_PARTS = (
    "apikey",
    "authorization",
    "authentication",
    "token",
    "secret",
    "password",
    "cookie",
    "credential",
    "copiedauth",
)
SECRET_KEY_EXACT = {"threadid", "sessionid"}
DETAIL_AUDIT_IDENTIFIER_KEYS = {
    "threadid",
    "sessionid",
    "callid",
    "imagegencallid",
    "invocationid",
    "runid",
    "runslideid",
}
DETAIL_SECRET_KEY_EXACT = {"auth", "authcontent", "authpayload", "authtoken"}
SAFE_TOKEN_COUNT_KEYS = {
    "cachedinputtokens",
    "inputtokens",
    "outputtokens",
    "reasoningoutputtokens",
    "totalinputtokens",
    "totaloutputtokens",
    "totaltokens",
}
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)([\"']?api\s*[_ -]?\s*key[\"']?\s*[=:]\s*[\"']?)([^\"'\s,;}\]]+)"),
    re.compile(r"(?i)([\"']?authorization[\"']?\s*[=:]\s*[\"']?(?:Bearer\s+)?)([^\"'\s,;}\]]+)"),
    re.compile(r"(?i)([\"']?(?:access|refresh)?\s*[_ -]?\s*token[\"']?\s*[=:]\s*[\"']?)([^\"'\s,;}\]]+)"),
    re.compile(r"(?i)([\"']?cookie[\"']?\s*[=:]\s*[\"']?)([^\"'\s,;}\]]+)"),
    re.compile(r"(?i)([\"']?(?:secret|password|credential)[\"']?\s*[=:]\s*[\"']?)([^\"'\s,;}\]]+)"),
    re.compile(r"(?i)([\"']?(?:copied\s*[_ -]?\s*auth(?:entication)?|auth\s*[_ -]?\s*content)[\"']?\s*[=:]\s*[\"']?)([^\"'\s,;}\]]+)"),
    re.compile(r"(?i)(bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)(thread\s*[_ -]?\s*id[\"']?\s*[=:]\s*[\"']?)([^\"'\s,;}]+)"),
    re.compile(r"(?i)(session\s*[_ -]?\s*id[\"']?\s*[=:]\s*[\"']?)([^\"'\s,;}]+)"),
)

NATIVE_PUBLIC_FIELDS = (
    "requested_model",
    "actual_model",
    "requested_reasoning_effort",
    "actual_reasoning_effort",
    "cli_version",
    "binary_sha256",
    "attempt",
    "terminal_state",
    "retry",
    "timeout",
    "skip",
    "fallback_used",
    "failure_code",
)
NATIVE_PRIVATE_EVIDENCE_KEY = "native_evidence_discriminator"
NATIVE_PRIVATE_EVIDENCE_VALUE = "codex_native_image_private_v1"
NATIVE_PUBLIC_AUDIT_BINDING_KEY = "native_audit"
NATIVE_PUBLIC_AUDIT_BINDING_VERSION = "public_native_audit_v1"
PATH_VALUE_PATTERNS = (
    re.compile(r"(?<![\w.])/(?:[^\s,;\]\[{}()<>\"']+/)*[^\s,;\]\[{}()<>\"']+"),
    re.compile(r"(?<![\w.])[A-Za-z]:\\(?:[^\s,;\]\[{}()<>\"']+\\)*[^\s,;\]\[{}()<>\"']+"),
)
OBSERVED_WRAPPER_PREFIX = re.compile(
    rb'^\s*\{\s*"observed_at"\s*:\s*("(?:\\.|[^"\\])*")\s*,\s*"event"\s*:'
)
EVENT_PAGE_RESPONSE_BYTES = 30_720
EVENT_PAGE_PAYLOAD_BYTES = 4_096
EVENT_PAGE_SIZE = 6
EVENT_PAGE_PUBLIC_SCALAR_FIELDS = (
    "id",
    "type",
    "name",
    "status",
    "message",
    "text",
    "summary",
    "title",
    "label",
    "detail",
    "reason",
    "error",
    "code",
)
DETAIL_CALL_PUBLIC_EVENT_FIELDS = (
    "id",
    "type",
    "name",
    "status",
    "code",
)
DETAIL_CALL_PUBLIC_SCALAR_FIELDS = (
    "call_id",
    "revised_prompt",
)
EVENT_CURSOR_VERSION = 1
EVENT_CURSOR_TTL_SECONDS = 900
EVENT_CURSOR_SECRET_BYTES = 32
EVENT_CURSOR_KEY_FILE = "audit-event-cursor-v1.key"
EVENT_CURSOR_KEY_INITIALIZATION_ATTEMPTS = 20
EVENT_CURSOR_KEY_INITIALIZATION_DELAY_SECONDS = 0.01


class CodexReconciliationError(RuntimeError):
    """Raised when raw-file and SQLite event evidence do not match exactly."""


def sha256_file(path: str | Path | None) -> str | None:
    if not path:
        return None
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(SOURCE_WINDOW_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl_lines(path: str | Path) -> Iterable[tuple[int, dict[str, Any]]]:
    for line_number, record in enumerate(iter_bounded_jsonl_file_records(path), start=1):
        payload = _event_for_jsonl_record(record)
        if payload is None:
            raise ValueError(f"invalid JSONL at line {line_number}")
        yield line_number, payload


def _event_timestamp(event: dict[str, Any]) -> str | None:
    for key in TIMESTAMP_KEYS:
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _event_item(event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item")
    return item if isinstance(item, dict) else {}


def _is_error_event(event: dict[str, Any]) -> bool:
    item = _event_item(event)
    return event.get("type") == "error" or item.get("type") == "error"


def _observed_wrapper_for_record(record: JsonlRecord, *, line_number: int) -> dict[str, Any]:
    if record.event is not None:
        return record.event
    if not record.oversized or record.prefix_bytes is None:
        raise ValueError(f"observed JSONL line {line_number} is invalid")
    match = OBSERVED_WRAPPER_PREFIX.match(record.prefix_bytes)
    if match is None:
        raise ValueError(f"observed JSONL line {line_number} is missing observed wrapper prefix")
    try:
        observed_at = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"observed JSONL line {line_number} has an invalid observed_at") from exc
    if not isinstance(observed_at, str) or not observed_at:
        raise ValueError(f"observed JSONL line {line_number} is missing observed_at")
    return {"observed_at": observed_at, "event": record.sqlite_placeholder_event()}


def _iter_observed_events(path: str | Path) -> Iterable[dict[str, Any]]:
    sequence = 0
    for line_number, record in enumerate(iter_semantic_jsonl_file_records(path, observed_wrapper=True), start=1):
        wrapper = record.event
        observed_at = wrapper.get("observed_at")
        event = wrapper.get("event")
        if not isinstance(observed_at, str) or not observed_at:
            raise ValueError(f"observed JSONL line {line_number} is missing observed_at")
        if not isinstance(event, dict):
            raise ValueError(f"observed JSONL line {line_number} is missing event object")
        item = _event_item(event)
        sequence += 1
        yield {
            "sequence": sequence,
            "observed_at": observed_at,
            "event_timestamp": _event_timestamp(event),
            "event_type": event.get("type") if isinstance(event.get("type"), str) else None,
            "item_id": item.get("id") if isinstance(item.get("id"), str) else None,
            "item_type": item.get("type") if isinstance(item.get("type"), str) else None,
            "is_error": _is_error_event(event),
            "payload": event,
        }


@dataclass
class _ObservedEvidenceSummary:
    event_count: int = 0
    error_event_count: int = 0
    usage: dict[str, Any] | None = None


def _summarize_observed_events(events: Iterable[dict[str, Any]]) -> _ObservedEvidenceSummary:
    summary = _ObservedEvidenceSummary()
    for event in events:
        summary.event_count += 1
        summary.error_event_count += int(bool(event["is_error"]))
        payload = event["payload"]
        usage = payload.get("usage")
        if isinstance(usage, dict):
            summary.usage = usage
    return summary


def _parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _redact_string(value: str, *, redact_paths: bool = False) -> str:
    redacted = value
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}<redacted>", redacted)
    if redact_paths:
        for pattern in PATH_VALUE_PATTERNS:
            redacted = pattern.sub("<redacted-path>", redacted)
    return redacted


def redact_audit_error(value: Any) -> Any:
    """Redact secrets and filesystem paths before an invocation error is public."""
    if isinstance(value, str):
        return _redact_string(value, redact_paths=True)
    return redact_audit_value(value)


def _is_secret_key(key_text: str) -> bool:
    if key_text in SAFE_TOKEN_COUNT_KEYS:
        return False
    if key_text in SECRET_KEY_EXACT:
        return True
    return any(part in key_text for part in SECRET_KEY_PARTS)


def _is_detail_secret_key(key_text: str) -> bool:
    """Keep audit linkage identifiers while removing authentication material."""
    if key_text in SAFE_TOKEN_COUNT_KEYS or key_text in DETAIL_AUDIT_IDENTIFIER_KEYS:
        return False
    if key_text in DETAIL_SECRET_KEY_EXACT or key_text == "account" or key_text.startswith("accountauth"):
        return True
    return any(part in key_text for part in SECRET_KEY_PARTS)


def _is_explicit_safe_boolean(key_text: str, value: Any) -> bool:
    """Keep the credential-presence status flag without exposing credential material."""
    return key_text == "credentialpresent" and isinstance(value, bool)


def redact_audit_value(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, child in value.items():
            key_text = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if _is_secret_key(key_text) and not _is_explicit_safe_boolean(key_text, child):
                safe[key] = "[REDACTED]"
            else:
                safe[key] = redact_audit_value(child)
        return safe
    if isinstance(value, list):
        return [redact_audit_value(child) for child in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact_audit_detail_value(value: Any) -> Any:
    """Redact credentials from explicit audit detail without hiding business evidence.

    The default Run projection continues to hide raw thread/session identifiers.
    This detail-only redactor deliberately preserves those identifiers, prompts,
    model input/output, and local evidence references so the internal audit can
    trace a business image to its exact Codex conversation.
    """
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, child in value.items():
            key_text = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if _is_detail_secret_key(key_text) and not _is_explicit_safe_boolean(key_text, child):
                safe[key] = "[REDACTED]"
            else:
                safe[key] = redact_audit_detail_value(child)
        return safe
    if isinstance(value, list):
        return [redact_audit_detail_value(child) for child in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def contains_native_private_evidence(value: Any) -> bool:
    """Identify only explicitly typed Native private evidence."""
    return (
        isinstance(value, dict)
        and value.get(NATIVE_PRIVATE_EVIDENCE_KEY) == NATIVE_PRIVATE_EVIDENCE_VALUE
    )


def contains_nested_native_private_evidence(value: Any) -> bool:
    """Find typed Native evidence when it is nested in a general snapshot."""
    if contains_native_private_evidence(value):
        return True
    if isinstance(value, dict):
        return any(contains_nested_native_private_evidence(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_nested_native_private_evidence(child) for child in value)
    return False


def find_nested_native_private_evidence(value: Any) -> dict[str, Any] | None:
    """Return the first explicitly typed Native evidence object in a snapshot."""
    if contains_native_private_evidence(value):
        return value
    if isinstance(value, dict):
        for child in value.values():
            found = find_nested_native_private_evidence(child)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_nested_native_private_evidence(child)
            if found is not None:
                return found
    return None


def _safe_native_image_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("png_valid", "width", "height", "bytes", "sha256")
    }


def _safe_native_normalization(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    public = {
        key: value.get(key)
        for key in (
            "normalized",
            "algorithm",
            "operation",
            "pillow_version",
            "parent_dimensions",
            "child_dimensions",
            "parent_bytes",
            "child_bytes",
            "parent_sha256",
            "child_sha256",
        )
    }
    derivation = value.get("derivation")
    if isinstance(derivation, dict):
        public["derivation"] = {
            key: derivation.get(key)
            for key in (
                "background",
                "foreground",
                "parent_dimensions",
                "child_dimensions",
                "parent_bytes",
                "child_bytes",
                "parent_sha256",
                "child_sha256",
            )
        }
    return public


def public_native_image_projection(evidence: Any) -> dict[str, Any]:
    """Whitelist path-free Native facts for every public backend surface."""
    source = evidence if isinstance(evidence, dict) else {}
    public = {key: source.get(key) for key in NATIVE_PUBLIC_FIELDS}
    image_output_protocol = source.get("image_output_protocol")
    if isinstance(image_output_protocol, str) and image_output_protocol:
        public["image_output_protocol"] = image_output_protocol
    business_image = _safe_native_image_record(source.get("business_image"))
    if business_image is not None:
        public["business_image"] = business_image
    normalization = _safe_native_normalization(source.get("normalization"))
    if normalization is not None:
        public["normalization"] = normalization
    return public


def native_public_audit_projection(evidence: Any) -> dict[str, Any]:
    """Return the path-free projection permitted in an artifact snapshot marker."""
    projection = public_native_image_projection(evidence)

    def redact_paths(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: redact_paths(child) for key, child in value.items()}
        if isinstance(value, list):
            return [redact_paths(child) for child in value]
        if isinstance(value, str):
            return _redact_string(value, redact_paths=True)
        return value

    return redact_paths(projection)


def native_public_audit_marker(invocation: dict[str, Any]) -> dict[str, Any] | None:
    """Build a path-free, identity-bound marker for one Native image invocation."""
    metadata = invocation.get("metadata")
    invocation_id = invocation.get("id")
    run_id = invocation.get("run_id")
    run_slide_id = invocation.get("run_slide_id")
    stage_id = invocation.get("stage_id")
    if (
        not contains_native_private_evidence(metadata)
        or type(invocation_id) is not int
        or invocation_id <= 0
        or type(run_id) is not int
        or run_id <= 0
        or type(run_slide_id) is not int
        or run_slide_id <= 0
        or invocation.get("role") != "image_generator"
        or not isinstance(stage_id, str)
        or not stage_id
    ):
        return None
    projection = native_public_audit_projection(metadata)
    image = projection.get("business_image")
    if not isinstance(image, dict) or not isinstance(image.get("sha256"), str) or not image["sha256"]:
        return None
    return {
        NATIVE_PRIVATE_EVIDENCE_KEY: NATIVE_PRIVATE_EVIDENCE_VALUE,
        NATIVE_PUBLIC_AUDIT_BINDING_KEY: {
            "version": NATIVE_PUBLIC_AUDIT_BINDING_VERSION,
            "invocation_id": invocation_id,
            "run_id": run_id,
            "run_slide_id": run_slide_id,
            "stage_id": stage_id,
            "role": "image_generator",
            "public_projection": projection,
        },
    }


def native_public_audit_binding(marker: Any) -> dict[str, Any] | None:
    """Accept only the exact path-free Native marker shape written by this service."""
    if (
        not contains_native_private_evidence(marker)
        or not isinstance(marker, dict)
        or set(marker) != {NATIVE_PRIVATE_EVIDENCE_KEY, NATIVE_PUBLIC_AUDIT_BINDING_KEY}
    ):
        return None
    binding = marker.get(NATIVE_PUBLIC_AUDIT_BINDING_KEY)
    if not isinstance(binding, dict) or set(binding) != {
        "version",
        "invocation_id",
        "run_id",
        "run_slide_id",
        "stage_id",
        "role",
        "public_projection",
    }:
        return None
    if (
        binding.get("version") != NATIVE_PUBLIC_AUDIT_BINDING_VERSION
        or type(binding.get("invocation_id")) is not int
        or type(binding.get("run_id")) is not int
        or type(binding.get("run_slide_id")) is not int
        or binding.get("role") != "image_generator"
        or not isinstance(binding.get("stage_id"), str)
        or not binding["stage_id"]
        or not isinstance(binding.get("public_projection"), dict)
    ):
        return None
    return binding


def project_native_public_value(value: Any) -> Any:
    """Recursively replace typed Native evidence, never shallow-copying it."""
    if contains_native_private_evidence(value):
        return public_native_image_projection(value)
    if isinstance(value, dict):
        return {key: project_native_public_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [project_native_public_value(child) for child in value]
    return value


def run_has_native_invocation(run_id: int) -> bool:
    """Return whether any durable invocation carries typed Native evidence."""
    return any(
        contains_native_private_evidence(invocation.get("metadata"))
        for invocation in dbmod.list_codex_invocations(run_id=run_id, include_events=False)
    )


def record_codex_invocation(
    *,
    run_id: int | None = None,
    run_slide_id: int | None = None,
    stage_id: str | None = None,
    role: str | None = None,
    attempt: int = 1,
    command: object | None = None,
    cwd: str | None = None,
    sandbox: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    prompt_sha256: str | None = None,
    raw_jsonl_path: str | None = None,
    observed_jsonl_path: str,
    output_path: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    elapsed_ms: int | None = None,
    exit_code: int | None = None,
    status: str | None = None,
    error_message: str | None = None,
    metadata: object | None = None,
) -> int:
    summary = _summarize_observed_events(_iter_observed_events(observed_jsonl_path))
    invocation_id = dbmod.create_codex_invocation(
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=stage_id,
        role=role,
        attempt=attempt,
        status=status or ("completed" if exit_code == 0 else "failed"),
        command=command,
        cwd=cwd,
        sandbox=sandbox,
        model=model,
        reasoning_effort=reasoning_effort,
        prompt_sha256=prompt_sha256,
        raw_jsonl_path=raw_jsonl_path,
        observed_jsonl_path=observed_jsonl_path,
        output_path=output_path,
        raw_jsonl_sha256=sha256_file(raw_jsonl_path),
        observed_jsonl_sha256=sha256_file(observed_jsonl_path),
        output_sha256=sha256_file(output_path),
        event_count=summary.event_count,
        error_event_count=summary.error_event_count,
        usage=summary.usage or {},
        exit_code=exit_code,
        error_message=error_message,
        metadata=metadata,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_ms=elapsed_ms,
    )
    for event in _iter_observed_events(observed_jsonl_path):
        dbmod.append_codex_event(
            invocation_id=invocation_id,
            sequence=event["sequence"],
            payload=event["payload"],
            observed_at=event["observed_at"],
            event_timestamp=event["event_timestamp"],
            event_type=event["event_type"],
            item_id=event["item_id"],
            item_type=event["item_type"],
            is_error=event["is_error"],
        )
    return invocation_id


def start_codex_invocation(
    *,
    run_id: int | None = None,
    run_slide_id: int | None = None,
    stage_id: str | None = None,
    role: str | None = None,
    attempt: int = 1,
    command: object | None = None,
    cwd: str | None = None,
    sandbox: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    prompt_sha256: str | None = None,
    raw_jsonl_path: str | None = None,
    observed_jsonl_path: str | None = None,
    output_path: str | None = None,
    metadata: object | None = None,
    started_at: str | None = None,
) -> int:
    """Create the running audit identity before the child process is spawned."""
    return dbmod.create_codex_invocation(
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=stage_id,
        role=role,
        attempt=attempt,
        status="running",
        command=command,
        cwd=cwd,
        sandbox=sandbox,
        model=model,
        reasoning_effort=reasoning_effort,
        prompt_sha256=prompt_sha256,
        raw_jsonl_path=raw_jsonl_path,
        observed_jsonl_path=observed_jsonl_path,
        output_path=output_path,
        metadata=metadata,
        started_at=started_at,
    )


def link_codex_invocation_to_work_item(
    *,
    work_item_id: int,
    invocation_id: int,
    attempt_number: int,
) -> None:
    """Link one durable model attempt to its supervised business work item."""
    db = dbmod.get_db()
    try:
        db.execute(
            """INSERT INTO codex_work_item_invocations
               (work_item_id, invocation_id, attempt_number)
               VALUES (?, ?, ?)""",
            (work_item_id, invocation_id, attempt_number),
        )
        db.commit()
    finally:
        db.close()


def mark_codex_invocation_interrupted(invocation_id: int, reason: str, *, ended_at: str) -> bool:
    """Close an attempt that lost its process before exact terminal reconciliation was possible."""
    db = dbmod.get_db()
    try:
        cursor = db.execute(
            """UPDATE codex_invocations
               SET status = 'interrupted', error_message = ?, ended_at = ?
               WHERE id = ? AND status = 'running'""",
            (reason, ended_at, invocation_id),
        )
        db.commit()
        return cursor.rowcount == 1
    finally:
        db.close()


def append_live_codex_event(
    *,
    invocation_id: int,
    sequence: int,
    event: dict[str, Any],
    observed_at: str,
    raw_bytes: bytes | None,
    raw_sha256: str | None = None,
    byte_offset_start: int,
    byte_offset_end: int,
    file_identity: str | None,
) -> int:
    """Append an exact JSONL line and its query projection in one transaction."""
    item = _event_item(event)
    projection = {"observed_at": observed_at, "event": event}
    return dbmod.append_codex_event_with_raw_line(
        invocation_id=invocation_id,
        sequence=sequence,
        payload=event,
        observed_at=observed_at,
        raw_bytes=raw_bytes or b"",
        raw_sha256=raw_sha256 or hashlib.sha256(raw_bytes or b"").hexdigest(),
        byte_offset_start=byte_offset_start,
        byte_offset_end=byte_offset_end,
        file_identity=file_identity,
        projection=projection,
        event_timestamp=_event_timestamp(event),
        event_type=event.get("type") if isinstance(event.get("type"), str) else None,
        item_id=item.get("id") if isinstance(item.get("id"), str) else None,
        item_type=item.get("type") if isinstance(item.get("type"), str) else None,
        is_error=_is_error_event(event),
    )


def inspect_live_codex_event_persistence(
    *,
    invocation_id: int,
    sequence: int,
    event: dict[str, Any],
    observed_at: str,
    raw_bytes: bytes | None,
    raw_sha256: str | None,
    byte_offset_start: int,
    byte_offset_end: int,
    file_identity: str | None,
) -> str:
    """Classify a raised live append from fresh durable state."""
    item = _event_item(event)
    projection = {"observed_at": observed_at, "event": event}
    compact_raw = raw_bytes or b""
    return dbmod.inspect_codex_event_persistence_outcome(
        invocation_id=invocation_id,
        sequence=sequence,
        payload=event,
        observed_at=observed_at,
        raw_bytes=compact_raw,
        raw_sha256=raw_sha256 or hashlib.sha256(compact_raw).hexdigest(),
        byte_offset_start=byte_offset_start,
        byte_offset_end=byte_offset_end,
        file_identity=file_identity,
        projection=projection,
    )


def _event_for_jsonl_record(record: JsonlRecord) -> dict[str, Any] | None:
    return record.sqlite_placeholder_event() if record.oversized else record.event


def _get_invocation_reconciliation_header(invocation_id: int) -> dict[str, Any] | None:
    """Fetch terminal reconciliation fields without expanding the invocation's events."""
    db = dbmod.get_db()
    try:
        row = db.execute(
            """SELECT raw_jsonl_path, observed_jsonl_path, event_count
               FROM codex_invocations WHERE id = ?""",
            (invocation_id,),
        ).fetchone()
    finally:
        db.close()
    return dict(row) if row is not None else None


def finalize_codex_invocation(
    *,
    invocation_id: int,
    output_path: str | None = None,
    ended_at: str | None = None,
    elapsed_ms: int | None = None,
    exit_code: int | None = None,
    status: str,
    error_message: str | None = None,
    metadata: object | None = None,
) -> int:
    """Finalize a live invocation in place after checking its projected count."""
    current = _get_invocation_reconciliation_header(invocation_id)
    if current is None:
        raise ValueError("codex invocation not found")
    observed_path = current.get("observed_jsonl_path")
    if not observed_path:
        raise ValueError("live Codex invocation is missing observed JSONL path")
    try:
        summary = _reconcile_codex_event_evidence(invocation_id, current, observed_path)
    except CodexReconciliationError as exc:
        reason = f"raw evidence reconciliation failed: {exc}"
        dbmod.mark_codex_invocation_reconciliation_failed(invocation_id, reason)
        raise
    updated = dbmod.finalize_codex_invocation(
        invocation_id,
        status=status,
        output_path=output_path,
        raw_jsonl_sha256=sha256_file(current.get("raw_jsonl_path")),
        observed_jsonl_sha256=sha256_file(observed_path),
        output_sha256=sha256_file(output_path),
        event_count=summary.event_count,
        error_event_count=summary.error_event_count,
        usage=summary.usage or {},
        exit_code=exit_code,
        error_message=error_message,
        metadata=metadata,
        ended_at=ended_at,
        elapsed_ms=elapsed_ms,
    )
    if not updated:
        raise ValueError("codex invocation not found")
    return invocation_id


def _reconcile_codex_event_evidence(
    invocation_id: int,
    invocation: dict[str, Any],
    observed_path: str | Path,
) -> _ObservedEvidenceSummary:
    raw_path_value = invocation.get("raw_jsonl_path")
    if not raw_path_value:
        raise CodexReconciliationError("raw JSONL path is missing")
    raw_path = Path(raw_path_value)
    if not raw_path.is_file():
        raise CodexReconciliationError("raw JSONL file is missing")
    stat = raw_path.stat()
    file_identity = f"{stat.st_dev}:{stat.st_ino}"
    file_records = iter_semantic_jsonl_file_records(raw_path)
    sentinel = object()
    expected_count = 0
    summary = _ObservedEvidenceSummary()
    db = dbmod.get_db()
    try:
        raw_rows = db.execute(
            """SELECT sequence, raw_bytes, raw_sha256, byte_offset_start,
                      byte_offset_end, file_identity, projection_json
               FROM codex_event_raw_lines WHERE invocation_id = ? ORDER BY sequence""",
            (invocation_id,),
        )
        projected_events = db.execute(
            """SELECT sequence, payload_json
               FROM codex_events WHERE invocation_id = ? ORDER BY sequence""",
            (invocation_id,),
        )
        observed_events = _iter_observed_events(observed_path)
        while True:
            try:
                record = next(file_records)
            except StopIteration:
                record = sentinel
            if record is sentinel:
                trailing = []
                for iterator in (raw_rows, projected_events, observed_events):
                    try:
                        trailing.append(next(iterator))
                    except StopIteration:
                        trailing.append(sentinel)
                if any(value is not sentinel for value in trailing):
                    raise CodexReconciliationError("file, raw-row and event-row counts differ")
                break
            expected_count += 1
            try:
                assert isinstance(record, SemanticJsonlRecord)
                try:
                    raw_row = next(raw_rows)
                except StopIteration:
                    raw_row = sentinel
                try:
                    event_row = next(projected_events)
                except StopIteration:
                    event_row = sentinel
                try:
                    observed_event = next(observed_events)
                except StopIteration:
                    observed_event = sentinel
                if raw_row is sentinel or event_row is sentinel or observed_event is sentinel:
                    raise CodexReconciliationError("file, raw-row and event-row counts differ")
                assert isinstance(observed_event, dict)
                payload = record.event
                raw_bytes = record.raw_bytes or b""
                start = record.raw_range.start
                end = record.raw_range.end
                if int(raw_row["sequence"]) != expected_count or int(event_row["sequence"]) != expected_count:
                    raise CodexReconciliationError(f"sequence mismatch at line {expected_count}")
                if bytes(raw_row["raw_bytes"]) != raw_bytes:
                    raise CodexReconciliationError(f"raw byte mismatch at line {expected_count}")
                if raw_row["raw_sha256"] != record.raw_range.sha256:
                    raise CodexReconciliationError(f"raw hash mismatch at line {expected_count}")
                if int(raw_row["byte_offset_start"]) != start or int(raw_row["byte_offset_end"]) != end:
                    raise CodexReconciliationError(f"byte offset mismatch at line {expected_count}")
                if raw_row["file_identity"] != file_identity:
                    raise CodexReconciliationError(f"file identity mismatch at line {expected_count}")
                projected_payload = _parse_json_dict(event_row["payload_json"])
                if projected_payload != payload or observed_event.get("payload") != payload:
                    raise CodexReconciliationError(f"projection mismatch at line {expected_count}")
                projection = _parse_json_dict(raw_row["projection_json"])
                if projection.get("event") != payload:
                    raise CodexReconciliationError(f"raw projection mismatch at line {expected_count}")
                summary.event_count += 1
                summary.error_event_count += int(bool(observed_event["is_error"]))
                usage = observed_event["payload"].get("usage")
                if isinstance(usage, dict):
                    summary.usage = usage
            finally:
                record.discard_final_capture()
    except ValueError as exc:
        raise CodexReconciliationError("raw JSONL ends with an invalid or incomplete record") from exc
    finally:
        db.close()

    if int(invocation.get("event_count") or 0) != expected_count:
        raise CodexReconciliationError("observed or invocation event count differs")
    return summary


def _public_codex_event(event: dict[str, Any], *, native: bool) -> dict[str, Any]:
    public = {
        key: event.get(key)
        for key in ("sequence", "event_type", "observed_at", "event_timestamp", "item_type", "is_error")
    }
    if not native:
        public["item_id"] = event.get("item_id")
    if isinstance(event.get("usage"), dict):
        public["usage"] = redact_audit_value(event["usage"])
    return public


def _public_codex_invocation(invocation: dict[str, Any]) -> dict[str, Any]:
    metadata = invocation.get("metadata")
    native = contains_native_private_evidence(metadata)
    public = {
        key: invocation.get(key)
        for key in (
            "id",
            "run_id",
            "run_slide_id",
            "stage_id",
            "role",
            "attempt",
            "status",
            "sandbox",
            "model",
            "reasoning_effort",
            "event_count",
            "error_event_count",
            "usage",
            "exit_code",
            "error_message",
            "started_at",
            "ended_at",
            "elapsed_ms",
        )
    }
    public["error_message"] = redact_audit_error(public.get("error_message"))
    public["usage"] = redact_audit_value(public.get("usage"))
    if isinstance(invocation.get("events"), list):
        public["events"] = [_public_codex_event(event, native=native) for event in invocation["events"]]
    if native:
        public["native_image"] = public_native_image_projection(metadata)
    return public


def get_codex_stream_cursor(invocation_id: int) -> dict[str, Any]:
    return dbmod.get_codex_event_cursor(invocation_id)


def mark_codex_stream_continuity_error(invocation_id: int, reason: str) -> None:
    if not dbmod.mark_codex_invocation_recovery_blocked(invocation_id, reason):
        raise ValueError("codex invocation not found")


def get_codex_invocation(invocation_id: int) -> dict[str, Any]:
    detail = dbmod.get_codex_invocation(invocation_id)
    if detail is None:
        raise ValueError("codex invocation not found")
    return detail


def list_codex_invocations_for_run(
    run_id: int,
    *,
    run_slide_id: int | None = None,
    include_events: bool = True,
    redacted: bool = True,
) -> list[dict[str, Any]]:
    invocations = dbmod.list_codex_invocations(
        run_id=run_id,
        run_slide_id=run_slide_id,
        include_events=include_events,
    )
    if not redacted:
        return invocations
    return [_public_codex_invocation(invocation) for invocation in invocations]


class CodexAuditDetailUnavailable(ValueError):
    """Raised when a Run-scoped audit detail cannot safely be loaded."""


def _native_private_root(run_id: int) -> Path:
    return (
        Path(config.ARTIFACTS_DIR).expanduser().resolve()
        / ".codex-private"
        / "native-image"
        / f"run-{run_id}"
    )


def _owned_native_private_path(value: object, *, private_root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise CodexAuditDetailUnavailable("Native audit detail is missing owned evidence")
    try:
        path = Path(value).expanduser().resolve()
        path.relative_to(private_root)
    except (OSError, ValueError) as exc:
        raise CodexAuditDetailUnavailable("Native audit detail evidence is outside the requested Run") from exc
    return path


def _native_attempt_directory(invocation: dict[str, Any], *, private_root: Path) -> Path:
    try:
        run_slide_id = int(invocation["run_slide_id"])
        attempt = int(invocation["attempt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CodexAuditDetailUnavailable("Native audit detail has no stable slide or attempt identity") from exc
    stage_id = invocation.get("stage_id")
    if run_slide_id <= 0 or attempt <= 0 or not isinstance(stage_id, str) or not stage_id:
        raise CodexAuditDetailUnavailable("Native audit detail has an invalid slide, stage, or attempt identity")
    if Path(stage_id).name != stage_id or stage_id in {".", ".."}:
        raise CodexAuditDetailUnavailable("Native audit detail has an invalid stage identity")
    return private_root / f"slide-{run_slide_id}" / stage_id / f"attempt-{attempt}"


def _read_owned_detail_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CodexAuditDetailUnavailable("Native audit detail evidence cannot be read") from exc


def _detail_jsonl_reference(path: Path, stored_sha256: object) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": stored_sha256 if isinstance(stored_sha256, str) and stored_sha256 else sha256_file(path),
    }


def _detail_event_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": event.get("sequence"),
            "event_type": event.get("event_type"),
            "item_type": event.get("item_type"),
            "is_error": event.get("is_error"),
            "observed_at": event.get("observed_at"),
            "event_timestamp": event.get("event_timestamp"),
            "payload": redact_audit_detail_value(event.get("payload")),
        }
        for event in events
    ]


def _detail_tool_call_events(invocation_id: int) -> list[dict[str, Any]]:
    """Load only durable tool-call projections, never an invocation timeline."""
    tool_item_types = (
        "function_call",
        "function_call_output",
        "tool_call",
        "tool_result",
        "command_execution",
        "image_generation",
        "image_generation_end",
    )
    placeholders = ", ".join("?" for _ in tool_item_types)
    db = dbmod.get_db()
    try:
        rows = db.execute(
            f"""SELECT sequence, event_type, item_type, is_error, observed_at,
                        event_timestamp, payload_json
                   FROM codex_events
                   WHERE invocation_id = ? AND item_type IN ({placeholders})
                   ORDER BY sequence""",
            (invocation_id, *tool_item_types),
        ).fetchall()
    finally:
        db.close()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        events.append(
            {
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "item_type": row["item_type"],
                "is_error": row["is_error"],
                "observed_at": row["observed_at"],
                "event_timestamp": row["event_timestamp"],
                "payload": payload,
            }
        )
    return events


class CodexAuditEventPageUnavailable(ValueError):
    """Raised when an event page or its signed continuation is not valid."""


class _EventCursorKeyInitializing(RuntimeError):
    """The winner of a concurrent key initialization has not finished writing."""


def _event_cursor_key_path() -> Path:
    """Return the private deployment key location without projecting it publicly."""
    return Path(config.ARTIFACTS_DIR).expanduser().resolve() / ".codex-private" / EVENT_CURSOR_KEY_FILE


def _event_cursor_key_unavailable() -> CodexAuditEventPageUnavailable:
    return CodexAuditEventPageUnavailable("Codex audit event cursor key is unavailable")


def _required_cursor_open_flag(name: str) -> int:
    value = getattr(os, name, None)
    if not isinstance(value, int) or value == 0:
        raise _event_cursor_key_unavailable()
    return value


def _validate_event_cursor_private_directory_stat(private_stat: os.stat_result) -> os.stat_result:
    if (
        not stat.S_ISDIR(private_stat.st_mode)
        or stat.S_IMODE(private_stat.st_mode) != 0o700
        or private_stat.st_uid != os.geteuid()
    ):
        raise _event_cursor_key_unavailable()
    return private_stat


def _validate_event_cursor_private_directory(descriptor: int) -> os.stat_result:
    try:
        private_stat = os.fstat(descriptor)
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    return _validate_event_cursor_private_directory_stat(private_stat)


def _open_event_cursor_private_directory() -> tuple[Path, int]:
    """Anchor the private directory to one validated nofollow descriptor."""
    private_dir = _event_cursor_key_path().parent
    try:
        private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            private_dir,
            os.O_RDONLY | _required_cursor_open_flag("O_DIRECTORY") | _required_cursor_open_flag("O_NOFOLLOW"),
        )
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    try:
        _validate_event_cursor_private_directory(descriptor)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return private_dir, descriptor


def _validate_event_cursor_key(descriptor: int) -> os.stat_result:
    try:
        key_stat = os.fstat(descriptor)
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    if (
        not stat.S_ISREG(key_stat.st_mode)
        or stat.S_IMODE(key_stat.st_mode) != 0o600
        or key_stat.st_uid != os.geteuid()
    ):
        raise _event_cursor_key_unavailable()
    return key_stat


def _read_event_cursor_key(private_directory_descriptor: int) -> bytes | None:
    """Read an already-private fixed-size key, or report a concurrent partial write."""
    try:
        descriptor = os.open(
            EVENT_CURSOR_KEY_FILE,
            os.O_RDONLY | _required_cursor_open_flag("O_NOFOLLOW"),
            dir_fd=private_directory_descriptor,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    try:
        key_stat = _validate_event_cursor_key(descriptor)
        if key_stat.st_size < EVENT_CURSOR_SECRET_BYTES:
            raise _EventCursorKeyInitializing()
        if key_stat.st_size != EVENT_CURSOR_SECRET_BYTES:
            raise _event_cursor_key_unavailable()
        key = b""
        while len(key) < EVENT_CURSOR_SECRET_BYTES:
            chunk = os.read(descriptor, EVENT_CURSOR_SECRET_BYTES - len(key))
            if not chunk:
                raise _EventCursorKeyInitializing()
            key += chunk
        if os.read(descriptor, 1):
            raise _event_cursor_key_unavailable()
        return key
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _create_event_cursor_key(*, private_directory_descriptor: int) -> bool:
    """Race-safely install one private deployment key and make its name durable."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _required_cursor_open_flag("O_NOFOLLOW")
    try:
        descriptor = os.open(EVENT_CURSOR_KEY_FILE, flags, 0o600, dir_fd=private_directory_descriptor)
    except FileExistsError:
        return False
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    try:
        restrict_owner_only_fd(descriptor)
        if _validate_event_cursor_key(descriptor).st_size != 0:
            raise _event_cursor_key_unavailable()
        secret = os.urandom(EVENT_CURSOR_SECRET_BYTES)
        written = 0
        while written < len(secret):
            count = os.write(descriptor, secret[written:])
            if count <= 0:
                raise OSError("private cursor key write did not advance")
            written += count
        os.fsync(descriptor)
        if _validate_event_cursor_key(descriptor).st_size != EVENT_CURSOR_SECRET_BYTES:
            raise _event_cursor_key_unavailable()
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        os.fsync(private_directory_descriptor)
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    return True


def _verify_event_cursor_private_directory_path(private_dir: Path, private_directory_descriptor: int) -> None:
    """Reject a renamed or symlink-replaced configured directory after key I/O."""
    anchored_stat = _validate_event_cursor_private_directory(private_directory_descriptor)
    try:
        current_stat = os.stat(private_dir, follow_symlinks=False)
    except OSError as exc:
        raise _event_cursor_key_unavailable() from exc
    _validate_event_cursor_private_directory_stat(current_stat)
    if (anchored_stat.st_dev, anchored_stat.st_ino) != (current_stat.st_dev, current_stat.st_ino):
        raise _event_cursor_key_unavailable()


def _private_event_cursor_secret() -> bytes:
    """Resolve one stable key across concurrent workers and later process restarts."""
    private_dir, private_directory_descriptor = _open_event_cursor_private_directory()
    try:
        for attempt in range(EVENT_CURSOR_KEY_INITIALIZATION_ATTEMPTS):
            try:
                key = _read_event_cursor_key(private_directory_descriptor)
            except _EventCursorKeyInitializing:
                if attempt + 1 == EVENT_CURSOR_KEY_INITIALIZATION_ATTEMPTS:
                    raise _event_cursor_key_unavailable()
                time.sleep(EVENT_CURSOR_KEY_INITIALIZATION_DELAY_SECONDS)
                continue
            if key is not None:
                _verify_event_cursor_private_directory_path(private_dir, private_directory_descriptor)
                return key
            if _create_event_cursor_key(private_directory_descriptor=private_directory_descriptor):
                try:
                    key = _read_event_cursor_key(private_directory_descriptor)
                except _EventCursorKeyInitializing as exc:
                    raise _event_cursor_key_unavailable() from exc
                if key is not None:
                    _verify_event_cursor_private_directory_path(private_dir, private_directory_descriptor)
                    return key
            if attempt + 1 < EVENT_CURSOR_KEY_INITIALIZATION_ATTEMPTS:
                time.sleep(EVENT_CURSOR_KEY_INITIALIZATION_DELAY_SECONDS)
        raise _event_cursor_key_unavailable()
    finally:
        try:
            os.close(private_directory_descriptor)
        except OSError:
            pass


def _event_cursor_secret() -> bytes:
    """Return the deployment-local signing key; tests supply an isolated value."""
    configured = os.environ.get("PPTGEN_CODEX_AUDIT_CURSOR_SECRET")
    if configured:
        configured_secret = configured.encode("utf-8")
        if len(configured_secret) < EVENT_CURSOR_SECRET_BYTES:
            raise _event_cursor_key_unavailable()
        return configured_secret
    return _private_event_cursor_secret()


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CodexAuditEventPageUnavailable("Codex audit event cursor is malformed") from exc
    if _urlsafe_b64encode(decoded) != value:
        raise CodexAuditEventPageUnavailable("Codex audit event cursor is malformed")
    return decoded


def _event_source_identity(invocation: dict[str, Any]) -> str:
    source = {
        "raw_jsonl_sha256": invocation.get("raw_jsonl_sha256"),
        "observed_jsonl_sha256": invocation.get("observed_jsonl_sha256"),
        "event_count": invocation.get("event_count"),
        "ended_at": invocation.get("ended_at"),
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _encode_event_cursor(*, run_id: int, invocation_id: int, position: int, source_identity: str) -> str:
    claims = {
        "version": EVENT_CURSOR_VERSION,
        "run_id": run_id,
        "invocation_id": invocation_id,
        "position": position,
        "next_sequence": position + 1,
        "source_identity": source_identity,
        "filter": "all",
        "page_size": EVENT_PAGE_SIZE,
        "expires_at": int(time.time()) + EVENT_CURSOR_TTL_SECONDS,
    }
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_event_cursor_secret(), payload, hashlib.sha256).digest()
    return f"{_urlsafe_b64encode(payload)}.{_urlsafe_b64encode(signature)}"


def _decode_event_cursor(
    cursor: str,
    *,
    run_id: int,
    invocation_id: int,
    source_identity: str,
) -> int:
    if not isinstance(cursor, str) or cursor.count(".") != 1:
        raise CodexAuditEventPageUnavailable("Codex audit event cursor is malformed")
    payload_text, signature_text = cursor.split(".", 1)
    payload = _urlsafe_b64decode(payload_text)
    signature = _urlsafe_b64decode(signature_text)
    expected_signature = hmac.new(_event_cursor_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise CodexAuditEventPageUnavailable("Codex audit event cursor signature is invalid")
    try:
        claims = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CodexAuditEventPageUnavailable("Codex audit event cursor is malformed") from exc
    if not isinstance(claims, dict):
        raise CodexAuditEventPageUnavailable("Codex audit event cursor is malformed")
    position = claims.get("position")
    if (
        claims.get("version") != EVENT_CURSOR_VERSION
        or claims.get("run_id") != run_id
        or claims.get("invocation_id") != invocation_id
        or claims.get("source_identity") != source_identity
        or claims.get("filter") != "all"
        or claims.get("page_size") != EVENT_PAGE_SIZE
        or not isinstance(position, int)
        or position < 0
        or claims.get("next_sequence") != position + 1
        or not isinstance(claims.get("expires_at"), int)
        or claims["expires_at"] < int(time.time())
    ):
        raise CodexAuditEventPageUnavailable("Codex audit event cursor is invalid or expired")
    return position


def _bounded_event_page_payload(payload: Any) -> Any:
    redacted = redact_audit_detail_value(payload)
    encoded = json.dumps(redacted, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= EVENT_PAGE_PAYLOAD_BYTES:
        return redacted
    return {
        "truncated": True,
        "serialized_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _public_event_page_payload(payload: Any) -> dict[str, Any]:
    """Project one timeline record to its readable public metadata only."""
    if not isinstance(payload, dict):
        return {}
    public: dict[str, Any] = {}
    for key in EVENT_PAGE_PUBLIC_SCALAR_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if value is None or isinstance(value, (bool, int, float)):
            public[key] = value
        elif isinstance(value, str):
            public[key] = _redact_string(value, redact_paths=True)
    item = payload.get("item")
    if isinstance(item, dict):
        public_item = _public_event_page_payload(item)
        if public_item:
            public["item"] = public_item
    usage = payload.get("usage")
    if isinstance(usage, dict):
        public_usage = {
            key: value
            for key, value in usage.items()
            if "".join(ch for ch in str(key).lower() if ch.isalnum()) in SAFE_TOKEN_COUNT_KEYS
            and isinstance(value, (int, float))
        }
        if public_usage:
            public["usage"] = public_usage
    return public


def _public_detail_call_payload(payload: Any) -> dict[str, Any]:
    """Keep only explicit, readable call facts from private Native evidence."""
    event_public = _public_event_page_payload(payload)
    public = {
        key: event_public[key]
        for key in DETAIL_CALL_PUBLIC_EVENT_FIELDS
        if key in event_public
    }
    if not isinstance(payload, dict):
        return public
    for key in DETAIL_CALL_PUBLIC_SCALAR_FIELDS:
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None or isinstance(value, (bool, int, float)):
            public[key] = value
        elif isinstance(value, str):
            public[key] = _redact_string(value, redact_paths=True)
    return public


def _public_detail_call_record(record: Any) -> dict[str, Any]:
    """Give every public detail call one fixed, path-free shape."""
    source = record if isinstance(record, dict) else {}

    def public_scalar(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return _redact_string(value, redact_paths=True)
        return None

    return {
        "event_sequence": public_scalar(source.get("event_sequence")),
        "kind": public_scalar(source.get("kind")),
        "name": public_scalar(source.get("name")),
        "call_id": public_scalar(source.get("call_id")),
        "payload": _public_detail_call_payload(source.get("payload")),
    }


def _public_detail_jsonl_reference(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    sha256 = source.get("sha256")
    return {"sha256": sha256 if isinstance(sha256, str) else None}


def _public_detail_canonical_session(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    byte_count = source.get("bytes")
    sha256 = source.get("sha256")
    return {
        "bytes": byte_count if isinstance(byte_count, int) and not isinstance(byte_count, bool) else None,
        "sha256": sha256 if isinstance(sha256, str) else None,
    }


def public_native_audit_detail_projection(detail: Any) -> dict[str, Any]:
    """Project validated Native invocation detail onto its one public contract."""
    source = detail if isinstance(detail, dict) else {}
    lineage_source = source.get("lineage") if isinstance(source.get("lineage"), dict) else {}
    session = _public_detail_canonical_session(lineage_source.get("session"))
    jsonl_source = source.get("jsonl") if isinstance(source.get("jsonl"), dict) else {}
    errors_source = source.get("errors") if isinstance(source.get("errors"), dict) else {}

    def public_text(value: Any) -> str | None:
        return _redact_string(value, redact_paths=True) if isinstance(value, str) else None

    lineage = {
        "run_id": lineage_source.get("run_id"),
        "run_slide_id": lineage_source.get("run_slide_id"),
        "stage_id": public_text(lineage_source.get("stage_id")),
        "attempt": lineage_source.get("attempt"),
        "invocation_id": lineage_source.get("invocation_id"),
        "session": session,
        "call": {
            "id": public_text(
                lineage_source.get("call", {}).get("id")
                if isinstance(lineage_source.get("call"), dict)
                else None
            ),
            "arguments_sha256": public_text(
                lineage_source.get("call", {}).get("arguments_sha256")
                if isinstance(lineage_source.get("call"), dict)
                else None
            ),
        },
    }
    output_protocol = public_text(lineage_source.get("output_protocol"))
    if output_protocol is not None:
        lineage["output_protocol"] = output_protocol
    return {
        "run_id": source.get("run_id"),
        "invocation_id": source.get("invocation_id"),
        "lineage": lineage,
        "prompt": public_text(source.get("prompt")),
        "assistant_output": public_text(source.get("assistant_output")),
        "tool_calls": [_public_detail_call_record(record) for record in source.get("tool_calls", []) if isinstance(record, dict)],
        "imagegen_calls": [
            _public_detail_call_record(record) for record in source.get("imagegen_calls", []) if isinstance(record, dict)
        ],
        "errors": {
            "invocation_error": redact_audit_error(errors_source.get("invocation_error")),
            "metadata_error": redact_audit_error(errors_source.get("metadata_error")),
            "event_errors": [],
        },
        "jsonl": {
            "raw": _public_detail_jsonl_reference(jsonl_source.get("raw")),
            "observed": _public_detail_jsonl_reference(jsonl_source.get("observed")),
            "canonical_session": _public_detail_canonical_session(jsonl_source.get("canonical_session")),
        },
        "metadata": public_native_image_projection(source.get("metadata")),
    }


def _bounded_event_page_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    encoded = value.encode("utf-8")
    if len(encoded) <= 512:
        return value
    return {"truncated": True, "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _event_page_item(row: Any) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        payload = {"invalid_payload": True}
    return {
        "sequence": row["sequence"],
        "event_type": _bounded_event_page_text(row["event_type"]),
        "item_type": _bounded_event_page_text(row["item_type"]),
        "is_error": row["is_error"],
        "observed_at": _bounded_event_page_text(row["observed_at"]),
        "event_timestamp": _bounded_event_page_text(row["event_timestamp"]),
        "payload": _bounded_event_page_payload(_public_event_page_payload(payload)),
    }


def get_codex_audit_event_page(*, run_id: int, invocation_id: int, cursor: str | None = None) -> dict[str, Any]:
    """Return one signed, fixed-size page for a Run-owned invocation timeline."""
    invocation = dbmod.get_codex_invocation(invocation_id, include_events=False)
    if invocation is None or invocation.get("run_id") != run_id:
        raise CodexAuditEventPageUnavailable("Codex audit invocation was not found for this Run")
    source_identity = _event_source_identity(invocation)
    position = 0 if cursor is None else _decode_event_cursor(
        cursor,
        run_id=run_id,
        invocation_id=invocation_id,
        source_identity=source_identity,
    )
    db = dbmod.get_db()
    try:
        rows = db.execute(
            """SELECT sequence, event_type, item_type, is_error, observed_at,
                      event_timestamp, payload_json
                 FROM codex_events
                 WHERE invocation_id = ? AND sequence > ?
                 ORDER BY sequence
                 LIMIT ?""",
            (invocation_id, position, EVENT_PAGE_SIZE + 1),
        ).fetchall()
    finally:
        db.close()
    has_more = len(rows) > EVENT_PAGE_SIZE
    page_rows = rows[:EVENT_PAGE_SIZE]
    items = [_event_page_item(row) for row in page_rows]
    next_position = int(items[-1]["sequence"]) if items else position
    page: dict[str, Any] = {
        "run_id": run_id,
        "invocation_id": invocation_id,
        "items": items,
        "next_cursor": (
            _encode_event_cursor(
                run_id=run_id,
                invocation_id=invocation_id,
                position=next_position,
                source_identity=source_identity,
            )
            if has_more
            else None
        ),
    }
    if len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > EVENT_PAGE_RESPONSE_BYTES:
        raise CodexAuditEventPageUnavailable("Codex audit event page exceeds its fixed response budget")
    return page


def _detail_call_records(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project actual tool and imagegen records from the persisted raw timeline."""
    tools: list[dict[str, Any]] = []
    imagegen: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        item = _event_item(payload)
        candidates = [item] if item else [payload]
        for candidate in candidates:
            kind = candidate.get("type") if isinstance(candidate.get("type"), str) else None
            name = candidate.get("name") if isinstance(candidate.get("name"), str) else None
            is_tool = kind in {"function_call", "function_call_output", "tool_call", "tool_result", "command_execution"}
            is_imagegen = name == "imagegen" or kind in {"image_generation", "image_generation_end"}
            if not is_tool and not is_imagegen:
                continue
            record = {
                "event_sequence": event.get("sequence"),
                "kind": kind,
                "name": name,
                "call_id": candidate.get("call_id"),
                "payload": _bounded_event_page_payload(candidate),
            }
            if is_tool:
                tools.append(record)
            if is_imagegen:
                imagegen.append(record)
    return tools, imagegen


def _validated_canonical_imagegen_calls(
    *,
    archive_path: Path,
    canonical: dict[str, Any],
    expected_call_id: object,
    allow_missing_completed_call: bool = False,
) -> list[dict[str, Any]]:
    """Project the one image result only from verified invocation-owned JSONL."""
    recorded_bytes = canonical.get("bytes")
    recorded_sha256 = canonical.get("sha256")
    if isinstance(recorded_bytes, bool) or not isinstance(recorded_bytes, int) or recorded_bytes < 0:
        raise CodexAuditDetailUnavailable("Native canonical session has no valid byte count")
    if not isinstance(recorded_sha256, str) or not recorded_sha256:
        raise CodexAuditDetailUnavailable("Native canonical session has no valid SHA-256")
    if not allow_missing_completed_call and (not isinstance(expected_call_id, str) or not expected_call_id):
        raise CodexAuditDetailUnavailable("Native image audit has no bound imagegen call id")
    try:
        archive_sha256 = sha256_file(archive_path)
        actual_bytes = archive_path.stat().st_size
    except OSError as exc:
        raise CodexAuditDetailUnavailable("Native canonical session cannot be read") from exc
    if actual_bytes != recorded_bytes:
        raise CodexAuditDetailUnavailable("Native canonical session byte count does not match")
    if archive_sha256 != recorded_sha256:
        raise CodexAuditDetailUnavailable("Native canonical session SHA-256 does not match")

    completed: tuple[int, dict[str, Any]] | None = None
    expected_offset = 0
    try:
        records = iter_semantic_jsonl_file_records(archive_path, canonical_imagegen_attestation=True)
        for line_number, semantic_record in enumerate(records, start=1):
            try:
                raw_range = semantic_record.raw_range
                if (
                    raw_range.start != expected_offset
                    or raw_range.end <= raw_range.start
                    or raw_range.length != raw_range.end - raw_range.start
                    or len(raw_range.sha256) != 64
                ):
                    raise CodexAuditDetailUnavailable("Native canonical session has an invalid raw record range")
                expected_offset = raw_range.end
                record = semantic_record.event
                if not isinstance(record, dict):
                    raise CodexAuditDetailUnavailable("Native canonical session contains a non-object record")
                payload = record.get("payload")
                if (
                    record.get("type") == "event_msg"
                    and isinstance(payload, dict)
                    and payload.get("type") == "image_generation_end"
                    and payload.get("status") == "completed"
                ):
                    if completed is not None:
                        raise CodexAuditDetailUnavailable("Native canonical session has ambiguous imagegen completion evidence")
                    completed = (line_number, payload)
            finally:
                semantic_record.discard_final_capture()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CodexAuditDetailUnavailable("Native canonical session contains malformed JSONL") from exc
    if expected_offset != actual_bytes:
        raise CodexAuditDetailUnavailable("Native canonical session raw record ranges do not cover its bytes")
    if allow_missing_completed_call:
        if completed is not None:
            raise CodexAuditDetailUnavailable("Failed Native canonical session unexpectedly has completed imagegen evidence")
        return []
    if completed is None:
        raise CodexAuditDetailUnavailable("Native canonical session has ambiguous imagegen completion evidence")

    line_number, payload = completed
    call_id = payload.get("call_id")
    if not isinstance(call_id, str) or not call_id or call_id != expected_call_id:
        raise CodexAuditDetailUnavailable("Native canonical imagegen call does not match its bound invocation")
    return [
        {
            "event_sequence": line_number,
            "kind": "image_generation_end",
            "name": "imagegen",
            "call_id": call_id,
            "payload": redact_audit_detail_value(payload),
        }
    ]


def _failed_native_without_completed_imagegen_call(*, invocation: dict[str, Any], metadata: dict[str, Any]) -> bool:
    """Allow detail projection only for a recorded terminal Native failure with no bound call."""
    failure_code = metadata.get("failure_code")
    return (
        invocation.get("status") == "failed"
        and metadata.get("terminal_state") == "failed"
        and metadata.get("imagegen_call_id") is None
        and isinstance(failure_code, str)
        and bool(failure_code)
    )


def get_native_codex_audit_detail(*, run_id: int, invocation_id: int) -> dict[str, Any]:
    """Load complete Native audit detail from one persisted Run/invocation identity.

    There is intentionally no caller-provided filesystem path.  Every readable
    file is either a database field owned by the requested invocation or a fixed
    sibling in the validated Run-private attempt directory.
    """
    invocation = dbmod.get_codex_invocation(invocation_id, include_events=False)
    if invocation is None or invocation.get("run_id") != run_id:
        raise CodexAuditDetailUnavailable("Codex audit invocation was not found for this Run")
    metadata = invocation.get("metadata")
    if not contains_native_private_evidence(metadata):
        raise CodexAuditDetailUnavailable("Codex audit detail is unavailable for this invocation")
    if not isinstance(metadata, dict):
        raise CodexAuditDetailUnavailable("Native audit metadata is unavailable")

    private_root = _native_private_root(run_id)
    raw_path = _owned_native_private_path(invocation.get("raw_jsonl_path"), private_root=private_root)
    observed_path = _owned_native_private_path(invocation.get("observed_jsonl_path"), private_root=private_root)
    attempt_dir = _native_attempt_directory(invocation, private_root=private_root)
    if raw_path.parent != attempt_dir or observed_path.parent != attempt_dir:
        raise CodexAuditDetailUnavailable("Native audit JSONL evidence does not match its slide, stage, and attempt")
    output_value = invocation.get("output_path")
    output_path = (
        _owned_native_private_path(output_value, private_root=private_root)
        if isinstance(output_value, str) and output_value
        else attempt_dir / "final_response.txt"
    )
    if output_path.parent != attempt_dir:
        raise CodexAuditDetailUnavailable("Native audit output does not share an invocation directory")

    canonical = metadata.get("canonical_session")
    if not isinstance(canonical, dict):
        canonical = {}
    archive_value = canonical.get("archive_path")
    archive_path = (
        _owned_native_private_path(archive_value, private_root=private_root)
        if isinstance(archive_value, str) and archive_value
        else None
    )
    if archive_path is not None and archive_path.parent != attempt_dir:
        raise CodexAuditDetailUnavailable("Native canonical session does not match its invocation directory")
    # The event timeline is an explicitly requested signed page.  Do not load
    # it merely to build and discard detail fields on Run Detail expansion.
    tool_calls, _outer_imagegen_calls = _detail_call_records(_detail_tool_call_events(invocation_id))
    expected_imagegen_call_id = metadata.get("imagegen_call_id")
    thread_scoped_output = metadata.get("thread_scoped_generated_image")
    thread_scoped_protocol = metadata.get("image_output_protocol") == "thread_scoped_generated_image_v1"
    if thread_scoped_protocol:
        if (
            expected_imagegen_call_id != "not_applicable"
            or metadata.get("imagegen_call_arguments_sha256") != "not_applicable"
            or metadata.get("imagegen_input") != "not_applicable"
            or not isinstance(thread_scoped_output, dict)
            or thread_scoped_output.get("protocol") != "thread_scoped_generated_image_v1"
        ):
            raise CodexAuditDetailUnavailable("Thread-scoped Native audit has invalid legacy-call fields")
        if archive_path is None:
            raise CodexAuditDetailUnavailable("Thread-scoped Native audit has no owned canonical session archive")
        imagegen_calls = _validated_canonical_imagegen_calls(
            archive_path=archive_path,
            canonical=canonical,
            expected_call_id=expected_imagegen_call_id,
            allow_missing_completed_call=True,
        )
    elif expected_imagegen_call_id == "not_applicable":
        imagegen_calls: list[dict[str, Any]] = []
    else:
        if archive_path is None:
            raise CodexAuditDetailUnavailable("Native image audit has no owned canonical session archive")
        imagegen_calls = _validated_canonical_imagegen_calls(
            archive_path=archive_path,
            canonical=canonical,
            expected_call_id=expected_imagegen_call_id,
            allow_missing_completed_call=_failed_native_without_completed_imagegen_call(
                invocation=invocation,
                metadata=metadata,
            ),
        )
    prompt = _read_owned_detail_text(attempt_dir / "prompt.md")
    assistant_output = _read_owned_detail_text(output_path)
    lineage = {
        "run_id": run_id,
        "run_slide_id": invocation.get("run_slide_id"),
        "stage_id": invocation.get("stage_id"),
        "attempt": invocation.get("attempt"),
        "invocation_id": invocation_id,
        "thread_id": metadata.get("thread_id"),
        "session": {
            "source_path": canonical.get("source_path"),
            "archive_path": str(archive_path) if archive_path is not None else None,
            "bytes": canonical.get("bytes"),
            "sha256": canonical.get("sha256"),
        },
        "output_protocol": metadata.get("image_output_protocol"),
        "call": {
            "id": None if thread_scoped_protocol else metadata.get("imagegen_call_id"),
            "arguments_sha256": None
            if thread_scoped_protocol
            else metadata.get("imagegen_call_arguments_sha256"),
        },
    }
    if thread_scoped_protocol:
        lineage["thread_scoped_output"] = thread_scoped_output
    detail = {
        "run_id": run_id,
        "invocation_id": invocation_id,
        "lineage": lineage,
        "prompt": prompt,
        "assistant_output": assistant_output,
        "tool_calls": tool_calls,
        "imagegen_calls": imagegen_calls,
        "errors": {
            "invocation_error": invocation.get("error_message"),
            "metadata_error": metadata.get("error"),
            "event_errors": [],
        },
        "jsonl": {
            "raw": _detail_jsonl_reference(raw_path, invocation.get("raw_jsonl_sha256")),
            "observed": _detail_jsonl_reference(observed_path, invocation.get("observed_jsonl_sha256")),
            "canonical_session": {
                "source_path": canonical.get("source_path"),
                "archive_path": str(archive_path) if archive_path is not None else None,
                "bytes": canonical.get("bytes"),
                "sha256": canonical.get("sha256"),
            },
        },
        "metadata": metadata,
    }
    return public_native_audit_detail_projection(detail)


def _slide_statuses_from_db(run_id: int) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for slide in dbmod.list_run_slides(run_id):
        artifacts = _parse_json_dict(slide.get("stage_artifacts"))
        codex_html = artifacts.get("codex_html") if isinstance(artifacts.get("codex_html"), dict) else {}
        statuses.append(
            {
                "run_slide_id": slide.get("id"),
                "position": slide.get("position"),
                "status": slide.get("status"),
                "attempt_count": codex_html.get("attempt_count"),
            }
        )
    return statuses


def _machine_qa_summary(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    pass_count = sum(1 for row in rows if row.get("verdict") == "pass")
    fail_count = sum(1 for row in rows if row.get("verdict") == "fail")
    skipped_count = sum(1 for row in rows if row.get("verdict") == "skipped")
    status = "fail" if fail_count else "skipped" if skipped_count and not pass_count else "pass"
    return {
        "status": status,
        "total": len(rows),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "skipped_count": skipped_count,
    }


def get_codex_run_audit(run_id: int, *, redacted: bool = True) -> dict[str, Any]:
    run = dbmod.get_run(run_id)
    if run is None:
        raise ValueError("run not found")
    metadata = _parse_json_dict(run.get("model_call_metadata"))
    invocations = dbmod.list_codex_invocations(run_id=run_id, include_events=False)
    machine_qa = dbmod.list_machine_qa_for_run(run_id)
    per_slide_statuses = metadata.get("per_slide_statuses")
    if not isinstance(per_slide_statuses, list):
        per_slide_statuses = _slide_statuses_from_db(run_id)
    failure_count = metadata.get("failure_count")
    if failure_count is None:
        failure_count = sum(1 for slide in per_slide_statuses if slide.get("status") == "failed")
    attempt_count = metadata.get("attempt_count")
    if attempt_count is None:
        attempt_count = sum(int(invocation.get("attempt") or 0) for invocation in invocations)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "status": run.get("status"),
        "failure_count": failure_count,
        "attempt_count": attempt_count,
        "per_slide_statuses": project_native_public_value(per_slide_statuses),
        "invocation_count": len(invocations),
        "event_count": sum(int(invocation.get("event_count") or 0) for invocation in invocations),
        "error_event_count": sum(int(invocation.get("error_event_count") or 0) for invocation in invocations),
        "raw_jsonl_paths": [invocation.get("raw_jsonl_path") for invocation in invocations if invocation.get("raw_jsonl_path")],
        "observed_jsonl_paths": [
            invocation.get("observed_jsonl_path") for invocation in invocations if invocation.get("observed_jsonl_path")
        ],
        "invocations": invocations,
    }
    if machine_qa:
        summary["machine_qa_summary"] = _machine_qa_summary(machine_qa)
        summary["machine_qa"] = machine_qa
    if not redacted:
        return summary
    summary.pop("raw_jsonl_paths", None)
    summary.pop("observed_jsonl_paths", None)
    summary["invocations"] = [_public_codex_invocation(invocation) for invocation in invocations]
    return redact_audit_value(summary)
