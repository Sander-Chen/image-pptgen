"""LLM-backed split draft workflow for decks."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import db as dbmod
import pipeline
from backend.services import auto_split_settings, model_profiles
from backend.services.codex_exec import materialize_codex_result_final_text, run_codex_exec_json
from backend.services.codex_executable import CodexExecutableUnavailable
from backend.services.codex_platform_gate import CodexGateCapacityTimeout
from backend.services.codex_jsonl_stream import (
    SOURCE_WINDOW_BYTES,
    iter_bounded_jsonl_file_records,
)
from pipeline import extract_fenced_block
from splitter import split_by_explicit_h1, split_by_markdown

BASE_DIR = Path(__file__).resolve().parents[2]
PROMPT_PATH = BASE_DIR / "example" / "自动切分.md"
PROMPT_PATHS = {
    "faithful": PROMPT_PATH,
    "editorial": BASE_DIR / "example" / "自动切分-编辑重构.md",
}
INTEGRITY_KEYWORDS = (
    "critical",
    "directive",
    "must",
    "never",
    "do not",
    "required",
    "guardrail",
    "sentinel",
)
COMPACT_CODEX_SPLIT_CONTRACT = """
For this local Codex Auto Split call, return only one fenced JSON array. Do not
repeat or summarize source body text. Each object must contain exactly these
fields: `title` (the page title) and `section_ids` (a non-empty array of integer
source-unit IDs assigned to that page). Use every available ID exactly once in
strict ascending order, without gaps or duplicates. The server reconstructs
page bodies from the source, so never put page body text in the response.
""".strip()

# The Public Image surface resolves these two server-owned profiles by name.
# Keep the command isolation scoped to those profiles so ordinary AutoSplit,
# Native Director/Renderer, and every other Codex caller retain their existing
# command options and timeout behavior.
_PUBLIC_SPLIT_EXECUTION_IDENTITIES = {
    ("AutoSplit · GPT-5.6 Luna", "gpt-5.6-luna"),
    ("AutoSplit · GPT-5.6 Terra", "gpt-5.6-terra"),
}
_PUBLIC_SPLIT_EXTRA_CONFIG = [
    "features.apps=false",
    "features.plugins=false",
    "apps._default.enabled=false",
]
_PUBLIC_SPLIT_CHILD_TIMEOUT_SECONDS = 840
_PUBLIC_SPLIT_ADMISSION_TIMEOUT_SECONDS = 30
_PLAIN_TEXT_SOURCE_UNIT_TARGET_CHARS = 1200
_PLAIN_TEXT_SOURCE_UNIT_MAX_CHARS = 1800
_PLAIN_TEXT_SAFE_BOUNDARY_PUNCTUATION = frozenset("。！？!?；;")

# Public Image faithful split may classify one very specific class of failed
# Codex calls as a transport-only failure.  The classifier is intentionally
# narrower than the runner's generic non-zero result handling: it reads only
# the bounded raw JSONL terminal and fails closed for any unknown shape.
_TRANSPORT_CLASSIFICATION_MAX_BYTES = SOURCE_WINDOW_BYTES * 2
_TRANSPORT_CLASSIFICATION_MAX_RECORDS = 64
_TRANSPORT_MESSAGE_MAX_BYTES = 2048
_TRANSPORT_MESSAGE_REQUIREMENTS = (
    ("stream disconnected before completion", "no route to host"),
    ("stream disconnected before completion", "error sending request for url"),
)
_TRANSPORT_DENY_MARKERS = (
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid model",
    "model not found",
    "rejected",
    "quota",
    "rate limit",
    "permission denied",
    "timed out",
    "timeout",
    "parse",
    "schema",
    "malformed",
)


class SplitDraftError(ValueError):
    status_code = 400


class SplitDraftConflict(SplitDraftError):
    status_code = 409


class TargetPageCountUnavailable(SplitDraftError):
    """A requested deterministic page count cannot be reached safely."""

    code = "target_page_count_unavailable"
    status_code = 422


class SplitExecutionFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        transport_only: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transport_only = transport_only


def _transport_message_is_strict(value: object) -> bool:
    """Return true only for a bounded, known network-transport message."""
    if not isinstance(value, str) or not value.strip():
        return False
    if len(value.encode("utf-8")) > _TRANSPORT_MESSAGE_MAX_BYTES:
        return False
    message = value.strip().lower()
    if any(marker in message for marker in _TRANSPORT_DENY_MARKERS):
        return False
    return any(
        all(marker in message for marker in required)
        for required in _TRANSPORT_MESSAGE_REQUIREMENTS
    )


def is_transport_only_codex_failure(result: Any) -> bool:
    """Classify the real Codex result's raw JSONL terminal, fail-closed.

    This is deliberately a capability used by the Public Image orchestrator;
    generic AutoSplit callers never invoke it.  A retry is allowed only when
    the bounded stream contains the expected started/error/failed sequence,
    ends with ``turn.failed``, and every error message is a known transport
    failure.  A missing, malformed, oversized, incomplete, or similar stream
    is not retryable.
    """
    if getattr(result, "exit_code", 0) == 0 or getattr(result, "timed_out", False):
        return False
    raw_path = getattr(result, "raw_jsonl_path", None)
    if not raw_path:
        return False
    try:
        path = Path(raw_path)
        if not path.is_file() or path.stat().st_size > _TRANSPORT_CLASSIFICATION_MAX_BYTES:
            return False
        events: list[dict[str, Any]] = []
        for record in iter_bounded_jsonl_file_records(path):
            if record.invalid or record.oversized or not isinstance(record.event, dict):
                return False
            events.append(record.event)
            if len(events) > _TRANSPORT_CLASSIFICATION_MAX_RECORDS:
                return False
    except (OSError, ValueError):
        return False

    if (
        len(events) < 3
        or events[0].get("type") != "thread.started"
        or events[1].get("type") != "turn.started"
        or events[-1].get("type") != "turn.failed"
    ):
        return False
    thread_id = events[0].get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip() or len(thread_id) > 256:
        return False
    saw_transport_error = False
    for index, event in enumerate(events[2:], start=2):
        event_type = event.get("type")
        if event_type == "error":
            if not _transport_message_is_strict(event.get("message")):
                return False
            saw_transport_error = True
        elif event_type == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "error":
                return False
            if not _transport_message_is_strict(item.get("message")):
                return False
            saw_transport_error = True
        elif event_type == "turn.failed":
            if index != len(events) - 1:
                return False
            error = event.get("error")
            if not isinstance(error, dict) or not _transport_message_is_strict(error.get("message")):
                return False
            saw_transport_error = True
        else:
            # In particular, turn.completed or an agent_message means this is
            # not a transport-only terminal, even if another record mentions
            # a network error.
            return False
    return saw_transport_error


class SplitDraftExecutionError(SplitDraftError):
    def __init__(self, message: str, *, code: str, draft: dict) -> None:
        super().__init__(message)
        self.code = code
        self.draft = draft
        self.status_code = {
            "configuration": 400,
            "timeout": 504,
            "resource_unavailable": 503,
            "executable_identity_unavailable": 503,
            "provider_rejected": 502,
            "parse": 422,
            "integrity": 422,
        }[code]


def prompt_path_for_mode(content_mode: str) -> Path:
    try:
        return PROMPT_PATHS[content_mode]
    except KeyError as e:
        raise SplitDraftError("Auto Split content mode is invalid") from e


def _is_public_split_execution(config: dict[str, Any]) -> bool:
    """Return whether *config* is one of the server-owned Public split profiles."""
    return (
        str(config.get("profile_name") or "").strip(),
        str(config.get("model") or "").strip(),
    ) in _PUBLIC_SPLIT_EXECUTION_IDENTITIES


def _run_codex_split(
    config: dict[str, Any],
    prompt: str,
    *,
    stage_id: str,
) -> str:
    model = str(config.get("model") or "").strip()
    if not model:
        raise SplitDraftError("Auto Split requires a configured model")
    reasoning_effort = str(config.get("thinking") or "low").strip().lower()
    artifact_base = Path(
        os.environ.get("PPT_ARTIFACTS_DIR") or (BASE_DIR / "artifacts")
    )
    artifact_dir = artifact_base / "split-drafts" / uuid4().hex
    runner_kwargs: dict[str, Any] = {
        "stage_id": stage_id,
        "role": "auto_spill",
        "prompt": prompt,
        "work_dir": artifact_dir / "scratch",
        "artifact_dir": artifact_dir,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": "read-only",
    }
    if _is_public_split_execution(config):
        runner_kwargs["extra_config"] = list(_PUBLIC_SPLIT_EXTRA_CONFIG)
        runner_kwargs["timeout_seconds"] = _PUBLIC_SPLIT_CHILD_TIMEOUT_SECONDS
        runner_kwargs["admission_timeout_seconds"] = _PUBLIC_SPLIT_ADMISSION_TIMEOUT_SECONDS
    result = asyncio.run(
        run_codex_exec_json(**runner_kwargs)
    )
    if getattr(result, "timed_out", False):
        raise SplitExecutionFailure("timeout", "Auto Split timed out")
    if result.exit_code != 0:
        if is_transport_only_codex_failure(result):
            raise SplitExecutionFailure(
                "provider_rejected",
                "Local Codex Exec rejected the Auto Split request",
                transport_only=True,
            )
        raise SplitExecutionFailure(
            "provider_rejected", "Local Codex Exec rejected the Auto Split request"
        )
    return materialize_codex_result_final_text(result)


def _run_split(config: dict[str, Any], prompt: str, *, stage_id: str) -> str:
    if config.get("api_type") == "gemini":
        try:
            return pipeline.call_llm(
                config,
                prompt,
                timeout_seconds=180,
                agent_role="auto_spill",
            )
        except TimeoutError as e:
            raise SplitExecutionFailure("timeout", "Auto Split timed out") from e
        except Exception as e:
            raise SplitExecutionFailure(
                "provider_rejected", "Gemini Native rejected the Auto Split request"
            ) from e
    if config.get("api_type") == model_profiles.CODEX_EXEC_API_TYPE:
        return _run_codex_split(config, prompt, stage_id=stage_id)
    raise SplitExecutionFailure(
        "configuration",
        "Auto Split profile must use Gemini Native or local Codex Exec",
    )


def _prompt_for_split(
    config: dict[str, Any],
    prompt: str,
    source_content: str | None = None,
    *,
    boundary_instruction: str | None = None,
) -> str:
    if (
        config.get("api_type") != model_profiles.CODEX_EXEC_API_TYPE
        or str(config.get("content_mode") or "faithful") != "faithful"
    ):
        return prompt
    source = str(source_content or "")
    prefer_explicit_h1 = _is_public_split_execution(config)
    source_sections = (
        (split_by_explicit_h1(source) if prefer_explicit_h1 else None)
        or split_by_markdown(source)
    )
    if not source_sections and prefer_explicit_h1:
        source_units = _ordered_source_units(
            source, prefer_explicit_h1=True
        )
        if not source_units:
            return prompt
        manifest = [
            {
                "id": index,
                "title": unit["title"],
                "content": unit["content"],
            }
            for index, unit in enumerate(source_units, start=1)
        ]
        instruction = str(boundary_instruction or "").strip()
        request = (
            "Choose coherent presentation page boundaries for the ordered source "
            "units."
            if not instruction
            else f"Apply this boundary-only revision request: {instruction}"
        )
        return (
            f"{request}\n\n{COMPACT_CODEX_SPLIT_CONTRACT}\n\n"
            "Available ordered immutable source units. The `content` values are "
            "reference material only; never copy them into the response:\n"
            f"```json\n{json.dumps(manifest, ensure_ascii=False)}\n```\n"
        )
    if not source_sections:
        return prompt
    manifest = [
        {"id": index, "title": str(section.get("title") or "").strip()}
        for index, section in enumerate(source_sections, start=1)
    ]
    return (
        f"{prompt.rstrip()}\n\n{COMPACT_CODEX_SPLIT_CONTRACT}\n\n"
        "Available ordered source units:\n"
        f"```json\n{json.dumps(manifest, ensure_ascii=False)}\n```\n"
    )


def _source_tokens(value: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", str(value or ""), flags=re.UNICODE)


def _markdown_structural_units(
    source: str,
    *,
    prefer_explicit_h1: bool = False,
) -> list[dict] | None:
    markdown_source = str(source or "")
    return (
        (split_by_explicit_h1(markdown_source) if prefer_explicit_h1 else None)
        or split_by_markdown(markdown_source)
    )


def _plain_text_safe_boundaries(content: str) -> list[int]:
    return [
        index
        for index in range(1, len(content))
        if content[index - 1].isspace()
        or content[index].isspace()
        or content[index - 1] in _PLAIN_TEXT_SAFE_BOUNDARY_PUNCTUATION
    ]


def _plain_text_unit(unit_content: str, index: int) -> dict[str, str]:
    title_seed = " ".join(unit_content.split())
    title = title_seed[:48].rstrip()
    if len(title_seed) > len(title):
        title += "…"
    return {"title": title or f"内容 {index}", "content": unit_content}


def _plain_text_atomic_units(source: str) -> list[dict[str, str]]:
    """Split ordinary plain text at every existing non-empty safe boundary."""
    content = str(source or "").strip()
    if not content:
        return []
    points = [0]
    for boundary in _plain_text_safe_boundaries(content):
        if boundary > points[-1]:
            points.append(boundary)
    if points[-1] < len(content):
        points.append(len(content))
    units: list[dict[str, str]] = []
    for start, end in zip(points, points[1:]):
        unit_content = content[start:end].strip()
        if not unit_content:
            continue
        units.append(_plain_text_unit(unit_content, len(units) + 1))
    return units


def _ordered_source_units(
    source: str,
    *,
    prefer_explicit_h1: bool = False,
) -> list[dict[str, str]]:
    """Return the server-derived source units in their original order.

    Markdown keeps its established H3-then-H2 units unless Public Image
    explicitly prefers two or more fence/comment-safe H1 page sections.
    Plain text is split only at existing whitespace or sentence punctuation,
    so concatenating page bodies preserves the exact ordered token stream
    while giving the boundary model bounded immutable units to group.
    """
    sections = _markdown_structural_units(
        source, prefer_explicit_h1=prefer_explicit_h1
    )
    if sections:
        units = [
            {
                "title": str(section.get("title") or "").strip(),
                "content": str(section.get("content") or "").strip(),
            }
            for section in sections
        ]
        # Empty source units cannot be represented by a valid slide body.  Do
        # not silently drop one: that would make a successful page-count
        # revision lose source structure while appearing to preserve order.
        if any(not unit["content"] for unit in units):
            return []
        return units
    content = str(source or "").strip()
    if not content:
        return []

    safe_boundaries = _plain_text_safe_boundaries(content)
    starts = [0]
    cursor = 0
    while len(content) - cursor > _PLAIN_TEXT_SOURCE_UNIT_MAX_CHARS:
        target = cursor + _PLAIN_TEXT_SOURCE_UNIT_TARGET_CHARS
        maximum = cursor + _PLAIN_TEXT_SOURCE_UNIT_MAX_CHARS
        after_target = next(
            (boundary for boundary in safe_boundaries if target <= boundary <= maximum),
            None,
        )
        before_maximum = next(
            (
                boundary
                for boundary in reversed(safe_boundaries)
                if cursor < boundary <= maximum
            ),
            None,
        )
        boundary = after_target or before_maximum
        if boundary is None or boundary <= cursor:
            break
        starts.append(boundary)
        cursor = boundary

    units: list[dict[str, str]] = []
    ends = [*starts[1:], len(content)]
    for start, end in zip(starts, ends):
        unit_content = content[start:end].strip()
        if not unit_content:
            return []
        units.append(_plain_text_unit(unit_content, len(units) + 1))
    return units


def _target_page_count_slides(
    source: str,
    target_page_count: int,
    *,
    prefer_explicit_h1: bool = False,
) -> list[dict[str, str]]:
    """Build deterministic contiguous pages from ordered source units.

    Unit weight is its non-empty source-content length.  Boundaries are chosen
    at the nearest cumulative weight to each equal target share, with ties
    resolved toward the earlier boundary; the remaining-unit constraint keeps
    every page non-empty and makes the result deterministic.
    Ordinary plain text may be refined at existing safe non-empty boundaries
    when the requested count is larger than the coarse unit count.  Explicit
    Markdown structural units are never split further.
    """
    if type(target_page_count) is not int or target_page_count <= 0:
        raise TargetPageCountUnavailable(
            "target_page_count must be a positive integer"
        )
    units = _ordered_source_units(
        source, prefer_explicit_h1=prefer_explicit_h1
    )
    expanded_plain_text = False
    if units and target_page_count > len(units):
        if _markdown_structural_units(
            source, prefer_explicit_h1=prefer_explicit_h1
        ):
            raise TargetPageCountUnavailable(
                "target_page_count is unavailable for the ordered source units"
            )
        units = _plain_text_atomic_units(source)
        expanded_plain_text = True
    if not units or target_page_count > len(units):
        raise TargetPageCountUnavailable(
            "target_page_count is unavailable for the ordered source units"
        )

    weights = [max(1, len(unit["content"])) for unit in units]
    cumulative = [0]
    for weight in weights:
        cumulative.append(cumulative[-1] + weight)
    total_weight = cumulative[-1]

    boundaries: list[int] = []
    start = 0
    unit_count = len(units)
    for page_index in range(1, target_page_count):
        minimum_end = start + 1
        maximum_end = unit_count - (target_page_count - page_index)
        desired_weight = total_weight * page_index / target_page_count
        end = min(
            range(minimum_end, maximum_end + 1),
            key=lambda candidate: (abs(cumulative[candidate] - desired_weight), candidate),
        )
        boundaries.append(end)
        start = end

    pages: list[dict[str, str]] = []
    starts = [0, *boundaries]
    ends = [*boundaries, unit_count]
    for start, end in zip(starts, ends):
        page_units = units[start:end]
        content = "\n\n".join(unit["content"] for unit in page_units).strip()
        if not content:
            raise TargetPageCountUnavailable(
                "target_page_count produced an empty page"
            )
        if expanded_plain_text:
            title = _plain_text_unit(content, len(pages) + 1)["title"]
        else:
            title = " / ".join(
                unit["title"] for unit in page_units if unit["title"]
            ).strip()
            if not title:
                title = f"第 {len(pages) + 1} 页"
        pages.append({"title": title, "content": content, "split_mode": "llm_auto"})
    return pages


def _is_source_prefix(raw: str, source: str) -> bool:
    """Accept only an ordered, faithful prefix for source reconstruction."""
    response = str(raw or "").strip()
    expected = str(source or "").strip()
    if not response or not expected:
        return False
    response_tokens = _source_tokens(response)
    source_tokens = _source_tokens(expected)
    minimum_prefix_tokens = max(32, min(256, len(source_tokens) // 4))
    if (
        len(response_tokens) < minimum_prefix_tokens
        or len(response_tokens) >= len(source_tokens)
    ):
        return False
    # Codex may stop in the middle of a source line. Ignore that incomplete
    # trailing token; every preceding token must still match source order.
    if response and not response[-1].isspace():
        response_tokens = response_tokens[:-1]
    if not response_tokens or source_tokens[: len(response_tokens)] != response_tokens:
        return False
    return bool(re.search(r"^##?\s+", response, flags=re.MULTILINE))


def _slides_from_source(source: str) -> list[dict[str, str]] | None:
    markdown_slides = split_by_markdown(source)
    if not markdown_slides:
        return None
    return [
        {**slide, "split_mode": "llm_auto"}
        for slide in markdown_slides
    ]


def _section_title(value: object) -> str:
    text = str(value or "").strip()
    return " ".join(re.sub(r"^#{1,6}\s+", "", text).split())


def _slides_from_boundary_plan(
    plan: object,
    source: str,
    *,
    prefer_explicit_h1: bool = False,
) -> list[dict[str, str]] | None:
    if not isinstance(plan, list) or not plan:
        return None
    source_sections = _ordered_source_units(
        source, prefer_explicit_h1=prefer_explicit_h1
    )
    if not source_sections:
        return None
    expected_titles = [_section_title(section.get("title")) for section in source_sections]
    cursor = 0
    reconstructed: list[dict[str, str]] = []
    for item in plan:
        if not isinstance(item, dict):
            return None
        if set(item) != {"title", "section_ids"}:
            return None
        raw_sections = item.get("section_ids")
        if not isinstance(raw_sections, list) or not raw_sections:
            return None
        section_indexes: list[int] = []
        for raw_id in raw_sections:
            if (
                isinstance(raw_id, bool)
                or not isinstance(raw_id, int)
                or cursor >= len(expected_titles)
                or raw_id != cursor + 1
            ):
                return None
            section_indexes.append(cursor)
            cursor += 1
        title = str(item.get("title") or expected_titles[section_indexes[0]]).strip()
        if not title:
            return None
        content = "\n\n".join(
            str(source_sections[index].get("content") or "").strip()
            for index in section_indexes
        ).strip()
        if not content:
            return None
        reconstructed.append(
            {"title": title, "content": content, "split_mode": "llm_auto"}
        )
    if cursor != len(source_sections):
        return None
    return reconstructed


def _parse_generated_slides(
    raw: str,
    *,
    source_content: str | None = None,
    allow_source_reconstruction: bool = False,
    prefer_explicit_h1: bool = False,
) -> list[dict[str, str]]:
    markdown_slides = split_by_markdown(raw)
    if markdown_slides:
        if allow_source_reconstruction and source_content and _is_source_prefix(
            raw, source_content
        ):
            reconstructed = _slides_from_source(source_content)
            if reconstructed:
                return reconstructed
        return [
            {**slide, "split_mode": "llm_auto"}
            for slide in markdown_slides
        ]

    # Faithful prompts ask for Markdown, and Codex may wrap that complete
    # document in a Markdown fence.  Unwrap it before falling through to the
    # JSON contract so a faithful source response remains a page split.
    fenced_markdown = extract_fenced_block(raw, "markdown")
    if fenced_markdown.strip() != raw.strip():
        markdown_slides = split_by_markdown(fenced_markdown)
        if markdown_slides:
            return [
                {**slide, "split_mode": "llm_auto"}
                for slide in markdown_slides
            ]

    if allow_source_reconstruction and source_content and _is_source_prefix(
        raw, source_content
    ):
        reconstructed = _slides_from_source(source_content)
        if reconstructed:
            return reconstructed

    json_text = extract_fenced_block(raw, "json")
    parsed = json.loads(json_text)
    if source_content:
        planned_slides = _slides_from_boundary_plan(
            parsed,
            source_content,
            prefer_explicit_h1=prefer_explicit_h1,
        )
        if planned_slides:
            return planned_slides
    return parsed


def generate_llm_split(deck_content: str, config: dict[str, Any], prompt: str) -> list[dict[str, str]]:
    """Generate draft slides through the selected AutoSplit provider.

    Kept as a module-level hook so tests can monkeypatch the expensive call.
    """
    faithful = str(config.get("content_mode") or "faithful") == "faithful"
    prefer_explicit_h1 = _is_public_split_execution(config)
    return _parse_generated_slides(
        _run_split(
            config,
            _prompt_for_split(config, prompt, deck_content),
            stage_id="deck-split",
        ),
        source_content=deck_content if faithful else None,
        allow_source_reconstruction=faithful,
        prefer_explicit_h1=prefer_explicit_h1,
    )


def generate_split_revision(
    deck_content: str,
    current_slides: list[dict[str, str]],
    instruction: str,
    config: dict[str, Any],
    *,
    content_mode: str,
) -> list[dict[str, str]]:
    current_markdown = "\n\n".join(
        f"## {slide['title']}\n\n{slide['content']}"
        for slide in current_slides
    )
    if content_mode == "faithful":
        mode_policy = (
            "Preserve all source wording and source order. Do not delete, summarize, "
            "correct, paraphrase, merge, or otherwise rewrite source content."
        )
    elif content_mode == "editorial":
        mode_policy = (
            "You may condense, merge, reorder, and rewrite the current split to improve "
            "the presentation narrative. Preserve every essential claim, named entity, "
            "number, definition, and item of structured evidence. Do not invent facts, "
            "opinions, examples, sources, or conclusions."
        )
    else:
        raise SplitDraftError("Auto Split content mode is invalid")
    prompt = (
        "Revise a pending presentation page split. Return only one fenced JSON "
        "array with at least two objects. Every object must contain exactly "
        "string fields `title` and `content`; do not put page wrapper headings "
        "inside `content`. Preserve all source facts and every critical "
        f"directive. {mode_policy}\n\n"
        f"# Original source\n\n{deck_content}\n\n"
        f"# Current split\n\n{current_markdown}\n\n"
        f"# User revision instruction\n\n{instruction}\n"
    )
    faithful = content_mode == "faithful"
    prefer_explicit_h1 = _is_public_split_execution(config)
    return _parse_generated_slides(
        _run_split(
            config,
            _prompt_for_split(
                config,
                prompt,
                deck_content,
                boundary_instruction=instruction,
            ),
            stage_id="deck-split-revision",
        ),
        source_content=deck_content if faithful else None,
        # A revision must carry its requested boundary plan; silently falling
        # back to the proposal would make a successful response misleading.
        allow_source_reconstruction=False,
        prefer_explicit_h1=prefer_explicit_h1,
    )


def create_split_draft(
    deck_id: int,
    config_id: int | None = None,
    *,
    mode: str = "llm",
) -> dict:
    del config_id  # Transitional clients may still send it; AutoSplit ignores it.
    deck = dbmod.get_deck(deck_id)
    if not deck:
        raise SplitDraftError("Deck not found")
    normalized_mode = str(mode or "llm").strip().lower()
    if normalized_mode == "deterministic":
        deterministic = split_by_markdown(deck["content"]) or [
            {
                "title": deck["title"],
                "content": deck["content"],
                "split_mode": "deterministic",
            }
        ]
        slides = normalize_slides(
            [{**slide, "split_mode": "deterministic"} for slide in deterministic]
        )
        db = dbmod.get_db()
        cur = db.execute(
            """INSERT INTO deck_split_drafts
               (deck_id, status, mode, model, slides_json, attempt_count)
               VALUES (?, 'pending', 'deterministic', NULL, ?, 0)""",
            (deck_id, json.dumps(slides, ensure_ascii=False)),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM deck_split_drafts WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        db.close()
        return draft_to_dict(row)
    if normalized_mode != "llm":
        raise SplitDraftError("mode must be deterministic or llm")

    try:
        llm_config = auto_split_settings.resolve_auto_split_execution()
    except auto_split_settings.AutoSplitSettingsError as e:
        raise SplitDraftError(str(e)) from e
    prompt_template = prompt_path_for_mode(llm_config["content_mode"]).read_text(
        encoding="utf-8"
    )
    prompt = prompt_template.replace("{{Context}}", deck["content"])
    db = dbmod.get_db()
    cur = db.execute(
        """INSERT INTO deck_split_drafts
           (deck_id, status, mode, model, model_profile_id, thinking_effort,
            content_mode, attempt_count, last_error_code, error_message, slides_json)
           VALUES (?, 'running', 'llm_auto', ?, ?, ?, ?, 1, NULL, NULL, '[]')""",
        (
            deck_id,
            llm_config["model"],
            llm_config["profile_id"],
            llm_config["thinking"],
            llm_config["content_mode"],
        ),
    )
    db.commit()
    draft_id = int(cur.lastrowid)
    db.close()
    return _execute_llm_draft(
        draft_id,
        deck_content=deck["content"],
        llm_config=llm_config,
        prompt=prompt,
    )


def _failure_for_exception(error: Exception) -> SplitExecutionFailure:
    if isinstance(error, SplitExecutionFailure):
        return error
    if isinstance(error, CodexGateCapacityTimeout):
        return SplitExecutionFailure(
            "resource_unavailable",
            "Auto Split resources are currently unavailable; please retry shortly",
        )
    if isinstance(error, CodexExecutableUnavailable):
        # Keep Windows paths, hashes, and signature diagnostics in private
        # evidence only. The public contract exposes a stable typed code.
        return SplitExecutionFailure(
            "executable_identity_unavailable",
            "Codex Desktop executable identity is unavailable; please retry after Codex Desktop is ready",
        )
    if isinstance(error, TimeoutError):
        return SplitExecutionFailure("timeout", "Auto Split timed out")
    if isinstance(error, json.JSONDecodeError):
        return SplitExecutionFailure("parse", "Auto Split returned invalid content")
    if isinstance(error, SplitDraftError):
        if "integrity" in str(error).lower():
            return SplitExecutionFailure(
                "integrity", "Auto Split integrity check failed"
            )
        return SplitExecutionFailure("parse", "Auto Split returned invalid content")
    return SplitExecutionFailure(
        "provider_rejected", "Auto Split provider rejected the request"
    )


def _mark_draft_failed(draft_id: int, failure: SplitExecutionFailure) -> dict:
    db = dbmod.get_db()
    db.execute(
        """UPDATE deck_split_drafts
           SET status = 'failed', last_error_code = ?, error_message = ?
           WHERE id = ? AND status = 'running'""",
        (failure.code, failure.message, draft_id),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    db.close()
    return draft_to_dict(row)


def _execute_llm_draft(
    draft_id: int,
    *,
    deck_content: str,
    llm_config: dict[str, Any],
    prompt: str,
) -> dict:
    try:
        slides = normalize_slides(
            generate_llm_split(deck_content, llm_config, prompt)
        )
        validate_split_for_mode(llm_config["content_mode"], deck_content, slides)
    except Exception as e:
        failure = _failure_for_exception(e)
        draft = _mark_draft_failed(draft_id, failure)
        raise SplitDraftExecutionError(
            failure.message,
            code=failure.code,
            draft=draft,
        ) from e

    db = dbmod.get_db()
    cur = db.execute(
        """UPDATE deck_split_drafts
           SET status = 'pending', slides_json = ?, last_error_code = NULL,
               error_message = NULL
           WHERE id = ? AND status = 'running'""",
        (json.dumps(slides, ensure_ascii=False), draft_id),
    )
    if cur.rowcount != 1:
        db.rollback()
        db.close()
        raise SplitDraftConflict("Split draft changed before execution completed")
    db.commit()
    row = db.execute(
        "SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    db.close()
    return draft_to_dict(row)


def confirm_split_draft(draft_id: int) -> dict:
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        draft = db.execute("SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)).fetchone()
        if not draft:
            raise SplitDraftError("Split draft not found")
        if draft["status"] != "pending":
            raise SplitDraftConflict("Split draft has already been confirmed")

        slides = normalize_slides(json.loads(draft["slides_json"]))
        db.execute("DELETE FROM slides WHERE deck_id = ?", (draft["deck_id"],))
        slide_ids: list[int] = []
        for position, slide in enumerate(slides, start=1):
            cur = db.execute(
                """INSERT INTO slides (deck_id, position, title, content, split_mode)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    draft["deck_id"],
                    position,
                    slide["title"],
                    slide["content"],
                    slide.get("split_mode", "llm_auto"),
                ),
            )
            slide_ids.append(cur.lastrowid)
        db.execute(
            """UPDATE deck_split_drafts
               SET status = 'confirmed', confirmed_at = datetime('now')
               WHERE id = ?""",
            (draft_id,),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "slide_ids": slide_ids,
        "slides": dbmod.list_slides(draft["deck_id"]),
    }


def confirm_image_skill_split_draft(draft_id: int) -> dict:
    """Confirm an Image Skill split and persist its server-owned cover marker.

    The normal split confirmation intentionally keeps its existing semantics.  The
    Image Skill workflow has a separate internal entry point so that the marker
    cannot be activated by caller-supplied generation metadata.  The marker and all
    confirmed content rows are committed together under one write transaction.
    """
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        draft = db.execute(
            "SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if not draft:
            raise SplitDraftError("Split draft not found")
        if draft["status"] != "pending":
            raise SplitDraftConflict("Split draft has already been confirmed")
        deck = db.execute(
            "SELECT title, content FROM decks WHERE id = ?", (draft["deck_id"],)
        ).fetchone()
        if not deck:
            raise SplitDraftError("Deck not found")

        slides = normalize_slides(json.loads(draft["slides_json"]))
        db.execute("DELETE FROM slides WHERE deck_id = ?", (draft["deck_id"],))
        slide_ids: list[int] = []
        marker = {
            "title": deck["title"],
            "content": deck["content"],
            "split_mode": "image_skill_cover",
        }
        all_slides = [marker, *slides]
        for position, slide in enumerate(all_slides, start=1):
            cur = db.execute(
                """INSERT INTO slides (deck_id, position, title, content, split_mode)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    draft["deck_id"],
                    position,
                    slide["title"],
                    slide["content"],
                    slide["split_mode"],
                ),
            )
            slide_ids.append(cur.lastrowid)
        db.execute(
            """UPDATE deck_split_drafts
               SET status = 'confirmed', confirmed_at = datetime('now')
               WHERE id = ?""",
            (draft_id,),
        )
        db.commit()
    except sqlite3.OperationalError as exc:
        db.rollback()
        if "locked" in str(exc).lower():
            raise SplitDraftConflict("Split draft confirmation is already in progress") from exc
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "slide_ids": slide_ids,
        "slides": dbmod.list_slides(draft["deck_id"]),
    }


def revise_split_draft(draft_id: int, instruction: str) -> dict:
    normalized_instruction = str(instruction or "").strip()
    if not normalized_instruction:
        raise SplitDraftError("instruction is required")

    db = dbmod.get_db()
    row = db.execute(
        "SELECT * FROM deck_split_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()
    db.close()
    if not row:
        raise SplitDraftError("Split draft not found")
    draft = dict(row)
    if draft["status"] != "pending":
        raise SplitDraftConflict("Only a pending split draft can be revised")
    if draft["mode"] != "llm_auto":
        raise SplitDraftConflict("Deterministic split drafts cannot be revised by an LLM")
    deck = dbmod.get_deck(draft["deck_id"])
    if not deck:
        raise SplitDraftError("Deck not found")

    try:
        llm_config = _resolve_draft_execution(draft)
        current_slides = normalize_slides(json.loads(draft["slides_json"]))
        revised_slides = normalize_slides(
            generate_split_revision(
                deck["content"],
                current_slides,
                normalized_instruction,
                llm_config,
                content_mode=llm_config["content_mode"],
            )
        )
        validate_split_for_mode(
            llm_config["content_mode"], deck["content"], revised_slides
        )
    except Exception as e:
        failure = _failure_for_exception(e)
        raise SplitDraftError(failure.message) from e

    db = dbmod.get_db()
    cur = db.execute(
        """UPDATE deck_split_drafts
           SET slides_json = ?
           WHERE id = ? AND status = 'pending'""",
        (
            json.dumps(revised_slides, ensure_ascii=False),
            draft_id,
        ),
    )
    if cur.rowcount != 1:
        db.rollback()
        db.close()
        raise SplitDraftConflict("Split draft changed before revision completed")
    db.commit()
    updated = db.execute(
        "SELECT * FROM deck_split_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()
    db.close()
    return draft_to_dict(updated)


def revise_split_draft_target_page_count(
    draft_id: int,
    target_page_count: int,
) -> dict:
    """Revise one pending LLM draft without invoking a model.

    The source is read from the owning deck and the update uses the original
    ``slides_json`` value as a compare-and-swap token.  A concurrent revision,
    confirmation, or deletion therefore leaves this draft untouched and
    returns the same conflict semantics as the existing revision path.
    """
    db = dbmod.get_db()
    row = db.execute(
        "SELECT * FROM deck_split_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()
    db.close()
    if not row:
        raise SplitDraftError("Split draft not found")
    draft = dict(row)
    if draft["status"] != "pending":
        raise SplitDraftConflict("Only a pending split draft can be revised")
    if draft["mode"] != "llm_auto":
        raise SplitDraftConflict("Deterministic split drafts cannot be revised")
    deck = dbmod.get_deck(draft["deck_id"])
    if not deck:
        raise SplitDraftError("Deck not found")

    revised_slides = _target_page_count_slides(
        deck["content"],
        target_page_count,
        prefer_explicit_h1=True,
    )
    original_slides_json = str(draft.get("slides_json") or "")
    db = dbmod.get_db()
    cur = db.execute(
        """UPDATE deck_split_drafts
           SET slides_json = ?
           WHERE id = ? AND status = 'pending' AND slides_json = ?""",
        (
            json.dumps(revised_slides, ensure_ascii=False),
            draft_id,
            original_slides_json,
        ),
    )
    if cur.rowcount != 1:
        db.rollback()
        db.close()
        raise SplitDraftConflict("Split draft changed before revision completed")
    db.commit()
    updated = db.execute(
        "SELECT * FROM deck_split_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()
    db.close()
    return draft_to_dict(updated)


def _resolve_draft_execution(draft: dict[str, Any]) -> dict[str, Any]:
    profile_id = draft.get("model_profile_id")
    effort = draft.get("thinking_effort")
    if not profile_id or not effort or not draft.get("model"):
        raise SplitExecutionFailure(
            "configuration", "Auto Split draft has no pinned execution identity"
        )
    try:
        content_mode = str(draft.get("content_mode") or "faithful")
        config = auto_split_settings.resolve_auto_split_execution(
            int(profile_id), str(effort), content_mode
        )
    except auto_split_settings.AutoSplitSettingsError as e:
        raise SplitExecutionFailure("configuration", str(e)) from e
    if config["model"] != draft["model"]:
        raise SplitExecutionFailure(
            "configuration", "Auto Split model profile changed after draft creation"
        )
    if config["content_mode"] != content_mode:
        raise SplitExecutionFailure(
            "configuration", "Auto Split content mode changed after draft creation"
        )
    return config


def retry_split_draft(draft_id: int) -> dict:
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute(
            """UPDATE deck_split_drafts
               SET status = 'running', attempt_count = attempt_count + 1,
                   last_error_code = NULL, error_message = NULL
               WHERE id = ? AND mode = 'llm_auto' AND status = 'failed'""",
            (draft_id,),
        )
        if cur.rowcount != 1:
            exists = db.execute(
                "SELECT id FROM deck_split_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            db.rollback()
            if not exists:
                raise SplitDraftError("Split draft not found")
            raise SplitDraftConflict("Only a failed LLM split draft can be retried")
        row = db.execute(
            "SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        db.commit()
    finally:
        db.close()

    draft = dict(row)
    deck = dbmod.get_deck(draft["deck_id"])
    try:
        if not deck:
            raise SplitExecutionFailure("configuration", "Deck not found")
        llm_config = _resolve_draft_execution(draft)
    except Exception as e:
        failure = _failure_for_exception(e)
        failed = _mark_draft_failed(draft_id, failure)
        raise SplitDraftExecutionError(
            failure.message, code=failure.code, draft=failed
        ) from e
    prompt = prompt_path_for_mode(llm_config["content_mode"]).read_text(
        encoding="utf-8"
    ).replace(
        "{{Context}}", deck["content"]
    )
    return _execute_llm_draft(
        draft_id,
        deck_content=deck["content"],
        llm_config=llm_config,
        prompt=prompt,
    )


def delete_split_draft(draft_id: int) -> bool:
    db = dbmod.get_db()
    cur = db.execute(
        "DELETE FROM deck_split_drafts WHERE id = ? AND status IN ('pending', 'failed')",
        (draft_id,),
    )
    db.commit()
    changed = cur.rowcount > 0
    db.close()
    return changed


def normalize_slides(slides: Any) -> list[dict[str, str]]:
    if not isinstance(slides, list) or not slides:
        raise SplitDraftError("LLM split produced no slides")

    normalized: list[dict[str, str]] = []
    for index, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            raise SplitDraftError("LLM split returned an invalid slide")
        title = str(slide.get("title") or f"Slide {index}").strip()
        content = str(slide.get("content") or "").strip()
        if not content:
            raise SplitDraftError("LLM split returned an empty slide")
        normalized.append(
            {
                "title": title,
                "content": content,
                "split_mode": str(slide.get("split_mode") or "llm_auto"),
            }
        )
    return normalized


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def _critical_source_lines(deck_content: str) -> list[str]:
    lines: list[str] = []
    seen = set()
    for raw_line in (deck_content or "").splitlines():
        line = _normalize_text(raw_line)
        if not line:
            continue
        lower = line.lower()
        if any(keyword in lower for keyword in INTEGRITY_KEYWORDS):
            if line not in seen:
                lines.append(line)
                seen.add(line)
    return lines


def validate_split_integrity(deck_content: str, slides: list[dict[str, str]]) -> None:
    output_text = _normalize_text(
        "\n".join(
            f"{slide.get('title', '')}\n{slide.get('content', '')}"
            for slide in slides
        )
    )
    missing_lines = [
        line
        for line in _critical_source_lines(deck_content)
        if line not in output_text
    ]
    if missing_lines:
        raise SplitDraftError(
            "Auto Split integrity check failed: missing original critical content "
            + " | ".join(missing_lines[:3])
        )


def validate_split_for_mode(
    content_mode: str,
    deck_content: str,
    slides: list[dict[str, str]],
) -> None:
    if content_mode == "faithful":
        validate_split_integrity(deck_content, slides)
    elif content_mode != "editorial":
        raise SplitDraftError("Auto Split content mode is invalid")


def draft_to_dict(row) -> dict:
    draft = dict(row)
    draft["slides"] = json.loads(draft.pop("slides_json"))
    return draft
