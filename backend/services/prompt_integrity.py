"""Prompt diff and integrity checks for publish gating."""

from __future__ import annotations

import difflib
import re
from typing import Any

MAX_MESSAGE_SAMPLE_LINES = 12
VARIABLE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
CRITICAL_KEYWORDS = (
    "critical",
    "instruction",
    "must",
    "never",
    "do not",
    "don't",
    "cannot",
    "required",
    "preserve",
    "guardrail",
    "output format",
    "return",
)


def _sample_message_lines(lines: list[str]) -> list[str]:
    return lines[:MAX_MESSAGE_SAMPLE_LINES]


def _normalize_line(line: str) -> str:
    return " ".join((line or "").strip().split())


def build_change_report(
    original: str,
    updated: str,
    inserted_variables: list[str] | None = None,
) -> dict[str, Any]:
    inserted_variables = inserted_variables or []
    original_lines = original.splitlines()
    updated_lines = updated.splitlines()
    matcher = difflib.SequenceMatcher(a=original_lines, b=updated_lines, autojunk=False)
    changed_hunks: list[dict[str, Any]] = []
    added_lines: list[str] = []
    removed_lines: list[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = original_lines[i1:i2]
        after = updated_lines[j1:j2]
        if tag in {"delete", "replace"}:
            removed_lines.extend(before)
        if tag in {"insert", "replace"}:
            added_lines.extend(after)
        changed_hunks.append(
            {
                "type": tag,
                "original": before,
                "updated": after,
            }
        )

    similarity = difflib.SequenceMatcher(None, original, updated, autojunk=False).ratio()
    removed_nonempty = [line for line in removed_lines if line.strip()]
    removed_limit = max(4, len([line for line in original_lines if line.strip()]) // 20)
    risk_level = "low"
    if (similarity < 0.72 and removed_nonempty) or len(removed_nonempty) > removed_limit:
        risk_level = "high"
    elif similarity < 0.9 or removed_nonempty:
        risk_level = "medium"

    summary = (
        f"{len(inserted_variables)} variable(s) inserted; "
        f"{len(added_lines)} line(s) added; {len(removed_nonempty)} original line(s) removed."
    )
    return {
        "similarity": round(similarity, 4),
        "risk_level": risk_level,
        "inserted_variables": inserted_variables,
        "original_length": len(original),
        "updated_length": len(updated),
        "added_line_count": len(added_lines),
        "removed_line_count": len(removed_nonempty),
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "changed_hunks": changed_hunks,
        "summary": summary,
    }


def _malformed_placeholders(content: str) -> list[str]:
    remainder = VARIABLE_RE.sub("", content or "")
    issues = []
    for match in VARIABLE_RE.finditer(content or ""):
        variable = match.group(1)
        if "\n" in variable or "\r" in variable:
            issues.append("Placeholder names cannot contain line breaks")
    if "{{" in remainder:
        issues.append("Unclosed or nested '{{' placeholder")
    if "}}" in remainder:
        issues.append("Unmatched '}}' placeholder")
    return issues


def _critical_lines(content: str) -> list[str]:
    lines: list[str] = []
    seen = set()
    for raw_line in (content or "").splitlines():
        line = _normalize_line(raw_line)
        if not line:
            continue
        lower = line.lower()
        if any(keyword in lower for keyword in CRITICAL_KEYWORDS):
            if line not in seen:
                lines.append(line)
                seen.add(line)
    return lines


def evaluate_prompt_integrity(
    content: str,
    *,
    baseline_content: str | None = None,
    variable_can_save: bool = True,
) -> dict[str, Any]:
    baseline = baseline_content if baseline_content is not None else content
    change_report = build_change_report(baseline, content, [])
    checks: list[dict[str, Any]] = []

    placeholder_issues = _malformed_placeholders(content)
    checks.append(
        {
            "key": "placeholder_syntax",
            "label": "Placeholder syntax",
            "status": "failed" if placeholder_issues else "passed",
            "severity": "blocker",
            "message": "; ".join(placeholder_issues) if placeholder_issues else "All explicit placeholders are syntactically valid.",
        }
    )

    if baseline_content is None:
        checks.append(
            {
                "key": "critical_instruction_preserved",
                "label": "Critical instruction preservation",
                "status": "skipped",
                "severity": "info",
                "message": "No baseline prompt was provided for line-preservation checks.",
            }
        )
    else:
        updated_lines = {_normalize_line(line) for line in content.splitlines() if _normalize_line(line)}
        missing_critical = [line for line in _critical_lines(baseline_content) if line not in updated_lines]
        checks.append(
            {
                "key": "critical_instruction_preserved",
                "label": "Critical instruction preservation",
                "status": "failed" if missing_critical else "passed",
                "severity": "blocker",
                "message": (
                    f"Missing critical baseline line(s): {' | '.join(_sample_message_lines(missing_critical))}"
                    if missing_critical
                    else "All critical baseline instructions are preserved."
                ),
            }
        )

    if baseline_content is None or not baseline_content.strip():
        checks.append(
            {
                "key": "content_retention",
                "label": "Content retention",
                "status": "skipped",
                "severity": "info",
                "message": "No baseline prompt was provided for retention checks.",
            }
        )
    else:
        baseline_len = len(baseline_content.strip())
        updated_len = len(content.strip())
        retention = updated_len / baseline_len if baseline_len else 1.0
        failed = retention < 0.75 or change_report["risk_level"] == "high"
        checks.append(
            {
                "key": "content_retention",
                "label": "Content retention",
                "status": "failed" if failed else "passed",
                "severity": "blocker",
                "message": (
                    f"Updated prompt retained {round(retention * 100)}% of baseline text; review the diff before publishing."
                    if failed
                    else f"Updated prompt retained {round(retention * 100)}% of baseline text."
                ),
            }
        )

    failed_blockers = [
        check
        for check in checks
        if check["severity"] == "blocker" and check["status"] == "failed"
    ]
    return {
        "change_report": change_report,
        "integrity_checks": checks,
        "can_publish": variable_can_save and not failed_blockers,
    }


__all__ = [
    "build_change_report",
    "evaluate_prompt_integrity",
]
