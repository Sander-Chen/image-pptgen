"""Bounded, private reader for effective Codex session projections.

This module deliberately has no dependency on the historical Codex Audit reader:
that reader owns a different, eager detail surface.  Every read made here is
windowed and the sidecar stores only the effective projection plus raw ranges.
"""

from __future__ import annotations

import base64
import codecs
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO

from backend.services.codex_jsonl_stream import SOURCE_WINDOW_BYTES

MAX_RESPONSE_BYTES = 30_720
PROJECTION_VERSION = "effective_v1"
CALL_TYPES = {"custom_tool_call", "function_call"}
OUTPUT_TYPES = {"custom_tool_call_output", "function_call_output"}

_PAGE_SIZE = 24
_FRAGMENT_BYTES = 4_096
_RAW_DISPLAY_BYTES = 8_192
_VALID_LEVELS = {"L1", "L2", "L3", "L4"}
_FILTER_COLUMNS = {
    "kind": "kind",
    "role": "role",
    "phase": "phase",
    "tool_name": "tool_name",
    "turn_id": "turn_id",
}


class SessionReaderError(RuntimeError):
    """Base error for a request that cannot safely be served."""


class InvalidSessionId(SessionReaderError):
    """The public selector is not an exact canonical UUID."""


class SessionNotFound(SessionReaderError):
    """No safe source file matched the selected UUID."""


class InvalidCursor(SessionReaderError):
    """A cursor is malformed, unsigned, or bound to another request."""


class SourceChanged(SessionReaderError):
    """The source no longer matches the sidecar/cursor identity."""


@dataclass(frozen=True)
class _SourceState:
    device: int
    inode: int
    size: int
    mtime_ns: int
    digest: str
    metadata: dict[str, Any]

    @property
    def cursor_identity(self) -> str:
        return f"{self.size}:{self.mtime_ns}:{self.digest}"


@dataclass
class _FragmentState:
    sequence: int
    field: str
    index: int = 0
    chars: int = 0
    encoded_bytes: int = 0
    text: list[str] = field(default_factory=list)


def _compact_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _encoded_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if not value or any(character.isspace() for character in value):
        raise InvalidCursor("cursor encoding is invalid")
    try:
        return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
    except (ValueError, UnicodeEncodeError) as error:
        raise InvalidCursor("cursor encoding is invalid") from error


def _same_identity(state: _SourceState, source_stat: os.stat_result) -> bool:
    return (
        state.device == source_stat.st_dev
        and state.inode == source_stat.st_ino
        and state.size == source_stat.st_size
        and state.mtime_ns == source_stat.st_mtime_ns
    )


def _small_text(value: object) -> str:
    """Reuse the legacy readable-text semantics only for a bounded record."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_small_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "input_text", "output_text"):
            if value.get(key) is not None:
                return _small_text(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def iter_record_chunks(handle: BinaryIO, digest: Any) -> Iterator[tuple[int, bytes]]:
    """Read source bytes in fixed windows while incrementally hashing them."""

    offset = 0
    while True:
        chunk = handle.read(SOURCE_WINDOW_BYTES)
        if not chunk:
            return
        if len(chunk) > SOURCE_WINDOW_BYTES:
            raise SessionReaderError("source handle exceeded the fixed read window")
        digest.update(chunk)
        yield offset, chunk
        offset += len(chunk)


class _JsonStringSink:
    """Incrementally unescape one JSON string without joining its raw bytes."""

    def __init__(self, on_text: Callable[[str], None], on_finish: Callable[[], None]) -> None:
        self._on_text = on_text
        self._on_finish = on_finish
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._raw = bytearray()
        self._escaping = False
        self._unicode_digits: list[str] | None = None
        self._pending_high: int | None = None

    def _flush_raw(self, *, final: bool = False) -> None:
        if self._raw or final:
            text = self._decoder.decode(bytes(self._raw), final=final)
            self._raw.clear()
            if text:
                self._on_text(text)

    def _emit_codepoint(self, codepoint: int) -> None:
        self._flush_raw()
        if 0xD800 <= codepoint <= 0xDBFF:
            if self._pending_high is not None:
                self._on_text("\ufffd")
            self._pending_high = codepoint
            return
        if 0xDC00 <= codepoint <= 0xDFFF and self._pending_high is not None:
            high = self._pending_high
            self._pending_high = None
            self._on_text(chr(0x10000 + ((high - 0xD800) << 10) + codepoint - 0xDC00))
            return
        if self._pending_high is not None:
            self._on_text("\ufffd")
            self._pending_high = None
        self._on_text(chr(codepoint))

    def feed(self, byte: int) -> None:
        if self._unicode_digits is not None:
            character = chr(byte)
            if character not in "0123456789abcdefABCDEF":
                raise ValueError("invalid JSON unicode escape")
            self._unicode_digits.append(character)
            if len(self._unicode_digits) == 4:
                self._emit_codepoint(int("".join(self._unicode_digits), 16))
                self._unicode_digits = None
            return
        if self._escaping:
            self._escaping = False
            if byte == ord("u"):
                self._unicode_digits = []
                return
            escapes = {
                ord('"'): '"',
                ord("\\"): "\\",
                ord("/"): "/",
                ord("b"): "\b",
                ord("f"): "\f",
                ord("n"): "\n",
                ord("r"): "\r",
                ord("t"): "\t",
            }
            if byte not in escapes:
                raise ValueError("invalid JSON escape")
            self._flush_raw()
            self._on_text(escapes[byte])
            return
        if byte == ord("\\"):
            self._escaping = True
            return
        self._raw.append(byte)
        if len(self._raw) >= 1_024:
            self._flush_raw()

    def finish(self) -> None:
        if self._escaping or self._unicode_digits is not None:
            raise ValueError("unterminated JSON escape")
        self._flush_raw(final=True)
        if self._pending_high is not None:
            self._on_text("\ufffd")
            self._pending_high = None
        self._on_finish()


@dataclass
class _Container:
    kind: str
    path: tuple[str, ...]
    expect: str
    key: str | None = None


class _StreamingRecordParser:
    """A small JSON lexer that sends only known fields to the projection sink."""

    _CONTROL_PATHS = {
        ("type",),
        ("timestamp",),
        ("payload", "type"),
        ("payload", "role"),
        ("payload", "phase"),
        ("payload", "name"),
        ("payload", "call_id"),
        ("payload", "id"),
        ("payload", "turn_id"),
        ("payload", "internal_chat_message_metadata_passthrough", "turn_id"),
        ("payload", "cwd"),
        ("payload", "originator"),
        ("payload", "cli_version"),
        ("payload", "model_provider"),
    }
    _TEXT_PATHS = {
        ("payload", "message"),
        ("payload", "input"),
        ("payload", "arguments"),
        ("payload", "output"),
        ("payload", "content", "[]", "text"),
        ("payload", "output", "[]", "text"),
    }

    def __init__(self, builder: _ProjectionBuilder, record_start: int) -> None:
        self._builder = builder
        self._builder.begin_large(record_start)
        self._stack: list[_Container] = []
        self._string: _JsonStringSink | None = None
        self._string_is_key = False
        self._key_parts: list[str] = []
        self._value_parts: list[str] = []
        self._value_path: tuple[str, ...] = ()
        self._literal: bytearray | None = None
        self._complete = False
        self._error = False

    def _current(self) -> _Container | None:
        return self._stack[-1] if self._stack else None

    def _value_location(self) -> tuple[str, ...]:
        current = self._current()
        if current is None:
            if self._complete:
                raise ValueError("multiple root values")
            return ()
        if current.kind == "object" and current.expect == "value" and current.key is not None:
            return current.path + (current.key,)
        if current.kind == "array" and current.expect == "value_or_end":
            return current.path + ("[]",)
        raise ValueError("value is not valid in this position")

    def _mark_value_complete(self) -> None:
        current = self._current()
        if current is None:
            self._complete = True
            return
        if current.kind == "object" and current.expect == "value":
            current.expect = "comma_or_end"
            current.key = None
            return
        if current.kind == "array" and current.expect == "value_or_end":
            current.expect = "comma_or_end"
            return
        raise ValueError("value completion is not valid")

    def _begin_string(self) -> None:
        current = self._current()
        self._string_is_key = bool(current and current.kind == "object" and current.expect == "key_or_end")
        self._key_parts = []
        self._value_parts = []
        self._value_path = () if self._string_is_key else self._value_location()

        def on_text(text: str) -> None:
            if self._string_is_key:
                if sum(len(part) for part in self._key_parts) + len(text) > 256:
                    raise ValueError("JSON key exceeds bounded parser limit")
                self._key_parts.append(text)
                return
            if self._value_path in self._TEXT_PATHS:
                self._builder.stream_text(self._value_path, text)
            if self._value_path in self._CONTROL_PATHS:
                if sum(len(part) for part in self._value_parts) + len(text) > 1_024:
                    raise ValueError("control value exceeds bounded parser limit")
                self._value_parts.append(text)

        def on_finish() -> None:
            if self._string_is_key:
                current = self._current()
                if current is None or current.kind != "object" or current.expect != "key_or_end":
                    raise ValueError("JSON key is not valid here")
                current.key = "".join(self._key_parts)
                current.expect = "colon"
            else:
                if self._value_path in self._CONTROL_PATHS:
                    self._builder.complete_value(self._value_path, "".join(self._value_parts))
                self._mark_value_complete()

        self._string = _JsonStringSink(on_text, on_finish)

    def _start_container(self, byte: int) -> None:
        path = self._value_location()
        if byte == ord("{"):
            self._stack.append(_Container("object", path, "key_or_end"))
        else:
            self._stack.append(_Container("array", path, "value_or_end"))

    def _close_container(self, byte: int) -> None:
        if not self._stack:
            raise ValueError("unexpected JSON closing delimiter")
        current = self._stack[-1]
        if byte == ord("}"):
            if current.kind != "object" or current.expect not in {"key_or_end", "comma_or_end"}:
                raise ValueError("invalid JSON object close")
        elif current.kind != "array" or current.expect not in {"value_or_end", "comma_or_end"}:
            raise ValueError("invalid JSON array close")
        self._stack.pop()
        self._mark_value_complete()

    def _finish_literal(self) -> None:
        if self._literal is None:
            return
        literal = self._literal.decode("ascii")
        if literal not in {"true", "false", "null"}:
            try:
                float(literal)
            except ValueError as error:
                raise ValueError("invalid JSON literal") from error
        self._literal = None
        self._mark_value_complete()

    def _feed_normal(self, byte: int) -> None:
        if self._literal is not None:
            if byte in b" \t\r\n,}]":
                self._finish_literal()
                self._feed_normal(byte)
                return
            self._literal.append(byte)
            if len(self._literal) > 128:
                raise ValueError("JSON literal exceeds bounded parser limit")
            return
        if byte in b" \t\r\n":
            return
        if byte == ord('"'):
            self._begin_string()
            return
        if byte in (ord("{"), ord("[")):
            self._start_container(byte)
            return
        if byte in (ord("}"), ord("]")):
            self._close_container(byte)
            return
        current = self._current()
        if byte == ord(":"):
            if current is None or current.kind != "object" or current.expect != "colon":
                raise ValueError("unexpected JSON colon")
            current.expect = "value"
            return
        if byte == ord(","):
            if current is None or current.expect != "comma_or_end":
                raise ValueError("unexpected JSON comma")
            current.expect = "key_or_end" if current.kind == "object" else "value_or_end"
            return
        self._value_location()
        self._literal = bytearray((byte,))

    def feed(self, data: bytes) -> None:
        if self._error:
            return
        try:
            for byte in data:
                if self._string is not None:
                    if byte == ord('"') and not self._string._escaping and self._string._unicode_digits is None:
                        string = self._string
                        self._string = None
                        string.finish()
                    else:
                        self._string.feed(byte)
                else:
                    self._feed_normal(byte)
        except (UnicodeDecodeError, ValueError):
            self._error = True

    def finish(self, record_end: int) -> None:
        if self._literal is not None:
            try:
                self._finish_literal()
            except ValueError:
                self._error = True
        valid = not self._error and self._string is None and not self._stack and self._complete
        self._builder.finish_large(record_end, valid=valid)


class _ProjectionBuilder:
    """Persist a current record's effective fields in bounded sidecar fragments."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.metadata: dict[str, Any] = {}
        self._states: dict[tuple[int, str], _FragmentState] = {}
        self._record_start = 0
        self._record_type: str | None = None
        self._timestamp: str | None = None
        self._payload_type: str | None = None
        self._role: str | None = None
        self._phase: str | None = None
        self._tool_name: str | None = None
        self._call_id: str | None = None
        self._active_turn_id: str | None = None
        self._record_turn_id: str | None = None
        self._event_sequence: int | None = None
        self._savepoint = False

    def _reset(self, record_start: int) -> None:
        self._states.clear()
        self._record_start = record_start
        self._record_type = None
        self._timestamp = None
        self._payload_type = None
        self._role = None
        self._phase = None
        self._tool_name = None
        self._call_id = None
        self._record_turn_id = None
        self._event_sequence = None

    def begin_large(self, record_start: int) -> None:
        self._reset(record_start)
        self.connection.execute("SAVEPOINT streamed_record")
        self._savepoint = True

    def _insert_event(self, *, kind: str, role: str | None = None, tool_name: str | None = None) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO projected_events(
                kind, role, phase, tool_name, call_id, turn_id, timestamp, source_start, source_end, truncated, output_chars
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (
                kind,
                role,
                self._phase,
                tool_name,
                self._call_id,
                self._record_turn_id or self._active_turn_id,
                self._timestamp,
                self._record_start,
                self._record_start,
            ),
        )
        return int(cursor.lastrowid)

    def _ensure_output_event(self) -> int:
        if self._event_sequence is not None:
            return self._event_sequence
        row = None
        if self._call_id:
            row = self.connection.execute(
                "SELECT sequence FROM projected_events WHERE kind = 'tool' AND call_id = ? ORDER BY sequence DESC LIMIT 1",
                (self._call_id,),
            ).fetchone()
        self._event_sequence = int(row[0]) if row is not None else self._insert_event(kind="tool", tool_name="tool output")
        if self._record_turn_id is not None:
            self.connection.execute(
                "UPDATE projected_events SET turn_id = ? WHERE sequence = ?",
                (self._record_turn_id, self._event_sequence),
            )
        return self._event_sequence

    def _ensure_event(self) -> int | None:
        if self._event_sequence is not None:
            return self._event_sequence
        if self._record_type == "event_msg" and self._payload_type in {"user_message", "agent_message"}:
            self._event_sequence = self._insert_event(
                kind="message",
                role="user" if self._payload_type == "user_message" else "assistant",
            )
        elif self._record_type == "response_item" and self._payload_type in CALL_TYPES:
            self._event_sequence = self._insert_event(kind="tool", tool_name=self._tool_name or "unknown")
        return self._event_sequence

    def _sync_event(self) -> None:
        if self._event_sequence is None:
            return
        if self._record_type == "response_item" and self._payload_type in OUTPUT_TYPES:
            if self._record_turn_id is not None:
                self.connection.execute(
                    "UPDATE projected_events SET turn_id = ? WHERE sequence = ?",
                    (self._record_turn_id, self._event_sequence),
                )
            return
        self.connection.execute(
            """
            UPDATE projected_events
            SET phase = ?, tool_name = ?, call_id = ?, turn_id = ?, timestamp = ?
            WHERE sequence = ?
            """,
            (
                self._phase,
                self._tool_name if self._payload_type in CALL_TYPES else None,
                self._call_id,
                self._record_turn_id or self._active_turn_id,
                self._timestamp,
                self._event_sequence,
            ),
        )

    def complete_value(self, path: tuple[str, ...], value: str) -> None:
        if path == ("type",):
            self._record_type = value
        elif path == ("timestamp",):
            self._timestamp = value
        elif path == ("payload", "type"):
            self._payload_type = value
        elif path == ("payload", "role"):
            self._role = value
        elif path == ("payload", "phase"):
            self._phase = value
        elif path == ("payload", "name"):
            self._tool_name = value
        elif path in {("payload", "call_id"), ("payload", "id")} and not self._call_id:
            self._call_id = value
        elif path in {
            ("payload", "turn_id"),
            ("payload", "internal_chat_message_metadata_passthrough", "turn_id"),
        }:
            self._record_turn_id = value
        elif self._record_type == "session_meta":
            metadata_key = {
                ("payload", "id"): "session_id",
                ("payload", "cwd"): "cwd",
                ("payload", "originator"): "originator",
                ("payload", "cli_version"): "cli_version",
                ("payload", "model_provider"): "model_provider",
            }.get(path)
            if metadata_key:
                self.metadata[metadata_key] = value
        if self._record_type == "session_meta" and path == ("timestamp",):
            self.metadata.setdefault("started_at", value)
        if (
            self._record_type == "event_msg"
            and self._payload_type == "task_started"
            and self._record_turn_id is not None
        ):
            self._active_turn_id = self._record_turn_id
        self._ensure_event()
        self._sync_event()

    def _flush(self, state: _FragmentState) -> None:
        if not state.text:
            return
        self.connection.execute(
            "INSERT INTO projected_fragments(event_sequence, field, fragment_index, text) VALUES (?, ?, ?, ?)",
            (state.sequence, state.field, state.index, "".join(state.text)),
        )
        state.index += 1
        state.text.clear()
        state.encoded_bytes = 0

    def _append(self, sequence: int, field_name: str, text: str) -> None:
        state = self._states.setdefault((sequence, field_name), _FragmentState(sequence, field_name))
        for character in text:
            encoded = len(character.encode("utf-8"))
            if state.text and state.encoded_bytes + encoded > _FRAGMENT_BYTES:
                self._flush(state)
            state.text.append(character)
            state.encoded_bytes += encoded
            state.chars += 1

    def stream_text(self, path: tuple[str, ...], text: str) -> None:
        if not text:
            return
        field_name: str | None = None
        sequence = self._ensure_event()
        if self._record_type == "event_msg" and path == ("payload", "message"):
            field_name = "text"
        elif self._record_type == "response_item" and self._payload_type in CALL_TYPES and path in {
            ("payload", "input"),
            ("payload", "arguments"),
        }:
            field_name = "input"
        elif self._record_type == "response_item" and self._payload_type in OUTPUT_TYPES and path in {
            ("payload", "output"),
            ("payload", "output", "[]", "text"),
        }:
            sequence = self._ensure_output_event()
            field_name = "output"
        if sequence is not None and field_name is not None:
            self._append(sequence, field_name, text)

    def _finish_current(self, record_end: int) -> None:
        for state in self._states.values():
            self._flush(state)
            if state.index > 1:
                self.connection.execute("UPDATE projected_events SET truncated = 1 WHERE sequence = ?", (state.sequence,))
            if state.field == "output":
                self.connection.execute(
                    "UPDATE projected_events SET output_chars = ? WHERE sequence = ?",
                    (state.chars, state.sequence),
                )
        if self._event_sequence is not None:
            self.connection.execute(
                "UPDATE projected_events SET source_end = ? WHERE sequence = ?",
                (record_end, self._event_sequence),
            )

    def finish_large(self, record_end: int, *, valid: bool) -> None:
        if valid:
            self._finish_current(record_end)
            self.connection.execute("RELEASE SAVEPOINT streamed_record")
        else:
            self.connection.execute("ROLLBACK TO SAVEPOINT streamed_record")
            self.connection.execute("RELEASE SAVEPOINT streamed_record")
        self._savepoint = False

    def add_small_record(self, record: dict[str, Any], record_start: int, record_end: int) -> None:
        self._reset(record_start)
        self._record_type = record.get("type") if isinstance(record.get("type"), str) else None
        self._timestamp = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        if self._record_type == "session_meta":
            self.metadata.update(
                {
                    "session_id": payload.get("session_id") or payload.get("id"),
                    "started_at": payload.get("timestamp") or self._timestamp,
                    "cwd": payload.get("cwd"),
                    "originator": payload.get("originator"),
                    "cli_version": payload.get("cli_version"),
                    "model_provider": payload.get("model_provider"),
                }
            )
            self.metadata = {key: value for key, value in self.metadata.items() if value is not None}
            return
        self._payload_type = payload.get("type") if isinstance(payload.get("type"), str) else None
        self._role = payload.get("role") if isinstance(payload.get("role"), str) else None
        self._phase = payload.get("phase") if isinstance(payload.get("phase"), str) else None
        self._tool_name = payload.get("name") if isinstance(payload.get("name"), str) else None
        call_id = payload.get("call_id") or payload.get("id")
        self._call_id = call_id if isinstance(call_id, str) else None
        direct_turn_id = payload.get("turn_id")
        passthrough = payload.get("internal_chat_message_metadata_passthrough")
        passthrough_turn_id = passthrough.get("turn_id") if isinstance(passthrough, dict) else None
        turn_id = direct_turn_id if isinstance(direct_turn_id, str) else passthrough_turn_id
        self._record_turn_id = turn_id if isinstance(turn_id, str) else None
        if (
            self._record_type == "event_msg"
            and self._payload_type == "task_started"
            and self._record_turn_id is not None
        ):
            self._active_turn_id = self._record_turn_id
        if self._record_type == "event_msg" and self._payload_type in {"user_message", "agent_message"}:
            self._ensure_event()
            self.stream_text(("payload", "message"), _small_text(payload.get("message")))
        elif self._record_type == "response_item" and self._payload_type in CALL_TYPES:
            self._ensure_event()
            self._sync_event()
            self.stream_text(("payload", "input"), _small_text(payload.get("input", payload.get("arguments"))))
        elif self._record_type == "response_item" and self._payload_type in OUTPUT_TYPES:
            self._ensure_output_event()
            self._sync_event()
            self.stream_text(("payload", "output"), _small_text(payload.get("output")))
        self._finish_current(record_end)


class CodexSessionReader:
    """Read one UUID-addressed session through a private bounded sidecar."""

    def __init__(
        self,
        *,
        sessions_root: str | Path,
        cache_root: str | Path,
        source_opener: Callable[[Path], BinaryIO] | None = None,
    ) -> None:
        self.sessions_root = Path(sessions_root)
        self.cache_root = Path(cache_root)
        self.source_opener = source_opener or (lambda path: path.open("rb"))

    def read(
        self,
        session_id: str,
        *,
        level: str,
        cursor: str | None = None,
        filters: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any]:
        session_id = self._canonical_session_id(session_id)
        if level not in _VALID_LEVELS:
            raise InvalidCursor("unknown reader level")
        if level == "L4":
            if filters:
                raise InvalidCursor("raw reads do not accept projection filters")
            normalized_filters = self._raw_scope_filters()
        else:
            normalized_filters = self._filters(level, filters)
        source = self._resolve_source(session_id)
        state = self._state_for_request(session_id, source, cursor is not None)
        # The private key is created even for a summary-only first request so
        # the sidecar/cache privacy invariant is observable independently of
        # whether this particular response happens to paginate.
        self._cursor_key()
        if cursor is not None:
            payload = self._decode_cursor(cursor)
            self._validate_cursor(payload, session_id, level, normalized_filters, state)
            after = payload["after"]
        else:
            after = 0
        if level == "L4":
            if cursor is None:
                raise InvalidCursor("raw access requires a signed range cursor")
            result = self._raw_envelope(session_id, source, state, int(after), normalized_filters)
        else:
            result = self._page_envelope(session_id, state, level, int(after), normalized_filters)
        if not _same_identity(state, source.stat()):
            raise SourceChanged("source changed during read")
        if _encoded_size(result) > MAX_RESPONSE_BYTES:
            raise SessionReaderError("response budget exceeded")
        return result

    def _canonical_session_id(self, session_id: str) -> str:
        if not isinstance(session_id, str):
            raise InvalidSessionId("session id must be an exact UUID")
        try:
            canonical = str(uuid.UUID(session_id))
        except (AttributeError, ValueError) as error:
            raise InvalidSessionId("session id must be an exact UUID") from error
        if session_id != canonical:
            raise InvalidSessionId("session id must use canonical lowercase UUID form")
        return canonical

    def _private_cache_root(self) -> Path:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_root, 0o700)
        return self.cache_root

    def _sidecar_path(self, session_id: str) -> Path:
        return self._private_cache_root() / f"{session_id}.sqlite3"

    def _cursor_key(self) -> bytes:
        key_path = self._private_cache_root() / "cursor.key"
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            os.chmod(key_path, 0o600)
            return key_path.read_bytes()
        try:
            key = secrets.token_bytes(32)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key)
            os.chmod(key_path, 0o600)
            return key
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def _resolve_source(self, session_id: str) -> Path:
        try:
            root = self.sessions_root.resolve(strict=True)
        except OSError as error:
            raise SessionNotFound("configured sessions root is unavailable") from error
        if not root.is_dir():
            raise SessionNotFound("configured sessions root is unavailable")
        suffix = f"{session_id}.jsonl"
        candidates: list[Path] = []
        for candidate in root.rglob(f"*{suffix}"):
            if not candidate.name.endswith(suffix):
                continue
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                candidates.append(resolved)
        if len(candidates) != 1:
            raise SessionNotFound("session was not found under the configured root")
        return candidates[0]

    def _filters(self, level: str, filters: Mapping[str, str | int] | None) -> dict[str, str | bool | int]:
        normalized: dict[str, str | bool | int] = {"effective_only": True}
        if level == "L3":
            if filters is None or "sequence" not in filters:
                raise InvalidCursor("exact detail reads require a positive sequence")
        elif filters is None:
            return normalized
        for key, value in (filters or {}).items():
            if key == "sequence":
                if level != "L3" or isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise InvalidCursor("sequence must be a positive integer for exact detail reads")
                normalized[key] = value
                continue
            if key not in _FILTER_COLUMNS or not isinstance(value, str) or not value:
                raise InvalidCursor("unsupported effective projection filter")
            normalized[key] = value
        return normalized

    def _raw_scope_filters(self) -> dict[str, bool]:
        """Raw cursors bind only their signed event range, never L3 selectors."""

        return {"effective_only": True}

    def _load_state(self, sidecar: Path) -> _SourceState | None:
        if not sidecar.exists():
            return None
        try:
            with sqlite3.connect(sidecar) as connection:
                columns = {
                    str(column[1])
                    for column in connection.execute("PRAGMA table_info(projected_events)")
                }
                if "turn_id" not in columns:
                    return None
                row = connection.execute(
                    "SELECT device, inode, size, mtime_ns, sha256, metadata_json FROM source_state WHERE singleton = 1"
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            metadata = json.loads(row[5])
        except (TypeError, ValueError):
            metadata = {}
        return _SourceState(int(row[0]), int(row[1]), int(row[2]), int(row[3]), str(row[4]), metadata)

    def _state_for_request(self, session_id: str, source: Path, cursor_present: bool) -> _SourceState:
        sidecar = self._sidecar_path(session_id)
        source_stat = source.stat()
        state = self._load_state(sidecar)
        if cursor_present:
            if state is None:
                raise InvalidCursor("cursor sidecar is unavailable")
            if not _same_identity(state, source_stat):
                raise SourceChanged("source no longer matches cursor")
            return state
        if state is not None and _same_identity(state, source_stat):
            return state
        return self._build_sidecar(session_id, source, source_stat)

    def _connect_sidecar(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE source_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                source_path TEXT NOT NULL,
                device INTEGER NOT NULL,
                inode INTEGER NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE projected_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                role TEXT,
                phase TEXT,
                tool_name TEXT,
                call_id TEXT,
                turn_id TEXT,
                timestamp TEXT,
                source_start INTEGER NOT NULL,
                source_end INTEGER NOT NULL,
                truncated INTEGER NOT NULL DEFAULT 0,
                output_chars INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE projected_fragments (
                event_sequence INTEGER NOT NULL,
                field TEXT NOT NULL,
                fragment_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY(event_sequence, field, fragment_index),
                FOREIGN KEY(event_sequence) REFERENCES projected_events(sequence)
            );
            CREATE INDEX projected_events_kind_sequence
                ON projected_events(kind, sequence);
            """
        )
        return connection

    def _build_sidecar(self, session_id: str, source: Path, expected_stat: os.stat_result) -> _SourceState:
        target = self._sidecar_path(session_id)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            connection = self._connect_sidecar(temporary)
            os.chmod(temporary, 0o600)
            try:
                builder = _ProjectionBuilder(connection)
                digest = self._scan_source(source, builder)
                final_stat = source.stat()
                if (
                    final_stat.st_dev != expected_stat.st_dev
                    or final_stat.st_ino != expected_stat.st_ino
                    or final_stat.st_size != expected_stat.st_size
                    or final_stat.st_mtime_ns != expected_stat.st_mtime_ns
                ):
                    raise SourceChanged("source changed during sidecar build")
                state = _SourceState(
                    expected_stat.st_dev,
                    expected_stat.st_ino,
                    expected_stat.st_size,
                    expected_stat.st_mtime_ns,
                    digest,
                    builder.metadata,
                )
                connection.execute(
                    """
                    INSERT INTO source_state(singleton, source_path, device, inode, size, mtime_ns, sha256, metadata_json)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(source),
                        state.device,
                        state.inode,
                        state.size,
                        state.mtime_ns,
                        state.digest,
                        json.dumps(state.metadata, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            return state
        finally:
            if temporary.exists():
                temporary.unlink()

    def _scan_source(self, source: Path, builder: _ProjectionBuilder) -> str:
        digest = sha256()
        small = bytearray()
        parser: _StreamingRecordParser | None = None
        record_start = 0
        total_size = 0

        def consume(segment: bytes, segment_start: int, ends_record: bool) -> None:
            nonlocal parser, small, record_start
            if parser is None and len(small) + len(segment) <= SOURCE_WINDOW_BYTES:
                small.extend(segment)
            else:
                if parser is None:
                    parser = _StreamingRecordParser(builder, record_start)
                    parser.feed(bytes(small))
                    small.clear()
                parser.feed(segment)
            if not ends_record:
                return
            record_end = segment_start + len(segment)
            if parser is not None:
                parser.finish(record_end)
                parser = None
            elif small.strip():
                try:
                    record = json.loads(small)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                else:
                    if isinstance(record, dict):
                        builder.add_small_record(record, record_start, record_end)
            small.clear()
            record_start = record_end + 1

        with self.source_opener(source) as handle:
            for chunk_start, chunk in iter_record_chunks(handle, digest):
                total_size = chunk_start + len(chunk)
                cursor = 0
                while cursor < len(chunk):
                    newline = chunk.find(b"\n", cursor)
                    if newline < 0:
                        consume(chunk[cursor:], chunk_start + cursor, False)
                        break
                    consume(chunk[cursor:newline], chunk_start + cursor, True)
                    cursor = newline + 1
        if parser is not None:
            parser.finish(total_size)
        elif small.strip():
            try:
                record = json.loads(small)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            else:
                if isinstance(record, dict):
                    builder.add_small_record(record, record_start, total_size)
        return digest.hexdigest()

    def _connection(self, session_id: str) -> sqlite3.Connection:
        connection = sqlite3.connect(self._sidecar_path(session_id))
        connection.row_factory = sqlite3.Row
        return connection

    def _cursor_filter_hash(self, filters: Mapping[str, str | bool | int]) -> str:
        return sha256(_compact_json(dict(filters))).hexdigest()

    def _make_cursor(
        self,
        session_id: str,
        level: str,
        after: int,
        filters: Mapping[str, str | bool | int],
        state: _SourceState,
    ) -> str:
        payload = {
            "v": 1,
            "sid": session_id,
            "level": level,
            "after": after,
            "filter": self._cursor_filter_hash(filters),
            "source": state.cursor_identity,
        }
        encoded = _compact_json(payload)
        signature = hmac.new(self._cursor_key(), encoded, "sha256").digest()
        return f"{_b64encode(encoded)}.{_b64encode(signature)}"

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        if not isinstance(cursor, str) or cursor.count(".") != 1:
            raise InvalidCursor("cursor is malformed")
        encoded_part, signature_part = cursor.split(".", 1)
        encoded = _b64decode(encoded_part)
        signature = _b64decode(signature_part)
        if encoded_part != _b64encode(encoded) or signature_part != _b64encode(signature):
            raise InvalidCursor("cursor encoding is not canonical")
        expected = hmac.new(self._cursor_key(), encoded, "sha256").digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCursor("cursor signature is invalid")
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise InvalidCursor("cursor JSON is invalid") from error
        if not isinstance(payload, dict):
            raise InvalidCursor("cursor JSON is invalid")
        return payload

    def _validate_cursor(
        self,
        payload: Mapping[str, Any],
        session_id: str,
        level: str,
        filters: Mapping[str, str | bool | int],
        state: _SourceState,
    ) -> None:
        if (
            payload.get("v") != 1
            or payload.get("sid") != session_id
            or payload.get("level") != level
            or not isinstance(payload.get("after"), int)
            or payload["after"] < 0
            or payload.get("filter") != self._cursor_filter_hash(filters)
        ):
            raise InvalidCursor("cursor does not match this request")
        if payload.get("source") != state.cursor_identity:
            raise SourceChanged("cursor source identity no longer matches")

    def _event_rows(
        self,
        connection: sqlite3.Connection,
        after: int,
        filters: Mapping[str, str | bool | int],
    ) -> list[sqlite3.Row]:
        clauses = ["sequence > ?"]
        values: list[object] = [after]
        for key, value in filters.items():
            if key == "effective_only":
                continue
            if key == "sequence":
                clauses.append("sequence = ?")
                values.append(value)
                continue
            clauses.append(f"{_FILTER_COLUMNS[key]} = ?")
            values.append(value)
        values.append(_PAGE_SIZE + 1)
        return connection.execute(
            """
            SELECT sequence, kind, role, phase, tool_name, call_id, turn_id, timestamp, source_start, source_end, truncated, output_chars
            FROM projected_events WHERE """
            + " AND ".join(clauses)
            + " ORDER BY sequence LIMIT ?",
            values,
        ).fetchall()

    def _source_payload(self, state: _SourceState) -> dict[str, object]:
        return {
            "bytes": state.size,
            "mtime_ns": state.mtime_ns,
            "sha256": state.digest,
            "projection_version": PROJECTION_VERSION,
        }

    def _envelope(
        self,
        session_id: str,
        level: str,
        state: _SourceState,
        items: list[dict[str, Any]],
        next_cursor: str | None,
        filters: Mapping[str, str | bool | int],
    ) -> dict[str, Any]:
        return {
            "schema_version": "codex_session_reader_v1",
            "session_id": session_id,
            "level": level,
            "source": self._source_payload(state),
            "items": items,
            "next_cursor": next_cursor,
            "truncated": any(bool(item.get("truncated")) for item in items),
            "filters": dict(filters),
        }

    def _first_fragments(self, connection: sqlite3.Connection, sequence: int) -> dict[str, str]:
        return {
            str(fragment["field"]): str(fragment["text"])
            for fragment in connection.execute(
                "SELECT field, text FROM projected_fragments WHERE event_sequence = ? AND fragment_index = 0 ORDER BY field",
                (sequence,),
            ).fetchall()
        }

    def _preview(self, row: sqlite3.Row, fragments: Mapping[str, str]) -> dict[str, str]:
        fields = ("text",) if row["kind"] == "message" else ("output", "input")
        for field_name in fields:
            text = fragments.get(field_name, "")
            if text:
                return {"preview": text, "preview_source": field_name}
        return {
            "preview": "",
            "preview_source": "none",
            "preview_reason": "no_persisted_fragment",
        }

    def _index_item(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        item = {
            "sequence": row["sequence"],
            "kind": row["kind"],
            "role": row["role"],
            "phase": row["phase"],
            "tool_name": row["tool_name"],
            "turn_id": row["turn_id"],
            "timestamp": row["timestamp"],
            "range_start": row["source_start"],
            "range_end": row["source_end"],
            "truncated": bool(row["truncated"]),
            "output_chars": row["output_chars"],
        }
        item.update(self._preview(row, self._first_fragments(connection, int(row["sequence"]))))
        return item

    def _detail_item(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        state: _SourceState,
        session_id: str,
    ) -> dict[str, Any]:
        item = self._index_item(connection, row)
        for field_name, text in self._first_fragments(connection, int(row["sequence"])).items():
            item[field_name] = text
        item["raw_cursor"] = self._make_cursor(
            session_id,
            "L4",
            int(row["sequence"]),
            self._raw_scope_filters(),
            state,
        )
        return item

    def _l1_core_conclusion(self, connection: sqlite3.Connection) -> dict[str, Any] | None:
        """Return only the latest visible message and its first bounded fragment."""

        row = connection.execute(
            """
            SELECT event.sequence, event.role, event.phase, event.timestamp, event.truncated, fragment.text
            FROM projected_events AS event
            LEFT JOIN projected_fragments AS fragment
              ON fragment.event_sequence = event.sequence
             AND fragment.field = 'text'
             AND fragment.fragment_index = 0
            WHERE event.kind = 'message'
            ORDER BY event.sequence DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "kind": "message",
            "sequence": row["sequence"],
            "role": row["role"],
            "phase": row["phase"],
            "timestamp": row["timestamp"],
            "text": row["text"] or "",
            "truncated": bool(row["truncated"]),
        }

    def _page_envelope(
        self,
        session_id: str,
        state: _SourceState,
        level: str,
        after: int,
        filters: Mapping[str, str | bool | int],
    ) -> dict[str, Any]:
        with self._connection(session_id) as connection:
            if level == "L1":
                counts = connection.execute(
                    "SELECT SUM(kind = 'message'), SUM(kind = 'tool'), COUNT(*) FROM projected_events"
                ).fetchone()
                item = {
                    "kind": "summary",
                    "message_count": int(counts[0] or 0),
                    "tool_count": int(counts[1] or 0),
                    "indexed_events": int(counts[2] or 0),
                    "core_conclusion": self._l1_core_conclusion(connection),
                    "metadata": state.metadata,
                }
                return self._envelope(session_id, level, state, [item], None, filters)
            rows = self._event_rows(connection, after, filters)
            if level == "L3" and len(rows) != 1:
                raise InvalidCursor("sequence does not match the effective projection filters")
            items: list[dict[str, Any]] = []
            for row in rows[:_PAGE_SIZE]:
                item = (
                    self._index_item(connection, row)
                    if level == "L2"
                    else self._detail_item(connection, row, state, session_id)
                )
                tentative_cursor = self._make_cursor(session_id, level, int(row["sequence"]), filters, state)
                candidate = self._envelope(session_id, level, state, [*items, item], tentative_cursor, filters)
                if _encoded_size(candidate) > MAX_RESPONSE_BYTES:
                    break
                items.append(item)
            if rows and not items:
                raise SessionReaderError("one projected item cannot fit the response budget")
            has_more = len(rows) > len(items)
            next_cursor = (
                self._make_cursor(session_id, level, int(items[-1]["sequence"]), filters, state)
                if has_more and items
                else None
            )
            return self._envelope(session_id, level, state, items, next_cursor, filters)

    def _raw_envelope(
        self,
        session_id: str,
        source: Path,
        state: _SourceState,
        sequence: int,
        filters: Mapping[str, str | bool],
    ) -> dict[str, Any]:
        with self._connection(session_id) as connection:
            row = connection.execute(
                "SELECT source_start, source_end FROM projected_events WHERE sequence = ?", (sequence,)
            ).fetchone()
        if row is None:
            raise InvalidCursor("raw cursor does not name an event")
        start = int(row["source_start"])
        end = int(row["source_end"])
        if start < 0 or end <= start or end > state.size:
            raise InvalidCursor("raw range is invalid")
        digest = sha256()
        display = bytearray()
        position = 0
        with self.source_opener(source) as handle:
            while position < end:
                chunk = handle.read(SOURCE_WINDOW_BYTES)
                if not chunk:
                    break
                if len(chunk) > SOURCE_WINDOW_BYTES:
                    raise SessionReaderError("source handle exceeded the fixed read window")
                chunk_end = position + len(chunk)
                if chunk_end > start:
                    raw_start = max(start - position, 0)
                    raw_end = min(end - position, len(chunk))
                    raw = chunk[raw_start:raw_end]
                    digest.update(raw)
                    remaining = _RAW_DISPLAY_BYTES - len(display)
                    if remaining > 0:
                        display.extend(raw[:remaining])
                position = chunk_end
        if position < end:
            raise SourceChanged("source ended before the signed raw range")
        item = {
            "kind": "raw",
            "range_start": start,
            "range_end": end,
            "sha256": digest.hexdigest(),
            "text": display.decode("utf-8", errors="ignore"),
            "truncated": end - start > len(display),
        }
        return self._envelope(session_id, "L4", state, [item], None, filters)
