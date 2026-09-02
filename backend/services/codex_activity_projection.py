"""Safe, deterministic user-facing projections of persisted Codex activity."""

from __future__ import annotations

import re
from typing import Any

import db as dbmod

from backend.services.codex_audit import redact_audit_value


HEARTBEAT_MESSAGE = "任务仍在运行，尚无新的业务里程碑。"
AUTHORIZATION_WAIT_MESSAGE = "正在等待用户授权，授权后会继续当前任务。"
UNSAFE_REPLY_MESSAGE = "子任务已返回结果，正在进行安全校验。"

_PATH_PATTERN = re.compile(
    r"(?:^|[\s'\"`(])(?:/(?:home|root|tmp|var|etc|Users|mnt)/[^\s'\"`)]*|[A-Za-z]:\\[^\s'\"`)]*)"
)
_SENSITIVE_REPLY_MARKERS = (
    "api_key",
    "apikey",
    "authorization:",
    "bearer ",
    "deck_content",
    "private key",
    "prompt",
    "secret",
    "source_material",
    "system message",
    "token=",
)
_APPROVAL_MARKERS = ("approval", "authorization", "permission")
_WAITING_STATES = {
    "awaiting_approval",
    "needs_approval",
    "requires_approval",
    "waiting_for_approval",
    "waiting_for_authorization",
}


def _cursor(invocation_id: int, sequence: int) -> str:
    return f"{invocation_id}:{sequence}"


def _parse_cursor(value: str | None) -> tuple[int, int]:
    if not value:
        return (0, 0)
    match = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*)", value)
    if match is None:
        raise ValueError("invalid Codex activity cursor")
    return (int(match.group(1)), int(match.group(2)))


def _source(invocation: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt": int(invocation.get("attempt") or 1),
        "role": invocation.get("role"),
        "run_slide_id": invocation.get("run_slide_id"),
        "stage_id": invocation.get("stage_id"),
    }


def _is_authorization_wait(event: dict[str, Any], item: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or "").lower()
    item_type = str(item.get("type") or "").lower()
    status = str(item.get("status") or event.get("status") or "").lower()
    return (
        status in _WAITING_STATES
        or any(marker in event_type for marker in _APPROVAL_MARKERS)
        or any(marker in item_type for marker in _APPROVAL_MARKERS)
    )


def _safe_agent_message(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    normalized = text.strip()
    if (
        not normalized
        or len(normalized) > 240
        or "\n" in normalized
        or "\r" in normalized
    ):
        return None
    lowered = normalized.lower()
    if normalized.startswith(("{", "[", "```")):
        return None
    if any(marker in lowered for marker in _SENSITIVE_REPLY_MARKERS):
        return None
    if _PATH_PATTERN.search(normalized):
        return None
    redacted = redact_audit_value(normalized)
    if not isinstance(redacted, str) or redacted != normalized:
        return None
    return normalized


def _stage_family(invocation: dict[str, Any]) -> str:
    stage = str(invocation.get("stage_id") or "").lower()
    if "design" in stage or invocation.get("role") == "designer":
        return "design"
    if "slide" in stage or invocation.get("run_slide_id") is not None:
        return "slide"
    return "generic"


def _business_message(
    item_type: str,
    event_type: str,
    invocation: dict[str, Any],
) -> str | None:
    family = _stage_family(invocation)
    completed = event_type in {"item.completed", "item.failed"}
    messages = {
        "command_execution": {
            "design": (
                "设计步骤已返回，正在校验结果。"
                if completed
                else "正在准备整体设计方案。"
            ),
            "slide": (
                "页面生成步骤已返回，正在校验产物。"
                if completed
                else "正在生成当前页面。"
            ),
            "generic": (
                "当前处理步骤已返回，正在校验结果。"
                if completed
                else "正在执行当前生成步骤。"
            ),
        },
        "file_change": {
            "design": "正在整理整体设计产物。",
            "slide": "正在整理当前页面产物。",
            "generic": "正在整理生成产物。",
        },
        "mcp_tool_call": {
            "design": "正在获取设计所需的辅助信息。",
            "slide": "正在获取页面生成所需的辅助信息。",
            "generic": "正在调用已配置的辅助能力。",
        },
        "todo_list": {
            "design": "正在更新整体设计步骤。",
            "slide": "正在更新页面生成步骤。",
            "generic": "正在更新任务执行计划。",
        },
        "plan": {
            "design": "正在更新整体设计步骤。",
            "slide": "正在更新页面生成步骤。",
            "generic": "正在更新任务执行计划。",
        },
    }
    by_family = messages.get(item_type)
    return by_family.get(family) if by_family else None


def project_codex_event(
    invocation: dict[str, Any],
    event_row: dict[str, Any],
) -> dict[str, Any] | None:
    """Project one persisted event without exposing its raw payload."""
    payload = event_row.get("payload")
    if not isinstance(payload, dict):
        return None
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    event_type = str(payload.get("type") or event_row.get("event_type") or "")
    item_type = str(item.get("type") or event_row.get("item_type") or "")
    sequence = int(event_row.get("sequence") or 0)
    invocation_id = int(invocation["id"])
    common = {
        "cursor": _cursor(invocation_id, sequence),
        "milestone": False,
        "observed_at": event_row.get("observed_at"),
        "source": _source(invocation),
    }

    if _is_authorization_wait(payload, item):
        return {
            **common,
            "kind": "authorization_wait",
            "message": AUTHORIZATION_WAIT_MESSAGE,
        }

    if item_type == "reasoning":
        return None

    if item_type == "agent_message" and event_type == "item.completed":
        safe_message = _safe_agent_message(item.get("text"))
        if safe_message is None:
            return {
                **common,
                "kind": "business_activity",
                "message": UNSAFE_REPLY_MESSAGE,
            }
        return {
            **common,
            "kind": "agent_message",
            "message": safe_message,
        }

    message = _business_message(item_type, event_type, invocation)
    if message is None:
        return None
    return {
        **common,
        "kind": "business_activity",
        "message": message,
    }


def _coalesce(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for event in events:
        if projected and all(
            projected[-1].get(key) == event.get(key)
            for key in ("kind", "message", "milestone", "source")
        ):
            projected[-1] = event
        else:
            projected.append(event)
    return projected


def project_run_activity(
    run_id: int,
    *,
    after_cursor: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return safe run activity after a durable invocation/sequence cursor."""
    after = _parse_cursor(after_cursor)
    committed_events: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    cursor_event_ids: dict[tuple[int, int], int] = {}
    for invocation in dbmod.list_codex_invocations(run_id=run_id, include_events=True):
        invocation_id = int(invocation["id"])
        for event in invocation.get("events", []):
            event_position = (invocation_id, int(event.get("sequence") or 0))
            event_id = int(event["id"])
            cursor_event_ids[event_position] = event_id
            committed_events.append((event_id, invocation, event))

    if after_cursor is None:
        after_event_id = 0
    else:
        after_event_id = cursor_event_ids.get(after)
        if after_event_id is None:
            raise ValueError("unknown Codex activity cursor")

    projected: list[dict[str, Any]] = []
    last_scanned_cursor = after_cursor
    for event_id, invocation, event in sorted(committed_events, key=lambda row: row[0]):
        if event_id <= after_event_id:
            continue
        invocation_id = int(invocation["id"])
        event_position = (invocation_id, int(event.get("sequence") or 0))
        last_scanned_cursor = _cursor(*event_position)
        public = project_codex_event(invocation, event)
        if public is not None:
            projected.append(public)

    coalesced = _coalesce(projected)
    if limit < 1:
        raise ValueError("activity limit must be positive")
    selected = coalesced[:limit]
    if len(coalesced) > limit:
        next_cursor = selected[-1]["cursor"]
    else:
        next_cursor = last_scanned_cursor
    return {
        "events": selected,
        "next_cursor": next_cursor,
        "run_id": run_id,
    }


def heartbeat_event(
    *,
    run_id: int,
    cursor: str | None,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Create an explicitly non-milestone liveness event without model work."""
    return {
        "cursor": cursor,
        "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
        "event": "heartbeat",
        "kind": "heartbeat",
        "message": HEARTBEAT_MESSAGE,
        "milestone": False,
        "run_id": run_id,
    }
