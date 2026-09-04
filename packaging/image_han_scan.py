#!/usr/bin/env python3
"""Fail-closed Han-literal scan for public source snapshots and extracted archives.

The scanner reports every CJK Unified Ideograph in textual files. It does not
skip directories. Exception authority is packaging/image_han_exceptions.json
and may contain at most one proven unavoidable literal named by exact file,
exact character, and reason. Whole-file, directory, and glob exemptions are
forbidden.
"""

from __future__ import annotations

import json
import re
import tarfile
from pathlib import Path
from typing import Iterable, Mapping

HAN_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
DEFAULT_EXCEPTIONS_PATH = Path(__file__).resolve().with_name("image_han_exceptions.json")
SKIP_SUFFIXES = frozenset(
    {
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".pyc",
        ".webp",
        ".woff",
        ".woff2",
        ".zip",
    }
)


class HanScanError(ValueError):
    """Unapproved Han literals were present in a public textual surface."""


def public_relative(relative: str) -> str:
    """Map archive members such as image-pptgen-x/app/README.md to README.md."""
    posix = relative.replace("\\", "/")
    parts = [part for part in posix.split("/") if part]
    if "app" in parts:
        return "/".join(parts[parts.index("app") + 1 :])
    return posix


def load_exceptions(path: Path | None) -> tuple[dict[str, object], ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload == []:
        return ()
    if not isinstance(payload, list):
        raise HanScanError("Han exception file must be a JSON array of exact literals")
    records: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise HanScanError("Han exception entries must be objects")
        if "residual_files" in item or set(item) - {"file", "character", "reason"}:
            raise HanScanError("Han exception entries may contain only file, character, and reason")
        file_name = item.get("file")
        character = item.get("character")
        reason = item.get("reason")
        if not isinstance(file_name, str) or not file_name.strip():
            raise HanScanError("Han exception is missing file")
        relative = public_relative(file_name.strip())
        if relative.endswith("/") or "*" in relative or relative.startswith("/"):
            raise HanScanError("Han exception file must be an exact path without globs")
        if not isinstance(character, str) or len(character) != 1:
            raise HanScanError("Han exception character must be exactly one code point")
        if not isinstance(reason, str) or not reason.strip():
            raise HanScanError("Han exception is missing reason")
        records.append(
            {
                "file": relative,
                "character": character,
                "reason": reason.strip(),
            }
        )
    if len(records) > 1:
        raise HanScanError("Han exception allowlist may contain at most one proven literal")
    return tuple(records)


def _payload_is_textual(payload: bytes, suffix: str) -> bool:
    if suffix in SKIP_SUFFIXES:
        return False
    if b"\x00" in payload[:1024]:
        return False
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _is_textual(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in SKIP_SUFFIXES:
        return False
    try:
        payload = path.read_bytes()
    except OSError:
        return False
    return _payload_is_textual(payload, suffix)


def _scan_text(relative: str, text: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in HAN_RE.finditer(line):
            findings.append(
                {
                    "file": relative.replace("\\", "/"),
                    "line": line_number,
                    "column": match.start() + 1,
                    "character": match.group(0),
                    "excerpt": line.strip()[:180],
                }
            )
    return findings


def apply_exceptions(
    findings: Iterable[Mapping[str, object]],
    exceptions: Iterable[Mapping[str, object]] = (),
) -> list[dict[str, object]]:
    unapproved: list[dict[str, object]] = []
    for item in findings:
        relative = public_relative(str(item.get("file") or ""))
        if any(
            relative == str(exception.get("file") or "")
            and item.get("character") == exception.get("character")
            for exception in exceptions
        ):
            continue
        unapproved.append(dict(item))
    return unapproved


def scan_text_tree(root: Path, *, exception_path: Path | None = None) -> list[dict[str, object]]:
    """Return unapproved Han findings under a public source or extracted tree."""
    root = root.expanduser().resolve()
    exceptions = load_exceptions(exception_path)
    findings: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not _is_textual(path):
            continue
        text = path.read_bytes().decode("utf-8")
        relative = path.relative_to(root).as_posix()
        findings.extend(_scan_text(relative, text))
    return apply_exceptions(findings, exceptions)


def scan_archive(archive: Path, *, exception_path: Path | None = None) -> list[dict[str, object]]:
    """Return unapproved Han findings in textual members of an extracted archive."""
    archive = archive.expanduser().resolve()
    exceptions = load_exceptions(exception_path)
    findings: list[dict[str, object]] = []
    with tarfile.open(archive, "r:*") as handle:
        for member in handle.getmembers():
            if not member.isfile():
                continue
            name = member.name.replace("\\", "/")
            suffix = Path(name).suffix.lower()
            extracted = handle.extractfile(member)
            if extracted is None:
                continue
            payload = extracted.read()
            if not _payload_is_textual(payload, suffix):
                continue
            findings.extend(_scan_text(name, payload.decode("utf-8")))
    return apply_exceptions(findings, exceptions)


def format_findings(findings: Iterable[Mapping[str, object]]) -> str:
    return "; ".join(
        f"{item['file']}:{item['line']}:{item['column']} {item['character']}"
        for item in findings
    )


def assert_no_unapproved_han(
    findings: Iterable[Mapping[str, object]],
    *,
    label: str,
) -> None:
    rows = list(findings)
    if rows:
        raise HanScanError(f"unapproved Han literal in {label}: " + format_findings(rows))
