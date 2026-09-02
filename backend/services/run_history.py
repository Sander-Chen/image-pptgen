"""Read orchestration for batches and runs, including reconciliation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import db as dbmod
from backend.domain import status as run_status
from backend.services.scheduler import pump_batch_queue as default_pump_batch_queue

QueuePump = Callable[[int, str], object]
SINGAPORE_TZ = timezone(timedelta(hours=8), name="Asia/Singapore")


def reconcile_default_timeout() -> None:
    dbmod.reconcile_run_statuses(dbmod.get_default_timeout_minutes())


def list_batches(db_path: str, pump_queue: QueuePump = default_pump_batch_queue) -> list[dict]:
    reconcile_default_timeout()
    for batch in dbmod.list_batch_summaries():
        if batch["status"] == run_status.QUEUED:
            pump_queue(batch["id"], db_path)
    reconcile_default_timeout()
    return dbmod.list_batch_summaries()


def get_active_batch(db_path: str, pump_queue: QueuePump = default_pump_batch_queue) -> dict:
    reconcile_default_timeout()
    active = dbmod.get_active_batch_summary()
    if active and active["status"] == run_status.QUEUED:
        pump_queue(active["id"], db_path)
        reconcile_default_timeout()
        active = dbmod.get_active_batch_summary()
    return active or {}


def get_batch_detail(
    batch_id: int,
    db_path: str,
    pump_queue: QueuePump = default_pump_batch_queue,
) -> dict | None:
    reconcile_default_timeout()
    batch_summary = dbmod.get_batch_summary(batch_id)
    if batch_summary and batch_summary["status"] == run_status.QUEUED:
        pump_queue(batch_id, db_path)
        reconcile_default_timeout()
    return dbmod.get_batch_detail(batch_id)


def get_runfail_stats(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    reconcile_default_timeout()
    resolved_filters = _resolve_runfail_filters(filters)
    runs = _filter_runfail_runs(dbmod.list_runs(), resolved_filters)
    failed_runs = [run for run in runs if run.get("status") in {"failed", "timed_out"}]
    total_runs = len(runs)
    failed_count = len(failed_runs)
    return {
        "window": _window_label(resolved_filters),
        "filters": resolved_filters,
        "total_runs": total_runs,
        "failed_or_timed_out": failed_count,
        "failure_rate": _percent(failed_count, total_runs),
        "by_route_type": _breakdown(failed_runs, _route_type, "route_type"),
        "by_route": _breakdown(failed_runs, _route_key),
        "by_mode": _breakdown(failed_runs, _mode_key, "mode"),
        "by_status": _breakdown(failed_runs, lambda run: str(run.get("status") or "unknown"), "status"),
        "by_error_class": _breakdown(failed_runs, _error_class, "error_class"),
        "by_model": _breakdown(failed_runs, _model_key, "model"),
        "by_retry_signal": _breakdown(failed_runs, _retry_signal, "retry_signal"),
        "trend": _trend(runs),
        "diagnostics": _diagnostics(failed_runs),
    }


def get_runfail_export_rows(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    stats = get_runfail_stats(filters)
    return _runfail_export_rows(stats)


def get_runfail_export_payload(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    stats = get_runfail_stats(filters)
    return {
        "window": stats["window"],
        "filters": stats["filters"],
        "rows": _runfail_export_rows(stats),
    }


def _runfail_export_rows(stats: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"section": "summary", "key": "total_runs", "count": stats["total_runs"], "percent": 100.0 if stats["total_runs"] else 0.0},
        {"section": "summary", "key": "failed_or_timed_out", "count": stats["failed_or_timed_out"], "percent": stats["failure_rate"]},
    ]
    for section, key_name in (
        ("by_route_type", "route_type"),
        ("by_route", "route"),
        ("by_mode", "mode"),
        ("by_status", "status"),
        ("by_error_class", "error_class"),
        ("by_model", "model"),
        ("by_retry_signal", "retry_signal"),
    ):
        for item in stats[section]:
            rows.append({
                "section": section,
                "key": item[key_name],
                "count": item["count"],
                "percent": item["percent"],
            })
    for item in stats["trend"]:
        rows.append({
            "section": "trend",
            "key": item["window"],
            "count": item["failed_or_timed_out"],
            "percent": item["failure_rate"],
            "total_runs": item["total_runs"],
            "failed_or_timed_out": item["failed_or_timed_out"],
        })
    for item in stats.get("diagnostics", []):
        rows.append({
            "section": "diagnostics",
            "key": item["key"],
            "count": item["count"],
            "percent": item["percent"],
            "insight": item["insight"],
            "recommended_action": item["recommended_action"],
        })
    return rows


def _breakdown(runs: list[dict], key_func: Callable[[dict], str], label: str = "route") -> list[dict[str, Any]]:
    total = len(runs)
    counts: dict[str, int] = {}
    for run in runs:
        key = key_func(run) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return [
        {label: key, "count": count, "percent": _percent(count, total)}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _route_key(run: dict) -> str:
    strategy = run.get("strategy")
    if strategy and strategy != "html_default":
        return str(strategy)
    return str(run.get("engine") or "html")


def _route_type(run: dict) -> str:
    engine = str(run.get("engine") or "html")
    strategy = str(run.get("strategy") or "")
    if engine == "html" or strategy == "html_default":
        return "html"
    return "image"


def _mode_key(run: dict) -> str:
    explicit = run.get("generation_mode") or run.get("mode")
    if explicit:
        return str(explicit)
    return "auto" if run.get("auto_candidate_index") else "manual"


def _model_key(run: dict) -> str:
    metadata = _parse_json(run.get("model_call_metadata"))
    model = metadata.get("model") or metadata.get("active_model") or metadata.get("provider_model")
    return str(model or "unknown")


def _error_class(run: dict) -> str:
    if run.get("status") == "timed_out":
        return "timeout"
    message = str(run.get("error_message") or "").lower()
    if "no inline image bytes" in message or "empty provider text" in message or "empty image" in message:
        return "empty_image_response"
    if "400" in message or "bad request" in message:
        return "bad_request"
    if any(token in message for token in ("model", "profile", "credential", "key")):
        return "configuration"
    return "unknown"


def _retry_signal(run: dict) -> str:
    error_class = _error_class(run)
    if error_class in {"empty_image_response", "timeout"}:
        return "retryable_provider_or_timeout"
    if error_class in {"bad_request", "configuration"}:
        return "terminal_request_or_config"
    return "needs_review"


def _trend(runs: list[dict]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for run in runs:
        created = str(run.get("created_at") or "unknown")
        window = created[:10] if created != "unknown" else "unknown"
        bucket = buckets.setdefault(window, {"total_runs": 0, "failed_or_timed_out": 0})
        bucket["total_runs"] += 1
        if run.get("status") in {"failed", "timed_out"}:
            bucket["failed_or_timed_out"] += 1
    return [
        {
            "window": window,
            "total_runs": counts["total_runs"],
            "failed_or_timed_out": counts["failed_or_timed_out"],
            "failure_rate": _percent(counts["failed_or_timed_out"], counts["total_runs"]),
        }
        for window, counts in sorted(buckets.items())
    ]


def _diagnostics(failed_runs: list[dict]) -> list[dict[str, Any]]:
    total = len(failed_runs)
    if not total:
        return []
    rows = []
    retryable = [run for run in failed_runs if _retry_signal(run) == "retryable_provider_or_timeout"]
    terminal = [run for run in failed_runs if _retry_signal(run) == "terminal_request_or_config"]
    needs_review = [run for run in failed_runs if _retry_signal(run) == "needs_review"]
    if retryable:
        rows.append({
            "key": "retryable_provider_or_timeout",
            "count": len(retryable),
            "percent": _percent(len(retryable), total),
            "insight": "Provider timeout or empty image response. Automatic retry or manual retry is appropriate.",
            "recommended_action": "retry",
            "raw_messages": _raw_messages(retryable),
        })
    if terminal:
        rows.append({
            "key": "terminal_request_or_config",
            "count": len(terminal),
            "percent": _percent(len(terminal), total),
            "insight": "Bad request, model, profile, credential, or key issue. Fix configuration before retrying.",
            "recommended_action": "fix_config",
            "raw_messages": _raw_messages(terminal),
        })
    if needs_review:
        rows.append({
            "key": "needs_review",
            "count": len(needs_review),
            "percent": _percent(len(needs_review), total),
            "insight": "Unclassified failure. Inspect Run Detail evidence and provider response metadata.",
            "recommended_action": "inspect_evidence",
            "raw_messages": _raw_messages(needs_review),
        })
    return rows


def _raw_messages(runs: list[dict], limit: int = 5) -> list[str]:
    messages = []
    for run in runs:
        raw = str(run.get("error_message") or "").strip()
        messages.append(_redact_message(raw or f"{run.get('status') or 'failed'} with no error message"))
        if len(messages) >= limit:
            break
    return messages


def _redact_message(message: str) -> str:
    if not message:
        return message
    patterns = [
        r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(authorization\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(secret\s*[=:]\s*)([^\s,;]+)",
    ]
    redacted = message
    for pattern in patterns:
        redacted = re.sub(pattern, lambda match: f"{match.group(1)}<redacted>", redacted)
    return redacted


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_runfail_filters(filters: dict[str, Any] | None) -> dict[str, str]:
    filters = filters or {}
    today = datetime.now(SINGAPORE_TZ).date()
    route_type = str(filters.get("route_type") or filters.get("type") or "all").lower()
    if route_type not in {"all", "html", "image"}:
        route_type = "all"

    start_raw = str(filters.get("start_date") or "").strip()
    end_raw = str(filters.get("end_date") or "").strip()
    preset = str(filters.get("date_preset") or filters.get("preset") or "").strip().lower()
    if start_raw or end_raw:
        preset = "custom"
    if not preset:
        preset = "today"

    if preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset == "last_7_days":
        start = today - timedelta(days=6)
        end = today
    elif preset in {"last_month", "last_30_days"}:
        preset = "last_month"
        start = today - timedelta(days=30)
        end = today
    elif preset == "this_year":
        start = date(today.year, 1, 1)
        end = today
    elif preset == "custom":
        start = _parse_filter_date(start_raw) or today
        end = _parse_filter_date(end_raw) or start
    else:
        preset = "today"
        start = end = today

    if start > end:
        start, end = end, start

    return {
        "route_type": route_type,
        "date_preset": preset,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "Asia/Singapore",
    }


def _filter_runfail_runs(runs: list[dict], filters: dict[str, str]) -> list[dict]:
    start = _parse_filter_date(filters["start_date"])
    end = _parse_filter_date(filters["end_date"])
    route_type = filters["route_type"]
    filtered = []
    for run in runs:
        local_date = _run_local_date(run)
        if start and local_date < start:
            continue
        if end and local_date > end:
            continue
        if route_type != "all" and _route_type(run) != route_type:
            continue
        filtered.append(run)
    return filtered


def _parse_filter_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_run_datetime(value: Any) -> datetime:
    raw = str(value or "")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _run_local_date(run: dict) -> date:
    timestamp = run.get("created_at") or run.get("started_at") or run.get("completed_at")
    return _parse_run_datetime(timestamp).astimezone(SINGAPORE_TZ).date()


def _window_label(filters: dict[str, str]) -> str:
    if filters["start_date"] == filters["end_date"]:
        return filters["start_date"]
    return f"{filters['start_date']}..{filters['end_date']}"


def _percent(count: int, total: int) -> float:
    return round((count / total) * 100, 1) if total else 0.0
