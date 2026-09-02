"""Deterministic cleanup for Codex HTML agent output."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any


@dataclass(frozen=True)
class HtmlCleanupResult:
    html: str
    method: str
    input_sha256: str
    output_sha256: str
    warnings: list[str]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": "cleaned",
            "method": self.method,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "warnings": self.warnings,
        }


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _fenced_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r"```([^\n`]*)\n(.*?)```", text, flags=re.S):
        language = (match.group(1) or "").strip().split(maxsplit=1)[0].lower()
        blocks.append((language, match.group(2).strip()))
    return blocks


def _extract_fenced(text: str, lang: str) -> str | None:
    matches = [
        block
        for language, block in _fenced_blocks(text)
        if language in {"", lang.lower()}
    ]
    if not matches:
        return None
    return max(matches, key=len)


def _fenced_json_html_field(text: str) -> str | None:
    matches: list[str] = []
    for language, block in _fenced_blocks(text):
        if language != "json":
            continue
        html = _json_html_field(block)
        if html is not None:
            matches.append(html)
    if not matches:
        return None
    return max(matches, key=len)


def _json_html_field(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        return None
    html = parsed.get("html")
    if not isinstance(html, str):
        return None
    return html


def _complete_html(candidate: str) -> str:
    lower = candidate.lower()
    starts = [pos for pos in (lower.find("<!doctype html"), lower.find("<html")) if pos != -1]
    if not starts:
        raise ValueError("No HTML document found in Codex output")
    start = min(starts)
    end = lower.rfind("</html>")
    if end == -1:
        raise ValueError("HTML document is missing </html>")
    html = candidate[start : end + len("</html>")].strip() + "\n"
    html_lower = html.lower()
    if "<html" not in html_lower or "</html>" not in html_lower:
        raise ValueError("Cleaned HTML is not a complete document")
    if html.lstrip().startswith(("{", '"')):
        raise ValueError("Cleaned HTML still contains a JSON wrapper")
    return html


def _decode_escaped_html_literal(candidate: str) -> str | None:
    stripped = candidate.strip()
    lower = stripped.lower()
    if "\\n" not in stripped and '\\"' not in stripped:
        return None
    if "<!doctype html" not in lower and "<html" not in lower:
        return None
    try:
        decoded = json.loads(f'"{stripped}"')
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, str):
        return None
    return decoded


def clean_codex_html_output(text: str) -> HtmlCleanupResult:
    warnings: list[str] = []
    method = "raw_html_document"
    candidate: str | None = None
    try:
        candidate = _json_html_field(text)
    except json.JSONDecodeError:
        warnings.append("json_parse_failed")
    if candidate is not None:
        method = "json_html_field"
    else:
        candidate = _fenced_json_html_field(text)
        if candidate is not None:
            method = "fenced_json_html_field"
        else:
            candidate = _extract_fenced(text, "html")
            if candidate is not None:
                method = "fenced_html"
            else:
                candidate = text
    decoded_candidate = _decode_escaped_html_literal(candidate)
    if decoded_candidate is not None:
        candidate = decoded_candidate
        method = f"{method}_escaped_literal"
    html = _complete_html(candidate)
    return HtmlCleanupResult(
        html=html,
        method=method,
        input_sha256=_digest(text),
        output_sha256=_digest(html),
        warnings=warnings,
    )
