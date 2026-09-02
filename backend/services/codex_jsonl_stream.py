"""Shared bounded JSONL framing for private Codex evidence streams."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import uuid
from typing import Any, BinaryIO, Callable, Iterator

from backend.services.private_file_permissions import restrict_owner_only_fd


SOURCE_WINDOW_BYTES = 30_720
MAX_SUMMARY_EVENT_PROJECTIONS = 128
# Selected fields never need arbitrary structural nesting. Enforce before
# allocating a child path so hostile JSON cannot grow the validator stack or
# tuple-path chain without a fixed upper bound.
MAX_JSON_NESTING = 64


class SemanticJsonlError(ValueError):
    """A complete JSONL source record failed UTF-8 or JSON/object validation."""


@dataclass(frozen=True)
class FinalTextCapture:
    """The last valid final text without retaining a large decoded string in RAM."""

    inline_text: str | None
    spool_path: Path | None
    text_length: int
    text_sha256: str
    owns_spool: bool = True

    @classmethod
    def inline(cls, text: str) -> "FinalTextCapture":
        return cls(
            inline_text=text,
            spool_path=None,
            text_length=len(text),
            text_sha256=sha256(text.encode("utf-8")).hexdigest(),
        )

    def iter_text_chunks(self) -> Iterator[str]:
        if self.inline_text is not None:
            yield self.inline_text
            return
        if self.spool_path is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        with self.spool_path.open("rb") as source:
            while chunk := source.read(SOURCE_WINDOW_BYTES):
                text = decoder.decode(chunk)
                if text:
                    yield text
            final = decoder.decode(b"", final=True)
            if final:
                yield final

    def copy_to(self, destination: str | Path) -> None:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as target:
            if self.inline_text is not None:
                target.write(self.inline_text.encode("utf-8"))
            elif self.spool_path is not None:
                with self.spool_path.open("rb") as source:
                    while chunk := source.read(SOURCE_WINDOW_BYTES):
                        target.write(chunk)

    def discard(self) -> None:
        if self.owns_spool and self.spool_path is not None:
            self.spool_path.unlink(missing_ok=True)

    def transcript_message(self) -> "TranscriptProjection":
        return TranscriptProjection(role="assistant", inline_text=self.inline_text, capture=self)

    def metadata(self) -> dict[str, int | str]:
        return {"length": self.text_length, "sha256": self.text_sha256}


@dataclass(frozen=True)
class FinalTextEvidence:
    """Digest-only decoded final-text identity safe for compact projections."""

    text_length: int
    text_sha256: str

    def metadata(self) -> dict[str, int | str]:
        return {"length": self.text_length, "sha256": self.text_sha256}


@dataclass
class FinalTextCaptureLease:
    """One explicit owner for a private final-text capture."""

    _capture: FinalTextCapture | None

    def peek(self) -> FinalTextCapture | None:
        return self._capture

    def transfer(self) -> FinalTextCapture | None:
        capture = self._capture
        self._capture = None
        return capture

    def discard(self) -> None:
        capture = self.transfer()
        if capture is not None:
            capture.discard()


@dataclass(frozen=True)
class TranscriptProjection:
    """One validated transcript message, optionally backed by a private capture."""

    role: str
    inline_text: str | None = None
    capture: FinalTextCapture | None = None


@dataclass(frozen=True)
class SemanticJsonlRecord:
    """A fully validated JSON object plus bounded selected projections."""

    raw_range: "JsonlRawRange"
    raw_bytes: bytes | None
    event: dict[str, Any]
    oversized: bool
    final_text_evidence: FinalTextEvidence | None = None
    final_capture_lease: FinalTextCaptureLease | None = None
    transcript_projection: TranscriptProjection | None = None

    @property
    def final_capture(self) -> FinalTextCapture | None:
        """Compatibility peek; consumers must transfer or discard the lease."""
        return self.final_capture_lease.peek() if self.final_capture_lease is not None else None

    def take_final_capture(self) -> FinalTextCapture | None:
        return self.final_capture_lease.transfer() if self.final_capture_lease is not None else None

    def discard_final_capture(self) -> None:
        if self.final_capture_lease is not None:
            self.final_capture_lease.discard()


@dataclass(frozen=True)
class JsonlRawRange:
    """Exact private-source coordinates for one complete JSONL record."""

    start: int
    end: int
    length: int
    sha256: str

    def as_dict(self) -> dict[str, int | str]:
        return {
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class JsonlRecord:
    """One complete record without retaining oversized source bytes."""

    raw_range: JsonlRawRange
    raw_bytes: bytes | None
    prefix_bytes: bytes | None
    event: dict[str, Any] | None
    oversized: bool
    invalid: bool

    def sqlite_placeholder_event(self) -> dict[str, Any]:
        """Bounded event shape for a complete source record over the inline cap."""
        return {
            "type": "codex.audit.oversized_jsonl_record",
            "raw_range": self.raw_range.as_dict(),
        }


@dataclass(frozen=True)
class CodexStreamSummary:
    """Bounded live facts extracted from a stdout JSONL stream."""

    thread_id: str | None = None
    final_text: str = ""
    final_capture: FinalTextCapture | None = None
    byte_count: int = 0
    complete_record_count: int = 0
    oversized_record_count: int = 0
    last_record_range: JsonlRawRange | None = None
    last_oversized_record_range: JsonlRawRange | None = None
    thread_ids: tuple[str, ...] = ()
    event_projections: tuple[dict[str, Any], ...] = ()
    omitted_event_projection_count: int = 0


class CodexStreamSummaryBuilder:
    """Collect only fixed-size stream metadata, never a full event array."""

    def __init__(self) -> None:
        self._thread_id: str | None = None
        self._final_text = ""
        self._final_capture: FinalTextCapture | None = None
        self._byte_count = 0
        self._complete_record_count = 0
        self._oversized_record_count = 0
        self._last_record_range: JsonlRawRange | None = None
        self._last_oversized_record_range: JsonlRawRange | None = None
        self._thread_ids: list[str] = []
        self._event_projections: list[dict[str, Any]] = []
        self._omitted_event_projection_count = 0

    def observe_chunk(self, chunk: bytes) -> None:
        self._byte_count += len(chunk)

    def observe_record(self, record: JsonlRecord) -> None:
        self._complete_record_count += 1
        self._last_record_range = record.raw_range
        if record.oversized:
            self._oversized_record_count += 1
            self._last_oversized_record_range = record.raw_range
            return
        event = record.event
        if not isinstance(event, dict):
            return
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            self._thread_id = event["thread_id"]
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            self._final_text = item["text"]

    def observe_semantic_record(self, record: SemanticJsonlRecord, *, observed_at: str) -> None:
        """Accept exactly one validated projection from the sole semantic owner."""
        self._complete_record_count += 1
        self._last_record_range = record.raw_range
        if record.oversized:
            self._oversized_record_count += 1
            self._last_oversized_record_range = record.raw_range
        event = record.event
        thread_id = event.get("thread_id")
        if event.get("type") == "thread.started" and isinstance(thread_id, str) and thread_id:
            if thread_id not in self._thread_ids and len(self._thread_ids) < 2:
                self._thread_ids.append(thread_id)
            if self._thread_id is None:
                self._thread_id = thread_id
        capture = record.take_final_capture()
        if capture is not None:
            if self._final_capture is not None and self._final_capture is not capture:
                self._final_capture.discard()
            self._final_capture = capture
            self._final_text = capture.inline_text or ""
        projection = {
            "sequence": self._complete_record_count,
            "observed_at": observed_at,
            "event_type": event.get("type") if isinstance(event.get("type"), str) else None,
            "thread_id": thread_id if isinstance(thread_id, str) else None,
            "item_id": event.get("item", {}).get("id") if isinstance(event.get("item"), dict) and isinstance(event["item"].get("id"), str) else None,
            "item_type": event.get("item", {}).get("type") if isinstance(event.get("item"), dict) and isinstance(event["item"].get("type"), str) else None,
        }
        if len(self._event_projections) < MAX_SUMMARY_EVENT_PROJECTIONS:
            self._event_projections.append(projection)
        else:
            self._omitted_event_projection_count += 1

    def discard_captures(self) -> None:
        if self._final_capture is not None:
            self._final_capture.discard()
            self._final_capture = None

    def build(self) -> CodexStreamSummary:
        return CodexStreamSummary(
            thread_id=self._thread_id,
            final_text=self._final_text,
            final_capture=self._final_capture,
            byte_count=self._byte_count,
            complete_record_count=self._complete_record_count,
            oversized_record_count=self._oversized_record_count,
            last_record_range=self._last_record_range,
            last_oversized_record_range=self._last_oversized_record_range,
            thread_ids=tuple(self._thread_ids),
            event_projections=tuple(self._event_projections),
            omitted_event_projection_count=self._omitted_event_projection_count,
        )


class BoundedJsonlFramer:
    """Frame JSONL chunks while retaining at most one 30,720-byte record."""

    def __init__(self, *, start_offset: int = 0, inline_limit: int = SOURCE_WINDOW_BYTES) -> None:
        self._inline_limit = inline_limit
        self._record_start = start_offset
        self._record_length = 0
        self._digest = sha256()
        self._inline_bytes: bytearray | None = bytearray()
        self._prefix_bytes: bytearray | None = None

    @property
    def has_incomplete_record(self) -> bool:
        return self._record_length > 0

    def feed(self, chunk: bytes) -> list[JsonlRecord]:
        if len(chunk) > SOURCE_WINDOW_BYTES:
            raise ValueError("JSONL source chunk exceeded the fixed read window")
        records: list[JsonlRecord] = []
        position = 0
        while position < len(chunk):
            newline = chunk.find(b"\n", position)
            end = len(chunk) if newline < 0 else newline + 1
            self._append(chunk[position:end])
            position = end
            if newline >= 0:
                records.append(self._complete_record())
        return records

    def _append(self, segment: bytes) -> None:
        next_length = self._record_length + len(segment)
        self._digest.update(segment)
        if self._inline_bytes is not None:
            if next_length <= self._inline_limit:
                self._inline_bytes.extend(segment)
            else:
                self._prefix_bytes = self._inline_bytes
                remaining_prefix = self._inline_limit - len(self._prefix_bytes)
                if remaining_prefix > 0:
                    self._prefix_bytes.extend(segment[:remaining_prefix])
                self._inline_bytes = None
        elif self._prefix_bytes is not None and len(self._prefix_bytes) < self._inline_limit:
            remaining_prefix = self._inline_limit - len(self._prefix_bytes)
            self._prefix_bytes.extend(segment[:remaining_prefix])
        self._record_length = next_length

    def _complete_record(self) -> JsonlRecord:
        raw_range = JsonlRawRange(
            start=self._record_start,
            end=self._record_start + self._record_length,
            length=self._record_length,
            sha256=self._digest.hexdigest(),
        )
        raw_bytes = bytes(self._inline_bytes) if self._inline_bytes is not None else None
        oversized = raw_bytes is None
        prefix_bytes = bytes(self._prefix_bytes) if oversized and self._prefix_bytes is not None else None
        event: dict[str, Any] | None = None
        invalid = False
        if raw_bytes is not None:
            try:
                payload = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                invalid = True
            else:
                if isinstance(payload, dict):
                    event = payload
                else:
                    invalid = True
        record = JsonlRecord(
            raw_range=raw_range,
            raw_bytes=raw_bytes,
            prefix_bytes=prefix_bytes,
            event=event,
            oversized=oversized,
            invalid=invalid,
        )
        self._record_start = raw_range.end
        self._record_length = 0
        self._digest = sha256()
        self._inline_bytes = bytearray()
        self._prefix_bytes = None
        return record


def iter_bounded_jsonl_records(
    handle: BinaryIO,
    *,
    start_offset: int = 0,
    require_complete: bool = True,
) -> Iterator[JsonlRecord]:
    """Yield complete records with every physical handle read capped at 30,720 bytes."""
    framer = BoundedJsonlFramer(start_offset=start_offset)
    while True:
        chunk = handle.read(SOURCE_WINDOW_BYTES)
        if not chunk:
            break
        if len(chunk) > SOURCE_WINDOW_BYTES:
            raise ValueError("JSONL source handle exceeded the fixed read window")
        yield from framer.feed(chunk)
    if require_complete and framer.has_incomplete_record:
        raise ValueError("JSONL source ended with an incomplete record")


def iter_bounded_jsonl_file_records(
    path: str | Path,
    *,
    start_offset: int = 0,
    require_complete: bool = True,
) -> Iterator[JsonlRecord]:
    """Open a private JSONL source and apply the shared bounded record framing."""
    with Path(path).open("rb") as handle:
        if start_offset:
            handle.seek(start_offset)
        yield from iter_bounded_jsonl_records(
            handle,
            start_offset=start_offset,
            require_complete=require_complete,
        )


@dataclass
class _JsonContext:
    kind: str
    path: tuple[str, ...]
    state: str
    current_key: str | None = None


class _TextCaptureWriter:
    """Decode one selected JSON string into bounded RAM or a 0600 private spool."""

    def __init__(self, spool_dir: Path) -> None:
        self._spool_dir = spool_dir
        self._inline_parts: list[str] = []
        self._inline_bytes = 0
        self._text_length = 0
        self._digest = sha256()
        self._temporary_path: Path | None = None
        self._published_path: Path | None = None
        self._handle = None

    def append(self, value: str) -> None:
        encoded = value.encode("utf-8")
        self._digest.update(encoded)
        self._text_length += len(value)
        if self._handle is None and self._inline_bytes + len(encoded) <= SOURCE_WINDOW_BYTES:
            self._inline_parts.append(value)
            self._inline_bytes += len(encoded)
            return
        if self._handle is None:
            self._spool_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary_name = tempfile.mkstemp(prefix=".codex-final-text-", suffix=".tmp", dir=self._spool_dir)
            restrict_owner_only_fd(fd)
            self._temporary_path = Path(temporary_name)
            self._handle = os.fdopen(fd, "wb")
            self._handle.write("".join(self._inline_parts).encode("utf-8"))
            self._inline_parts.clear()
        self._handle.write(encoded)

    def publish(self) -> FinalTextCapture:
        if self._handle is None:
            return FinalTextCapture.inline("".join(self._inline_parts))
        self._handle.flush()
        self._handle.close()
        self._handle = None
        assert self._temporary_path is not None
        published = self._spool_dir / f"codex-final-text-{uuid.uuid4().hex}.txt"
        os.replace(self._temporary_path, published)
        os.chmod(published, 0o600)
        self._temporary_path = None
        self._published_path = published
        return FinalTextCapture(
            inline_text=None,
            spool_path=published,
            text_length=self._text_length,
            text_sha256=self._digest.hexdigest(),
        )

    def evidence(self) -> FinalTextEvidence:
        return FinalTextEvidence(
            text_length=self._text_length,
            text_sha256=self._digest.hexdigest(),
        )

    def abort(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        if self._temporary_path is not None:
            self._temporary_path.unlink(missing_ok=True)
            self._temporary_path = None
        if self._published_path is not None:
            self._published_path.unlink(missing_ok=True)
            self._published_path = None


class _TextEvidenceWriter:
    """Digest one decoded selected string without retaining or spooling it."""

    def __init__(self) -> None:
        self._text_length = 0
        self._digest = sha256()

    def append(self, value: str) -> None:
        encoded = value.encode("utf-8")
        self._text_length += len(value)
        self._digest.update(encoded)

    def evidence(self) -> FinalTextEvidence:
        return FinalTextEvidence(
            text_length=self._text_length,
            text_sha256=self._digest.hexdigest(),
        )

    def abort(self) -> None:
        return None


class _ProjectionCollector:
    """Keep only fields consumed by normal downstream execution paths."""

    def __init__(
        self,
        spool_dir: Path,
        *,
        observed_wrapper: bool = False,
        canonical_imagegen_attestation: bool = False,
    ) -> None:
        self._spool_dir = spool_dir
        self._observed_wrapper = observed_wrapper
        self._canonical_imagegen_attestation = canonical_imagegen_attestation
        self._event_prefix = ("event",) if observed_wrapper else ()
        self.root_type: str | None = None
        self.thread_id: str | None = None
        self.item_id: str | None = None
        self.item_type: str | None = None
        self.observed_at: str | None = None
        self.payload_type: str | None = None
        self.payload_status: str | None = None
        self.payload_call_id: str | None = None
        self.payload_revised_prompt: str | None = None
        self._canonical_root_type_seen = False
        self._canonical_payload_seen = False
        self._canonical_payload_fields_seen: set[str] = set()
        self._text_capture_writer: _TextCaptureWriter | _TextEvidenceWriter | None = None

    def begins_text_capture(self, path: tuple[str, ...]) -> _TextCaptureWriter | _TextEvidenceWriter | None:
        if self._canonical_imagegen_attestation:
            return None
        if path == self._event_prefix + ("item", "text"):
            if self._text_capture_writer is not None:
                self._text_capture_writer.abort()
            self._text_capture_writer = (
                _TextEvidenceWriter() if self._observed_wrapper else _TextCaptureWriter(self._spool_dir)
            )
            return self._text_capture_writer
        return None

    def collects_string(self, path: tuple[str, ...]) -> bool:
        default_paths = {
            self._event_prefix + ("type",),
            self._event_prefix + ("thread_id",),
            self._event_prefix + ("item", "id"),
            self._event_prefix + ("item", "type"),
            ("observed_at",),
        }
        if self._canonical_imagegen_attestation:
            default_paths.update(
                {
                    self._event_prefix + ("payload", "type"),
                    self._event_prefix + ("payload", "status"),
                    self._event_prefix + ("payload", "call_id"),
                    self._event_prefix + ("payload", "revised_prompt"),
                }
            )
        return path in default_paths

    def object_key(self, object_path: tuple[str, ...], key: str) -> None:
        """Reject only ambiguity that could forge canonical image completion."""
        if not self._canonical_imagegen_attestation:
            return
        if object_path == self._event_prefix and key == "type":
            if self._canonical_root_type_seen:
                raise SemanticJsonlError("canonical attestation has duplicate root type")
            self._canonical_root_type_seen = True
        elif object_path == self._event_prefix and key == "payload":
            if self._canonical_payload_seen:
                raise SemanticJsonlError("canonical attestation has duplicate payload objects")
            self._canonical_payload_seen = True
        elif object_path == self._event_prefix + ("payload",) and key in {
            "type",
            "status",
            "call_id",
            "revised_prompt",
        }:
            if key in self._canonical_payload_fields_seen:
                raise SemanticJsonlError("canonical attestation has duplicate selected payload field")
            self._canonical_payload_fields_seen.add(key)

    def string(self, path: tuple[str, ...], value: str) -> None:
        if path == self._event_prefix + ("type",):
            self.root_type = value
        elif path == self._event_prefix + ("thread_id",):
            self.thread_id = value
        elif path == self._event_prefix + ("item", "id"):
            self.item_id = value
        elif path == self._event_prefix + ("item", "type"):
            self.item_type = value
        elif path == self._event_prefix + ("payload", "type"):
            self.payload_type = value
        elif path == self._event_prefix + ("payload", "status"):
            self.payload_status = value
        elif path == self._event_prefix + ("payload", "call_id"):
            self.payload_call_id = value
        elif path == self._event_prefix + ("payload", "revised_prompt"):
            self.payload_revised_prompt = value
        elif path == ("observed_at",):
            self.observed_at = value

    def final_text_evidence(self) -> FinalTextEvidence | None:
        if self.item_type != "agent_message" or self._text_capture_writer is None:
            if self._text_capture_writer is not None:
                self._text_capture_writer.abort()
                self._text_capture_writer = None
            return None
        return self._text_capture_writer.evidence()

    def final_capture(self) -> FinalTextCapture | None:
        if self._observed_wrapper or self.item_type != "agent_message" or self._text_capture_writer is None:
            return None
        assert isinstance(self._text_capture_writer, _TextCaptureWriter)
        return self._text_capture_writer.publish()

    def compact_event(self, raw_range: JsonlRawRange, evidence: FinalTextEvidence | None) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": self.root_type or "codex.audit.compact_jsonl_record",
            "raw_range": raw_range.as_dict(),
        }
        if self.thread_id is not None:
            event["thread_id"] = self.thread_id
        item: dict[str, Any] = {}
        if self.item_id is not None:
            item["id"] = self.item_id
        if self.item_type is not None:
            item["type"] = self.item_type
        if evidence is not None:
            item["text_capture"] = evidence.metadata()
        if item:
            event["item"] = item
        if self._canonical_imagegen_attestation:
            payload = {
                key: value
                for key, value in (
                    ("type", self.payload_type),
                    ("status", self.payload_status),
                    ("call_id", self.payload_call_id),
                    ("revised_prompt", self.payload_revised_prompt),
                )
                if value is not None
            }
            if payload:
                event["payload"] = payload
        if self._observed_wrapper:
            if not self.observed_at:
                raise SemanticJsonlError("observed JSONL record is missing observed_at")
            return {"observed_at": self.observed_at, "event": event}
        return event

    def abort(self) -> None:
        if self._text_capture_writer is not None:
            self._text_capture_writer.abort()


class _JsonString:
    """Incrementally unescape one JSON string without retaining an arbitrary value."""

    def __init__(
        self,
        *,
        path: tuple[str, ...] | None,
        is_key: bool,
        collector: _ProjectionCollector,
        complete: Callable[[tuple[str, ...] | None, bool, str], None],
    ) -> None:
        self._path = path
        self._is_key = is_key
        self._collector = collector
        self._complete = complete
        self._parts: list[str] | None = [] if is_key or (path is not None and collector.collects_string(path)) else None
        self._parts_bytes = 0
        self._capture = collector.begins_text_capture(path) if path is not None else None
        self._escape = False
        self._unicode: list[str] | None = None
        self._pending_high_surrogate: int | None = None

    def feed(self, char: str) -> bool:
        if self._unicode is not None:
            if char not in "0123456789abcdefABCDEF":
                raise SemanticJsonlError("invalid JSON unicode escape")
            self._unicode.append(char)
            if len(self._unicode) == 4:
                codepoint = int("".join(self._unicode), 16)
                if self._pending_high_surrogate is not None:
                    if not 0xDC00 <= codepoint <= 0xDFFF:
                        raise SemanticJsonlError("invalid JSON surrogate pair")
                    high = self._pending_high_surrogate
                    self._pending_high_surrogate = None
                    self._append(chr(0x10000 + ((high - 0xD800) << 10) + (codepoint - 0xDC00)))
                elif 0xD800 <= codepoint <= 0xDBFF:
                    self._pending_high_surrogate = codepoint
                elif 0xDC00 <= codepoint <= 0xDFFF:
                    raise SemanticJsonlError("unpaired JSON low surrogate")
                else:
                    self._append(chr(codepoint))
                self._unicode = None
                self._escape = False
            return False
        if self._pending_high_surrogate is not None and not self._escape:
            if char != "\\":
                raise SemanticJsonlError("unpaired JSON high surrogate")
            self._escape = True
            return False
        if self._escape:
            if char == "u":
                self._unicode = []
                return False
            mapping = {"\"": "\"", "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
            if char not in mapping:
                raise SemanticJsonlError("invalid JSON string escape")
            self._append(mapping[char])
            self._escape = False
            return False
        if char == "\"":
            if self._pending_high_surrogate is not None:
                raise SemanticJsonlError("unpaired JSON high surrogate")
            value = "".join(self._parts or ())
            self._complete(self._path, self._is_key, value)
            return True
        if char == "\\":
            self._escape = True
            return False
        if ord(char) < 0x20:
            raise SemanticJsonlError("unescaped control character in JSON string")
        self._append(char)
        return False

    def _append(self, value: str) -> None:
        try:
            if self._capture is not None:
                self._capture.append(value)
        except UnicodeEncodeError as exc:
            raise SemanticJsonlError("invalid Unicode in selected JSON string") from exc
        if self._parts is not None:
            self._parts_bytes += len(value.encode("utf-8"))
            if self._parts_bytes > SOURCE_WINDOW_BYTES:
                raise SemanticJsonlError("selected JSON field exceeds bounded semantic limit")
            self._parts.append(value)

    @property
    def incomplete(self) -> bool:
        return self._escape or self._unicode is not None or self._pending_high_surrogate is not None


class _IncrementalJsonObjectValidator:
    """Schema-aware JSON token parser which validates all nested syntax incrementally."""

    def __init__(self, collector: _ProjectionCollector) -> None:
        self._collector = collector
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._stack: list[_JsonContext] = []
        self._root_started = False
        self._root_complete = False
        self._string: _JsonString | None = None
        self._number_state: str | None = None
        self._literal: tuple[str, int] | None = None

    def feed(self, payload: bytes) -> None:
        try:
            text = self._decoder.decode(payload)
        except UnicodeDecodeError as exc:
            raise SemanticJsonlError("invalid UTF-8 in JSONL record") from exc
        for char in text:
            self._process(char)

    def finish(self) -> None:
        try:
            tail = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise SemanticJsonlError("incomplete UTF-8 in JSONL record") from exc
        for char in tail:
            self._process(char)
        if self._string is not None or self._number_state is not None or self._literal is not None:
            raise SemanticJsonlError("incomplete JSON token")
        if not self._root_complete or self._stack:
            raise SemanticJsonlError("incomplete JSON object")

    def _process(self, char: str) -> None:
        while True:
            if self._string is not None:
                if self._string.feed(char):
                    self._string = None
                return
            if self._literal is not None:
                expected, position = self._literal
                if position < len(expected):
                    if char != expected[position]:
                        raise SemanticJsonlError("invalid JSON literal")
                    self._literal = (expected, position + 1)
                    return
                if not self._is_delimiter(char):
                    raise SemanticJsonlError("invalid JSON literal suffix")
                self._literal = None
                self._complete_value()
                continue
            if self._number_state is not None:
                if self._advance_number(char):
                    return
                self._number_state = None
                self._complete_value()
                continue
            if char in " \t\r\n":
                return
            if self._root_complete:
                raise SemanticJsonlError("trailing content after JSON object")
            if char == "{":
                self._start_container("object")
                return
            if char == "[":
                self._start_container("array")
                return
            if char == "}":
                self._close_container("object")
                return
            if char == "]":
                self._close_container("array")
                return
            if char == ",":
                self._comma()
                return
            if char == ":":
                self._colon()
                return
            if char == "\"":
                self._start_string()
                return
            if char == "-" or self._is_ascii_digit(char):
                self._start_value("number")
                self._number_state = "minus" if char == "-" else ("zero" if char == "0" else "int")
                return
            if char in "tfn":
                self._start_value("literal")
                expected = {"t": "true", "f": "false", "n": "null"}[char]
                self._literal = (expected, 1)
                return
            raise SemanticJsonlError("invalid JSON token")

    def _start_string(self) -> None:
        if self._stack and self._stack[-1].kind == "object" and self._stack[-1].state in {"key_or_end", "key"}:
            self._string = _JsonString(path=None, is_key=True, collector=self._collector, complete=self._complete_string)
            return
        path = self._start_value("string")
        self._string = _JsonString(path=path, is_key=False, collector=self._collector, complete=self._complete_string)

    def _complete_string(self, path: tuple[str, ...] | None, is_key: bool, value: str) -> None:
        if is_key:
            if not self._stack or self._stack[-1].kind != "object" or self._stack[-1].state not in {"key_or_end", "key"}:
                raise SemanticJsonlError("unexpected JSON object key")
            context = self._stack[-1]
            self._collector.object_key(context.path, value)
            context.current_key = value
            context.state = "colon"
            return
        assert path is not None
        self._collector.string(path, value)
        self._complete_value()

    def _start_container(self, kind: str) -> None:
        path = self._start_value(kind, container=True)
        self._stack.append(_JsonContext(kind=kind, path=path, state="key_or_end" if kind == "object" else "value_or_end"))

    def _close_container(self, kind: str) -> None:
        if not self._stack or self._stack[-1].kind != kind:
            raise SemanticJsonlError("mismatched JSON container close")
        context = self._stack[-1]
        if context.state not in ({"key_or_end", "comma_or_end"} if kind == "object" else {"value_or_end", "comma_or_end"}):
            raise SemanticJsonlError("incomplete JSON container")
        self._stack.pop()
        self._complete_value()

    def _comma(self) -> None:
        if not self._stack or self._stack[-1].state != "comma_or_end":
            raise SemanticJsonlError("unexpected JSON comma")
        context = self._stack[-1]
        context.state = "key" if context.kind == "object" else "value"
        context.current_key = None

    def _colon(self) -> None:
        if not self._stack or self._stack[-1].kind != "object" or self._stack[-1].state != "colon":
            raise SemanticJsonlError("unexpected JSON colon")
        self._stack[-1].state = "value"

    def _start_value(self, _kind: str, *, container: bool = False) -> tuple[str, ...]:
        if not self._stack:
            if self._root_started:
                raise SemanticJsonlError("multiple JSON root values")
            if _kind != "object":
                raise SemanticJsonlError("JSONL record must be an object")
            self._root_started = True
            return ()
        context = self._stack[-1]
        if container and len(self._stack) >= MAX_JSON_NESTING:
            raise SemanticJsonlError("JSON nesting exceeds the fixed semantic limit")
        if context.kind == "object":
            if context.state != "value" or context.current_key is None:
                raise SemanticJsonlError("JSON object value is missing a key")
            return context.path + (context.current_key,)
        if context.state not in {"value_or_end", "value"}:
            raise SemanticJsonlError("unexpected JSON array value")
        return context.path + ("[]",)

    def _complete_value(self) -> None:
        if not self._stack:
            if not self._root_started or self._root_complete:
                raise SemanticJsonlError("unexpected JSON root completion")
            self._root_complete = True
            return
        context = self._stack[-1]
        if context.kind == "object":
            if context.state != "value":
                raise SemanticJsonlError("unexpected JSON object completion")
        elif context.state not in {"value_or_end", "value"}:
            raise SemanticJsonlError("unexpected JSON array completion")
        context.state = "comma_or_end"

    def _advance_number(self, char: str) -> bool:
        state = self._number_state
        assert state is not None
        if state == "minus":
            if not self._is_ascii_digit(char):
                raise SemanticJsonlError("invalid JSON number")
            self._number_state = "zero" if char == "0" else "int"
            return True
        if state == "zero":
            if char == ".":
                self._number_state = "fraction_need"
                return True
            if char in "eE":
                self._number_state = "exponent_need"
                return True
            if self._is_ascii_digit(char):
                raise SemanticJsonlError("invalid JSON number leading zero")
            return self._finish_number_on_delimiter(char)
        if state == "int":
            if self._is_ascii_digit(char):
                return True
            if char == ".":
                self._number_state = "fraction_need"
                return True
            if char in "eE":
                self._number_state = "exponent_need"
                return True
            return self._finish_number_on_delimiter(char)
        if state == "fraction_need":
            if not self._is_ascii_digit(char):
                raise SemanticJsonlError("invalid JSON fraction")
            self._number_state = "fraction"
            return True
        if state == "fraction":
            if self._is_ascii_digit(char):
                return True
            if char in "eE":
                self._number_state = "exponent_need"
                return True
            return self._finish_number_on_delimiter(char)
        if state == "exponent_need":
            if char in "+-":
                self._number_state = "exponent_sign"
                return True
            if self._is_ascii_digit(char):
                self._number_state = "exponent"
                return True
            raise SemanticJsonlError("invalid JSON exponent")
        if state == "exponent_sign":
            if not self._is_ascii_digit(char):
                raise SemanticJsonlError("invalid JSON exponent")
            self._number_state = "exponent"
            return True
        if state == "exponent":
            if self._is_ascii_digit(char):
                return True
            return self._finish_number_on_delimiter(char)
        raise SemanticJsonlError("invalid JSON number state")

    def _finish_number_on_delimiter(self, char: str) -> bool:
        if not self._is_delimiter(char):
            raise SemanticJsonlError("invalid JSON number suffix")
        return False

    @staticmethod
    def _is_delimiter(char: str) -> bool:
        return char in " \t\r\n,]}"

    @staticmethod
    def _is_ascii_digit(char: str) -> bool:
        return "0" <= char <= "9"


class _SemanticRecordState:
    def __init__(
        self,
        *,
        start_offset: int,
        spool_dir: Path,
        inline_limit: int,
        observed_wrapper: bool,
        canonical_imagegen_attestation: bool,
    ) -> None:
        self.start_offset = start_offset
        self.length = 0
        self.digest = sha256()
        self.inline_limit = inline_limit
        self.inline_bytes: bytearray | None = bytearray()
        self.observed_wrapper = observed_wrapper
        self.collector = _ProjectionCollector(
            spool_dir,
            observed_wrapper=observed_wrapper,
            canonical_imagegen_attestation=canonical_imagegen_attestation,
        )
        self.validator = _IncrementalJsonObjectValidator(self.collector)

    def append(self, payload: bytes, *, semantic: bool) -> None:
        self.digest.update(payload)
        self.length += len(payload)
        if self.inline_bytes is not None:
            if len(self.inline_bytes) + len(payload) <= self.inline_limit:
                self.inline_bytes.extend(payload)
            else:
                self.inline_bytes = None
        if semantic:
            self.validator.feed(payload)

    def complete(self) -> SemanticJsonlRecord:
        self.validator.finish()
        raw_range = JsonlRawRange(
            start=self.start_offset,
            end=self.start_offset + self.length,
            length=self.length,
            sha256=self.digest.hexdigest(),
        )
        raw_bytes = bytes(self.inline_bytes) if self.inline_bytes is not None else None
        evidence = self.collector.final_text_evidence()
        if raw_bytes is not None:
            try:
                event = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SemanticJsonlError("validated JSON record could not be decoded") from exc
            if not isinstance(event, dict):
                raise SemanticJsonlError("JSONL record must be an object")
            item = event.get("item")
            capture = self.collector.final_capture()
        else:
            capture = self.collector.final_capture()
            event = self.collector.compact_event(raw_range, evidence)
        transcript: TranscriptProjection | None = None
        if capture is not None:
            transcript = capture.transcript_message()
        else:
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "error":
                transcript = TranscriptProjection(role="codex_error", inline_text=json.dumps(item, ensure_ascii=False))
        return SemanticJsonlRecord(
            raw_range=raw_range,
            raw_bytes=raw_bytes,
            event=event,
            oversized=raw_bytes is None,
            final_text_evidence=evidence,
            final_capture_lease=FinalTextCaptureLease(capture) if capture is not None else None,
            transcript_projection=transcript,
        )

    def abort(self) -> None:
        self.collector.abort()


class StreamingJsonlSemanticProjector:
    """The sole live owner of UTF-8/JSON validation and selected projections."""

    def __init__(
        self,
        *,
        spool_dir: str | Path,
        start_offset: int = 0,
        inline_limit: int = SOURCE_WINDOW_BYTES,
        observed_wrapper: bool = False,
        canonical_imagegen_attestation: bool = False,
    ) -> None:
        self._spool_dir = Path(spool_dir)
        self._inline_limit = inline_limit
        self._observed_wrapper = observed_wrapper
        self._canonical_imagegen_attestation = canonical_imagegen_attestation
        self._record = _SemanticRecordState(
            start_offset=start_offset,
            spool_dir=self._spool_dir,
            inline_limit=inline_limit,
            observed_wrapper=observed_wrapper,
            canonical_imagegen_attestation=canonical_imagegen_attestation,
        )

    @property
    def has_incomplete_record(self) -> bool:
        return self._record.length > 0

    def feed(self, chunk: bytes) -> Iterator[SemanticJsonlRecord]:
        """Yield each complete record before parsing later bytes in the chunk.

        The consumer becomes the capture owner at each yield point.  If parsing
        a later record fails, a caller which already accepted an earlier record
        can deterministically discard that capture during its failure cleanup.
        """
        if len(chunk) > SOURCE_WINDOW_BYTES:
            raise ValueError("JSONL source chunk exceeded the fixed read window")
        position = 0
        while position < len(chunk):
            newline = chunk.find(b"\n", position)
            end = len(chunk) if newline < 0 else newline
            segment = chunk[position:end]
            try:
                if segment:
                    self._record.append(segment, semantic=True)
                if newline < 0:
                    break
                self._record.append(b"\n", semantic=False)
                completed = self._record.complete()
            except Exception:
                self._record.abort()
                raise
            self._record = _SemanticRecordState(
                start_offset=completed.raw_range.end,
                spool_dir=self._spool_dir,
                inline_limit=self._inline_limit,
                observed_wrapper=self._observed_wrapper,
                canonical_imagegen_attestation=self._canonical_imagegen_attestation,
            )
            position = newline + 1
            yield completed

    def finish(self) -> None:
        if self.has_incomplete_record:
            self._record.abort()
            raise SemanticJsonlError("JSONL source ended with an incomplete record")

    def abort(self) -> None:
        self._record.abort()


def iter_semantic_jsonl_file_records(
    path: str | Path,
    *,
    observed_wrapper: bool = False,
    start_offset: int = 0,
    canonical_imagegen_attestation: bool = False,
) -> Iterator[SemanticJsonlRecord]:
    """Validate a private JSONL file through the same single semantic projector."""
    file_path = Path(path)
    projector = StreamingJsonlSemanticProjector(
        spool_dir=file_path.parent,
        observed_wrapper=observed_wrapper,
        start_offset=start_offset,
        canonical_imagegen_attestation=canonical_imagegen_attestation,
    )
    try:
        with file_path.open("rb") as handle:
            if start_offset:
                handle.seek(start_offset)
            while chunk := handle.read(SOURCE_WINDOW_BYTES):
                if len(chunk) > SOURCE_WINDOW_BYTES:
                    raise ValueError("JSONL source handle exceeded the fixed read window")
                yield from projector.feed(chunk)
        projector.finish()
    except Exception:
        projector.abort()
        raise
