"""Bounded command-line entry point for one private Codex session projection."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from backend.services.codex_session_reader import (
    MAX_RESPONSE_BYTES,
    CodexSessionReader,
    InvalidCursor,
    InvalidSessionId,
    SessionNotFound,
    SessionReaderError,
    SourceChanged,
)


_FILTER_ARGUMENTS = {
    "turn_id": "turn_id",
    "role": "role",
    "kind": "kind",
    "phase": "phase",
    "tool_name": "tool_name",
}


class _CliArgumentError(ValueError):
    """A command-line argument is absent, malformed, or not allowlisted."""


class _JsonArgumentParser(argparse.ArgumentParser):
    """Raise instead of writing non-JSON argument errors to stdout."""

    def error(self, message: str) -> None:
        raise _CliArgumentError(message)


def _compact_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _write_envelope(value: object) -> None:
    encoded = _compact_bytes(value)
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = b'{"error":"response exceeded the bounded output budget","code":"response_budget"}'
    sys.stdout.buffer.write(encoded + b"\n")


def _error(message: str, *, code: str) -> int:
    _write_envelope({"error": message, "code": code})
    print(message, file=sys.stderr)
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="codex-session-reader")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--level", required=True, choices=("L1", "L2", "L3", "L4"))
    parser.add_argument("--cursor")
    parser.add_argument("--sequence")
    parser.add_argument("--turn-id", dest="turn_id")
    parser.add_argument("--role")
    parser.add_argument("--kind")
    parser.add_argument("--phase")
    parser.add_argument("--tool-name", dest="tool_name")
    return parser


def _configured_root(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SessionReaderError(f"{name} must be configured by the operator")
    return Path(value).expanduser()


def _reader_from_environment() -> CodexSessionReader:
    return CodexSessionReader(
        sessions_root=_configured_root("PPTGEN_CODEX_SESSIONS_ROOT"),
        cache_root=_configured_root("PPTGEN_CODEX_SESSION_READER_CACHE"),
    )


def _positive_sequence(value: str | None) -> int:
    if (
        value is None
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 19
        or (len(value) == 19 and value > "9223372036854775807")
    ):
        raise _CliArgumentError("sequence must be a positive base-10 integer")
    return int(value)


def _filters(arguments: argparse.Namespace) -> dict[str, str | int]:
    filters = {
        reader_name: value
        for argument_name, reader_name in _FILTER_ARGUMENTS.items()
        if (value := getattr(arguments, argument_name)) is not None
    }
    if arguments.level == "L3":
        if arguments.cursor is not None:
            raise _CliArgumentError("L3 does not accept --cursor")
        if arguments.sequence is None:
            raise _CliArgumentError("L3 requires --sequence")
        filters["sequence"] = _positive_sequence(arguments.sequence)
    elif filters or arguments.sequence is not None:
        raise _CliArgumentError("projection filters are only valid for L3")
    return filters


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        envelope = _reader_from_environment().read(
            arguments.session_id,
            level=arguments.level,
            cursor=arguments.cursor,
            filters=_filters(arguments),
        )
    except _CliArgumentError as error:
        return _error(str(error), code="invalid_arguments")
    except InvalidSessionId as error:
        return _error(str(error), code="invalid_session_id")
    except SessionNotFound as error:
        return _error(str(error), code="session_not_found")
    except InvalidCursor as error:
        return _error(str(error), code="invalid_cursor")
    except SourceChanged as error:
        return _error(str(error), code="source_changed")
    except SessionReaderError as error:
        return _error(str(error), code="reader_error")
    _write_envelope(envelope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
