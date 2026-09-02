"""Shared pipeline functions for HTML-PPT-Gen.

Functions are extracted from main.py for reuse by the web backend.
The new entry point `run_pipeline_from_db` drives a run using SQLite state.
"""

import asyncio
import json
import logging
import mimetypes
import os
import re
import time
import base64
import hashlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
import xml.etree.ElementTree as ET

import requests

from backend.domain import status as run_status
from backend.services import codex_audit
from backend.services.codex_child_recovery import run_supervised_codex_child
from backend.services.codex_executable import resolve_codex_executable
from backend.services.codex_html_cleanup import clean_codex_html_output
from backend.services.codex_exec import (
    CodexAuditContext,
    clean_json_output,
    materialize_codex_result_final_text,
    run_codex_async_from_sync,
    run_codex_exec_json,
)
from backend.services.llm_concurrency import acquire_provider_slot, provider_limit_for_config
from backend.services.pipeline_context import load_run_context
from backend.services.prompt_skeleton_parity import RenderedPrompt, render_canonical_prompt
from config import (
    ARTIFACTS_DIR,
    PROMPT_DIR,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
)

log = logging.getLogger("ppt-pipeline")

TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
TRANSIENT_REQUEST_ERRORS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.Timeout,
)
LLM_CALL_STATE = threading.local()
THINKING_BUDGET_BY_TIER = {"low": 1024, "medium": 4096, "high": 10000}
IMAGE_DIRECT_PROMPT_PREFIX = "请根据以下内容生成一个 16:9、内容精美的 PPT 图像。"
CODEX_HTML_SANDBOX = "read-only"
CODEX_HTML_SCRATCH_PARENT = Path(tempfile.gettempdir()) / "ppt-gen-platform-codex-html"
NATIVE_IMAGE_3_0_DIRECTOR_MODELS = frozenset({"gpt-5.6-sol", "gpt-5.6-luna"})
NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT = "low"
NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX = "read-only"
NATIVE_IMAGE_3_0_RENDERER_MODEL = "gpt-5.6-luna"
NATIVE_IMAGE_3_0_RENDERER_REASONING_EFFORT = "low"


def _native_image_3_0_director_models() -> frozenset[str]:
    if os.environ.get("IMAGE_PPTGEN_E2E_TERRA_LOW") == "1":
        return frozenset({*NATIVE_IMAGE_3_0_DIRECTOR_MODELS, "gpt-5.6-terra"})
    return NATIVE_IMAGE_3_0_DIRECTOR_MODELS


def _native_image_3_0_renderer_models() -> frozenset[str]:
    if os.environ.get("IMAGE_PPTGEN_E2E_TERRA_LOW") == "1":
        return frozenset({NATIVE_IMAGE_3_0_RENDERER_MODEL, "gpt-5.6-terra"})
    return frozenset({NATIVE_IMAGE_3_0_RENDERER_MODEL})


@dataclass(frozen=True)
class ImageRouteOutcome:
    status: Literal["completed", "completed_with_failures", "failed"]
    reason: str | None
    completed_slide_ids: tuple[int, ...]
    failed_slide_ids: tuple[int, ...]


@dataclass(frozen=True)
class SeedPaletteLineage:
    run_id: int
    run_slide_id: int
    deck_position: int
    extraction_stage: str
    seed_png_sha256: str
    palette_sha256: str
    colors: tuple[str, ...]
    effective_color: dict[str, str]


def _palette_color_values(palette_xml: str) -> tuple[str, ...]:
    colors = tuple(
        dict.fromkeys(
            match.upper()
            for match in re.findall(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b", palette_xml)
        )
    )
    if colors:
        return colors
    fallback = palette_xml.strip()
    return (fallback,) if fallback else ()


def _seed_palette_lineage_prompt_block(lineage: SeedPaletteLineage) -> str:
    return (
        "# Seed Palette Lineage\n"
        f"{json.dumps(asdict(lineage), ensure_ascii=False, sort_keys=True)}"
    )


def thinking_budget_for_tier(thinking: str | None) -> int | None:
    if thinking is None:
        return None
    return THINKING_BUDGET_BY_TIER.get(thinking)


def apply_gemini_thinking_config(body: dict, thinking: str | None) -> None:
    budget = thinking_budget_for_tier(thinking)
    if budget is None:
        return
    body.setdefault("generationConfig", {})["thinkingConfig"] = {"thinkingBudget": budget}


def apply_openai_chat_thinking_config(body: dict, thinking: str | None) -> None:
    if thinking is None:
        return
    body["reasoning_effort"] = thinking


def image_part_for_path(image_path: str | os.PathLike[str]) -> dict:
    """Build a Gemini inline image part from a local reference image."""
    path = Path(image_path)
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        mime_type = "image/png"
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    }


def openai_image_content_for_path(image_path: str | os.PathLike[str]) -> dict:
    path = Path(image_path)
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        mime_type = "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}}


def redact_inline_payloads(value):
    if isinstance(value, list):
        return [redact_inline_payloads(item) for item in value]
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in {"inlineData", "inline_data"} and isinstance(item, dict):
                redacted[key] = {
                    **{inner_key: inner_value for inner_key, inner_value in item.items() if inner_key != "data"},
                    "data": "[IMAGE_BYTES_REDACTED]",
                }
            elif key == "image_url" and isinstance(item, dict):
                redacted[key] = {**item, "url": "[IMAGE_DATA_URL_REDACTED]"}
            else:
                redacted[key] = redact_inline_payloads(item)
        return redacted
    return value


# ---------------------------------------------------------------------------
# Prompt Template Rendering
# ---------------------------------------------------------------------------

def render_template(template_path: str, variables: dict[str, str]) -> str:
    """Load a .prompt.md file and substitute {{variable}} placeholders."""
    with open(template_path, encoding="utf-8") as f:
        template = f.read()
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        template = template.replace(placeholder, value)
    return template


def render_template_string(template_content: str, variables: dict[str, str]) -> str:
    """Substitute {{variable}} placeholders in a template string (not from file)."""
    for key, value in variables.items():
        template_content = template_content.replace("{{" + key + "}}", value)
    return template_content


def prompt_skeleton_evidence(rendered_prompt: RenderedPrompt) -> dict[str, Any]:
    return {
        "role": rendered_prompt.role,
        "template_path": rendered_prompt.template_path,
        "template_sha256": rendered_prompt.template_sha256,
        "slot_names": list(rendered_prompt.slot_names),
        "slot_sha256": dict(rendered_prompt.slot_sha256),
        "slot_hashes": dict(rendered_prompt.slot_sha256),
        "rendered_prompt_sha256": rendered_prompt.prompt_sha256,
        "parity_status": "passed",
    }


# ---------------------------------------------------------------------------
# LLM API Callers
# ---------------------------------------------------------------------------

def call_openai_compatible(
    endpoint: str,
    model: str,
    api_key: str,
    prompt: str,
    temperature: float = 1.0,
    thinking: str | None = None,
    timeout_seconds: int = 180,
    image_paths: list[str] | None = None,
) -> str:
    """Call an OpenAI-compatible chat/completions endpoint."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    message_content: str | list[dict[str, object]] = prompt
    if image_paths:
        message_content = [{"type": "text", "text": prompt}]
        message_content.extend(openai_image_content_for_path(path) for path in image_paths)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": message_content}],
        "temperature": temperature,
    }
    apply_openai_chat_thinking_config(body, thinking)
    LLM_CALL_STATE.last_request_evidence = {
        "api_type": "openai",
        "endpoint": endpoint,
        "model": model,
        "json": redact_inline_payloads(body),
    }

    log.info("Calling OpenAI-compatible API: model=%s", model)
    resp, attempts = post_json_with_retries(endpoint, headers, body, timeout_seconds=timeout_seconds)
    LLM_CALL_STATE.last_attempts = attempts
    elapsed = sum(attempt.get("elapsed_seconds", 0) for attempt in attempts)
    log.info("API response in %.1fs, status=%d", elapsed, resp.status_code)

    if resp.status_code != 200:
        log.error("API error: %s", resp.text[:500])
        resp.raise_for_status()

    data = resp.json()
    return data["choices"][0]["message"]["content"]


def call_gemini_native(
    endpoint: str,
    model: str,
    api_key: str,
    prompt: str,
    temperature: float = 1.0,
    thinking: str | None = None,
    timeout_seconds: int = 180,
    image_paths: list[str] | None = None,
    max_attempts: int = 3,
) -> str:
    """Call Google's native Gemini generateContent API."""
    native_model = model.removeprefix("google/")
    url = f"{endpoint.rstrip('/')}/{native_model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    parts = [{"text": prompt}]
    if image_paths:
        parts.extend(image_part_for_path(path) for path in image_paths)
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature},
    }
    apply_gemini_thinking_config(body, thinking)
    LLM_CALL_STATE.last_request_evidence = {
        "api_type": "gemini",
        "url": url,
        "model": model,
        "json": redact_inline_payloads(body),
    }

    log.info("Calling Gemini native API: model=%s", model)
    resp, attempts = post_json_with_retries(
        url,
        headers,
        body,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    )
    LLM_CALL_STATE.last_attempts = attempts
    elapsed = sum(attempt.get("elapsed_seconds", 0) for attempt in attempts)
    log.info("API response in %.1fs, status=%d", elapsed, resp.status_code)

    if resp.status_code != 200:
        log.error("API error: %s", resp.text[:500])
        resp.raise_for_status()

    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    # The last part contains the actual text (thinking parts come first)
    return parts[-1]["text"]


def post_json_with_retries(
    url: str,
    headers: dict,
    body: dict,
    timeout_seconds: int,
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
) -> tuple[requests.Response, list[dict[str, object]]]:
    """POST JSON with bounded retries for transient Gemini/provider failures."""
    attempts: list[dict[str, object]] = []
    max_attempts = max(1, int(max_attempts))
    for attempt in range(1, max_attempts + 1):
        start = time.time()
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout_seconds)
            elapsed = time.time() - start
            attempts.append(
                {
                    "attempt": attempt,
                    "status_code": resp.status_code,
                    "elapsed_seconds": round(elapsed, 3),
                    "transient": resp.status_code in TRANSIENT_HTTP_STATUS_CODES,
                }
            )
            if resp.status_code in TRANSIENT_HTTP_STATUS_CODES and attempt < max_attempts:
                time.sleep(base_delay_seconds * attempt)
                continue
            return resp, attempts
        except TRANSIENT_REQUEST_ERRORS as exc:
            elapsed = time.time() - start
            attempts.append(
                {
                    "attempt": attempt,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc)[:500],
                    "elapsed_seconds": round(elapsed, 3),
                    "transient": True,
                }
            )
            if attempt >= max_attempts:
                LLM_CALL_STATE.last_attempts = attempts
                raise
            time.sleep(base_delay_seconds * attempt)
    raise RuntimeError("unreachable retry state")


def call_llm(
    agent_config: dict,
    prompt: str,
    timeout_seconds: int = 180,
    agent_role: str | None = None,
    image_paths: list[str] | None = None,
    max_attempts: int = 3,
) -> str:
    """Dispatch to the correct API caller based on api_type."""
    with acquire_provider_slot(agent_config):
        api_type = agent_config["api_type"]
        if api_type == "openai":
            return call_openai_compatible(
                endpoint=agent_config["endpoint"],
                model=agent_config["model"],
                api_key=agent_config["api_key"],
                prompt=prompt,
                temperature=agent_config.get("temperature", 1.0),
                thinking=agent_config.get("thinking"),
                timeout_seconds=timeout_seconds,
                image_paths=image_paths,
            )
        elif api_type == "gemini":
            return call_gemini_native(
                endpoint=agent_config["endpoint"],
                model=agent_config["model"],
                api_key=agent_config["api_key"],
                prompt=prompt,
                temperature=agent_config.get("temperature", 1.0),
                thinking=agent_config.get("thinking"),
                timeout_seconds=timeout_seconds,
                image_paths=image_paths,
                max_attempts=max_attempts,
            )
        else:
            raise ValueError(f"Unknown api_type: {api_type}")


def call_llm_with_metadata(
    agent_config: dict,
    prompt: str,
    timeout_seconds: int = 180,
    agent_role: str | None = None,
    image_paths: list[str] | None = None,
    max_attempts: int = 3,
) -> tuple[str, list[dict[str, object]]]:
    """Call an LLM and return provider retry metadata captured by the caller."""
    LLM_CALL_STATE.last_attempts = []
    LLM_CALL_STATE.last_request_evidence = None
    call_kwargs: dict[str, object] = {
        "timeout_seconds": timeout_seconds,
        "agent_role": agent_role,
    }
    if image_paths:
        call_kwargs["image_paths"] = image_paths
    if max_attempts != 3:
        call_kwargs["max_attempts"] = max_attempts
    response_text = call_llm(agent_config, prompt, **call_kwargs)
    return response_text, list(getattr(LLM_CALL_STATE, "last_attempts", []) or [])


# ---------------------------------------------------------------------------
# Response Extraction (code fence parsing)
# ---------------------------------------------------------------------------

def extract_fenced_block(text: str, language: str = "json") -> str:
    """Extract content between ```{language} ... ``` fences.

    Fallback chain:
    1. Match ```{language} ... ```
    2. Match any ``` ... ```
    3. Return raw text
    """
    # Try language-specific fence
    pattern = rf"```{re.escape(language)}\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback: any fenced block
    pattern = r"```\w*\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        log.warning("No ```%s fence found, using first fenced block", language)
        return match.group(1).strip()

    log.warning("No code fences found at all, returning raw text")
    return text.strip()


def extract_json(raw_response: str) -> dict:
    """Extract and parse JSON from an LLM response."""
    json_text = extract_fenced_block(raw_response, "json")
    return json.loads(json_text)


def extract_html(raw_response: str) -> str:
    """Extract HTML from an LLM response."""
    return extract_fenced_block(raw_response, "html")


# ---------------------------------------------------------------------------
# File Naming
# ---------------------------------------------------------------------------

# Linux NAME_MAX is 255 bytes. Bound stems so the longest current appended
# suffix (`_image_designer_response.json`, 29 bytes) still fits. Duplicate
# position/id/counter expansions are re-sanitized after they are added.
FILENAME_COMPONENT_MAX_BYTES = 255
_FILENAME_COMPONENT_LONGEST_SUFFIX = "_image_designer_response.json"
_FILENAME_COMPONENT_LONGEST_SUFFIX_BYTES = len(
    _FILENAME_COMPONENT_LONGEST_SUFFIX.encode("utf-8")
)
_FILENAME_COMPONENT_DIGEST_HEX_LEN = 12
_FILENAME_STEM_MAX_BYTES = (
    FILENAME_COMPONENT_MAX_BYTES - _FILENAME_COMPONENT_LONGEST_SUFFIX_BYTES
)


def _utf8_byte_prefix(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if max_bytes <= 0:
        return ""
    if len(encoded) <= max_bytes:
        return text
    prefix = encoded[:max_bytes]
    while prefix:
        try:
            return prefix.decode("utf-8")
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return ""


def _bound_filename_component(sanitized: str) -> str:
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= _FILENAME_STEM_MAX_BYTES:
        return sanitized
    digest = hashlib.sha256(encoded).hexdigest()[:_FILENAME_COMPONENT_DIGEST_HEX_LEN]
    digest_part = f"_{digest}"
    prefix_budget = _FILENAME_STEM_MAX_BYTES - len(digest_part.encode("utf-8"))
    return _utf8_byte_prefix(sanitized, prefix_budget) + digest_part


def sanitize_filename(name: str) -> str:
    """Replace invalid filename characters and bound the UTF-8 component length."""
    invalid = r'[/\\:*?"<>|]'
    return _bound_filename_component(re.sub(invalid, "_", name))


def _unique_slide_artifact_stems(ordered_slides: list[dict]) -> dict[int, str]:
    """Assign unique internal stems, re-bounding after position/id/counter expansion."""
    slide_safe_titles = {
        int(slide["id"]): sanitize_filename(slide["slide_title"])
        for slide in ordered_slides
    }
    slide_title_counts: dict[str, int] = {}
    for safe_title in slide_safe_titles.values():
        slide_title_counts[safe_title] = slide_title_counts.get(safe_title, 0) + 1
    slide_stems = {
        run_slide_id: safe_title
        for run_slide_id, safe_title in slide_safe_titles.items()
        if slide_title_counts[safe_title] == 1
    }
    used_slide_stems = set(slide_stems.values())
    for slide in ordered_slides:
        run_slide_id = int(slide["id"])
        safe_title = slide_safe_titles[run_slide_id]
        if slide_title_counts[safe_title] == 1:
            continue
        position = int(slide["position"])
        expanded = f"{position:02d}_{safe_title}"
        stem = sanitize_filename(expanded)
        if stem in used_slide_stems:
            expanded = f"{expanded}_{run_slide_id}"
            stem = sanitize_filename(expanded)
            suffix = 2
            while stem in used_slide_stems:
                expanded = f"{position:02d}_{safe_title}_{run_slide_id}_{suffix}"
                stem = sanitize_filename(expanded)
                suffix += 1
        slide_stems[run_slide_id] = stem
        used_slide_stems.add(stem)
    return slide_stems


def output_dir_name(run: dict, requirement: dict, color: dict) -> str:
    parts = [f"run_{run['id']}"]
    if run.get("auto_candidate_index"):
        parts.append(f"candidate_{run['auto_candidate_index']}")
    parts.extend([requirement["title"], color["title"]])
    return sanitize_filename("_".join(parts))


def _parse_metadata_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _write_text(path: Path, value: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return str(path)


def _codex_reasoning_effort(route_metadata: dict, *configs: dict) -> str:
    route_value = route_metadata.get("reasoning_effort")
    if route_value:
        return str(route_value)
    for config in configs:
        configured = config.get("thinking") if isinstance(config, dict) else None
        if configured:
            return str(configured)
    return "high"


def _codex_slide_concurrency(route_metadata: dict, slide_count: int) -> int:
    try:
        configured = int(route_metadata.get("concurrency") or 10)
    except (TypeError, ValueError):
        configured = 10
    return max(1, min(max(1, slide_count), configured))


def _codex_scratch_root(run_id: int) -> Path:
    CODEX_HTML_SCRATCH_PARENT.mkdir(parents=True, exist_ok=True)
    try:
        CODEX_HTML_SCRATCH_PARENT.chmod(0o700)
    except OSError:
        pass
    return Path(tempfile.mkdtemp(prefix=f"run-{run_id}-", dir=str(CODEX_HTML_SCRATCH_PARENT)))


def _codex_model_config(config: dict, reasoning_effort: str) -> dict:
    merged = dict(config or {})
    merged["api_type"] = "codex_exec"
    merged["thinking"] = reasoning_effort
    return merged


class _NativeImageDirectorCommandIdentityError(ValueError):
    """The returned Director command does not match its selected profile."""


@dataclass(frozen=True)
class _NativeImageDirectorCommandIdentity:
    executable: str
    subcommand: str
    model: str
    reasoning_effort: str
    sandbox: str


def _require_native_image_director_command_identity(
    command: object,
    *,
    model: str,
) -> _NativeImageDirectorCommandIdentity:
    """Reject an executed Director command that drifted from its bound profile."""

    if not isinstance(command, (list, tuple)) or not all(
        isinstance(item, str) for item in command
    ):
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director command identity is unavailable"
        )
    values = list(command)
    if not values:
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director command identity is unavailable"
        )

    executable = values[0]
    if executable == "codex":
        # Linux keeps the historical PATH command identity.  Desktop builds
        # pass the bounded executable resolved by codex_exec instead.
        executable_is_trusted = True
    elif Path(executable).is_absolute():
        try:
            resolved_codex = resolve_codex_executable()
            executable_is_trusted = (
                Path(resolved_codex).is_absolute()
                and Path(executable).resolve() == Path(resolved_codex).resolve()
            )
        except Exception:
            # A resolver failure must not turn an arbitrary absolute path into
            # a trusted command identity.
            executable_is_trusted = False
    else:
        executable_is_trusted = False
    if not executable_is_trusted:
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director executed an untrusted executable"
        )

    index = 1
    approval_values: list[str] = []
    while index < len(values) and values[index] not in {"exec", "e"}:
        token = values[index]
        if token in {"--ask-for-approval", "-a"}:
            if index + 1 >= len(values):
                raise _NativeImageDirectorCommandIdentityError(
                    "Native Image 3.0 Director approval option is dangling"
                )
            approval_values.append(values[index + 1])
            index += 2
            continue
        if token.startswith("--ask-for-approval=") or token.startswith("-a="):
            approval_values.append(token.split("=", 1)[1])
            index += 1
            continue
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director executed an unexpected pre-exec option"
        )
    if index >= len(values):
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director exec subcommand is unavailable"
        )
    if approval_values not in ([], ["never"]):
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director approval policy does not match its contract"
        )
    index += 1

    option_aliases = {
        "--model": "model",
        "-m": "model",
        "--sandbox": "sandbox",
        "-s": "sandbox",
        "--config": "config",
        "-c": "config",
        "--cd": "path",
        "-C": "path",
        "--image": "path",
        "-i": "path",
    }
    allowed_switches = {
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
    }
    identity_values: dict[str, list[str]] = {
        "model": [],
        "sandbox": [],
        "reasoning_effort": [],
    }

    def record_option(kind: str, value: str) -> None:
        if not value:
            raise _NativeImageDirectorCommandIdentityError(
                "Native Image 3.0 Director command option is empty"
            )
        if kind in {"model", "sandbox"}:
            identity_values[kind].append(value)
            return
        if kind == "path":
            return
        key, separator, raw_value = value.partition("=")
        if not separator or not key.strip() or not raw_value.strip():
            raise _NativeImageDirectorCommandIdentityError(
                "Native Image 3.0 Director config override is malformed"
            )
        normalized_key = key.strip().lower()
        normalized_value = raw_value.strip()
        if (
            len(normalized_value) >= 2
            and normalized_value[0] == normalized_value[-1]
            and normalized_value[0] in {'"', "'"}
        ):
            normalized_value = normalized_value[1:-1]
        if normalized_key == "model_reasoning_effort":
            identity_values["reasoning_effort"].append(normalized_value)
            return
        if normalized_key in {
            "model",
            "model_provider",
            "sandbox",
            "sandbox_mode",
            "sandbox_permissions",
            "approval_policy",
            "profile",
        }:
            raise _NativeImageDirectorCommandIdentityError(
                "Native Image 3.0 Director config overrides command identity"
            )

    while index < len(values):
        token = values[index]
        if token == "-" and index == len(values) - 1:
            # Official non-interactive stdin sentinel. It is valid only as the
            # final positional value; all other positional arguments remain
            # fail-closed.
            index += 1
            continue
        if token in allowed_switches:
            index += 1
            continue
        if token in option_aliases:
            if index + 1 >= len(values):
                raise _NativeImageDirectorCommandIdentityError(
                    f"Native Image 3.0 Director executed command has no value for {token}"
                )
            record_option(option_aliases[token], values[index + 1])
            index += 2
            continue
        matched_alias = next(
            (alias for alias in option_aliases if token.startswith(f"{alias}=")),
            None,
        )
        if matched_alias is not None:
            record_option(option_aliases[matched_alias], token.split("=", 1)[1])
            index += 1
            continue
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director executed an unknown option or positional argument"
        )

    if identity_values["model"] != [model]:
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director executed model does not match its bound profile"
        )
    if identity_values["sandbox"] != [NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX]:
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director executed sandbox does not match its contract"
        )
    if identity_values["reasoning_effort"] != [
        NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT
    ]:
        raise _NativeImageDirectorCommandIdentityError(
            "Native Image 3.0 Director executed reasoning does not match its contract"
        )
    return _NativeImageDirectorCommandIdentity(
        executable="codex",
        subcommand="exec",
        model=model,
        reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
        sandbox=NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX,
    )


def run_native_image_three_zero_director(
    agent_config: dict,
    prompt: str,
    *,
    run_id: int,
    run_slide_id: int,
    stage_id: str,
    timeout_seconds: int,
    reference_image_paths: list[str] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    """Run the Native 3.0 Director through the audited Codex child boundary.

    The caller continues to own the established 3.0 prompt files, XML cleanup,
    Seed ordering, and renderer seam.  This helper owns only the selected
    allowlisted low-effort Director invocation and its Run-private audit.
    """
    from backend.services import codex_native_image

    configured_model = str(agent_config.get("model") or "").strip()
    configured_reasoning = str(
        agent_config.get("thinking") or agent_config.get("reasoning_effort") or ""
    ).strip()
    if agent_config.get("api_type") != "codex_exec":
        raise ValueError("Native Image 3.0 Director requires a Codex execution profile")
    if configured_model not in _native_image_3_0_director_models():
        raise ValueError("Native Image 3.0 Director requires an allowlisted Codex model")
    if configured_reasoning != NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT:
        raise ValueError("Native Image 3.0 Director requires low reasoning effort")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("Native Image 3.0 Director prompt is required")

    references = [Path(path) for path in (reference_image_paths or []) if path]
    private_dir = codex_native_image.native_runner_artifact_dir(
        artifacts_root=ARTIFACTS_DIR,
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=stage_id,
        attempt=1,
    )
    safe_metadata = {
        codex_audit.NATIVE_PRIVATE_EVIDENCE_KEY: codex_audit.NATIVE_PRIVATE_EVIDENCE_VALUE,
        "route": "image_3_0",
        "stage": stage_id,
    }
    observed_result = None
    execution = None
    command_identity_rejected = False

    def require_observed_command_identity(result: object) -> None:
        nonlocal command_identity_rejected
        try:
            _require_native_image_director_command_identity(
                getattr(result, "command", None),
                model=configured_model,
            )
        except _NativeImageDirectorCommandIdentityError:
            command_identity_rejected = True
            raise

    async def invoke(audit_context: CodexAuditContext):
        nonlocal observed_result
        attempt_dir = codex_native_image.native_runner_artifact_dir(
            artifacts_root=ARTIFACTS_DIR,
            run_id=run_id,
            run_slide_id=run_slide_id,
            stage_id=stage_id,
            attempt=audit_context.attempt,
        )
        result = await run_codex_exec_json(
            stage_id=stage_id,
            role="image_designer",
            prompt=prompt,
            work_dir=attempt_dir / "work",
            artifact_dir=attempt_dir,
            model=configured_model,
            reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            sandbox=NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX,
            ephemeral=False,
            image_paths=references or None,
            timeout_seconds=timeout_seconds,
            audit_context=audit_context,
        )
        observed_result = result
        require_observed_command_identity(result)
        return result

    try:
        execution = run_codex_async_from_sync(
            lambda: run_supervised_codex_child(
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                role="image_designer",
                idempotency_key=(
                    f"codex-native-image-director:run:{run_id}:slide:{run_slide_id}:stage:{stage_id}"
                ),
                invoke=invoke,
                metadata=safe_metadata,
                max_recoveries=0,
                require_final_text=True,
            )
        )
        result = execution.result
        observed_result = result
        require_observed_command_identity(result)
        if result.exit_code != 0 or result.timed_out:
            raise RuntimeError("native_image_director_failed")
        director_final_text = materialize_codex_result_final_text(result)
        if not director_final_text.strip():
            raise RuntimeError("native_image_director_empty_final")

        invocation_id = _record_codex_result(
            result,
            run_id=run_id,
            run_slide_id=run_slide_id,
            sandbox=NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX,
            model=configured_model,
            reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            output_path=result.final_response_path,
            status="result_received",
            metadata=safe_metadata,
        )
        _write_codex_request_evidence(
            result,
            prompt=prompt,
            prompt_skeleton={"role": "image_designer", "rendered_prompt_sha256": _sha256_text(prompt)},
            invocation_id=invocation_id,
            sandbox=NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX,
            model=configured_model,
            reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            output_path=result.final_response_path,
            status="result_received",
        )
        cli_binary, cli_version, binary_sha256 = codex_native_image._native_cli_identity(
            list(result.command), agent_config
        )
        codex_native_image.collect_native_image_evidence(
            director_audit=True,
            stdout_events=codex_native_image._stdout_events_from_stream_summary(result),
            codex_home=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
            private_dir=codex_native_image.native_runner_artifact_dir(
                artifacts_root=ARTIFACTS_DIR,
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                attempt=execution.attempt_count,
            ),
            requested_model=configured_model,
            requested_reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            actual_model=configured_model,
            actual_reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            cli_binary=cli_binary,
            cli_version=cli_version,
            binary_sha256=binary_sha256,
            attempt=execution.attempt_count,
            terminal_state="result_received",
            retry=False,
            error=None,
            timeout=False,
            skip=False,
            fallback_used=False,
            invocation_id=invocation_id,
        )
        if not execution.complete_projection():
            raise RuntimeError("native_image_director_projection_failed")
        return director_final_text, [{"attempt": execution.attempt_count, "status": "result_received"}]
    except Exception as exc:
        if observed_result is not None and not command_identity_rejected:
            codex_native_image._native_failure_evidence(
                result=observed_result,
                agent_config=agent_config,
                private_dir=private_dir,
                timeout=bool(getattr(observed_result, "timed_out", False)),
            )
        if execution is not None:
            execution.fail_unrecoverable("native_image_director_failed")
        raise RuntimeError("native_image_director_failed") from exc


def run_native_image_three_zero_palette(
    agent_config: dict,
    *,
    run_id: int,
    run_slide_id: int,
    seed_png_path: str | Path,
    timeout_seconds: int,
) -> str:
    """Extract the Native 3.0 Seed palette through one audited Codex child."""
    from backend.services import codex_native_image, color_extraction

    configured_model = str(agent_config.get("model") or "").strip()
    configured_reasoning = str(
        agent_config.get("thinking") or agent_config.get("reasoning_effort") or ""
    ).strip()
    if agent_config.get("api_type") != "codex_exec":
        raise ValueError("Native Image 3.0 Palette requires a Codex execution profile")
    if configured_model not in _native_image_3_0_director_models():
        raise ValueError("Native Image 3.0 Palette requires an allowlisted Codex model")
    if configured_reasoning != NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT:
        raise ValueError("Native Image 3.0 Palette requires low reasoning effort")
    seed_path = Path(seed_png_path)
    if not seed_path.is_file() or seed_path.stat().st_size == 0:
        raise ValueError("Native Image 3.0 Palette requires a non-empty Seed PNG")

    stage_id = "seed-palette-extraction"
    seed_sha256 = hashlib.sha256(seed_path.read_bytes()).hexdigest()
    prompt = color_extraction.PROMPT_PATH.read_text(encoding="utf-8")
    private_dir = codex_native_image.native_runner_artifact_dir(
        artifacts_root=ARTIFACTS_DIR,
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=stage_id,
        attempt=1,
    )
    safe_metadata: dict[str, object] = {
        codex_audit.NATIVE_PRIVATE_EVIDENCE_KEY: codex_audit.NATIVE_PRIVATE_EVIDENCE_VALUE,
        "route": "image_3_0",
        "stage": stage_id,
        "seed_png_sha256": seed_sha256,
    }
    observed_result = None
    execution = None
    command_identity_rejected = False

    def require_observed_command_identity(result: object) -> None:
        nonlocal command_identity_rejected
        try:
            _require_native_image_director_command_identity(
                getattr(result, "command", None),
                model=configured_model,
            )
        except _NativeImageDirectorCommandIdentityError:
            command_identity_rejected = True
            raise

    async def invoke(audit_context: CodexAuditContext):
        nonlocal observed_result
        attempt_dir = codex_native_image.native_runner_artifact_dir(
            artifacts_root=ARTIFACTS_DIR,
            run_id=run_id,
            run_slide_id=run_slide_id,
            stage_id=stage_id,
            attempt=audit_context.attempt,
        )
        result = await run_codex_exec_json(
            stage_id=stage_id,
            role="palette_analysis",
            prompt=prompt,
            work_dir=attempt_dir / "work",
            artifact_dir=attempt_dir,
            model=configured_model,
            reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            sandbox=NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX,
            ephemeral=False,
            image_paths=[seed_path],
            timeout_seconds=timeout_seconds,
            audit_context=audit_context,
        )
        observed_result = result
        require_observed_command_identity(result)
        return result

    try:
        execution = run_codex_async_from_sync(
            lambda: run_supervised_codex_child(
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                role="palette_analysis",
                idempotency_key=(
                    f"codex-native-image-palette:run:{run_id}:slide:{run_slide_id}:stage:{stage_id}"
                ),
                invoke=invoke,
                metadata=safe_metadata,
                max_recoveries=0,
                require_final_text=True,
            )
        )
        result = execution.result
        observed_result = result
        require_observed_command_identity(result)
        if result.exit_code != 0 or result.timed_out:
            raise RuntimeError("native_image_palette_failed")
        palette_xml = materialize_codex_result_final_text(result)
        palette_xml = color_extraction.validate_palette_xml(palette_xml)
        palette_colors = _palette_color_values(palette_xml)
        safe_metadata.update(
            {
                "palette_sha256": _sha256_text(palette_xml),
                "palette_colors": list(palette_colors),
            }
        )
        invocation_id = _record_codex_result(
            result,
            run_id=run_id,
            run_slide_id=run_slide_id,
            sandbox=NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX,
            model=configured_model,
            reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            output_path=result.final_response_path,
            status="result_received",
            metadata=safe_metadata,
        )
        _write_codex_request_evidence(
            result,
            prompt=prompt,
            prompt_skeleton={
                "role": "palette_analysis",
                "rendered_prompt_sha256": _sha256_text(prompt),
                "seed_png_sha256": seed_sha256,
            },
            invocation_id=invocation_id,
            sandbox=NATIVE_IMAGE_3_0_DIRECTOR_SANDBOX,
            model=configured_model,
            reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            output_path=result.final_response_path,
            status="result_received",
            extra_evidence={"seed_png_path": str(seed_path)},
        )
        cli_binary, cli_version, binary_sha256 = codex_native_image._native_cli_identity(
            list(result.command), agent_config
        )
        codex_native_image.collect_native_image_evidence(
            director_audit=True,
            stdout_events=codex_native_image._stdout_events_from_stream_summary(result),
            codex_home=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
            private_dir=codex_native_image.native_runner_artifact_dir(
                artifacts_root=ARTIFACTS_DIR,
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                attempt=execution.attempt_count,
            ),
            requested_model=configured_model,
            requested_reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            actual_model=configured_model,
            actual_reasoning_effort=NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT,
            cli_binary=cli_binary,
            cli_version=cli_version,
            binary_sha256=binary_sha256,
            attempt=execution.attempt_count,
            terminal_state="result_received",
            retry=False,
            error=None,
            timeout=False,
            skip=False,
            fallback_used=False,
            invocation_id=invocation_id,
        )
        if not execution.complete_projection():
            raise RuntimeError("native_image_palette_projection_failed")
        return palette_xml
    except Exception as exc:
        if observed_result is not None and not command_identity_rejected:
            codex_native_image._native_failure_evidence(
                result=observed_result,
                agent_config=agent_config,
                private_dir=private_dir,
                timeout=bool(getattr(observed_result, "timed_out", False)),
            )
        if execution is not None:
            execution.fail_unrecoverable("native_image_palette_failed")
        raise RuntimeError("native_image_palette_failed") from exc


def _record_codex_result(
    result,
    *,
    run_id: int,
    run_slide_id: int | None,
    sandbox: str,
    model: str,
    reasoning_effort: str,
    output_path: str | Path | None,
    status: str,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    live_invocation_id = getattr(result, "invocation_id", None)
    if live_invocation_id is not None:
        return codex_audit.finalize_codex_invocation(
            invocation_id=int(live_invocation_id),
            output_path=str(output_path) if output_path else None,
            ended_at=result.ended_at,
            elapsed_ms=result.elapsed_ms,
            exit_code=result.exit_code,
            status=status,
            error_message=error_message,
            metadata=metadata,
        )
    return codex_audit.record_codex_invocation(
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=result.stage_id,
        role=result.role,
        attempt=1,
        command=result.command,
        cwd=str(result.cwd),
        sandbox=sandbox,
        model=model,
        reasoning_effort=reasoning_effort,
        prompt_sha256=result.prompt_sha256,
        raw_jsonl_path=str(result.raw_jsonl_path),
        observed_jsonl_path=str(result.observed_jsonl_path),
        output_path=str(output_path) if output_path else None,
        started_at=result.started_at,
        ended_at=result.ended_at,
        elapsed_ms=result.elapsed_ms,
        exit_code=result.exit_code,
        status=status,
        error_message=error_message,
        metadata=metadata,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _codex_transcript_summary(result) -> dict[str, Any]:
    summary = getattr(result, "stream_summary", None)
    if summary is None:
        # Historical test/recovery doubles predate the producer summary. Live
        # executions always take the branch below and never reopen raw stdout.
        events: list[dict[str, Any]] = []
        thread_ids: list[str] = []
        observed_path = Path(result.observed_jsonl_path)
        if observed_path.exists():
            for line_number, line in enumerate(observed_path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    wrapper = json.loads(line)
                except json.JSONDecodeError:
                    events.append({"sequence": len(events) + 1, "line_number": line_number, "parse_error": "invalid_json"})
                    continue
                event = wrapper.get("event") if isinstance(wrapper, dict) else None
                event = event if isinstance(event, dict) else {}
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str) and thread_id and thread_id not in thread_ids:
                    thread_ids.append(thread_id)
                events.append(
                    {
                        "sequence": len(events) + 1,
                        "observed_at": wrapper.get("observed_at") if isinstance(wrapper, dict) else None,
                        "event_type": event.get("type") if isinstance(event.get("type"), str) else None,
                        "thread_id": thread_id if isinstance(thread_id, str) else None,
                        "item_id": item.get("id") if isinstance(item.get("id"), str) else None,
                        "item_type": item.get("type") if isinstance(item.get("type"), str) else None,
                    }
                )
        final_text = materialize_codex_result_final_text(result)
        return {
            "event_count": len(events),
            "thread_ids": thread_ids,
            "events": events,
            "final_text_sha256": _sha256_text(final_text) if final_text else None,
        }
    events = list(getattr(summary, "event_projections", ()) or ())
    thread_ids = list(getattr(summary, "thread_ids", ()) or ())
    if not thread_ids:
        thread_id = getattr(summary, "thread_id", None)
        if isinstance(thread_id, str) and thread_id:
            thread_ids.append(thread_id)
    final_capture = getattr(summary, "final_capture", None)
    final_text = None if final_capture is not None else materialize_codex_result_final_text(result)
    return {
        "event_count": getattr(summary, "complete_record_count", len(events)),
        "thread_ids": thread_ids,
        "events": events,
        "final_text_sha256": (
            final_capture.text_sha256
            if final_capture is not None
            else (_sha256_text(final_text) if final_text else None)
        ),
    }


def _write_codex_request_evidence(
    result,
    *,
    prompt: str,
    prompt_skeleton: dict[str, Any],
    invocation_id: int,
    sandbox: str,
    model: str,
    reasoning_effort: str,
    output_path: str | Path | None,
    status: str,
    error_message: str | None = None,
    extra_evidence: dict[str, Any] | None = None,
) -> Path:
    request_path = Path(result.raw_jsonl_path).with_name("codex.request.json")
    command_path = Path(getattr(result, "command_path", Path(result.raw_jsonl_path).with_name("codex.command.json")))
    final_response_path = getattr(result, "final_response_path", None)
    transcript_path = getattr(result, "transcript_path", None)
    payload = {
        "schema_version": 1,
        "provider": "codex_exec",
        "stage_id": result.stage_id,
        "role": result.role,
        "codex_invocation_id": invocation_id,
        "request": {
            "command": list(result.command),
            "command_path": str(command_path) if command_path.exists() else None,
            "cwd": str(result.cwd),
            "sandbox": sandbox,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "rendered_prompt": prompt,
            "prompt_path": str(result.prompt_path),
            "prompt_sha256": result.prompt_sha256,
            "prompt_skeleton": prompt_skeleton,
        },
        "evidence": {
            "status": status,
            "error_message": error_message,
            "exit_code": result.exit_code,
            "started_at": result.started_at,
            "ended_at": result.ended_at,
            "elapsed_ms": result.elapsed_ms,
            "peak_rss_kb": result.peak_rss_kb,
            "raw_jsonl_path": str(result.raw_jsonl_path),
            "observed_jsonl_path": str(result.observed_jsonl_path),
            "stderr_path": str(result.stderr_path),
            "command_path": str(command_path) if command_path.exists() else None,
            "final_response_path": str(final_response_path) if final_response_path else None,
            "transcript_path": str(transcript_path) if transcript_path else None,
            "output_path": str(output_path) if output_path else None,
            "hashes": {
                "rendered_prompt_sha256": _sha256_text(prompt),
                "prompt_file_sha256": _sha256_path(result.prompt_path),
                "command_sha256": _sha256_path(command_path),
                "command_file_sha256": _sha256_path(command_path),
                "raw_jsonl_sha256": _sha256_path(result.raw_jsonl_path),
                "observed_jsonl_sha256": _sha256_path(result.observed_jsonl_path),
                "stderr_sha256": _sha256_path(result.stderr_path),
                "final_response_sha256": _sha256_path(final_response_path),
                "transcript_sha256": _sha256_path(transcript_path),
                "output_sha256": _sha256_path(output_path),
            },
            "transcript": _codex_transcript_summary(result),
        },
    }
    if extra_evidence:
        payload["evidence"].update(extra_evidence)
    _write_text(request_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return request_path


def _codex_stage_extra(result, invocation_id: int, request_path: str | Path | None = None) -> dict[str, Any]:
    extra = {
        "codex_invocation_id": invocation_id,
        "codex_prompt_path": str(result.prompt_path),
        "codex_raw_jsonl_path": str(result.raw_jsonl_path),
        "codex_observed_jsonl_path": str(result.observed_jsonl_path),
        "codex_stderr_path": str(result.stderr_path),
        "codex_command_path": str(result.command_path) if getattr(result, "command_path", None) else None,
        "codex_final_response_path": str(result.final_response_path) if getattr(result, "final_response_path", None) else None,
        "codex_transcript_path": str(result.transcript_path) if getattr(result, "transcript_path", None) else None,
        "codex_elapsed_ms": result.elapsed_ms,
        "codex_exit_code": result.exit_code,
        "codex_peak_rss_kb": result.peak_rss_kb,
    }
    if request_path:
        extra["codex_request_path"] = str(request_path)
    return extra


def _summarize_codex_slide_results(results: list[dict]) -> dict[str, Any]:
    failure_count = sum(1 for item in results if item["status"] == run_status.FAILED)
    completed_count = sum(1 for item in results if item["status"] == run_status.COMPLETED)
    attempt_count = sum(int(item.get("attempt_count") or 0) for item in results)
    if not results or failure_count == 0:
        status = run_status.COMPLETED
    elif completed_count > 0:
        status = run_status.COMPLETED_WITH_FAILURES
    else:
        status = run_status.FAILED
    return {
        "status": status,
        "failure_count": failure_count,
        "attempt_count": attempt_count,
        "per_slide_statuses": results,
    }


def _set_run_terminal_status(run_id: int, status: str, error_message: str | None = None) -> None:
    import db as dbmod

    conn = dbmod.get_db()
    conn.execute(
        "UPDATE runs SET status = ?, error_message = ?, completed_at = datetime('now') WHERE id = ?",
        (status, error_message, run_id),
    )
    conn.commit()
    conn.close()


def _machine_qa_summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pass_count = sum(1 for row in rows if row.get("verdict") == "pass")
    fail_count = sum(1 for row in rows if row.get("verdict") == "fail")
    skipped_count = sum(1 for row in rows if row.get("verdict") == "skipped")
    status = "fail" if fail_count else "skipped" if skipped_count and not pass_count else "pass" if rows else "empty"
    return {
        "status": status,
        "total": len(rows),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "skipped_count": skipped_count,
    }


def _codex_html_machine_qa_evidence_from_rows(
    rows: list[dict[str, Any]],
    *,
    base: dict[str, Any] | None = None,
    status: str = "complete",
) -> dict[str, Any]:
    summary = _machine_qa_summary_from_rows(rows)
    return {
        "source": "codex_html_auto_machine_qa",
        "status": status,
        "error": None,
        "evaluation_id": (base or {}).get("evaluation_id"),
        "variant_id": (base or {}).get("variant_id"),
        "attempt_id": (base or {}).get("attempt_id"),
        "total": summary["total"],
        "pass_count": summary["pass_count"],
        "fail_count": summary["fail_count"],
        "skipped_count": summary["skipped_count"],
        "verdict_status": summary["status"],
        "run_slide_ids": [row.get("run_slide_id") for row in rows if row.get("run_slide_id") is not None],
    }


def _codex_html_cleanup_prompt(*, slide: dict, html: str, qa_row: dict) -> str:
    issues = qa_row.get("issues") if isinstance(qa_row.get("issues"), list) else []
    return (
        "You are repairing one generated HTML presentation slide after Machine QA found visual defects.\n"
        "Return JSON only with a single string field named html. The html value must be one complete, self-contained "
        "HTML document for a 1280x720 slide.\n\n"
        "Hard requirements:\n"
        "- Preserve the source slide's core meaning, but aggressively summarize secondary details.\n"
        "- Fix all listed overlap, truncation, clipping, spacing, and missing-icon issues.\n"
        "- All visible content must fit inside 1280x720 at first render with no scrolling.\n"
        "- Use grid/flex for primary text layout; do not use absolute positioning for text-bearing content.\n"
        "- Prefer inline SVG or text badges for icons; icon placeholders must never render empty.\n"
        "- Do not use external raster images or background-image URLs.\n\n"
        f"Slide position: {slide.get('position')}\n"
        f"Slide title: {slide.get('slide_title') or slide.get('title') or slide.get('slide_title_snapshot') or ''}\n"
        "Machine QA issues:\n"
        f"{json.dumps(issues, ensure_ascii=False, indent=2)}\n\n"
        "Original slide source content:\n"
        f"{str(slide.get('slide_content') or '')[:12000]}\n\n"
        "Current HTML to repair:\n"
        "```html\n"
        f"{html[:30000]}\n"
        "```\n"
    )


def _codex_html_cleanup_model_evidence(profile: dict[str, Any], agent_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "evaluation_visual_qa",
        "profile_id": profile.get("id"),
        "profile_name": profile.get("name"),
        "api_type": agent_config.get("api_type"),
        "endpoint": agent_config.get("endpoint"),
        "model": agent_config.get("model"),
        "thinking": agent_config.get("thinking"),
        "credential_present": bool(agent_config.get("api_key")),
        "credential_provenance": "model_profile_redacted",
    }


def _resolve_codex_html_gemini_cleanup_config() -> tuple[dict[str, Any], dict[str, Any]]:
    from backend.services import model_profiles

    profiles = [
        profile
        for profile in model_profiles.list_profiles(role="evaluation_visual_qa", status="active")
        if str(profile.get("api_type") or "").strip().lower() == "gemini"
    ]
    if not profiles:
        try:
            profile_id = model_profiles.ensure_evaluation_visual_qa_profile()
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        profile = model_profiles.get_profile(profile_id)
    else:
        profile = next((item for item in profiles if item.get("api_key")), profiles[0])
    if not profile:
        raise RuntimeError("No Gemini cleanup profile is configured.")
    agent_config = model_profiles.profile_to_agent_config(int(profile["id"]))
    if str(agent_config.get("api_type") or "").strip().lower() != "gemini":
        raise RuntimeError("Codex HTML cleanup requires a Gemini model profile.")
    if not agent_config.get("api_key"):
        raise RuntimeError("No Gemini API key is configured for Codex HTML cleanup.")
    return profile, agent_config


def _write_gemini_cleanup_request(
    request_path: Path,
    *,
    prompt: str,
    response_path: Path,
    html_path: Path,
    screenshot_path: str | None,
    model: dict[str, Any],
    trigger: dict[str, Any],
    provider_request: dict | None,
    attempts: list[dict[str, object]],
    cleanup: dict[str, Any],
) -> None:
    payload = {
        "schema_version": 1,
        "provider": "gemini",
        "role": "codex_html_cleanup",
        "request": {
            "rendered_prompt": prompt,
            "rendered_prompt_sha256": _sha256_text(prompt),
            "model": model,
            "provider_request": provider_request,
        },
        "evidence": {
            "status": "applied",
            "trigger": trigger,
            "response_path": str(response_path),
            "html_path": str(html_path),
            "screenshot_path": screenshot_path,
            "provider_attempts": attempts,
            "cleanup": cleanup,
            "hashes": {
                "response_sha256": _sha256_path(response_path),
                "html_sha256": _sha256_path(html_path),
                "screenshot_sha256": _sha256_path(screenshot_path),
            },
        },
    }
    _write_text(request_path, json.dumps(codex_audit.redact_audit_value(payload), ensure_ascii=False, indent=2))


async def _apply_codex_html_gemini_cleanup(
    *,
    context,
    output_root: Path,
    machine_qa_evidence: dict[str, Any],
    failed_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    import db as dbmod

    profile, agent_config = _resolve_codex_html_gemini_cleanup_config()
    model_evidence = _codex_html_cleanup_model_evidence(profile, agent_config)
    slides_by_id = {int(slide["id"]): slide for slide in dbmod.list_run_slides(int(context.run["id"]))}
    applied: list[dict[str, Any]] = []

    for row in failed_rows:
        run_slide_id = row.get("run_slide_id")
        if run_slide_id is None or int(run_slide_id) not in slides_by_id:
            continue
        slide = slides_by_id[int(run_slide_id)]
        original_html = str(slide.get("clean_html") or "")
        original_screenshot_path = slide.get("screenshot_path")
        if not original_html or not original_screenshot_path:
            continue
        position = int(slide.get("position") or 0)
        safe_title = sanitize_filename(f"{position:02d}_{slide.get('slide_title') or slide.get('title') or 'slide'}")
        prompt_path = output_root / f"{safe_title}_gemini_cleanup_prompt.txt"
        response_path = output_root / f"{safe_title}_gemini_cleanup_raw.txt"
        repaired_html_path = output_root / f"{safe_title}_gemini_cleanup.html"
        request_path = output_root / f"{safe_title}_gemini_cleanup_request.json"

        prompt = _codex_html_cleanup_prompt(slide=slide, html=original_html, qa_row=row)
        _write_text(prompt_path, prompt)
        raw_response, attempts = call_llm_with_metadata(
            agent_config,
            prompt,
            timeout_seconds=context.timeout_seconds,
            agent_role="codex_html_cleanup",
            image_paths=[str(original_screenshot_path)],
        )
        _write_text(response_path, raw_response)
        provider_request = getattr(LLM_CALL_STATE, "last_request_evidence", None)
        cleanup_result = clean_codex_html_output(raw_response)
        cleanup_metadata = cleanup_result.to_metadata()
        _write_text(repaired_html_path, cleanup_result.html)
        repaired_screenshot_path = await async_screenshot_html_file(
            str(repaired_html_path),
            VIEWPORT_WIDTH,
            VIEWPORT_HEIGHT,
        )
        trigger = {
            "machine_qa_evaluation_id": machine_qa_evidence.get("evaluation_id"),
            "machine_qa_attempt_id": machine_qa_evidence.get("attempt_id"),
            "machine_qa_row_id": row.get("id"),
            "machine_qa_verdict": row.get("verdict"),
            "issues": row.get("issues") or [],
            "original_html_path": slide.get("html_path"),
            "original_screenshot_path": original_screenshot_path,
            "original_html_sha256": _sha256_text(original_html),
        }
        cleanup_evidence = {
            "status": "applied",
            "source": "codex_html_gemini_cleanup",
            "trigger": trigger,
            "model": model_evidence,
            "prompt_path": str(prompt_path),
            "request_path": str(request_path),
            "response_path": str(response_path),
            "html_path": str(repaired_html_path),
            "screenshot_path": repaired_screenshot_path,
            "provider_attempts": attempts,
            "provider_request": provider_request,
            "cleanup": cleanup_metadata,
            "hashes": {
                "prompt_sha256": _sha256_text(prompt),
                "response_sha256": _sha256_path(response_path),
                "html_sha256": _sha256_path(repaired_html_path),
                "screenshot_sha256": _sha256_path(repaired_screenshot_path),
            },
        }
        _write_gemini_cleanup_request(
            request_path,
            prompt=prompt,
            response_path=response_path,
            html_path=repaired_html_path,
            screenshot_path=repaired_screenshot_path,
            model=model_evidence,
            trigger=trigger,
            provider_request=provider_request,
            attempts=attempts,
            cleanup=cleanup_metadata,
        )

        artifacts = _parse_metadata_dict(slide.get("stage_artifacts"))
        artifacts["gemini_cleanup"] = cleanup_evidence
        artifacts.setdefault("html_agent", {})["gemini_cleanup"] = {
            "status": "applied",
            "request_path": str(request_path),
            "response_path": str(response_path),
            "html_path": str(repaired_html_path),
            "screenshot_path": repaired_screenshot_path,
            "cleanup": cleanup_metadata,
        }
        artifacts.setdefault("codex_html", {})["gemini_cleanup"] = {
            "status": "applied",
            "source": "codex_html_gemini_cleanup",
            "request_path": str(request_path),
            "html_path": str(repaired_html_path),
            "screenshot_path": repaired_screenshot_path,
        }
        request_chain = artifacts.get("request_chain")
        if isinstance(request_chain, dict):
            actual = request_chain.setdefault("actual_evidence", {})
            if isinstance(actual, dict):
                actual["gemini_cleanup"] = {
                    "status": "applied",
                    "request_path": str(request_path),
                    "response_path": str(response_path),
                    "html_path": str(repaired_html_path),
                    "screenshot_path": repaired_screenshot_path,
                    "cleanup": cleanup_metadata,
                }
            planned = request_chain.setdefault("planned_chain", [])
            if isinstance(planned, list) and "gemini_cleanup" not in planned:
                planned.append("gemini_cleanup")
            stages = request_chain.setdefault("stages", [])
            if isinstance(stages, list):
                stages.append(
                    request_chain_stage_evidence(
                        stage_id="gemini-cleanup",
                        stage_name="Gemini HTML Cleanup",
                        role="codex_html_cleanup",
                        prompt_role=None,
                        model_config=model_evidence,
                        prompt_path=str(prompt_path),
                        request_path=str(request_path),
                        response_path=str(response_path),
                        artifact_path=str(repaired_html_path),
                        provider_request=provider_request,
                        health="complete",
                        extra={
                            "cleanup": cleanup_metadata,
                            "trigger": {
                                "machine_qa_verdict": row.get("verdict"),
                                "issue_count": len(row.get("issues") or []),
                            },
                            "screenshot_path": repaired_screenshot_path,
                        },
                    )
                )
            request_chain["health"] = "complete"
        artifacts["evidence_health"] = "complete"
        dbmod.update_run_slide(
            int(slide["id"]),
            clean_html=cleanup_result.html,
            html_path=str(repaired_html_path),
            screenshot_path=repaired_screenshot_path,
            stage_artifacts=json.dumps(codex_audit.redact_audit_value(artifacts), ensure_ascii=False),
        )
        applied.append(
            {
                "run_slide_id": slide["id"],
                "position": position,
                "request_path": str(request_path),
                "html_path": str(repaired_html_path),
                "screenshot_path": repaired_screenshot_path,
                "cleanup": cleanup_metadata,
            }
        )

    return {
        "source": "codex_html_gemini_cleanup",
        "status": "applied" if applied else "skipped",
        "model": model_evidence,
        "applied_count": len(applied),
        "slides": applied,
    }


def _mark_codex_html_pre_slide_failure(
    context,
    output_dir: str,
    *,
    route_metadata: dict,
    reasoning_effort: str,
    error_message: str,
    designer_result=None,
    designer_invocation_id: int | None = None,
    designer_stage_extra: dict[str, Any] | None = None,
    designer_request_path: str | None = None,
) -> None:
    import db as dbmod

    run = context.run
    deck = context.deck
    requirement = context.requirement
    color = context.color
    design_raw_path = Path(output_dir) / "design_principle_raw.txt"
    designer_stage = None
    if designer_result is not None:
        designer_stage = request_chain_stage_evidence(
            stage_id="design-principle-generation",
            stage_name="Design Principle Generation",
            role="designer",
            prompt_role="designer",
            model_config=_codex_model_config(context.designer_config, reasoning_effort),
            prompt_path=str(designer_result.prompt_path),
            request_path=designer_request_path or str(designer_result.prompt_path),
            response_path=str(design_raw_path),
            provider_request=None,
            health="failed",
            extra=designer_stage_extra or {},
        )

    slide_results: list[dict[str, Any]] = []
    for rs in context.run_slides:
        slide_result = {
            "run_slide_id": rs["id"],
            "position": int(rs["position"]),
            "status": run_status.FAILED,
            "attempt_count": 0,
        }
        slide_results.append(slide_result)
        stages = [designer_stage] if designer_stage else []
        failure_artifacts = {
            "error": error_message,
            "designer": {
                "raw_path": str(design_raw_path) if designer_result is not None else None,
                "codex_invocation_id": designer_invocation_id,
                **(designer_stage_extra or {}),
            },
            "codex_html": {
                "final_status": run_status.FAILED,
                "attempt_count": 0,
                "failure_count": 1,
                "designer_invocation_id": designer_invocation_id,
            },
            "request_chain": request_chain_evidence(
                strategy="codex_html",
                slide=rs,
                prompt_role="html_agent",
                planned_chain=["designer", "html_agent", "screenshot"],
                actual_evidence={
                    "design_principle_raw_path": str(design_raw_path) if designer_result is not None else None,
                    "error": error_message,
                },
                model=context.html_agent_config.get("model"),
                stages=stages,
                health="failed",
                reason=error_message,
            ),
            "dependencies": slide_dependency_evidence(
                deck=deck,
                requirement=requirement,
                color=color,
                slide=rs,
                prompt_role="html_agent",
                model_config=_codex_model_config(context.html_agent_config, reasoning_effort),
            ),
            "evidence_health": "failed",
        }
        dbmod.update_run_slide(
            rs["id"],
            status=run_status.FAILED,
            error_message=error_message,
            stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
        )

    summary = _summarize_codex_slide_results(slide_results)
    summary["designer"] = {
        "status": run_status.FAILED,
        "error_message": error_message,
        "codex_invocation_id": designer_invocation_id,
    }
    run_stage_artifacts = {
        "codex_html": {
            "status": summary["status"],
            "failure_count": summary["failure_count"],
            "attempt_count": summary["attempt_count"],
            "per_slide_statuses": summary["per_slide_statuses"],
            "reasoning_effort": reasoning_effort,
            "designer_invocation_id": designer_invocation_id,
            "failure_stage": "designer",
        },
        "designer": {
            "raw_path": str(design_raw_path) if designer_result is not None else None,
            "codex_invocation_id": designer_invocation_id,
            **(designer_stage_extra or {}),
        },
    }
    update_fields: dict[str, Any] = {
        "stage_artifacts": json.dumps(run_stage_artifacts, ensure_ascii=False),
        "model_call_metadata": json.dumps(summary, ensure_ascii=False),
    }
    if designer_result is not None:
        update_fields["design_principle_raw"] = materialize_codex_result_final_text(designer_result)
    dbmod.update_run(run["id"], **update_fields)
    _set_run_terminal_status(run["id"], run_status.FAILED, error_message)
    dbmod.update_batch_statuses()


async def _run_codex_html_route_async(context, output_dir: str) -> None:
    import db as dbmod

    run = context.run
    deck = context.deck
    requirement = context.requirement
    color = context.color
    run_slides = list(context.run_slides)
    route_metadata = _parse_metadata_dict(run.get("route_metadata"))
    reasoning_effort = _codex_reasoning_effort(route_metadata, context.designer_config, context.html_agent_config)
    sandbox = CODEX_HTML_SANDBOX
    extra_config = None
    model_designer = str(context.designer_config.get("model") or "")
    model_html = str(context.html_agent_config.get("model") or "")
    output_root = Path(output_dir)
    codex_artifact_root = output_root / "codex"
    scratch_root = _codex_scratch_root(int(run["id"]))
    scratch_root.mkdir(parents=True, exist_ok=True)
    _write_text(
        scratch_root / "AGENTS.md",
        "You are running inside an isolated scratch directory for one audited HTML generation call.\n"
        "Do not read or write files outside this directory. Return only the requested final artifact.\n",
    )

    designer_vars = {
        "Deck-Full-Content": context.confirmed_full_content,
        "Deck-User-Requirement": requirement["content"],
        "Deck-Required-color": color["content"],
    }
    designer_rendered_prompt = render_canonical_prompt("designer", designer_vars)
    designer_prompt = designer_rendered_prompt.prompt
    designer_prompt_skeleton = prompt_skeleton_evidence(designer_rendered_prompt)

    async def invoke_designer(audit_context: CodexAuditContext):
        return await run_codex_exec_json(
            stage_id="deck-design-director",
            role="designer",
            prompt=designer_prompt,
            work_dir=scratch_root / "designer" / f"attempt-{audit_context.attempt}",
            artifact_dir=codex_artifact_root / "designer" / f"attempt-{audit_context.attempt}",
            model=model_designer,
            reasoning_effort=reasoning_effort,
            sandbox=sandbox,
            extra_config=extra_config,
            timeout_seconds=context.timeout_seconds,
            audit_context=audit_context,
        )

    designer_execution = await run_supervised_codex_child(
        run_id=int(run["id"]),
        run_slide_id=None,
        stage_id="deck-design-director",
        role="designer",
        idempotency_key=f"codex-html:run:{int(run['id'])}:designer",
        invoke=invoke_designer,
        metadata={"route": "codex_html", "stage": "designer", "prompt_skeleton": designer_prompt_skeleton},
    )
    designer_result = designer_execution.result
    designer_final_text = materialize_codex_result_final_text(designer_result)
    design_raw_path = Path(output_dir) / "design_principle_raw.txt"
    _write_text(design_raw_path, designer_final_text)
    try:
        if designer_result.exit_code != 0:
            raise RuntimeError(f"Codex designer exited with code {designer_result.exit_code}")
        design_json_str, _design_json = clean_json_output(designer_final_text)
    except Exception as designer_err:
        error_message = str(designer_err)
        designer_execution.fail_unrecoverable(error_message)
        designer_invocation_id = _record_codex_result(
            designer_result,
            run_id=run["id"],
            run_slide_id=None,
            sandbox=sandbox,
            model=model_designer,
            reasoning_effort=reasoning_effort,
            output_path=design_raw_path,
            status=run_status.FAILED,
            error_message=error_message,
            metadata={"route": "codex_html", "stage": "designer", "prompt_skeleton": designer_prompt_skeleton},
        )
        designer_request_path = _write_codex_request_evidence(
            designer_result,
            prompt=designer_prompt,
            prompt_skeleton=designer_prompt_skeleton,
            invocation_id=designer_invocation_id,
            sandbox=sandbox,
            model=model_designer,
            reasoning_effort=reasoning_effort,
            output_path=design_raw_path,
            status=run_status.FAILED,
            error_message=error_message,
        )
        designer_stage_extra = {
            **_codex_stage_extra(designer_result, designer_invocation_id, designer_request_path),
            "prompt_skeleton": designer_prompt_skeleton,
        }
        _mark_codex_html_pre_slide_failure(
            context,
            output_dir,
            route_metadata=route_metadata,
            reasoning_effort=reasoning_effort,
            error_message=error_message,
            designer_result=designer_result,
            designer_invocation_id=designer_invocation_id,
            designer_stage_extra=designer_stage_extra,
            designer_request_path=str(designer_request_path),
        )
        return

    designer_invocation_id = _record_codex_result(
        designer_result,
        run_id=run["id"],
        run_slide_id=None,
        sandbox=sandbox,
        model=model_designer,
        reasoning_effort=reasoning_effort,
        output_path=design_raw_path,
        status=run_status.COMPLETED,
        metadata={"route": "codex_html", "stage": "designer", "prompt_skeleton": designer_prompt_skeleton},
    )
    designer_request_path = _write_codex_request_evidence(
        designer_result,
        prompt=designer_prompt,
        prompt_skeleton=designer_prompt_skeleton,
        invocation_id=designer_invocation_id,
        sandbox=sandbox,
        model=model_designer,
        reasoning_effort=reasoning_effort,
        output_path=design_raw_path,
        status=run_status.COMPLETED,
    )
    design_json_path = Path(output_dir) / "design_principle.json"
    _write_text(design_json_path, design_json_str)
    dbmod.update_run(
        run["id"],
        design_principle_raw=designer_final_text,
        design_principle_json=design_json_str,
    )
    designer_stage_extra = {
        **_codex_stage_extra(designer_result, designer_invocation_id, designer_request_path),
        "prompt_skeleton": designer_prompt_skeleton,
    }
    designer_stage = request_chain_stage_evidence(
        stage_id="design-principle-generation",
        stage_name="Design Principle Generation",
        role="designer",
        prompt_role="designer",
        model_config=_codex_model_config(context.designer_config, reasoning_effort),
        prompt_path=str(designer_result.prompt_path),
        request_path=str(designer_request_path),
        response_path=str(design_raw_path),
        artifact_path=str(design_json_path),
        provider_request=None,
        health="complete",
        extra=designer_stage_extra,
    )
    if not designer_execution.complete_projection():
        raise RuntimeError("designer projection lost supervised ownership")

    async def process_slide(rs: dict) -> dict:
        position = int(rs["position"])
        safe_title = sanitize_filename(f"{position:02d}_{rs['slide_title']}")
        html_prompt_path = output_root / f"{safe_title}_html_agent_prompt.txt"
        raw_html_path = output_root / f"{safe_title}_raw.txt"
        html_path = output_root / f"{safe_title}.html"
        screenshot_path: str | None = None
        result = None
        result_final_text: str | None = None
        child_execution = None
        html_prompt_skeleton: dict[str, Any] | None = None
        codex_request_path: Path | None = None
        screenshot_error: str | None = None
        dbmod.update_run_slide(rs["id"], status=run_status.RUNNING)
        try:
            html_vars = {
                "Deck-Design-principle": design_json_str,
                "Deck-User-Requirement": requirement["content"],
                "Slide-Content": rs["slide_content"],
            }
            html_rendered_prompt = render_canonical_prompt("html_agent", html_vars)
            html_prompt = html_rendered_prompt.prompt
            html_prompt_skeleton = prompt_skeleton_evidence(html_rendered_prompt)
            _write_text(html_prompt_path, html_prompt)
            async def invoke_slide(audit_context: CodexAuditContext):
                return await run_codex_exec_json(
                    stage_id=f"slide-{position:03d}-html",
                    role="html_agent",
                    prompt=html_prompt,
                    work_dir=scratch_root / f"slide-{position:03d}" / f"attempt-{audit_context.attempt}",
                    artifact_dir=(
                        codex_artifact_root
                        / f"slide-{position:03d}-html"
                        / f"attempt-{audit_context.attempt}"
                    ),
                    model=model_html,
                    reasoning_effort=reasoning_effort,
                    sandbox=sandbox,
                    extra_config=extra_config,
                    timeout_seconds=context.timeout_seconds,
                    audit_context=audit_context,
                )

            child_execution = await run_supervised_codex_child(
                run_id=int(run["id"]),
                run_slide_id=int(rs["id"]),
                stage_id=f"slide-{position:03d}-html",
                role="html_agent",
                idempotency_key=f"codex-html:run:{int(run['id'])}:slide:{int(rs['id'])}:html",
                invoke=invoke_slide,
                metadata={
                    "route": "codex_html",
                    "stage": "html_agent",
                    "slide_position": position,
                    "prompt_skeleton": html_prompt_skeleton,
                },
            )
            result = child_execution.result
            result_final_text = materialize_codex_result_final_text(result)
            _write_text(raw_html_path, result_final_text)
            if result.exit_code != 0:
                raise RuntimeError(f"Codex exited with code {result.exit_code}")
            cleanup_result = clean_codex_html_output(result_final_text)
            cleanup_metadata = cleanup_result.to_metadata()
            clean_html = cleanup_result.html
            _write_text(html_path, clean_html)
            try:
                screenshot_path = await async_screenshot_html_file(str(html_path), VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
            except Exception as screenshot_err:
                screenshot_error = str(screenshot_err)
                log.warning("Codex HTML slide %d screenshot failed (non-fatal): %s", position, screenshot_err)
            screenshot_health = "complete" if screenshot_path else "incomplete"

            invocation_id = _record_codex_result(
                result,
                run_id=run["id"],
                run_slide_id=rs["id"],
                sandbox=sandbox,
                model=model_html,
                reasoning_effort=reasoning_effort,
                output_path=html_path,
                status=run_status.COMPLETED,
                metadata={
                    "route": "codex_html",
                    "stage": "html_agent",
                    "slide_position": position,
                    "prompt_skeleton": html_prompt_skeleton,
                },
            )
            codex_request_path = _write_codex_request_evidence(
                result,
                prompt=html_prompt,
                prompt_skeleton=html_prompt_skeleton,
                invocation_id=invocation_id,
                sandbox=sandbox,
                model=model_html,
                reasoning_effort=reasoning_effort,
                output_path=html_path,
                status=run_status.COMPLETED,
                extra_evidence={"cleanup": cleanup_metadata},
            )
            html_stage_extra = {
                **_codex_stage_extra(result, invocation_id, codex_request_path),
                "prompt_skeleton": html_prompt_skeleton,
                "cleanup": cleanup_metadata,
            }
            html_stage = request_chain_stage_evidence(
                stage_id="html-generation",
                stage_name="HTML Generation",
                role="html_agent",
                prompt_role="html_agent",
                model_config=_codex_model_config(context.html_agent_config, reasoning_effort),
                prompt_path=str(result.prompt_path),
                request_path=str(codex_request_path),
                response_path=str(raw_html_path),
                artifact_path=str(html_path),
                provider_request=None,
                health="complete",
                extra=html_stage_extra,
            )
            slide_artifacts = {
                "designer": {
                    "raw_path": str(design_raw_path),
                    "json_path": str(design_json_path),
                    "codex_invocation_id": designer_invocation_id,
                    **designer_stage_extra,
                },
                "html_agent": {
                    "prompt_path": str(html_prompt_path),
                    "request_path": str(codex_request_path),
                    "codex_prompt_path": str(result.prompt_path),
                    "raw_html_path": str(raw_html_path),
                    "html_path": str(html_path),
                    "screenshot_path": screenshot_path,
                    "screenshot_error": screenshot_error,
                    "codex_invocation_id": invocation_id,
                    "prompt_skeleton": html_prompt_skeleton,
                    "cleanup": cleanup_metadata,
                },
                "codex_html": {
                    "final_status": run_status.COMPLETED,
                    "attempt_count": child_execution.attempt_count,
                    "failure_count": 0,
                    "html_invocation_id": invocation_id,
                    "cleanup": cleanup_metadata,
                    "screenshot_health": screenshot_health,
                },
                "request_chain": request_chain_evidence(
                    strategy="codex_html",
                    slide=rs,
                    prompt_role="html_agent",
                    planned_chain=["designer", "html_agent", "screenshot"],
                    actual_evidence={
                        "design_principle_raw_path": str(design_raw_path),
                        "design_principle_json_path": str(design_json_path),
                        "design_principle_request_path": str(designer_request_path),
                        "html_prompt_path": str(html_prompt_path),
                        "html_request_path": str(codex_request_path),
                        "html_raw_path": str(raw_html_path),
                        "html_path": str(html_path),
                        "html_cleanup": cleanup_metadata,
                        "screenshot_path": screenshot_path,
                        "screenshot_error": screenshot_error,
                    },
                    model=context.html_agent_config.get("model"),
                    stages=[
                        dict(designer_stage),
                        html_stage,
                        request_chain_stage_evidence(
                            stage_id="screenshot",
                            stage_name="Screenshot",
                            role="screenshot",
                            prompt_role=None,
                            model_config={},
                            artifact_path=screenshot_path,
                            health=screenshot_health,
                            extra={"error": screenshot_error} if screenshot_error else None,
                        ),
                    ],
                    health=screenshot_health,
                    reason=screenshot_error,
                ),
                "dependencies": slide_dependency_evidence(
                    deck=deck,
                    requirement=requirement,
                    color=color,
                    slide=rs,
                    prompt_role="html_agent",
                    model_config=_codex_model_config(context.html_agent_config, reasoning_effort),
                    extra={"codex_html": {"reasoning_effort": reasoning_effort, "route_metadata": route_metadata}},
                ),
                "evidence_health": screenshot_health,
            }
            dbmod.update_run_slide(
                rs["id"],
                raw_response=result_final_text,
                clean_html=clean_html,
                html_path=str(html_path),
                screenshot_path=screenshot_path,
                status=run_status.COMPLETED,
                stage_artifacts=json.dumps(slide_artifacts, ensure_ascii=False),
            )
            if not child_execution.complete_projection():
                raise RuntimeError("slide projection lost supervised ownership")
            return {
                "run_slide_id": rs["id"],
                "position": position,
                "status": run_status.COMPLETED,
                "attempt_count": child_execution.attempt_count,
            }
        except Exception as slide_err:
            error_message = str(slide_err)
            if child_execution is not None:
                child_execution.fail_unrecoverable(error_message)
            invocation_id = None
            html_stage_extra: dict[str, Any] = {}
            if result is not None:
                if result_final_text is None:
                    result_final_text = materialize_codex_result_final_text(result)
                _write_text(raw_html_path, result_final_text)
                invocation_id = _record_codex_result(
                    result,
                    run_id=run["id"],
                    run_slide_id=rs["id"],
                    sandbox=sandbox,
                    model=model_html,
                    reasoning_effort=reasoning_effort,
                    output_path=raw_html_path,
                    status=run_status.FAILED,
                    error_message=error_message,
                    metadata={
                        "route": "codex_html",
                        "stage": "html_agent",
                        "slide_position": position,
                        "prompt_skeleton": html_prompt_skeleton,
                    },
                )
                codex_request_path = _write_codex_request_evidence(
                    result,
                    prompt=html_prompt,
                    prompt_skeleton=html_prompt_skeleton,
                    invocation_id=invocation_id,
                    sandbox=sandbox,
                    model=model_html,
                    reasoning_effort=reasoning_effort,
                    output_path=raw_html_path,
                    status=run_status.FAILED,
                    error_message=error_message,
                )
                html_stage_extra = {
                    **_codex_stage_extra(result, invocation_id, codex_request_path),
                    "prompt_skeleton": html_prompt_skeleton,
                }
            html_stage = request_chain_stage_evidence(
                stage_id="html-generation",
                stage_name="HTML Generation",
                role="html_agent",
                prompt_role="html_agent",
                model_config=_codex_model_config(context.html_agent_config, reasoning_effort),
                prompt_path=str(result.prompt_path) if result is not None else str(html_prompt_path),
                request_path=str(codex_request_path) if codex_request_path is not None else str(html_prompt_path),
                response_path=str(raw_html_path) if raw_html_path.exists() else None,
                provider_request=None,
                health="failed",
                extra=html_stage_extra,
            )
            failure_artifacts = {
                "error": error_message,
                "designer": {
                    "raw_path": str(design_raw_path),
                    "json_path": str(design_json_path),
                    "request_path": str(designer_request_path),
                    "codex_invocation_id": designer_invocation_id,
                    **designer_stage_extra,
                },
                "html_agent": {
                    "prompt_path": str(html_prompt_path),
                    "request_path": str(codex_request_path) if codex_request_path is not None else None,
                    "codex_prompt_path": str(result.prompt_path) if result is not None else None,
                    "raw_html_path": str(raw_html_path) if raw_html_path.exists() else None,
                    "codex_invocation_id": invocation_id,
                    "prompt_skeleton": html_prompt_skeleton,
                    **html_stage_extra,
                },
                "codex_html": {
                    "final_status": run_status.FAILED,
                    "attempt_count": 1 if result is not None else 0,
                    "failure_count": 1,
                    "html_invocation_id": invocation_id,
                },
                "request_chain": request_chain_evidence(
                    strategy="codex_html",
                    slide=rs,
                    prompt_role="html_agent",
                    planned_chain=["designer", "html_agent", "screenshot"],
                    actual_evidence={
                        "design_principle_raw_path": str(design_raw_path),
                        "design_principle_json_path": str(design_json_path),
                        "design_principle_request_path": str(designer_request_path),
                        "html_prompt_path": str(html_prompt_path),
                        "html_request_path": str(codex_request_path) if codex_request_path is not None else None,
                        "html_raw_path": str(raw_html_path) if raw_html_path.exists() else None,
                        "error": error_message,
                    },
                    model=context.html_agent_config.get("model"),
                    stages=[dict(designer_stage), html_stage],
                    health="failed",
                    reason=error_message,
                ),
                "dependencies": slide_dependency_evidence(
                    deck=deck,
                    requirement=requirement,
                    color=color,
                    slide=rs,
                    prompt_role="html_agent",
                    model_config=_codex_model_config(context.html_agent_config, reasoning_effort),
                ),
                "evidence_health": "failed",
            }
            dbmod.update_run_slide(
                rs["id"],
                raw_response=result_final_text if result is not None else None,
                status=run_status.FAILED,
                error_message=error_message,
                stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
            )
            log.error("Codex HTML slide %d failed: %s", position, error_message)
            return {"run_slide_id": rs["id"], "position": position, "status": run_status.FAILED, "attempt_count": 1 if result is not None else 0}

    semaphore = asyncio.Semaphore(_codex_slide_concurrency(route_metadata, len(run_slides)))

    async def limited_process_slide(rs: dict) -> dict:
        async with semaphore:
            return await process_slide(rs)

    slide_results = sorted(await asyncio.gather(*(limited_process_slide(slide) for slide in run_slides)), key=lambda item: item["position"])
    summary = _summarize_codex_slide_results(slide_results)
    machine_qa_evidence = None
    machine_qa_before_gemini_cleanup = None
    gemini_cleanup_evidence = None
    if any(item["status"] == run_status.COMPLETED for item in slide_results):
        try:
            from backend.services import evaluation_machine_qa

            machine_qa_evidence = evaluation_machine_qa.run_codex_html_machine_qa_for_run(int(run["id"]))
            machine_qa_rows = dbmod.list_machine_qa_for_run(int(run["id"]))
            if _machine_qa_summary_from_rows(machine_qa_rows)["status"] == "fail":
                machine_qa_before_gemini_cleanup = _codex_html_machine_qa_evidence_from_rows(
                    machine_qa_rows,
                    base=machine_qa_evidence,
                )
                failed_rows = [row for row in machine_qa_rows if row.get("verdict") == "fail"]
                try:
                    gemini_cleanup_evidence = await _apply_codex_html_gemini_cleanup(
                        context=context,
                        output_root=output_root,
                        machine_qa_evidence=machine_qa_evidence,
                        failed_rows=failed_rows,
                    )
                    if gemini_cleanup_evidence.get("applied_count"):
                        evaluation_machine_qa.run_machine_qa(
                            int(machine_qa_evidence["evaluation_id"]),
                            {
                                "scope": "all_slides",
                                "attempt_ids": [int(machine_qa_evidence["attempt_id"])],
                            },
                        )
                        final_rows = dbmod.list_machine_qa_for_run(int(run["id"]))
                        machine_qa_evidence = _codex_html_machine_qa_evidence_from_rows(
                            final_rows,
                            base=machine_qa_evidence,
                            status="complete_after_gemini_cleanup",
                        )
                    else:
                        gemini_cleanup_evidence.setdefault("reason", "no failed slides were repairable")
                except Exception as cleanup_err:
                    gemini_cleanup_evidence = {
                        "source": "codex_html_gemini_cleanup",
                        "status": "blocked",
                        "error": str(cleanup_err),
                    }
                    log.warning("Codex HTML Gemini cleanup did not complete for run %s: %s", run["id"], cleanup_err)
        except Exception as qa_err:
            machine_qa_evidence = {
                "source": "codex_html_auto_machine_qa",
                "status": "blocked",
                "error": str(qa_err),
            }
            log.warning("Codex HTML Machine QA did not complete for run %s: %s", run["id"], qa_err)
    if machine_qa_evidence is not None:
        summary["machine_qa"] = machine_qa_evidence
    if machine_qa_before_gemini_cleanup is not None:
        summary["machine_qa_before_gemini_cleanup"] = machine_qa_before_gemini_cleanup
    if gemini_cleanup_evidence is not None:
        summary["gemini_cleanup"] = gemini_cleanup_evidence
    run_stage_artifacts = {
        "codex_html": {
            "status": summary["status"],
            "failure_count": summary["failure_count"],
            "attempt_count": summary["attempt_count"],
            "per_slide_statuses": summary["per_slide_statuses"],
            "reasoning_effort": reasoning_effort,
            "concurrency": _codex_slide_concurrency(route_metadata, len(run_slides)),
            "designer_invocation_id": designer_invocation_id,
            "machine_qa": machine_qa_evidence,
            "machine_qa_before_gemini_cleanup": machine_qa_before_gemini_cleanup,
            "gemini_cleanup": gemini_cleanup_evidence,
        },
        "designer": {
            "raw_path": str(design_raw_path),
            "json_path": str(design_json_path),
            "codex_invocation_id": designer_invocation_id,
            **designer_stage_extra,
        },
    }
    dbmod.update_run(
        run["id"],
        stage_artifacts=json.dumps(run_stage_artifacts, ensure_ascii=False),
        model_call_metadata=json.dumps(summary, ensure_ascii=False),
    )
    error_message = "all Codex HTML slides failed" if summary["status"] == run_status.FAILED else None
    _set_run_terminal_status(run["id"], summary["status"], error_message)
    dbmod.update_batch_statuses()


def run_codex_html_route(context, output_dir: str) -> None:
    asyncio.run(_run_codex_html_route_async(context, output_dir))


# ---------------------------------------------------------------------------
# Playwright Screenshots
# ---------------------------------------------------------------------------

def screenshot_html_file(html_path: str, viewport_w: int = 1280, viewport_h: int = 720) -> str:
    """Use Playwright to screenshot one HTML file and return the PNG path."""
    from playwright.sync_api import sync_playwright

    html_file = Path(html_path)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": viewport_w, "height": viewport_h})
        png_path = _screenshot_html_file_with_page(page, html_file)

        browser.close()

    return str(png_path)


async def async_screenshot_html_file(html_path: str, viewport_w: int = 1280, viewport_h: int = 720) -> str:
    """Run the sync Playwright screenshot helper outside the event loop."""
    return await asyncio.to_thread(screenshot_html_file, html_path, viewport_w, viewport_h)


def _screenshot_html_file_with_page(page, html_file: Path) -> Path:
    file_url = f"file://{html_file.resolve()}"
    log.info("Screenshotting: %s", html_file.name)
    page.goto(file_url, wait_until="networkidle")
    # Extra wait for Google Fonts CDN
    page.wait_for_timeout(2000)
    png_path = html_file.with_suffix(".png")
    page.screenshot(path=str(png_path))
    log.info("Saved: %s", png_path.name)
    return png_path


def screenshot_html_files(html_dir: str, viewport_w: int = 1280, viewport_h: int = 720) -> list[str]:
    """Use Playwright to screenshot all .html files in a directory."""
    from playwright.sync_api import sync_playwright

    html_files = sorted(Path(html_dir).glob("*.html"))
    if not html_files:
        log.warning("No HTML files found in %s", html_dir)
        return []

    png_paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": viewport_w, "height": viewport_h})

        for html_file in html_files:
            png_paths.append(str(_screenshot_html_file_with_page(page, html_file)))

        browser.close()

    return png_paths


# ---------------------------------------------------------------------------
# Image route helpers
# ---------------------------------------------------------------------------

XML_TAG_TOKEN_RE = re.compile(r"<(/?)([A-Za-z_][\w:.-]*)([^<>]*?)(/?)>")


def _xml_parseable(xml: str) -> bool:
    try:
        ET.fromstring(xml)
    except ET.ParseError:
        return False
    return True


def _escape_xml_tag_token(token: str) -> str:
    return f"&lt;{token[1:-1]}&gt;"


def _escape_unbalanced_opening_tag_references(xml: str) -> str:
    stack: list[dict[str, object]] = []
    escape_ranges: list[tuple[int, int]] = []
    for match in XML_TAG_TOKEN_RE.finditer(xml):
        token = match.group(0)
        closing_marker, tag_name, attributes, self_closing_marker = match.groups()
        if self_closing_marker or attributes.rstrip().endswith("/"):
            continue
        if closing_marker:
            matching_index = None
            for index in range(len(stack) - 1, -1, -1):
                if stack[index]["name"] == tag_name:
                    matching_index = index
                    break
            if matching_index is None:
                continue
            for unbalanced in stack[matching_index + 1 :]:
                escape_ranges.append((int(unbalanced["start"]), int(unbalanced["end"])))
            del stack[matching_index:]
            continue
        stack.append({"name": tag_name, "start": match.start(), "end": match.end()})

    if not escape_ranges:
        return xml
    pieces: list[str] = []
    cursor = 0
    for start, end in sorted(set(escape_ranges)):
        pieces.append(xml[cursor:start])
        pieces.append(_escape_xml_tag_token(xml[start:end]))
        cursor = end
    pieces.append(xml[cursor:])
    return "".join(pieces)


def repair_image_xml_for_parsing(xml: str) -> str:
    if not xml or _xml_parseable(xml):
        return xml
    repaired = _escape_unbalanced_opening_tag_references(xml)
    if repaired != xml and _xml_parseable(repaired):
        return repaired
    return xml


def clean_image_xml(xml: str) -> str:
    """Remove checklist noise and keep the structured Image XML parseable when possible."""
    cleaned = re.sub(r"<Quality_Checklist\b[^>]*>.*?</Quality_Checklist>", "", xml, flags=re.DOTALL).strip()
    return repair_image_xml_for_parsing(cleaned)


_SOURCE_QUALIFIER_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "simulation",
        re.compile(r"模拟|示意|假设|simulation|simulated|illustrative", re.IGNORECASE),
    ),
    (
        "estimate",
        re.compile(
            r"估算|估计|推算|近似|大约|(?<!\w)约(?=\s*(?:\d|[一二三四五六七八九十]))|"
            r"estimate|estimated|approx(?:imate)?|forecast",
            re.IGNORECASE,
        ),
    ),
    (
        "range",
        re.compile(
            r"超过|远超|至少|最多|数以|左右|不低于|不超过|more than|at least|up to|around|roughly",
            re.IGNORECASE,
        ),
    ),
)


_SOURCE_QUALIFIER_VALUE = (
    r"(?:\d+(?:[.,]\d+)?\s*(?:%|％|万|亿|千|百|项|人|个|次|倍|元)?|"
    r"[零〇一二三四五六七八九十百千万亿两]+\s*(?:%|％|万|亿|千|百|项|人|个|次|倍|元))"
)
_SOURCE_QUALIFIER_DATA_CONTEXT = (
    r"(?:数据|数值|数量|比例|占比|指标|统计|图表|图示|图例|示意图|表格|表头|人口|人数|金额|成本)"
)
_SOURCE_QUALIFIER_ESTIMATE_TERM = (
    r"(?:估算|估计|推算|近似|大约|(?<!\w)约|estimate|estimated|approx(?:imate)?|forecast)"
)
_SOURCE_QUALIFIER_SIMULATION_SCOPE = re.compile(
    rf"(?:"
    rf"(?:模拟|假设|simulation|simulated|illustrative)\s*"
    rf"(?:{_SOURCE_QUALIFIER_VALUE}|{_SOURCE_QUALIFIER_DATA_CONTEXT}|{_SOURCE_QUALIFIER_ESTIMATE_TERM})|"
    rf"示意\s*(?:{_SOURCE_QUALIFIER_DATA_CONTEXT})|"
    rf"(?:{_SOURCE_QUALIFIER_VALUE}|{_SOURCE_QUALIFIER_DATA_CONTEXT}).{{0,12}}"
    rf"(?:模拟|假设|simulation|simulated|illustrative)"
    rf")",
    re.IGNORECASE,
)
_SOURCE_QUALIFIER_ESTIMATE_SCOPE = re.compile(
    rf"(?:"
    rf"{_SOURCE_QUALIFIER_ESTIMATE_TERM}\s*(?:[:：\(（]?\s*)"
    rf"(?:{_SOURCE_QUALIFIER_VALUE}|{_SOURCE_QUALIFIER_DATA_CONTEXT})|"
    rf"(?:{_SOURCE_QUALIFIER_VALUE}|{_SOURCE_QUALIFIER_DATA_CONTEXT}).{{0,12}}"
    rf"{_SOURCE_QUALIFIER_ESTIMATE_TERM}"
    rf")",
    re.IGNORECASE,
)
_SOURCE_QUALIFIER_RANGE_SCOPE = re.compile(
    rf"(?:"
    rf"(?:超过|远超|至少|最多|数以|左右|不低于|不超过|more than|at least|up to|around|roughly)"
    rf"\s*(?:[:：\(（]?\s*){_SOURCE_QUALIFIER_VALUE}|"
    rf"{_SOURCE_QUALIFIER_VALUE}.{{0,12}}"
    rf"(?:超过|远超|至少|最多|数以|左右|不低于|不超过|more than|at least|up to|around|roughly)"
    rf")",
    re.IGNORECASE,
)
_SOURCE_QUALIFIER_SCOPED_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("simulation", _SOURCE_QUALIFIER_SIMULATION_SCOPE),
    ("estimate", _SOURCE_QUALIFIER_ESTIMATE_SCOPE),
    ("range", _SOURCE_QUALIFIER_RANGE_SCOPE),
)

# Native Image receives the visual blueprint as its business prompt.  These terms
# identify the narrowly scoped case where a historical fact was expanded into a
# direct-harm composition rather than an archival or symbolic presentation.
_NON_GRAPHIC_HISTORICAL_VISUALIZATION_TERMS = re.compile(
    r"(?:"
    r"武斗|批斗|屠杀|镇压|迫害|枪击|枪杀|开枪|中弹|射杀|枪火|枪口火线|"
    r"靶射|靶子|被悬挂为靶|活活打死|十五岁(?:女孩|少女)|"
    r"尸体|遗体|肢解|斩首|刺穿|穿刺|钉在|未成年|少年|儿童|十五岁|15\s*岁|自伤|自残|"
    r"\b(?:historical\s+violence|violent\s+history|gunfire|"
    r"shooting\s+(?:impact|(?:at\s+)?(?:a\s+)?(?:person|people|victim|child|body|target))|being\s+shot|"
    r"impal(?:e|ement)|corpse|dismember(?:ment|ed)?|behead(?:ing|ed)?|gore|"
    r"identifiable\s+(?:fifteen[- ]year[- ]old|minor|child|teen(?:ager)?)\s+victim|"
    r"body\s+(?:displayed|hung|used)\s+(?:as\s+)?(?:a\s+)?target)\b"
    r")",
    re.IGNORECASE,
)

_DIRECT_HARM_VISUAL_COMPOSITION_TERMS = re.compile(
    r"(?:"
    r"枪击|枪杀|开枪|中弹|射杀|枪火|枪口火线|靶射|靶子|被悬挂为靶|活活打死|"
    r"十五岁(?:女孩|少女)|从楼顶坠落|尸体|遗体|肢解|斩首|刺穿|穿刺|钉在|"
    r"铁刺|铁制尖刺|尖刺|"
    r"\b(?:gunfire|shooting\s+(?:impact|(?:at\s+)?(?:a\s+)?(?:person|people|victim|child|body|target))|being\s+shot|"
    r"impal(?:e|ement)|corpse|iron\s+spikes|"
    r"dismember(?:ment|ed)?|behead(?:ing|ed)?|gore|"
    r"identifiable\s+(?:fifteen[- ]year[- ]old|minor|child|teen(?:ager)?)\s+victim|"
    r"body\s+(?:displayed|hung|used)\s+(?:as\s+)?(?:a\s+)?target)\b"
    r")",
    re.IGNORECASE,
)

_NON_GRAPHIC_HISTORICAL_VISUALIZATION_GUARD = (
    "# Non-Graphic Historical Visualization Guard\n"
    "This slide records difficult historical facts. Preserve the factual chronology and every source qualification "
    "in the presentation text and audit trail. Use an editorial, non-photorealistic historical-archive or symbolic "
    "composition only: dated documents, newspaper facsimiles, sealed files, empty objects, memorial ribbons, and "
    "abstract fractured textures. Render no people and no action scene. Convey consequence, loss, and reflection "
    "without depicting the event itself."
)

_NON_GRAPHIC_HISTORICAL_VISUAL_REPLACEMENT = (
    "Editorial non-photorealistic historical archive composition: dated documents, newspaper facsimiles, sealed files, "
    "empty objects, memorial ribbons, and abstract fractured textures. No people or action scene; convey consequence "
    "and reflection without depicting the event itself."
)

_NON_GRAPHIC_VISUAL_COMPOSITION_TAG = re.compile(
    r"(?:style_anchor_extraction|colour_role_syntax|shape_and_line_syntax|this_slide_style_delta|"
    r"text_safe_zones_and_contrast_guards|spatial_axes_semantics|visual_mass_map|module_blueprint|"
    r"reading_path_control|material_and_light_physics|form_grammar|noise_ceiling_rules|"
    r"visual(?:_[a-z0-9]+)*|scene(?:_[a-z0-9]+)*|composition(?:_[a-z0-9]+)*|"
    r"image(?:_[a-z0-9]+)*|illustration(?:_[a-z0-9]+)*|art(?:_[a-z0-9]+)*|"
    r"key_visual|background|foreground|subject)",
    re.IGNORECASE,
)
_NON_GRAPHIC_VISUAL_COMPOSITION_XML_FIELD = re.compile(
    r"(?P<opening><(?P<tag>[A-Za-z][A-Za-z0-9_.:-]*)\b[^>]*>)(?P<text>[^<]*)(?P<closing></(?P=tag)\s*>)",
    re.DOTALL,
)
_NON_GRAPHIC_FACTUAL_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"十五岁(?:女孩|少女)在(?:枪火|枪口火线)中倒下并被悬挂为靶",
            re.IGNORECASE,
        ),
        "十五岁个体成为致命暴力与非人化对待的受害者",
    ),
    (re.compile(r"十五岁(?:女孩|少女)", re.IGNORECASE), "十五岁个体"),
    (re.compile(r"被悬挂为靶", re.IGNORECASE), "遭受非人化对待"),
    (re.compile(r"被活活打死|活活打死", re.IGNORECASE), "死亡"),
    (re.compile(r"从楼顶坠落", re.IGNORECASE), "历史暴力的受害者"),
    (re.compile(r"枪口火线", re.IGNORECASE), "斜向冷白光线"),
    (re.compile(r"枪火", re.IGNORECASE), "暴力痕迹"),
    (
        re.compile(
            r"\bidentifiable\s+(?:fifteen[- ]year[- ]old|minor|child|teen(?:ager)?)\s+victim\b",
            re.IGNORECASE,
        ),
        "a fifteen-year-old student",
    ),
    (re.compile(r"遭(?:枪击|枪杀|开枪|中弹|射杀)", re.IGNORECASE), "遭受致命暴力"),
    (re.compile(r"枪击|枪杀|开枪|中弹|射杀", re.IGNORECASE), "遭受致命暴力"),
    (re.compile(r"靶射|靶子", re.IGNORECASE), "非人化符号"),
    (re.compile(r"尸体|遗体", re.IGNORECASE), "遇难者"),
    (re.compile(r"肢解|斩首|刺穿|穿刺|钉在", re.IGNORECASE), "致命暴力"),
    (re.compile(r"铁刺|铁制尖刺|尖刺", re.IGNORECASE), "金属边缘"),
    (re.compile(r"\b(?:gunfire|shooting(?:\s+impact)?|\bshot\b|iron\s+spikes)\b", re.IGNORECASE), "armed violence"),
    (re.compile(r"\b(?:impal(?:e|ement)|corpse|dismember(?:ment|ed)?|behead(?:ing|ed)?|gore)\b", re.IGNORECASE), "death and loss"),
    (re.compile(r"\bbody\s+(?:displayed|hung|used)\s+(?:as\s+)?(?:a\s+)?target\b", re.IGNORECASE), "victims suffered loss of life"),
)


def _source_qualifier_names(line: str) -> tuple[str, ...]:
    """Return qualifier categories that modify a quantitative source claim."""
    return tuple(
        name for name, pattern in _SOURCE_QUALIFIER_SCOPED_RULES if pattern.search(line)
    )


def _non_graphic_historical_factual_text(text: str) -> str:
    """Retain historical facts and quantities while removing direct-harm wording."""
    rewritten = text
    for pattern, replacement in _NON_GRAPHIC_FACTUAL_REPLACEMENTS:
        rewritten = pattern.sub(replacement, rewritten)
    return rewritten


def _is_source_qualified_quantitative_line(line: str) -> bool:
    """Return whether qualifier words in a line apply to a quantitative claim."""
    return bool(_source_qualifier_names(line))


def _source_qualifier_lines(slide_content: object) -> tuple[str, ...]:
    """Return source lines that qualify numerical claims or their certainty."""
    if not isinstance(slide_content, str) or not slide_content.strip():
        return ()
    lines: list[str] = []
    for raw_line in slide_content.splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        if not line:
            continue
        if _is_source_qualified_quantitative_line(line):
            lines.append(line)
    return tuple(dict.fromkeys(lines))


def _source_qualifier_guard(slide_content: object) -> str:
    """Build a visible renderer instruction from qualifier-bearing source lines."""
    lines = _source_qualifier_lines(slide_content)
    if not lines:
        return ""
    source_lines = "\n".join(
        f"- {_non_graphic_historical_factual_text(line)}" for line in lines
    )
    return (
        "# Source Qualification Guard\n"
        "以下来源行包含带限定条件的数值声明。必须保留每个数值的模拟、估算或不确定性含义，"
        "并在同一数据项、表头、图例或脚注旁显示清晰限定；不得把限定数值改写成无条件的历史事实。\n"
        "来源限定行（保留其含义）：\n"
        f"{source_lines}"
    )


def _validate_source_qualifiers_in_xml(slide_content: object, rendered_xml: object) -> None:
    """Fail closed before native rendering if source qualification disappeared."""
    lines = _source_qualifier_lines(slide_content)
    if not lines:
        return
    if not isinstance(rendered_xml, str) or not rendered_xml.strip():
        raise ValueError("native_image_source_qualifier_missing:empty_blueprint")
    source_qualifiers = {
        name
        for line in lines
        for name in _source_qualifier_names(line)
    }
    missing = [
        name
        for name, pattern in _SOURCE_QUALIFIER_RULES
        if name in source_qualifiers and not pattern.search(rendered_xml)
    ]
    if missing:
        raise ValueError(
            "native_image_source_qualifier_missing:" + ",".join(missing)
        )


def _non_graphic_historical_visualization_guard(
    rendered_xml: str,
    slide_content: object,
) -> str:
    """Return a renderer guard only for historical direct-harm visual material."""
    source = slide_content if isinstance(slide_content, str) else ""
    if not _NON_GRAPHIC_HISTORICAL_VISUALIZATION_TERMS.search(f"{rendered_xml}\n{source}"):
        return ""
    return _NON_GRAPHIC_HISTORICAL_VISUALIZATION_GUARD


def _non_graphic_historical_renderer_blueprint(rendered_xml: str) -> str:
    """Use safe visual fields and non-graphic copy in the renderer prompt."""
    if not _DIRECT_HARM_VISUAL_COMPOSITION_TERMS.search(rendered_xml):
        return rendered_xml

    replaced = re.sub(
        _NON_GRAPHIC_VISUAL_COMPOSITION_XML_FIELD,
        lambda match: (
            f"{match.group('opening')}{_NON_GRAPHIC_HISTORICAL_VISUAL_REPLACEMENT}{match.group('closing')}"
            if _NON_GRAPHIC_VISUAL_COMPOSITION_TAG.fullmatch(match.group("tag"))
            else (
                f"{match.group('opening')}{_non_graphic_historical_factual_text(match.group('text'))}{match.group('closing')}"
                if _DIRECT_HARM_VISUAL_COMPOSITION_TERMS.search(match.group("text"))
                else match.group(0)
            )
        ),
        rendered_xml,
    )
    return replaced


def _native_renderer_prompt_with_source_qualifier(
    rendered_xml: str,
    slide_content: object,
) -> str:
    """Bind source qualifiers and non-graphic historical framing to the renderer prompt."""
    source_qualifier_guard = _source_qualifier_guard(slide_content)
    if source_qualifier_guard and (
        not isinstance(rendered_xml, str) or not rendered_xml.strip()
    ):
        _validate_source_qualifiers_in_xml(slide_content, rendered_xml)
    non_graphic_guard = _non_graphic_historical_visualization_guard(rendered_xml, slide_content)
    if not source_qualifier_guard and not non_graphic_guard:
        return rendered_xml
    renderer_blueprint = (
        _non_graphic_historical_renderer_blueprint(rendered_xml)
        if non_graphic_guard
        else rendered_xml
    )
    guards = [guard for guard in (source_qualifier_guard, non_graphic_guard) if guard]
    renderer_prompt = "\n\n".join([renderer_blueprint, *guards])
    if source_qualifier_guard:
        _validate_source_qualifiers_in_xml(slide_content, renderer_prompt)
    return renderer_prompt


def image_prompt_role(strategy: str, run_slide: dict, seed_slide: dict | None, cover_slide: dict | None) -> str:
    if run_slide.get("slide_type") == "cover":
        return "image_cover_3_1"
    if strategy == "image_direct":
        return "image_direct"
    if strategy == "image_1_0":
        return "image_1_0"
    if strategy == "image_5_0":
        return "image_5_0_unified"
    if strategy == "image_3_0":
        return "image_3_0_seed" if seed_slide and run_slide["id"] == seed_slide["id"] else "image_3_0_non_seed"
    if strategy == "image_3_2":
        is_seed_stage = (seed_slide and run_slide["id"] == seed_slide["id"]) or (
            cover_slide and run_slide["id"] == cover_slide["id"]
        )
        return "image_3_2_seed" if is_seed_stage else "image_3_2_non_seed"
    raise ValueError(f"Unsupported Image strategy: {strategy}")


def _image_prompt_lineage(prompt: dict, rendered_prompt: str) -> dict[str, int | str]:
    prompt_content = prompt.get("content")
    prompt_id = prompt.get("id")
    if not isinstance(prompt_content, str) or not isinstance(prompt_id, int) or isinstance(prompt_id, bool):
        raise ValueError("Image Prompt lineage requires an active Prompt row")
    return {
        "prompt_id": prompt_id,
        "prompt_content_sha256": _sha256_text(prompt_content),
        "rendered_prompt_sha256": _sha256_text(rendered_prompt),
    }


def _render_image_prompt_with_lineage(
    role: str,
    deck: dict,
    requirement: dict,
    color: dict,
    run_slide: dict,
    *,
    full_content: str,
) -> tuple[str, dict[str, int | str]]:
    import db as dbmod

    prompt = dbmod.get_active_prompt(role)
    if not prompt:
        raise ValueError(f"Missing active Prompt System prompt for {role}")
    rendered_prompt = render_template_string(
        prompt["content"],
        {
            "Deck-Full-Content": full_content,
            "Deck-Title": deck["title"],
            "Deck-User-Requirement": requirement["content"],
            "Deck-Required-color": color["content"],
            "Slide-Content": run_slide["slide_content"],
        },
    )
    return rendered_prompt, _image_prompt_lineage(prompt, rendered_prompt)


def render_image_prompt(
    role: str,
    deck: dict,
    requirement: dict,
    color: dict,
    run_slide: dict,
    *,
    full_content: str,
) -> str:
    rendered_prompt, _lineage = _render_image_prompt_with_lineage(
        role,
        deck,
        requirement,
        color,
        run_slide,
        full_content=full_content,
    )
    return rendered_prompt


def _native_image_prompt_lineage_for_stage(
    native_result: dict,
    *,
    stage_id: str,
    invocations: list[dict],
) -> dict[str, int | str]:
    lineage = native_result.get("native_prompt_lineage")
    required_keys = {
        "role",
        "prompt_id",
        "prompt_content_sha256",
        "rendered_prompt_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != required_keys:
        raise ValueError("Native Image launcher prompt lineage is missing or malformed")
    if lineage.get("role") != "image_generator":
        raise ValueError("Native Image launcher prompt lineage role is invalid")
    prompt_id = lineage.get("prompt_id")
    content_sha256 = lineage.get("prompt_content_sha256")
    rendered_sha256 = lineage.get("rendered_prompt_sha256")
    if (
        not isinstance(prompt_id, int)
        or isinstance(prompt_id, bool)
        or not isinstance(content_sha256, str)
        or not isinstance(rendered_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", rendered_sha256) is None
    ):
        raise ValueError("Native Image launcher prompt lineage is missing or malformed")
    matching_invocations = [
        invocation
        for invocation in invocations
        if invocation.get("stage_id") == stage_id and invocation.get("role") == "image_generator"
    ]
    if len(matching_invocations) != 1 or matching_invocations[0].get("prompt_sha256") != rendered_sha256:
        raise ValueError("Native Image launcher prompt lineage does not match its invocation")
    return {
        "prompt_id": prompt_id,
        "prompt_content_sha256": content_sha256,
        "rendered_prompt_sha256": rendered_sha256,
    }


def render_image_direct_prompt(run_slide: dict) -> str:
    return f"{IMAGE_DIRECT_PROMPT_PREFIX}\n\n{run_slide.get('slide_content') or ''}"


def extract_cover_image_prompt(raw_prompt_response: str) -> str:
    """Extract the generated cover image prompt while preserving usable fallback text."""
    match = re.search(r"###\s*(.+)", raw_prompt_response, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw_prompt_response.strip()


def short_file_hash(path: str | os.PathLike[str] | None) -> str | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return None
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def reference_image_evidence(
    path: str | os.PathLike[str] | None,
    *,
    reference_type: str,
    request_stage: str,
    source_slide: dict | None = None,
    sent: bool = True,
    reason: str | None = None,
) -> dict:
    artifact_path = str(path) if sent and path else None
    return {
        "reference_type": reference_type,
        "request_stage": request_stage,
        "sent": bool(sent and artifact_path),
        "artifact_path": artifact_path,
        "short_hash": short_file_hash(artifact_path),
        "source_slide_id": (source_slide or {}).get("id"),
        "source_slide_position": (source_slide or {}).get("position"),
        "source_slide_title": (source_slide or {}).get("slide_title") or (source_slide or {}).get("title"),
        "reason": reason,
        "redaction": "Image bytes are not stored in evidence; use artifact path for preview.",
    }


def raw_thinking_fields_from_provider_request(provider_request: dict | None) -> dict:
    body = provider_request.get("json") if isinstance(provider_request, dict) else None
    if not isinstance(body, dict):
        return {}
    fields: dict[str, object] = {}
    reasoning_effort = body.get("reasoning_effort")
    if reasoning_effort is not None:
        fields["reasoning_effort"] = reasoning_effort
    generation_config = body.get("generationConfig") if isinstance(body.get("generationConfig"), dict) else {}
    thinking_config = generation_config.get("thinkingConfig") if isinstance(generation_config.get("thinkingConfig"), dict) else {}
    if "thinkingBudget" in thinking_config:
        fields["generationConfig.thinkingConfig.thinkingBudget"] = thinking_config["thinkingBudget"]
    if "responseModalities" in generation_config:
        fields["generationConfig.responseModalities"] = generation_config["responseModalities"]
    return fields


def mapped_provider_thinking(raw_fields: dict) -> str:
    if "reasoning_effort" in raw_fields:
        return f"reasoning_effort = {raw_fields['reasoning_effort']}"
    if "generationConfig.thinkingConfig.thinkingBudget" in raw_fields:
        return "generationConfig.thinkingConfig.thinkingBudget = " + str(raw_fields["generationConfig.thinkingConfig.thinkingBudget"])
    return "not_applicable"


def provider_request_evidence_from_path(request_path: str | os.PathLike[str] | None) -> dict | None:
    if not request_path:
        return None
    try:
        payload = json.loads(Path(request_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("provider_request"), dict):
        return payload["provider_request"]
    if isinstance(payload.get("json"), dict):
        return payload
    return None


def _json_dict_from_path(path: str | os.PathLike[str] | None) -> dict:
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def image_generation_stage_extra(
    *,
    provider_request: dict | None,
    response_path: str | os.PathLike[str] | None = None,
    image_result: dict | None = None,
    reference_bindings: dict | None = None,
) -> dict:
    request = provider_request if isinstance(provider_request, dict) else {}
    request_metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    route_metadata = request_metadata.get("route_metadata") if isinstance(request_metadata.get("route_metadata"), dict) else {}
    body = request.get("json") if isinstance(request.get("json"), dict) else {}
    response = _json_dict_from_path(response_path)
    response_image = response.get("image") if isinstance(response.get("image"), dict) else {}
    result_response = (image_result or {}).get("response") if isinstance((image_result or {}).get("response"), dict) else {}
    provider_params = {
        key: body[key]
        for key in ("model", "size", "quality", "background", "moderation", "output_format")
        if key in body
    }
    if isinstance(body.get("generationConfig"), dict):
        provider_params["generationConfig"] = body["generationConfig"]
    seed_dependency = request_metadata.get("seed_dependency")
    reference_count = request_metadata.get("reference_image_count")
    if seed_dependency is None and reference_count is None:
        sent_references = [
            reference
            for reference in (reference_bindings or {}).values()
            if isinstance(reference, dict) and reference.get("sent") and reference.get("artifact_path")
        ]
        if sent_references:
            reference_count = len(sent_references)
            reference = sent_references[0]
            if reference.get("reference_type") == "cover_png":
                seed_dependency = {
                    "cover_reference_slide_id": reference.get("source_slide_id"),
                    "cover_reference_position": reference.get("source_slide_position"),
                }
            elif reference.get("reference_type") == "seed_png":
                seed_dependency = {
                    "seed_slide_id": reference.get("source_slide_id"),
                    "seed_slide_position": reference.get("source_slide_position"),
                }
    reference_status = "used" if seed_dependency or reference_count else "not_used"
    extra = {
        "image_renderer": request_metadata.get("image_renderer") or route_metadata.get("image_renderer"),
        "provider_channel": request_metadata.get("provider_channel") or route_metadata.get("provider_channel"),
        "request_mode": request_metadata.get("request_mode") or route_metadata.get("request_mode"),
        "provider_params": provider_params,
        "elapsed_seconds": response.get("elapsed_seconds"),
        "output_path": response_image.get("path") or response.get("image_path") or result_response.get("image_path"),
        "error": response.get("error") or response.get("error_type") or response.get("body"),
        "seed_reference_dependency": {
            "status": reference_status,
            "reference_image_count": reference_count or 0,
            "seed_dependency": seed_dependency or "not_applicable",
        },
    }
    return {key: value for key, value in extra.items() if value not in (None, {}, "")}


def request_chain_stage_evidence(
    *,
    stage_id: str,
    stage_name: str,
    role: str,
    prompt_role: str | None,
    model_config: dict | None,
    prompt_path: str | None = None,
    request_path: str | None = None,
    response_path: str | None = None,
    artifact_path: str | None = None,
    provider_request: dict | None = None,
    health: str = "complete",
    extra: dict | None = None,
) -> dict:
    config = model_config or {}
    raw_fields = raw_thinking_fields_from_provider_request(provider_request)
    stage = {
        "id": stage_id,
        "stage_name": stage_name,
        "role": role,
        "prompt_role": prompt_role,
        "model": config.get("model"),
        "profile_id": config.get("profile_id"),
        "profile_name": config.get("profile_name") or config.get("name"),
        "api_type": config.get("api_type"),
        "configured_thinking": config.get("thinking"),
        "mapped_provider_thinking": mapped_provider_thinking(raw_fields),
        "raw_thinking_fields": raw_fields,
        "prompt_path": prompt_path,
        "request_path": request_path,
        "response_path": response_path,
        "artifact_path": artifact_path,
        "health": health,
    }
    if extra:
        stage.update(extra)
    return stage


def request_chain_evidence(
    *,
    strategy: str,
    slide: dict,
    prompt_role: str,
    planned_chain: list[str],
    actual_evidence: dict,
    model: str | None = None,
    stages: list[dict] | None = None,
    references: dict | None = None,
    palette: dict | None = None,
    seed_xml: dict | None = None,
    health: str = "complete",
    reason: str | None = None,
) -> dict:
    evidence = {
        "strategy": strategy,
        "slide_id": slide.get("id"),
        "slide_position": slide.get("position"),
        "slide_title": slide.get("slide_title"),
        "prompt_role": prompt_role,
        "model": model,
        "planned_chain": planned_chain,
        "actual_evidence": actual_evidence,
        "references": references or {},
        "palette": palette or {"status": "not_applicable"},
        "seed_xml": seed_xml or {"status": "not_applicable"},
        "health": health,
        "reason": reason,
    }
    if stages is not None:
        evidence["schema_version"] = 2
        evidence["stages"] = stages
    return evidence


def slide_dependency_evidence(
    *,
    deck: dict,
    requirement: dict,
    color: dict,
    slide: dict,
    prompt_role: str,
    model_config: dict,
    extra: dict | None = None,
) -> dict:
    data = {
        "deck": {"id": deck.get("id"), "title": deck.get("title"), "content_length": len(deck.get("content") or "")},
        "requirement": {
            "id": requirement.get("id"),
            "title": requirement.get("title"),
            "content_length": len(requirement.get("content") or ""),
        },
        "color": {
            "id": color.get("id"),
            "title": color.get("title"),
            "content_length": len(color.get("content") or ""),
            "status": "value" if str(color.get("content") or "").strip() else "not_applicable",
            "label": color.get("title") or "No Color Selected",
        },
        "current_slide": {
            "id": slide.get("slide_id") or slide.get("id"),
            "run_slide_id": slide.get("id"),
            "position": slide.get("position"),
            "title": slide.get("slide_title"),
            "content_length": len(slide.get("slide_content") or ""),
        },
        "prompt_role": prompt_role,
        "model": {
            "role": model_config.get("role"),
            "profile_id": model_config.get("profile_id"),
            "api_type": model_config.get("api_type"),
            "model": model_config.get("model"),
        },
    }
    if extra:
        data.update(extra)
    return data


def redact_gemini_part(part: dict, *, image_path: str | None = None, byte_length: int | None = None) -> dict:
    redacted: dict[str, object] = {}
    if "text" in part:
        redacted["text"] = str(part["text"])
    inline_data = part.get("inlineData") or part.get("inline_data")
    if inline_data:
        data = inline_data.get("data") or ""
        resolved_byte_length = byte_length
        if resolved_byte_length is None and data:
            try:
                resolved_byte_length = len(base64.b64decode(data))
            except Exception:
                resolved_byte_length = None
        redacted["inlineData"] = {
            "mimeType": inline_data.get("mimeType") or inline_data.get("mime_type"),
            "data": "[IMAGE_BYTES_SAVED]" if image_path else "[IMAGE_BYTES_REDACTED]",
        }
        if resolved_byte_length is not None:
            redacted["inlineData"]["byteLength"] = resolved_byte_length
        if image_path:
            redacted["inlineData"]["path"] = image_path
    if part.get("thought") is not None:
        redacted["thought"] = part.get("thought")
    if part.get("thoughtSignature") or part.get("thought_signature"):
        redacted["thoughtSignature"] = "[PRESENT]"
    if not redacted:
        redacted["part_keys"] = sorted(part.keys())
    return redacted


def redact_gemini_content(content: dict) -> dict:
    return {
        "role": content.get("role"),
        "parts": [redact_gemini_part(part) for part in content.get("parts", [])],
    }


def redact_gemini_body(body: dict) -> dict:
    redacted = redact_inline_payloads(body)
    redacted["contents"] = [redact_gemini_content(content) for content in body.get("contents", [])]
    if "systemInstruction" in body:
        redacted["systemInstruction"] = {
            "parts": [redact_gemini_part(part) for part in body.get("systemInstruction", {}).get("parts", [])]
        }
    return redacted


def public_image_generator_metadata(metadata: dict) -> dict:
    public = {key: value for key, value in metadata.items() if key != "conversation_history"}
    if "conversation_history" in metadata:
        public["conversation_history_turn_count"] = len(metadata.get("conversation_history") or [])
    return public


def build_gpt_image_2_blueprint_first_prompt(role_definition: str, blueprint: str, slide_content: str | None) -> str:
    parts = [
        "# Role Definition",
        role_definition or "",
        "# Visual Blueprint",
        blueprint or "",
        "Context:",
        slide_content or "",
    ]
    return "\n\n".join(part for part in parts if part is not None)


def extract_gpt_image_2_image_payload(value, path: str = "") -> dict | None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = extract_gpt_image_2_image_payload(item, f"{path}[{index}]")
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    for key in ("b64_json", "base64", "image_base64"):
        payload = value.get(key)
        if isinstance(payload, str) and payload:
            return {"source_field": key, "payload": payload, "encoding": "base64", "path": f"{path}.{key}" if path else key}
    url = value.get("url")
    if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
        return {
            "source_field": "url",
            "payload": url.split(";base64,", 1)[1],
            "encoding": "data_url",
            "path": f"{path}.url" if path else "url",
        }
    for key, item in value.items():
        found = extract_gpt_image_2_image_payload(item, f"{path}.{key}" if path else key)
        if found:
            return found
    return None


def redact_gpt_image_2_response(value):
    if isinstance(value, list):
        return [redact_gpt_image_2_response(item) for item in value]
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in {"b64_json", "base64", "image_base64"} and isinstance(item, str):
                redacted[key] = "[IMAGE_BYTES_SAVED]"
            elif key == "url" and isinstance(item, str) and item.startswith("data:image/"):
                redacted[key] = "[IMAGE_DATA_URL_SAVED]"
            else:
                redacted[key] = redact_gpt_image_2_response(item)
        return redacted
    return value


def generate_gpt_image_2_image_generator(agent_config: dict, xml: str, output_path: str, **metadata) -> dict:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    request_path = output.with_suffix(output.suffix + ".request.json")
    response_path = output.with_suffix(output.suffix + ".response.json")
    native_model = str(agent_config["model"]).removeprefix("openai/")
    url = agent_config["endpoint"]
    user_prompt_only = metadata.get("image_prompt_mode") == "user_prompt_only"
    if user_prompt_only:
        prompt = str(metadata.get("image_prompt") or "")
        request_mode = "user_prompt_only"
    else:
        role_definition = str(
            metadata.get("image_system_prompt")
            or (
                "You are SlideGen-Pro, an expert presentation designer. Convert the user input into a visually appealing, "
                "creative, logical 16:9 presentation image.\n\n"
                "Do not output text, output an image."
            )
        )
        prompt = build_gpt_image_2_blueprint_first_prompt(role_definition, xml, metadata.get("slide_content"))
        request_mode = "blueprint_first"
    body = {
        "model": native_model,
        "prompt": prompt,
        "size": "1536x864",
        "quality": "high",
        "background": "opaque",
        "moderation": "auto",
        "output_format": "png",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {agent_config['api_key']}",
    }
    timeout_seconds = int(metadata.get("timeout_seconds") or agent_config.get("timeout_seconds") or agent_config.get("timeout_minutes", 3) * 60)
    evidence_metadata = public_image_generator_metadata(metadata)
    evidence_metadata.update(
        {
            "provider_channel": "zenmux_images_api",
            "request_mode": request_mode,
            "image_renderer": "gpt_image_2",
        }
    )
    redacted_request = {
        "url": url,
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer ***REDACTED***"},
        "json": body,
        "metadata": evidence_metadata,
    }

    try:
        with acquire_provider_slot(agent_config):
            resp, attempts = post_json_with_retries(url, headers, body, timeout_seconds=timeout_seconds)
    except Exception as exc:
        attempts = getattr(LLM_CALL_STATE, "last_attempts", None) or [
            {"attempt": 1, "error_type": exc.__class__.__name__, "error": str(exc)[:500], "transient": True}
        ]
        evidence_metadata.update({"attempt_count": len(attempts), "retry_attempts": attempts})
        request_path.write_text(json.dumps(redacted_request, ensure_ascii=False, indent=2), encoding="utf-8")
        response_path.write_text(
            json.dumps({"error_type": exc.__class__.__name__, "error": str(exc)[:2000], "attempts": attempts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise

    elapsed = sum(attempt.get("elapsed_seconds", 0) for attempt in attempts)
    evidence_metadata.update({"attempt_count": len(attempts), "retry_attempts": attempts})
    request_path.write_text(json.dumps(redacted_request, ensure_ascii=False, indent=2), encoding="utf-8")
    if resp.status_code != 200:
        response_path.write_text(
            json.dumps(
                {
                    "status_code": resp.status_code,
                    "body": resp.text[:2000],
                    "elapsed_seconds": round(elapsed, 3),
                    "attempts": attempts,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        resp.raise_for_status()

    data = resp.json()
    image_payload = extract_gpt_image_2_image_payload(data)
    response_evidence = {
        "status_code": resp.status_code,
        "elapsed_seconds": round(elapsed, 3),
        "response": redact_gpt_image_2_response(data),
        "attempts": attempts,
    }
    if not image_payload:
        response_path.write_text(json.dumps(response_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError("GPT Image 2 provider returned no image payload")

    image_bytes = base64.b64decode(image_payload["payload"])
    output.write_bytes(image_bytes)
    response_evidence["image"] = {
        "path": str(output),
        "byte_length": len(image_bytes),
        "source_field": image_payload["source_field"],
        "source_path": image_payload["path"],
    }
    response_path.write_text(json.dumps(response_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "request": {
            "path": str(request_path),
            "headers": redacted_request["headers"],
            "metadata": evidence_metadata,
        },
        "response": {
            "path": str(response_path),
            "image_path": str(output),
        },
    }


def generate_image_generator(agent_config: dict, xml: str, output_path: str, **metadata) -> dict:
    """Generate an image from XML and persist redacted request/response evidence."""
    if agent_config.get("api_type") == "zenmux_images":
        return generate_gpt_image_2_image_generator(agent_config, xml, output_path, **metadata)
    if agent_config.get("api_type") != "gemini":
        raise ValueError("Image generator provider adapter currently supports gemini and zenmux_images api_type only")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    request_path = output.with_suffix(output.suffix + ".request.json")
    response_path = output.with_suffix(output.suffix + ".response.json")
    native_model = str(agent_config["model"]).removeprefix("google/")
    url = f"{agent_config['endpoint'].rstrip('/')}/{native_model}:generateContent"
    user_prompt_only = metadata.get("image_prompt_mode") == "user_prompt_only"
    image_system_prompt = metadata.get("image_system_prompt")
    system_prompt = None if user_prompt_only else (
        str(image_system_prompt)
        if image_system_prompt is not None
        else (
            "# ROLE DEFINITION\n"
            "You are SlideGen-Pro, an expert presentation designer. Convert the user input into a visually appealing, "
            "creative, logical 16:9 presentation image.\n\n"
            "Do not output text, output an image.\n\n"
            "# Visual Blueprint\n"
            f"{xml}"
        )
    )
    user_prompt = str(
        metadata.get("image_prompt")
        if user_prompt_only
        else metadata.get("slide_content") or metadata.get("user_prompt") or ""
    )
    reference_image_paths = [str(path) for path in (metadata.get("reference_image_paths") or []) if path]
    reference_context = str(metadata.get("reference_context") or "").strip()
    if reference_context and system_prompt is not None:
        system_prompt = f"{system_prompt}\n\n# Reference Context\n{reference_context}"
    metadata["reference_image_paths"] = reference_image_paths
    metadata["reference_image_count"] = len(reference_image_paths)
    conversation_history = list(metadata.get("conversation_history") or [])
    current_user_parts = [{"text": user_prompt}]
    current_user_parts.extend(image_part_for_path(path) for path in reference_image_paths)
    current_user_content = {"role": "user", "parts": current_user_parts}
    body = {
        "contents": [*conversation_history, current_user_content],
        "generationConfig": {
            "temperature": agent_config.get("temperature", 1.0),
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }
    apply_gemini_thinking_config(body, agent_config.get("thinking"))
    if system_prompt is not None:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    try:
        with acquire_provider_slot(agent_config):
            resp, attempts = post_json_with_retries(
                url,
                {"Content-Type": "application/json", "x-goog-api-key": agent_config["api_key"]},
                body,
                timeout_seconds=int(metadata.get("timeout_seconds") or 180),
            )
    except Exception as exc:
        attempts = [{"attempt": 1, "error_type": exc.__class__.__name__, "error": str(exc)[:500], "transient": True}]
        evidence_metadata = public_image_generator_metadata(metadata)
        evidence_metadata.update({"attempt_count": len(attempts), "retry_attempts": attempts})
        request_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "headers": {"Content-Type": "application/json", "x-goog-api-key": "[REDACTED]"},
                    "json": redact_gemini_body(body),
                    "metadata": evidence_metadata,
                    "conversation": {
                        "mode": metadata.get("continuation_mode"),
                        "conversation_id": metadata.get("conversation_id"),
                        "history_turn_count": len(conversation_history),
                        "request_turn_count": len(body["contents"]),
                        "continued_from_slide_id": metadata.get("continued_from_slide_id"),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        response_path.write_text(
            json.dumps({"error_type": exc.__class__.__name__, "error": str(exc)[:2000], "attempts": attempts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise
    elapsed = sum(attempt.get("elapsed_seconds", 0) for attempt in attempts)
    evidence_metadata = public_image_generator_metadata(metadata)
    evidence_metadata.update({"attempt_count": len(attempts), "retry_attempts": attempts})
    request_path.write_text(
        json.dumps(
            {
                "url": url,
                "headers": {"Content-Type": "application/json", "x-goog-api-key": "[REDACTED]"},
                "json": redact_gemini_body(body),
                "metadata": evidence_metadata,
                "conversation": {
                    "mode": metadata.get("continuation_mode"),
                    "conversation_id": metadata.get("conversation_id"),
                    "history_turn_count": len(conversation_history),
                    "request_turn_count": len(body["contents"]),
                    "continued_from_slide_id": metadata.get("continued_from_slide_id"),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if resp.status_code != 200:
        response_path.write_text(
            json.dumps({"status_code": resp.status_code, "body": resp.text[:2000], "attempts": attempts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        resp.raise_for_status()

    data = resp.json()
    saved_image = False
    redacted_parts: list[dict[str, object]] = []
    text_parts: list[str] = []
    model_content: dict | None = None
    thought_signature_count = 0
    for candidate in data.get("candidates", []):
        candidate_content = candidate.get("content") or {}
        if model_content is None and candidate_content:
            model_content = candidate_content
        for part in candidate_content.get("parts", []):
            if part.get("thoughtSignature") or part.get("thought_signature"):
                thought_signature_count += 1
            inline_data = part.get("inlineData") or part.get("inline_data")
            if inline_data and inline_data.get("data") and not part.get("thought"):
                image_bytes = base64.b64decode(inline_data["data"])
                output.write_bytes(image_bytes)
                saved_image = True
                redacted_parts.append(redact_gemini_part(part, image_path=str(output), byte_length=len(image_bytes)))
            elif "text" in part:
                text_parts.append(part["text"])
                redacted_parts.append(redact_gemini_part(part))
            else:
                redacted_parts.append(redact_gemini_part(part))

    conversation_id = data.get("sessionId") or data.get("conversationId") or metadata.get("conversation_id")
    response_path.write_text(
        json.dumps(
            {
                "status_code": resp.status_code,
                "elapsed_seconds": round(elapsed, 3),
                "model_version": data.get("modelVersion"),
                "response_id": data.get("responseId"),
                "parts": redacted_parts,
                "conversation_id": conversation_id,
                "conversation_mode": metadata.get("continuation_mode"),
                "thought_signature_count": thought_signature_count,
                "model_content": redact_gemini_content(model_content or {}),
                "attempts": attempts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if not saved_image:
        raise RuntimeError(f"Image generator provider returned no inline image bytes; text={text_parts[:1]}")
    return {
        "request": {
            "path": str(request_path),
            "metadata": evidence_metadata,
            "conversation": {
                "mode": metadata.get("continuation_mode"),
                "conversation_id": conversation_id,
                "history_turn_count": len(conversation_history),
                "request_turn_count": len(body["contents"]),
                "continued_from_slide_id": metadata.get("continued_from_slide_id"),
            },
        },
        "response": {
            "path": str(response_path),
            "image_path": str(output),
            "conversation_id": conversation_id,
            "thought_signature_count": thought_signature_count,
        },
        "_next_conversation_history": [*body["contents"], model_content] if model_content else None,
    }


def run_image_route(context) -> ImageRouteOutcome:
    import db as dbmod
    from backend.services import model_profiles

    run = context.run
    deck = context.deck
    requirement = context.requirement
    color = context.color
    route_metadata = {}
    if run.get("route_metadata"):
        try:
            route_metadata = json.loads(run["route_metadata"])
        except json.JSONDecodeError:
            route_metadata = {}

    combo_name = output_dir_name(run, requirement, color)
    output_dir = os.path.join(ARTIFACTS_DIR, combo_name)
    os.makedirs(output_dir, exist_ok=True)
    dbmod.update_run(run["id"], output_dir=output_dir)

    model_call_metadata: dict[str, dict] = {}
    stage_artifacts: dict[str, dict] = {}
    content_metadata_lock = threading.Lock()
    strategy = run.get("strategy") or "image_5_0"
    effective_color = dict(color)
    seed_palette_lineage: SeedPaletteLineage | None = None
    generated_cover_image_path: str | None = None
    image_1_0_anchor_history: list[dict] | None = None
    image_1_0_anchor_slide_id: int | None = None
    image_1_0_anchor_image_path: str | None = None
    image_1_0_anchor_conversation_id: str | None = None
    seed_reference_image_path: str | None = None
    seed_reference_xml: str | None = None
    cover_slide = next((item for item in context.run_slides if item.get("slide_type") == "cover"), None)
    seed_slide = next((item for item in context.run_slides if item.get("slide_type") == "content"), None)
    content_slides = [item for item in context.run_slides if item.get("slide_type") != "cover"]
    if strategy == "image_direct":
        from backend.services import codex_audit, codex_native_image, model_profiles

        prompt_role = "image_direct"
        ordered_slides = sorted(context.run_slides, key=lambda item: item["position"])
        slide_stems = _unique_slide_artifact_stems(ordered_slides)
        direct_metadata_lock = threading.Lock()
        native_route_metadata = route_metadata.get("native_image")
        native_config_route = model_profiles.native_image_route_for_config(context.config_row)
        native_direct = (
            route_metadata.get("image_renderer") == model_profiles.NATIVE_IMAGE_ADAPTER
            and native_route_metadata
            == {"adapter": model_profiles.NATIVE_IMAGE_ADAPTER, "route": "image_direct"}
            and native_config_route == "image_direct"
            and context.image_generator_config.get("api_type") == model_profiles.NATIVE_IMAGE_API_TYPE
        )
        native_route_requested = (
            route_metadata.get("image_renderer") == model_profiles.NATIVE_IMAGE_ADAPTER
            or native_route_metadata is not None
            or native_config_route is not None
            or context.image_generator_config.get("api_type") == model_profiles.NATIVE_IMAGE_API_TYPE
        )
        if native_route_requested and not native_direct:
            raise ValueError("Native Direct requires the server-resolved Native Direct configuration")

        native_not_applicable = {
            "director": "not_applicable",
            "director_xml_raw": "not_applicable",
            "director_xml_clean": "not_applicable",
            "seed_image": "not_applicable",
            "seed_xml": "not_applicable",
            "style_dna": "not_applicable",
            "reference_dependencies": "not_applicable",
        }

        def remove_native_business_output(value: object) -> None:
            if not isinstance(value, str) or not value:
                return
            try:
                candidate = Path(value).resolve()
                artifact_root = Path(ARTIFACTS_DIR).resolve()
                candidate.relative_to(artifact_root)
                if ".codex-private" in candidate.relative_to(artifact_root).parts:
                    return
                if candidate.is_file() or candidate.is_symlink():
                    candidate.unlink()
            except (OSError, ValueError):
                return

        def clear_native_displayable_version(run_slide_id: int) -> None:
            """A failed current Native attempt may not retain an active old PNG."""
            conn = dbmod.get_db()
            try:
                conn.execute(
                    """UPDATE artifact_versions
                       SET final_image_path = NULL
                       WHERE target_run_slide_id = ? AND artifact_run_slide_id = ?""",
                    (run_slide_id, run_slide_id),
                )
                conn.execute(
                    "DELETE FROM active_artifact_versions WHERE target_run_slide_id = ?",
                    (run_slide_id,),
                )
                conn.commit()
            finally:
                conn.close()

        def process_image_direct_slide(rs: dict) -> None:
            stale_final_image_path = rs.get("final_image_path") if native_direct else None
            dbmod.update_run_slide(rs["id"], status="running")
            slide_stem = slide_stems[int(rs["id"])]
            prompt = render_image_direct_prompt(rs)
            prompt_path = os.path.join(output_dir, f"{slide_stem}_image_direct_prompt.txt")
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
            try:
                image_path = os.path.join(output_dir, f"{slide_stem}.png")
                image_metadata = {
                    "run_id": run["id"],
                    "run_slide_id": rs["id"],
                    "strategy": strategy,
                    "slide_type": rs.get("slide_type"),
                    "slide_content": rs["slide_content"],
                    "image_prompt_mode": "user_prompt_only",
                    "image_prompt": prompt,
                    "prompt_role": prompt_role,
                    "timeout_seconds": context.timeout_seconds,
                }
                if native_direct:
                    image_metadata["route_metadata"] = route_metadata
                    native_result = codex_native_image.generate_codex_native_image(
                        context.image_generator_config,
                        prompt,
                        image_path,
                        run_id=run["id"],
                        run_slide_id=rs["id"],
                        stage_id="image-generation",
                        output_dir=output_dir,
                        timeout_seconds=context.timeout_seconds,
                        metadata=image_metadata,
                    )
                    image_result = {
                        "response": {"image_path": image_path},
                        "native_public": codex_audit.public_native_image_projection(
                            native_result.get("native_public")
                        ),
                    }
                else:
                    image_result = generate_image_generator(
                        context.image_generator_config,
                        "",
                        image_path,
                        **image_metadata,
                    )
                image_result.pop("_next_conversation_history", None)
                response_conversation_id = None if native_direct else (image_result.get("response") or {}).get("conversation_id")
                image_request_path = (image_result.get("request") or {}).get("path")
                image_response_path = (image_result.get("response") or {}).get("path")
                image_provider_request = provider_request_evidence_from_path(image_request_path)
                slide_artifacts = {
                    "direct_prompt": {
                        "prompt_role": prompt_role,
                        "prompt_path": prompt_path,
                        "rendered_prompt": prompt,
                    },
                    "image": image_result,
                }
                if native_direct:
                    slide_artifacts["direct_prompt"]["not_applicable"] = native_not_applicable
                slide_artifacts["request_chain"] = request_chain_evidence(
                    strategy=strategy,
                    slide=rs,
                    prompt_role=prompt_role,
                    planned_chain=["direct_image_generation"],
                    actual_evidence={
                        "prompt_path": prompt_path,
                        "image_request_path": image_request_path,
                        "image_response_path": image_response_path,
                    },
                    model=context.image_generator_config.get("model"),
                    stages=[
                        request_chain_stage_evidence(
                            stage_id="image-generation",
                            stage_name="Image Generation",
                            role="image_generator",
                            prompt_role=prompt_role,
                            model_config=context.image_generator_config,
                            prompt_path=prompt_path,
                            request_path=image_request_path,
                            response_path=image_response_path,
                            artifact_path=image_path,
                            provider_request=image_provider_request,
                            health="complete",
                        )
                    ],
                    references={},
                )
                slide_artifacts["dependencies"] = slide_dependency_evidence(
                    deck=deck,
                    requirement=requirement,
                    color=effective_color,
                    slide=rs,
                    prompt_role=prompt_role,
                    model_config=context.image_generator_config,
                    extra={
                        "image_direct": {
                            "input_scope": "current_slide_only",
                            "uses_requirement": False,
                            "uses_color": False,
                            "uses_design_director": False,
                            "route_metadata": route_metadata,
                        }
                    },
                )
                slide_artifacts["evidence_health"] = "complete"
                dbmod.update_run_slide(
                    rs["id"],
                    status="completed",
                    final_image_path=image_path,
                    stage_artifacts=json.dumps(slide_artifacts, ensure_ascii=False),
                    conversation_id=response_conversation_id,
                )
                with direct_metadata_lock:
                    stage_artifacts[f"slide_{rs['position']}"] = slide_artifacts
                    model_call_metadata[f"slide_{rs['position']}"] = {
                        "direct_prompt": {
                            "role": "image_generator",
                            "prompt_role": prompt_role,
                            "prompt_path": prompt_path,
                        },
                        "image": {
                            "role": "image_generator",
                            "model": context.image_generator_config.get("model"),
                            "profile_id": context.image_generator_config.get("profile_id"),
                            "mode": "user_prompt_only",
                        },
                    }
            except Exception as slide_err:
                if native_direct:
                    remove_native_business_output(stale_final_image_path)
                    remove_native_business_output(os.path.join(output_dir, f"{slide_stem}.png"))
                    clear_native_displayable_version(rs["id"])
                    failure_artifacts = {
                        "direct_prompt": {
                            "prompt_role": prompt_role,
                            "prompt_path": prompt_path,
                            "rendered_prompt": prompt,
                            "not_applicable": native_not_applicable,
                        },
                        "image": {
                            "native_public": {
                                "terminal_state": "failed",
                                "failure_code": "native_direct_failed",
                            }
                        },
                        "evidence_health": "failed",
                    }
                    log.error("Native ImageDirect slide %d failed", rs["position"])
                    dbmod.update_run_slide(
                        rs["id"],
                        status="failed",
                        error_message="native_image_generation_failed",
                        final_image_path=None,
                        conversation_id=None,
                        stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
                    )
                    raise RuntimeError("native_image_generation_failed") from slide_err
                failure_artifacts = {
                    "direct_prompt": {
                        "prompt_role": prompt_role,
                        "prompt_path": prompt_path,
                        "rendered_prompt": prompt,
                        "error": str(slide_err),
                    }
                }
                log.error("ImageDirect slide %d failed: %s", rs["position"], slide_err)
                dbmod.update_run_slide(
                    rs["id"],
                    status="failed",
                    error_message=str(slide_err),
                    stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
                )
                raise
        if ordered_slides:
            max_workers = max(1, min(len(ordered_slides), provider_limit_for_config(context.image_generator_config)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(process_image_direct_slide, slide) for slide in ordered_slides]
                for future in as_completed(futures):
                    future.result()
        dbmod.update_run(
            run["id"],
            stage_artifacts=json.dumps(stage_artifacts, ensure_ascii=False),
            model_call_metadata=json.dumps(model_call_metadata, ensure_ascii=False),
        )
        return ImageRouteOutcome(
            status=run_status.COMPLETED,
            reason=None,
            completed_slide_ids=tuple(int(slide["id"]) for slide in ordered_slides),
            failed_slide_ids=(),
        )

    native_route_metadata = route_metadata.get("native_image")
    native_config_route = model_profiles.native_image_route_for_config(context.config_row)
    native_three_zero_director = (
        strategy == "image_3_0"
        and route_metadata.get("image_renderer") == model_profiles.NATIVE_IMAGE_ADAPTER
        and native_route_metadata
        == {"adapter": model_profiles.NATIVE_IMAGE_ADAPTER, "route": model_profiles.NATIVE_IMAGE_3_0_ROUTE}
        and native_config_route == model_profiles.NATIVE_IMAGE_3_0_ROUTE
        and context.image_designer_config.get("api_type") == model_profiles.CODEX_EXEC_API_TYPE
        and context.image_designer_config.get("model") in _native_image_3_0_director_models()
        and context.image_designer_config.get("thinking") == NATIVE_IMAGE_3_0_DIRECTOR_REASONING_EFFORT
    )
    native_three_zero_renderer = (
        native_three_zero_director
        and context.image_generator_config.get("api_type") == model_profiles.NATIVE_IMAGE_API_TYPE
        and context.image_generator_config.get("model") in _native_image_3_0_renderer_models()
        and context.image_generator_config.get("thinking") == NATIVE_IMAGE_3_0_RENDERER_REASONING_EFFORT
    )
    native_three_zero_requested = (
        route_metadata.get("image_renderer") == model_profiles.NATIVE_IMAGE_ADAPTER
        or native_route_metadata is not None
        or native_config_route is not None
    )
    if native_three_zero_requested and not native_three_zero_renderer:
        raise ValueError("Native Image 3.0 Director requires the server-resolved Native Image 3.0 configuration")

    def remove_native_three_zero_business_output(value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        try:
            candidate = Path(value).resolve()
            artifact_root = Path(ARTIFACTS_DIR).resolve()
            candidate.relative_to(artifact_root)
            if ".codex-private" in candidate.relative_to(artifact_root).parts:
                return
            if candidate.is_file() or candidate.is_symlink():
                candidate.unlink()
        except (OSError, ValueError):
            return

    def clear_native_three_zero_displayable_version(run_slide_id: int) -> None:
        """A failed current Native renderer attempt cannot retain an old PNG."""
        conn = dbmod.get_db()
        try:
            conn.execute(
                """UPDATE artifact_versions
                   SET final_image_path = NULL
                   WHERE target_run_slide_id = ? AND artifact_run_slide_id = ?""",
                (run_slide_id, run_slide_id),
            )
            conn.execute(
                "DELETE FROM active_artifact_versions WHERE target_run_slide_id = ?",
                (run_slide_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def generate_native_three_zero_image(
        *,
        prompt: str,
        output_path: str,
        run_slide_id: int,
        stage_id: str,
        reference_image_paths: list[str | Path] | None,
        metadata: dict[str, object],
    ) -> dict:
        from backend.services import codex_audit, codex_native_image

        native_result = codex_native_image.generate_codex_native_image(
            context.image_generator_config,
            prompt,
            output_path,
            run_id=run["id"],
            run_slide_id=run_slide_id,
            stage_id=stage_id,
            output_dir=output_dir,
            timeout_seconds=context.timeout_seconds,
            reference_image_paths=[Path(path) for path in (reference_image_paths or [])],
            metadata=metadata,
        )
        native_result["native_prompt_lineage"] = _native_image_prompt_lineage_for_stage(
            native_result,
            stage_id=stage_id,
            invocations=dbmod.list_codex_invocations(run_slide_id=run_slide_id),
        )
        return {
            "response": {"image_path": output_path},
            "native_public": codex_audit.public_native_image_projection(
                native_result.get("native_public")
            ),
            "native_prompt_lineage": native_result["native_prompt_lineage"],
        }

    def process_cover_slide() -> None:
        nonlocal generated_cover_image_path
        if not cover_slide:
            return
        stale_final_image_path = cover_slide.get("final_image_path") if native_three_zero_renderer else None
        dbmod.update_run_slide(cover_slide["id"], status="running")
        slide_title = sanitize_filename(cover_slide["slide_title"])
        prompt_role = "image_cover_3_1"
        prompt, director_prompt_lineage = _render_image_prompt_with_lineage(
            prompt_role,
            deck,
            requirement,
            color,
            cover_slide,
            full_content=context.confirmed_full_content,
        )
        if strategy == "image_3_0":
            if seed_palette_lineage is None:
                raise RuntimeError("image_3_0_seed_palette_lineage_missing")
            prompt = f"{prompt}\n\n{_seed_palette_lineage_prompt_block(seed_palette_lineage)}"
            director_prompt_lineage = {
                **director_prompt_lineage,
                "rendered_prompt_sha256": _sha256_text(prompt),
            }
        prompt_path = os.path.join(output_dir, f"{slide_title}_cover_prompt_3_1.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        cover_request_path = os.path.join(output_dir, f"{slide_title}_cover_prompt_request.json")
        cover_response_path = os.path.join(output_dir, f"{slide_title}_cover_prompt_response.json")
        with open(cover_request_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "role": "image_cover_3_1",
                    "prompt_role": prompt_role,
                    "model": context.image_designer_config.get("model"),
                    "api_type": context.image_designer_config.get("api_type"),
                    "endpoint": context.image_designer_config.get("endpoint"),
                    "api_key": "[REDACTED]",
                    "prompt_path": prompt_path,
                    "rendered_prompt": prompt,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        native_renderer_started = False
        try:
            if native_three_zero_director:
                cover_prompt_raw, cover_attempts = run_native_image_three_zero_director(
                    context.image_designer_config,
                    prompt,
                    run_id=run["id"],
                    run_slide_id=cover_slide["id"],
                    stage_id="cover-prompt-generation",
                    timeout_seconds=context.timeout_seconds,
                )
                cover_provider_request = None
            else:
                cover_prompt_raw, cover_attempts = call_llm_with_metadata(
                    context.image_designer_config,
                    prompt,
                    timeout_seconds=context.timeout_seconds,
                    agent_role="image_designer",
                )
                cover_provider_request = getattr(LLM_CALL_STATE, "last_request_evidence", None)
            cover_image_prompt = extract_cover_image_prompt(cover_prompt_raw)
            if native_three_zero_renderer:
                cover_image_prompt = re.sub(
                    r"^##\s*Image\s+prompt\s*\n", "", cover_image_prompt, flags=re.IGNORECASE
                ).strip()
                if strategy == "image_3_0":
                    if seed_palette_lineage is None:
                        raise RuntimeError("image_3_0_seed_palette_lineage_missing")
                    cover_image_prompt = (
                        f"{cover_image_prompt}\n\n"
                        f"{_seed_palette_lineage_prompt_block(seed_palette_lineage)}"
                    )
            with open(cover_request_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "role": "image_cover_3_1",
                        "prompt_role": prompt_role,
                        "model": context.image_designer_config.get("model"),
                        "api_type": context.image_designer_config.get("api_type"),
                        "endpoint": context.image_designer_config.get("endpoint"),
                        "api_key": "[REDACTED]",
                        "prompt_path": prompt_path,
                        "rendered_prompt": prompt,
                        "provider_request": cover_provider_request,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            with open(cover_response_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "role": "image_cover_3_1",
                        "prompt_role": prompt_role,
                        "model": context.image_designer_config.get("model"),
                        "response_text": cover_prompt_raw,
                        "image_prompt": cover_image_prompt,
                        "attempt_count": len(cover_attempts) or 1,
                        "retry_attempts": cover_attempts,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
            )
            image_path = os.path.join(output_dir, f"{slide_title}.png")
            image_metadata: dict[str, object] = {
                "run_id": run["id"],
                "run_slide_id": cover_slide["id"],
                "strategy": strategy,
                "route_metadata": route_metadata,
                "slide_type": "cover",
                "slide_content": cover_slide["slide_content"],
                "image_prompt_mode": "user_prompt_only",
                "image_prompt": cover_image_prompt,
                "prompt_role": prompt_role,
                "timeout_seconds": context.timeout_seconds,
            }
            if native_three_zero_renderer:
                native_renderer_started = True
                image_result = generate_native_three_zero_image(
                    prompt=cover_image_prompt,
                    output_path=image_path,
                    run_slide_id=cover_slide["id"],
                    stage_id="cover-image-generation",
                    reference_image_paths=[],
                    metadata=image_metadata,
                )
            else:
                image_result = generate_image_generator(
                    context.image_generator_config,
                    "",
                    image_path,
                    **image_metadata,
                )
            native_launcher_prompt_lineage = image_result.pop("native_prompt_lineage", None)
            image_request_path = (image_result.get("request") or {}).get("path")
            image_response_path = (image_result.get("response") or {}).get("path")
            image_provider_request = provider_request_evidence_from_path(image_request_path)
            generated_cover_image_path = image_path
            slide_artifacts = {
                "cover_prompt": {
                    "prompt_role": prompt_role,
                    "prompt_path": prompt_path,
                    "request_path": cover_request_path,
                    "response_path": cover_response_path,
                    "rendered_prompt": prompt,
                    "image_prompt": cover_image_prompt,
                    "attempt_count": len(cover_attempts) or 1,
                },
                "image": image_result,
            }
            slide_artifacts["request_chain"] = request_chain_evidence(
                strategy=strategy,
                slide=cover_slide,
                prompt_role=prompt_role,
                planned_chain=["cover_designer_prompt", "cover_image_generation"],
                actual_evidence={
                    "designer_request_path": cover_request_path,
                    "designer_response_path": cover_response_path,
                    "image_request_path": image_request_path,
                    "image_response_path": image_response_path,
                },
                model=context.image_generator_config.get("model"),
                stages=[
                    request_chain_stage_evidence(
                        stage_id="cover-prompt-generation",
                        stage_name="Cover Prompt Generation",
                        role="image_designer",
                        prompt_role=prompt_role,
                        model_config=context.image_designer_config,
                        prompt_path=prompt_path,
                        request_path=cover_request_path,
                        response_path=cover_response_path,
                        artifact_path=cover_response_path,
                        provider_request=cover_provider_request,
                        health="complete",
                        extra=director_prompt_lineage if native_three_zero_director else None,
                    ),
                    request_chain_stage_evidence(
                        stage_id="cover-image-generation",
                        stage_name="Cover Image Generation",
                        role="image_generator",
                        prompt_role="image_generator" if native_three_zero_renderer else prompt_role,
                        model_config=context.image_generator_config,
                        request_path=image_request_path,
                        response_path=image_response_path,
                        artifact_path=image_path,
                        provider_request=image_provider_request,
                        health="complete",
                        extra=(
                            image_generation_stage_extra(
                                provider_request=image_provider_request,
                                response_path=image_response_path,
                                image_result=image_result,
                            )
                            | {"native_image": image_result["native_public"]}
                            | native_launcher_prompt_lineage
                            if native_three_zero_renderer
                            else image_generation_stage_extra(
                                provider_request=image_provider_request,
                                response_path=image_response_path,
                                image_result=image_result,
                            )
                        ),
                    ),
                ],
                references={},
            )
            slide_artifacts["dependencies"] = slide_dependency_evidence(
                deck=deck,
                requirement=requirement,
                color=effective_color,
                slide=cover_slide,
                prompt_role=prompt_role,
                model_config=context.image_generator_config,
                extra={"cover_prompt": {"image_prompt_length": len(cover_image_prompt)}},
            )
            slide_artifacts["evidence_health"] = "complete"
            response_conversation_id = (image_result.get("response") or {}).get("conversation_id")
            dbmod.update_run_slide(
                cover_slide["id"],
                status="completed",
                raw_response=cover_prompt_raw,
                final_image_path=image_path,
                stage_artifacts=json.dumps(slide_artifacts, ensure_ascii=False),
                conversation_id=response_conversation_id,
            )
            with content_metadata_lock:
                stage_artifacts[f"slide_{cover_slide['position']}"] = slide_artifacts
                model_call_metadata[f"slide_{cover_slide['position']}"] = {
                    "cover_prompt": {
                        "role": "image_cover_3_1",
                        "prompt_role": prompt_role,
                        "model": context.image_designer_config.get("model"),
                        "profile_id": context.image_designer_config.get("profile_id"),
                        "prompt_path": prompt_path,
                        "request_path": cover_request_path,
                        "response_path": cover_response_path,
                        "attempt_count": len(cover_attempts) or 1,
                    },
                    "image": {
                        "role": "image_generator",
                        "model": context.image_generator_config.get("model"),
                        "profile_id": context.image_generator_config.get("profile_id"),
                        "mode": "user_prompt_only",
                    },
                }
        except Exception as slide_err:
            if native_three_zero_renderer and native_renderer_started:
                remove_native_three_zero_business_output(stale_final_image_path)
                remove_native_three_zero_business_output(os.path.join(output_dir, f"{slide_title}.png"))
                clear_native_three_zero_displayable_version(cover_slide["id"])
                failure_artifacts = {
                    "cover_prompt": {
                        "prompt_role": prompt_role,
                        "prompt_path": prompt_path,
                        "request_path": cover_request_path,
                        "response_path": cover_response_path,
                        "rendered_prompt": prompt,
                        "error": "native_image_generation_failed",
                    },
                    "image": {
                        "native_public": {
                            "terminal_state": "failed",
                            "failure_code": "native_image_generation_failed",
                        }
                    },
                    "evidence_health": "failed",
                }
                dbmod.update_run_slide(
                    cover_slide["id"],
                    status="failed",
                    error_message="native_image_generation_failed",
                    final_image_path=None,
                    conversation_id=None,
                    stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
                )
                raise RuntimeError("native_image_generation_failed") from slide_err
            cover_attempts = list(getattr(LLM_CALL_STATE, "last_attempts", []) or [])
            if not os.path.exists(cover_response_path):
                with open(cover_response_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "role": "image_cover_3_1",
                            "prompt_role": prompt_role,
                            "model": context.image_designer_config.get("model"),
                            "error": str(slide_err),
                            "attempt_count": len(cover_attempts) or 1,
                            "retry_attempts": cover_attempts,
                        },
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )
            failure_artifacts = {
                "cover_prompt": {
                    "prompt_role": prompt_role,
                    "prompt_path": prompt_path,
                    "request_path": cover_request_path,
                    "response_path": cover_response_path,
                    "rendered_prompt": prompt,
                    "attempt_count": len(cover_attempts) or 1,
                    "error": str(slide_err),
                }
            }
            log.error("Image cover slide %d failed: %s", cover_slide["position"], slide_err)
            dbmod.update_run_slide(
                cover_slide["id"],
                status="failed",
                error_message=str(slide_err),
                stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
            )
            raise

    if strategy != "image_3_0":
        process_cover_slide()

    if strategy == "image_5_0" and generated_cover_image_path and not str(effective_color.get("content") or "").strip():
        try:
            from backend.services.color_extraction import extract_palette_xml_from_file

            extracted_palette = extract_palette_xml_from_file(
                generated_cover_image_path,
                api_key=context.image_palette_extractor_config.get("api_key"),
                model=context.image_palette_extractor_config.get("model"),
                endpoint=context.image_palette_extractor_config.get("endpoint"),
            )
            effective_color["content"] = extracted_palette
            stage_artifacts["cover_palette"] = {
                "source": "cover_image",
                "applied": True,
                "image_path": generated_cover_image_path,
                "color_id": color.get("id"),
                "color_title": color.get("title"),
                "content_length": len(extracted_palette),
            }
        except Exception as palette_err:
            stage_artifacts["cover_palette"] = {
                "source": "cover_image",
                "applied": False,
                "status": "failed",
                "error_code": "palette_extraction_failed",
                "image_path": generated_cover_image_path,
                "color_id": color.get("id"),
                "color_title": color.get("title"),
                "error": str(palette_err),
            }
            blocked_slides = list(content_slides)
            for index, blocked_slide in enumerate(blocked_slides):
                if index == 0:
                    failure_artifacts = {
                        "error_code": "palette_extraction_failed",
                        "error": str(palette_err),
                        "request_chain": request_chain_evidence(
                            strategy=strategy,
                            slide=blocked_slide,
                            prompt_role=image_prompt_role(strategy, blocked_slide, seed_slide, cover_slide),
                            planned_chain=["cover_palette_extraction", "content_designer", "content_image_generation"],
                            actual_evidence={"cover_palette": stage_artifacts["cover_palette"]},
                            palette={"status": "failed", "source": "cover", "error_code": "palette_extraction_failed"},
                            health="failed",
                            reason="Palette extraction is required before Image 5.0 content generation.",
                        ),
                        "dependencies": slide_dependency_evidence(
                            deck=deck,
                            requirement=requirement,
                            color=effective_color,
                            slide=blocked_slide,
                            prompt_role=image_prompt_role(strategy, blocked_slide, seed_slide, cover_slide),
                            model_config=context.image_designer_config,
                            extra={"cover_palette": stage_artifacts["cover_palette"]},
                        ),
                        "evidence_health": "failed",
                    }
                    dbmod.update_run_slide(
                        blocked_slide["id"],
                        status="failed",
                        error_message=f"palette_extraction_failed: {palette_err}",
                        stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
                    )
                    stage_artifacts[f"slide_{blocked_slide['position']}"] = failure_artifacts
                else:
                    skipped_artifacts = {
                        "skip_reason": "blocked_by_palette_extraction_failed",
                        "request_chain": request_chain_evidence(
                            strategy=strategy,
                            slide=blocked_slide,
                            prompt_role=image_prompt_role(strategy, blocked_slide, seed_slide, cover_slide),
                            planned_chain=["content_designer", "content_image_generation"],
                            actual_evidence={"blocked_by": "palette_extraction_failed"},
                            palette={"status": "skipped", "source": "cover"},
                            health="skipped",
                            reason="Blocked because required cover palette extraction failed.",
                        ),
                        "dependencies": slide_dependency_evidence(
                            deck=deck,
                            requirement=requirement,
                            color=effective_color,
                            slide=blocked_slide,
                            prompt_role=image_prompt_role(strategy, blocked_slide, seed_slide, cover_slide),
                            model_config=context.image_designer_config,
                        ),
                        "evidence_health": "skipped",
                    }
                    dbmod.update_run_slide(
                        blocked_slide["id"],
                        status="skipped",
                        error_message="blocked_by_palette_extraction_failed",
                        stage_artifacts=json.dumps(skipped_artifacts, ensure_ascii=False),
                    )
                    stage_artifacts[f"slide_{blocked_slide['position']}"] = skipped_artifacts
            dbmod.update_run(
                run["id"],
                stage_artifacts=json.dumps(stage_artifacts, ensure_ascii=False),
            )
            raise

    if strategy == "image_3_2" and generated_cover_image_path:
        try:
            from backend.services.color_extraction import extract_palette_xml_from_file

            extracted_palette = extract_palette_xml_from_file(
                generated_cover_image_path,
                api_key=context.image_palette_extractor_config.get("api_key"),
                model=context.image_palette_extractor_config.get("model"),
                endpoint=context.image_palette_extractor_config.get("endpoint"),
            )
            effective_color["content"] = extracted_palette
            stage_artifacts["cover_palette"] = {
                "source": "cover_image",
                "strategy": "image_3_2",
                "applied": True,
                "image_path": generated_cover_image_path,
                "color_id": color.get("id"),
                "color_title": color.get("title"),
                "content_length": len(extracted_palette),
            }
        except Exception as palette_err:
            stage_artifacts["cover_palette"] = {
                "source": "cover_image",
                "strategy": "image_3_2",
                "applied": False,
                "status": "failed",
                "error_code": "cover_palette_extraction_failed",
                "image_path": generated_cover_image_path,
                "color_id": color.get("id"),
                "color_title": color.get("title"),
                "error": str(palette_err),
            }
            dbmod.update_run(
                run["id"],
                stage_artifacts=json.dumps(stage_artifacts, ensure_ascii=False),
            )
            raise

    ordered_slides = list(content_slides)
    if strategy in {"image_3_0", "image_3_2"} and seed_slide:
        ordered_slides = [seed_slide] + [item for item in content_slides if item["id"] != seed_slide["id"]]
    content_slide_stems: dict[int, str]
    if strategy in {"image_3_0", "image_3_2"}:
        content_slide_stems = _unique_slide_artifact_stems(ordered_slides)
    else:
        content_slide_stems = {}

    def process_content_slide(rs: dict) -> None:
        nonlocal image_1_0_anchor_history, image_1_0_anchor_slide_id, image_1_0_anchor_image_path
        nonlocal image_1_0_anchor_conversation_id, seed_reference_image_path, seed_reference_xml
        stale_final_image_path = rs.get("final_image_path") if native_three_zero_renderer else None
        dbmod.update_run_slide(rs["id"], status="running")
        slide_title = sanitize_filename(rs["slide_title"])
        slide_stem = content_slide_stems.get(int(rs["id"]), slide_title)
        prompt_role = image_prompt_role(strategy, rs, seed_slide, cover_slide)
        prompt, director_prompt_lineage = _render_image_prompt_with_lineage(
            prompt_role,
            deck,
            requirement,
            effective_color,
            rs,
            full_content=context.confirmed_full_content,
        )
        source_qualifier_guard = ""
        if native_three_zero_renderer:
            source_qualifier_guard = _source_qualifier_guard(rs.get("slide_content"))
            if source_qualifier_guard:
                prompt = f"{prompt}\n\n{source_qualifier_guard}"
                director_prompt_lineage = {
                    **director_prompt_lineage,
                    "rendered_prompt_sha256": _sha256_text(prompt),
                }
        director_image_paths: list[str] = []
        director_reference_context = ""
        if strategy == "image_3_2" and seed_slide and rs["id"] == seed_slide["id"] and generated_cover_image_path:
            director_image_paths = []
            director_reference_context = "Image 3.2 seed uses the cover-derived palette only; cover PNG is not sent."
        elif strategy in {"image_3_0", "image_3_2"} and seed_slide and rs["id"] != seed_slide["id"] and seed_reference_image_path:
            director_image_paths = [seed_reference_image_path]
            director_reference_context = (
                f"Seed slide position: {seed_slide['position']}\n"
                f"Seed XML:\n{seed_reference_xml or ''}"
            )
        if director_reference_context:
            prompt = f"{prompt}\n\n# Reference Context\n{director_reference_context}"
            director_prompt_lineage = {
                **director_prompt_lineage,
                "rendered_prompt_sha256": _sha256_text(prompt),
            }
        if strategy == "image_1_0":
            prompt_path = os.path.join(output_dir, f"{slide_title}_image_direct_prompt.txt")
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
            try:
                image_path = os.path.join(output_dir, f"{slide_title}.png")
                direct_metadata: dict[str, object] = {
                    "run_id": run["id"],
                    "run_slide_id": rs["id"],
                    "strategy": strategy,
                    "route_metadata": route_metadata,
                    "slide_type": rs.get("slide_type"),
                    "slide_content": rs["slide_content"],
                    "image_system_prompt": prompt,
                    "prompt_role": prompt_role,
                    "timeout_seconds": context.timeout_seconds,
                }
                if seed_slide and rs["id"] == seed_slide["id"] and generated_cover_image_path:
                    direct_metadata["reference_image_paths"] = [generated_cover_image_path]
                    direct_metadata["reference_context"] = "Generated cover image reference for the first content slide."
                elif seed_slide and image_1_0_anchor_history and image_1_0_anchor_image_path:
                    direct_metadata["conversation_history"] = image_1_0_anchor_history
                    direct_metadata["continuation_mode"] = "image_1_0_first_content_context"
                    direct_metadata["continued_from_slide_id"] = image_1_0_anchor_slide_id
                    direct_metadata["conversation_id"] = image_1_0_anchor_conversation_id
                    direct_metadata["reference_image_paths"] = [image_1_0_anchor_image_path]
                    direct_metadata["reference_context"] = "First content slide image reference for the continuing Image 1.0 conversation."
                image_result = generate_image_generator(
                    context.image_generator_config,
                    "",
                    image_path,
                    **direct_metadata,
                )
                next_history = image_result.pop("_next_conversation_history", None)
                response_conversation_id = (image_result.get("response") or {}).get("conversation_id")
                image_request_path = (image_result.get("request") or {}).get("path")
                image_response_path = (image_result.get("response") or {}).get("path")
                image_provider_request = provider_request_evidence_from_path(image_request_path)
                if seed_slide and rs["id"] == seed_slide["id"]:
                    image_1_0_anchor_history = next_history
                    image_1_0_anchor_slide_id = rs["id"]
                    image_1_0_anchor_image_path = image_path
                    image_1_0_anchor_conversation_id = response_conversation_id
                slide_artifacts = {
                    "direct_prompt": {
                        "prompt_role": prompt_role,
                        "prompt_path": prompt_path,
                        "rendered_prompt": prompt,
                    },
                    "image": image_result,
                }
                direct_references = {}
                if seed_slide and rs["id"] == seed_slide["id"] and generated_cover_image_path:
                    direct_references["cover_png"] = reference_image_evidence(
                        generated_cover_image_path,
                        reference_type="cover_png",
                        request_stage="image_generator",
                        source_slide=cover_slide,
                    )
                elif seed_slide and image_1_0_anchor_image_path:
                    direct_references["anchor_png"] = reference_image_evidence(
                        image_1_0_anchor_image_path,
                        reference_type="anchor_png",
                        request_stage="image_generator",
                        source_slide=seed_slide,
                    )
                slide_artifacts["request_chain"] = request_chain_evidence(
                    strategy=strategy,
                    slide=rs,
                    prompt_role=prompt_role,
                    planned_chain=["direct_image_generation"],
                    actual_evidence={
                        "prompt_path": prompt_path,
                        "image_request_path": image_request_path,
                        "image_response_path": image_response_path,
                    },
                    model=context.image_generator_config.get("model"),
                    stages=[
                        request_chain_stage_evidence(
                            stage_id="direct-image-prompt",
                            stage_name="Direct Image Prompt",
                            role="image_generator",
                            prompt_role=prompt_role,
                            model_config=context.image_generator_config,
                            prompt_path=prompt_path,
                            artifact_path=prompt_path,
                            health="complete",
                        ),
                        request_chain_stage_evidence(
                            stage_id="image-generation",
                            stage_name="Image Generation",
                            role="image_generator",
                            prompt_role=prompt_role,
                            model_config=context.image_generator_config,
                            prompt_path=prompt_path,
                            request_path=image_request_path,
                            response_path=image_response_path,
                            artifact_path=image_path,
                            provider_request=image_provider_request,
                            health="complete",
                            extra=image_generation_stage_extra(
                                provider_request=image_provider_request,
                                response_path=image_response_path,
                                image_result=image_result,
                            ),
                        ),
                    ],
                    references=direct_references,
                )
                slide_artifacts["dependencies"] = slide_dependency_evidence(
                    deck=deck,
                    requirement=requirement,
                    color=effective_color,
                    slide=rs,
                    prompt_role=prompt_role,
                    model_config=context.image_generator_config,
                )
                slide_artifacts["evidence_health"] = "complete"
                dbmod.update_run_slide(
                    rs["id"],
                    status="completed",
                    final_image_path=image_path,
                    stage_artifacts=json.dumps(slide_artifacts, ensure_ascii=False),
                    conversation_id=response_conversation_id,
                )
                with content_metadata_lock:
                    stage_artifacts[f"slide_{rs['position']}"] = slide_artifacts
                    model_call_metadata[f"slide_{rs['position']}"] = {
                        "direct_prompt": {
                            "role": "image_generator",
                            "prompt_role": prompt_role,
                            "prompt_path": prompt_path,
                        },
                        "image": {
                            "role": "image_generator",
                            "model": context.image_generator_config.get("model"),
                            "profile_id": context.image_generator_config.get("profile_id"),
                            "mode": "system_prompt",
                            "conversation": (image_result.get("request") or {}).get("conversation"),
                        },
                    }
            except Exception as slide_err:
                failure_artifacts = {
                    "direct_prompt": {
                        "prompt_role": prompt_role,
                        "prompt_path": prompt_path,
                        "rendered_prompt": prompt,
                        "error": str(slide_err),
                    }
                }
                log.error("Image direct slide %d failed: %s", rs["position"], slide_err)
                dbmod.update_run_slide(
                    rs["id"],
                    status="failed",
                    error_message=str(slide_err),
                    stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
                )
                raise
            return

        prompt_path = os.path.join(output_dir, f"{slide_stem}_image_designer_prompt.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        director_request_path = os.path.join(
            output_dir, f"{slide_stem}_image_designer_request.json"
        )
        director_response_path = os.path.join(
            output_dir, f"{slide_stem}_image_designer_response.json"
        )
        with open(director_request_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "role": "image_designer",
                    "prompt_role": prompt_role,
                    "model": context.image_designer_config.get("model"),
                    "api_type": context.image_designer_config.get("api_type"),
                    "endpoint": context.image_designer_config.get("endpoint"),
                    "api_key": "[REDACTED]",
                    "prompt_path": prompt_path,
                    "rendered_prompt": prompt,
                    "reference_image_paths": director_image_paths,
                    "reference_context": director_reference_context,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        native_renderer_started = False
        try:
            if native_three_zero_director:
                xml_raw, director_attempts = run_native_image_three_zero_director(
                    context.image_designer_config,
                    prompt,
                    run_id=run["id"],
                    run_slide_id=rs["id"],
                    stage_id="blueprint-generation",
                    timeout_seconds=context.timeout_seconds,
                    reference_image_paths=director_image_paths or None,
                )
                provider_request = None
            else:
                xml_raw, director_attempts = call_llm_with_metadata(
                    context.image_designer_config,
                    prompt,
                    timeout_seconds=context.timeout_seconds,
                    agent_role="image_designer",
                    image_paths=director_image_paths or None,
                )
                provider_request = getattr(LLM_CALL_STATE, "last_request_evidence", None)
            with open(director_request_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "role": "image_designer",
                        "prompt_role": prompt_role,
                        "model": context.image_designer_config.get("model"),
                        "api_type": context.image_designer_config.get("api_type"),
                        "endpoint": context.image_designer_config.get("endpoint"),
                        "api_key": "[REDACTED]",
                        "prompt_path": prompt_path,
                        "rendered_prompt": prompt,
                        "reference_image_paths": director_image_paths,
                        "reference_context": director_reference_context,
                        "provider_request": provider_request,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            with open(director_response_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "role": "image_designer",
                        "prompt_role": prompt_role,
                        "model": context.image_designer_config.get("model"),
                        "response_text": xml_raw,
                        "attempt_count": len(director_attempts) or 1,
                        "retry_attempts": director_attempts,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            xml_clean = clean_image_xml(xml_raw)
            xml_raw_path = os.path.join(output_dir, f"{slide_stem}_raw.xml")
            xml_clean_path = os.path.join(output_dir, f"{slide_stem}.xml")
            with open(xml_raw_path, "w", encoding="utf-8") as f:
                f.write(xml_raw)
            with open(xml_clean_path, "w", encoding="utf-8") as f:
                f.write(xml_clean)

            image_path = os.path.join(output_dir, f"{slide_stem}.png")
            dependency: dict[str, object] = {}
            image_metadata: dict[str, object] = {
                "run_id": run["id"],
                "run_slide_id": rs["id"],
                "strategy": strategy,
                "route_metadata": route_metadata,
                "slide_type": rs.get("slide_type"),
                "slide_content": rs["slide_content"],
                "timeout_seconds": context.timeout_seconds,
            }
            if source_qualifier_guard:
                image_metadata["source_qualifier_guard"] = source_qualifier_guard
            if (
                native_three_zero_renderer
                and seed_slide
                and rs["id"] == seed_slide["id"]
                and generated_cover_image_path
            ):
                image_metadata["reference_image_paths"] = [generated_cover_image_path]
                image_metadata["reference_context"] = (
                    "Generated cover image reference for the first content Seed slide."
                )
            elif strategy in {"image_3_0", "image_3_2"} and seed_slide and rs["id"] != seed_slide["id"]:
                dependency.update(
                    {
                        "seed_slide_id": seed_slide["id"],
                        "seed_slide_position": seed_slide["position"],
                    }
                )
                image_metadata["seed_dependency"] = dependency
                if seed_reference_image_path:
                    image_metadata["reference_image_paths"] = [seed_reference_image_path]
                    image_metadata["reference_context"] = (
                        f"Seed slide position: {seed_slide['position']}\n"
                        f"Seed XML:\n{seed_reference_xml or ''}"
                    )
                if strategy == "image_3_2" and cover_slide:
                    dependency["cover_reference_slide_id"] = cover_slide["id"]
                    dependency["cover_reference_position"] = cover_slide["position"]
            elif strategy == "image_3_2" and cover_slide and seed_slide and rs["id"] == seed_slide["id"]:
                dependency.update(
                    {
                        "cover_reference_slide_id": cover_slide["id"],
                        "cover_reference_position": cover_slide["position"],
                        "cover_png_sent": False,
                        "cover_reference_mode": "palette_only",
                    }
                )
                image_metadata["seed_dependency"] = dependency
                image_metadata["reference_context"] = "Image 3.2 seed image generation uses cover-derived palette only; cover PNG is not sent."

            renderer_prompt = xml_clean
            if native_three_zero_renderer:
                renderer_prompt = _native_renderer_prompt_with_source_qualifier(
                    xml_clean,
                    rs.get("slide_content"),
                )

            if native_three_zero_renderer:
                native_renderer_started = True
                image_result = generate_native_three_zero_image(
                    prompt=renderer_prompt,
                    output_path=image_path,
                    run_slide_id=rs["id"],
                    stage_id="image-generation",
                    reference_image_paths=list(image_metadata.get("reference_image_paths") or []),
                    metadata=image_metadata,
                )
            else:
                image_result = generate_image_generator(
                    context.image_generator_config,
                    xml_clean,
                    image_path,
                    **image_metadata,
                )
            native_launcher_prompt_lineage = image_result.pop("native_prompt_lineage", None)
            image_result.pop("_next_conversation_history", None)
            image_request_path = (image_result.get("request") or {}).get("path")
            image_response_path = (image_result.get("response") or {}).get("path")
            image_provider_request = provider_request_evidence_from_path(image_request_path)
            if strategy in {"image_3_0", "image_3_2"} and seed_slide and rs["id"] == seed_slide["id"]:
                seed_reference_image_path = image_path
                seed_reference_xml = xml_clean
            response_conversation_id = (image_result.get("response") or {}).get("conversation_id")
            slide_artifacts = {
                "director": {
                    "prompt_path": prompt_path,
                    "request_path": director_request_path,
                    "response_path": director_response_path,
                    "rendered_prompt": prompt,
                    "attempt_count": len(director_attempts) or 1,
                    "xml_raw_path": xml_raw_path,
                    "xml_clean_path": xml_clean_path,
                },
                "image": image_result,
            }
            references: dict[str, dict] = {}
            palette_state = {"status": "not_applicable"}
            seed_xml_state = {"status": "not_applicable"}
            if (
                native_three_zero_renderer
                and seed_slide
                and rs["id"] == seed_slide["id"]
                and generated_cover_image_path
            ):
                references["cover_png"] = reference_image_evidence(
                    generated_cover_image_path,
                    reference_type="cover_png",
                    request_stage="image_generator",
                    source_slide=cover_slide,
                )
            elif strategy == "image_3_2" and cover_slide and seed_slide and rs["id"] == seed_slide["id"]:
                references["cover_png"] = reference_image_evidence(
                    generated_cover_image_path,
                    reference_type="cover_png",
                    request_stage="designer_and_image_generator",
                    source_slide=cover_slide,
                    sent=False,
                    reason="Image 3.2 seed uses cover-derived palette only.",
                )
                palette_state = {
                    "status": "used" if str(effective_color.get("content") or "").strip() else "missing_evidence",
                    "source": "cover",
                    "sent_to_model": True,
                    "cover_png_sent": False,
                    "content_length": len(str(effective_color.get("content") or "")),
                }
            elif strategy in {"image_3_0", "image_3_2"} and seed_slide and rs["id"] != seed_slide["id"]:
                references["seed_png"] = reference_image_evidence(
                    seed_reference_image_path,
                    reference_type="seed_png",
                    request_stage="designer_and_image_generator",
                    source_slide=seed_slide,
                )
                seed_xml_state = {
                    "status": "present" if seed_reference_xml else "missing_evidence",
                    "source_slide_id": seed_slide["id"],
                    "content_length": len(seed_reference_xml or ""),
                }
            slide_artifacts["request_chain"] = request_chain_evidence(
                strategy=strategy,
                slide=rs,
                prompt_role=prompt_role,
                planned_chain=["image_designer", "xml_cleanup", "image_generator"],
                actual_evidence={
                    "designer_request_path": director_request_path,
                    "designer_response_path": director_response_path,
                    "xml_raw_path": xml_raw_path,
                    "xml_clean_path": xml_clean_path,
                    "image_request_path": image_request_path,
                    "image_response_path": image_response_path,
                },
                model=context.image_generator_config.get("model"),
                stages=[
                    request_chain_stage_evidence(
                        stage_id="blueprint-generation",
                        stage_name="Blueprint Generation",
                        role="image_designer",
                        prompt_role=prompt_role,
                        model_config=context.image_designer_config,
                        prompt_path=prompt_path,
                        request_path=director_request_path,
                        response_path=director_response_path,
                        artifact_path=xml_clean_path,
                        provider_request=provider_request,
                        health="complete",
                        extra=director_prompt_lineage if native_three_zero_director else None,
                    ),
                    request_chain_stage_evidence(
                        stage_id="image-generation",
                        stage_name="Image Generation",
                        role="image_generator",
                        prompt_role="image_generator" if native_three_zero_renderer else prompt_role,
                        model_config=context.image_generator_config,
                        prompt_path=prompt_path,
                        request_path=image_request_path,
                        response_path=image_response_path,
                        artifact_path=image_path,
                        provider_request=image_provider_request,
                        health="complete",
                        extra=image_generation_stage_extra(
                            provider_request=image_provider_request,
                            response_path=image_response_path,
                            image_result=image_result,
                            reference_bindings=references if native_three_zero_renderer else None,
                        )
                        | (
                            {"native_image": image_result["native_public"]}
                            | native_launcher_prompt_lineage
                            if native_three_zero_renderer
                            else {}
                        ),
                    ),
                ],
                references=references,
                palette=palette_state,
                seed_xml=seed_xml_state,
            )
            slide_artifacts["dependencies"] = slide_dependency_evidence(
                deck=deck,
                requirement=requirement,
                color=effective_color,
                slide=rs,
                prompt_role=prompt_role,
                model_config=context.image_generator_config,
                extra={
                    "director": {
                        "model": context.image_designer_config.get("model"),
                        "prompt_path": prompt_path,
                        "request_path": director_request_path,
                        "response_path": director_response_path,
                    },
                    "image": {
                        "request_path": image_request_path,
                        "response_path": image_response_path,
                    },
                    "seed_dependency": dependency,
                },
            )
            slide_artifacts["evidence_health"] = "complete"
            dbmod.update_run_slide(
                rs["id"],
                status="completed",
                raw_response=xml_raw,
                xml_raw=xml_raw,
                xml_clean=xml_clean,
                final_image_path=image_path,
                stage_artifacts=json.dumps(slide_artifacts, ensure_ascii=False),
                seed_dependency=json.dumps(dependency, ensure_ascii=False) if dependency else None,
                conversation_id=response_conversation_id,
            )
            with content_metadata_lock:
                stage_artifacts[f"slide_{rs['position']}"] = slide_artifacts
                model_call_metadata[f"slide_{rs['position']}"] = {
                    "director": {
                        "role": "image_designer",
                        "prompt_role": prompt_role,
                        "model": context.image_designer_config.get("model"),
                        "profile_id": context.image_designer_config.get("profile_id"),
                        "prompt_path": prompt_path,
                        "request_path": director_request_path,
                        "response_path": director_response_path,
                        "attempt_count": len(director_attempts) or 1,
                    },
                    "image": {
                        "role": "image_generator",
                        "model": context.image_generator_config.get("model"),
                        "profile_id": context.image_generator_config.get("profile_id"),
                        "conversation": (image_result.get("request") or {}).get("conversation"),
                    },
                }
        except Exception as slide_err:
            if native_three_zero_renderer and native_renderer_started:
                remove_native_three_zero_business_output(stale_final_image_path)
                remove_native_three_zero_business_output(os.path.join(output_dir, f"{slide_stem}.png"))
                clear_native_three_zero_displayable_version(rs["id"])
                failure_artifacts = {
                    "director": {
                        "prompt_role": prompt_role,
                        "prompt_path": prompt_path,
                        "request_path": director_request_path,
                        "response_path": director_response_path,
                        "rendered_prompt": prompt,
                        "error": "native_image_generation_failed",
                    },
                    "image": {
                        "native_public": {
                            "terminal_state": "failed",
                            "failure_code": "native_image_generation_failed",
                        }
                    },
                    "evidence_health": "failed",
                }
                dbmod.update_run_slide(
                    rs["id"],
                    status="failed",
                    error_message="native_image_generation_failed",
                    final_image_path=None,
                    conversation_id=None,
                    stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
                )
                raise RuntimeError("native_image_generation_failed") from slide_err
            director_attempts = list(getattr(LLM_CALL_STATE, "last_attempts", []) or [])
            if not os.path.exists(director_response_path):
                with open(director_response_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "role": "image_designer",
                            "prompt_role": prompt_role,
                            "model": context.image_designer_config.get("model"),
                            "error": str(slide_err),
                            "attempt_count": len(director_attempts) or 1,
                            "retry_attempts": director_attempts,
                        },
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )
            failure_artifacts = {
                "director": {
                    "prompt_role": prompt_role,
                    "prompt_path": prompt_path,
                    "request_path": director_request_path,
                    "response_path": director_response_path,
                    "rendered_prompt": prompt,
                    "attempt_count": len(director_attempts) or 1,
                    "error": str(slide_err),
                }
            }
            log.error("Image slide %d failed: %s", rs["position"], slide_err)
            dbmod.update_run_slide(
                rs["id"],
                status="failed",
                error_message=str(slide_err),
                stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
            )
            raise


    def run_concurrent_content_slides(slides: list[dict]) -> None:
        if not slides:
            return
        max_workers = max(
            1,
            min(
                len(slides),
                provider_limit_for_config(context.image_designer_config),
                provider_limit_for_config(context.image_generator_config),
            ),
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_content_slide, slide) for slide in slides]
            for future in as_completed(futures):
                future.result()

    def terminalize_unstarted_image_3_0_slides(
        slides: list[dict], *, reason: str
    ) -> None:
        current_by_id = {
            int(slide["id"]): slide for slide in dbmod.list_run_slides(run["id"])
        }
        for slide in slides:
            current = current_by_id.get(int(slide["id"]), slide)
            if current.get("status") in {run_status.COMPLETED, run_status.FAILED}:
                continue
            failure_artifacts = {
                "terminalization": {
                    "status": run_status.FAILED,
                    "reason": reason,
                    "source": "image_3_0_pipeline",
                },
                "evidence_health": "failed",
            }
            dbmod.update_run_slide(
                slide["id"],
                status=run_status.FAILED,
                error_message=reason,
                stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
            )
            with content_metadata_lock:
                stage_artifacts[f"slide_{slide['position']}"] = failure_artifacts

    def image_route_outcome(
        status: Literal["completed", "completed_with_failures", "failed"],
        *,
        reason: str | None,
    ) -> ImageRouteOutcome:
        slides = dbmod.list_run_slides(run["id"])
        return ImageRouteOutcome(
            status=status,
            reason=reason,
            completed_slide_ids=tuple(
                int(slide["id"])
                for slide in slides
                if slide.get("status") == run_status.COMPLETED
            ),
            failed_slide_ids=tuple(
                int(slide["id"])
                for slide in slides
                if slide.get("status") == run_status.FAILED
            ),
        )

    def persist_image_route_outcome(outcome: ImageRouteOutcome) -> ImageRouteOutcome:
        stage_artifacts["image_route_outcome"] = asdict(outcome)
        dbmod.update_run(
            run["id"],
            stage_artifacts=json.dumps(stage_artifacts, ensure_ascii=False),
            model_call_metadata=json.dumps(model_call_metadata, ensure_ascii=False),
        )
        return outcome

    def extract_image_3_0_seed_palette() -> SeedPaletteLineage:
        nonlocal seed_palette_lineage
        if not seed_slide or not seed_reference_image_path:
            raise RuntimeError("image_3_0_seed_output_missing")
        if native_three_zero_director:
            extracted_palette = run_native_image_three_zero_palette(
                context.image_palette_extractor_config,
                run_id=int(run["id"]),
                run_slide_id=int(seed_slide["id"]),
                seed_png_path=seed_reference_image_path,
                timeout_seconds=context.timeout_seconds,
            )
            palette_colors = _palette_color_values(extracted_palette)
        else:
            from backend.services.color_extraction import extract_palette_xml_from_file

            extracted_palette = extract_palette_xml_from_file(
                seed_reference_image_path,
                api_key=context.image_palette_extractor_config.get("api_key"),
                model=context.image_palette_extractor_config.get("model"),
                endpoint=context.image_palette_extractor_config.get("endpoint"),
            )
            palette_colors = _palette_color_values(extracted_palette)
        palette_sha256 = hashlib.sha256(extracted_palette.encode("utf-8")).hexdigest()
        effective_color["content"] = extracted_palette
        seed_palette_lineage = SeedPaletteLineage(
            run_id=int(run["id"]),
            run_slide_id=int(seed_slide["id"]),
            deck_position=int(seed_slide["position"]),
            extraction_stage="seed_palette_extraction",
            seed_png_sha256=hashlib.sha256(
                Path(seed_reference_image_path).read_bytes()
            ).hexdigest(),
            palette_sha256=palette_sha256,
            colors=palette_colors,
            effective_color={
                "content": extracted_palette,
                "sha256": palette_sha256,
            },
        )
        stage_artifacts["seed_palette_lineage"] = asdict(seed_palette_lineage)
        dbmod.update_run(
            run["id"],
            stage_artifacts=json.dumps(stage_artifacts, ensure_ascii=False),
        )
        return seed_palette_lineage

    def run_image_3_0_fanout(slides: list[dict]) -> list[Exception]:
        work = []
        if cover_slide:
            work.append(process_cover_slide)
        work.extend(
            (lambda slide=slide: process_content_slide(slide)) for slide in slides
        )
        if not work:
            return []
        max_workers = max(
            1,
            min(
                len(work),
                provider_limit_for_config(context.image_designer_config),
                provider_limit_for_config(context.image_generator_config),
            ),
        )
        errors: list[Exception] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(operation) for operation in work]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    errors.append(exc)
        return errors

    if strategy == "image_1_0":
        for rs in ordered_slides:
            process_content_slide(rs)
    elif strategy == "image_3_0" and seed_slide:
        remaining_slides = [
            item for item in ordered_slides if item["id"] != seed_slide["id"]
        ]
        blocked_slides = ([cover_slide] if cover_slide else []) + remaining_slides
        try:
            process_content_slide(seed_slide)
        except Exception as exc:
            reason = f"image_3_0_seed_failed: {exc}"
            terminalize_unstarted_image_3_0_slides(
                [seed_slide, *blocked_slides],
                reason=reason,
            )
            return persist_image_route_outcome(
                image_route_outcome(run_status.FAILED, reason=reason)
            )
        try:
            extract_image_3_0_seed_palette()
        except Exception as exc:
            reason = f"image_3_0_seed_palette_extraction_failed: {exc}"
            terminalize_unstarted_image_3_0_slides(
                blocked_slides,
                reason=reason,
            )
            return persist_image_route_outcome(
                image_route_outcome(
                    run_status.COMPLETED_WITH_FAILURES,
                    reason=reason,
                )
            )
        fanout_errors = run_image_3_0_fanout(remaining_slides)
        if fanout_errors:
            reason = "image_3_0_fanout_partial: " + "; ".join(
                sorted({str(error) for error in fanout_errors})
            )
            terminalize_unstarted_image_3_0_slides(
                blocked_slides,
                reason=reason,
            )
            return persist_image_route_outcome(
                image_route_outcome(
                    run_status.COMPLETED_WITH_FAILURES,
                    reason=reason,
                )
            )
        return persist_image_route_outcome(
            image_route_outcome(run_status.COMPLETED, reason=None)
        )
    elif strategy == "image_3_2" and seed_slide:
        process_content_slide(seed_slide)
        run_concurrent_content_slides([item for item in ordered_slides if item["id"] != seed_slide["id"]])
    else:
        run_concurrent_content_slides(ordered_slides)

    return persist_image_route_outcome(
        image_route_outcome(run_status.COMPLETED, reason=None)
    )


# ---------------------------------------------------------------------------
# DB-driven Pipeline Orchestration
# ---------------------------------------------------------------------------

def _is_native_image_context(context) -> bool:
    route_metadata = context.run.get("route_metadata")
    if isinstance(route_metadata, str):
        try:
            route_metadata = json.loads(route_metadata)
        except json.JSONDecodeError:
            return False
    native_image = route_metadata.get("native_image") if isinstance(route_metadata, dict) else None
    return (
        context.run.get("engine") == "image"
        and isinstance(native_image, dict)
        and native_image.get("adapter") == "codex_native"
    )


def _terminalize_cancelled_native_writer(run_id: int, reason: str) -> None:
    """Close a cancelled Native writer only through its current fenced owner."""
    from datetime import datetime, timezone

    import db as dbmod

    from backend.services import codex_supervisor

    now = datetime.now(timezone.utc)
    terminal_statuses = tuple(sorted(run_status.TERMINAL_STATUSES))
    placeholders = ", ".join("?" for _ in terminal_statuses)
    conn = dbmod.get_db()
    try:
        work_items = [
            dict(row)
            for row in conn.execute(
                f"""SELECT id, lease_owner, fencing_token
                    FROM codex_work_items
                    WHERE run_id = ? AND status NOT IN ({placeholders})""",
                (run_id, *terminal_statuses),
            ).fetchall()
        ]
        run_slide_ids = [
            int(row["id"])
            for row in conn.execute(
                f"""SELECT id FROM run_slides
                    WHERE run_id = ? AND status NOT IN ({placeholders})""",
                (run_id, *terminal_statuses),
            ).fetchall()
        ]
        invocation_ids = [
            int(row["id"])
            for row in conn.execute(
                """SELECT ci.id
                   FROM codex_invocations ci
                   JOIN codex_work_item_invocations cwi ON cwi.invocation_id = ci.id
                   JOIN codex_work_items parent ON parent.id = cwi.work_item_id
                   WHERE parent.run_id = ? AND ci.status = 'running'""",
                (run_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    for invocation_id in invocation_ids:
        codex_audit.mark_codex_invocation_interrupted(
            invocation_id,
            reason,
            ended_at=now.isoformat(),
        )
    for work_item in work_items:
        if not codex_supervisor.mark_terminal(
            work_item_id=int(work_item["id"]),
            owner=str(work_item["lease_owner"]),
            fencing_token=int(work_item["fencing_token"]),
            status=run_status.CANCELLED,
            reason=reason,
            now=now,
        ):
            raise RuntimeError("lost fenced ownership while terminalizing cancelled Native child")
    for run_slide_id in run_slide_ids:
        dbmod.update_run_slide(
            run_slide_id,
            status=run_status.CANCELLED,
            error_message=reason,
        )

    conn = dbmod.get_db()
    try:
        conn.execute(
            """UPDATE runs
               SET status = ?, error_message = ?, completed_at = datetime('now')
               WHERE id = ?""",
            (run_status.CANCELLED, reason, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def run_pipeline_from_db(run_id: int, db_path: str | None = None) -> None:
    """Execute a pipeline run using data from SQLite.

    1. Read deck, requirement, color, config, slides from DB
    2. Update run status to 'running'
    3. Run Designer Agent -> save design_principle to DB + file
    4. For each slide: run HTML Agent -> save raw/clean HTML to DB + file
    5. Screenshot all HTML files -> save paths to DB
    6. Update run status to 'completed' (or 'failed' on error)
    """
    import db as dbmod

    try:
        # ── Load run data ──
        context = load_run_context(run_id, db_path)
        run = context.run
        deck = context.deck
        requirement = context.requirement
        color = context.color
        run_slides = context.run_slides
        designer_config = context.designer_config
        html_agent_config = context.html_agent_config
        timeout_seconds = context.timeout_seconds

        if (run.get("engine") or "html") == "image":
            conn = dbmod.get_db()
            conn.execute(
                "UPDATE runs SET status = 'running', started_at = datetime('now') WHERE id = ?",
                (run_id,),
            )
            conn.commit()
            conn.close()
            outcome = run_image_route(context)
            conn = dbmod.get_db()
            conn.execute(
                """UPDATE runs
                   SET status = ?,
                       error_message = ?,
                       completed_at = datetime('now')
                   WHERE id = ?""",
                (outcome.status, outcome.reason, run_id),
            )
            conn.commit()
            conn.close()
            return

        # Mark run as running
        dbmod.update_run(run_id, status="running", started_at="datetime('now')")
        # Use raw SQL for the datetime function
        conn = dbmod.get_db()
        conn.execute(
            "UPDATE runs SET status = 'running', started_at = datetime('now') WHERE id = ?",
            (run_id,),
        )
        conn.commit()
        conn.close()

        log.info("=== Pipeline run %d starting ===", run_id)
        log.info("Deck: %s | Requirement: %s | Color: %s",
                 deck["title"], requirement["title"], color["title"])

        # Create output directory
        combo_name = output_dir_name(run, requirement, color)
        output_dir = os.path.join(ARTIFACTS_DIR, combo_name)
        os.makedirs(output_dir, exist_ok=True)
        dbmod.update_run(run_id, output_dir=output_dir)

        if (run.get("strategy") or "html_default") == "codex_html":
            log.info("── Codex HTML direct route ──")
            run_codex_html_route(context, output_dir)
            log.info("=== Pipeline run %d completed via Codex HTML direct route ===", run_id)
            return

        # ── Stage 1: Designer Agent ──
        log.info("── Stage 1: Designer Agent ──")
        designer_vars = {
            "Deck-Full-Content": context.confirmed_full_content,
            "Deck-User-Requirement": requirement["content"],
            "Deck-Required-color": color["content"],
        }
        designer_rendered_prompt = render_canonical_prompt("designer", designer_vars)
        designer_prompt = designer_rendered_prompt.prompt
        designer_prompt_skeleton = prompt_skeleton_evidence(designer_rendered_prompt)
        log.debug("Designer prompt: %d chars", len(designer_prompt))

        designer_prompt_artifact_path = os.path.join(output_dir, "design_principle_prompt.txt")
        designer_request_path = os.path.join(output_dir, "design_principle_request.json")
        designer_response_path = os.path.join(output_dir, "design_principle_response.json")
        with open(designer_prompt_artifact_path, "w", encoding="utf-8") as f:
            f.write(designer_prompt)

        raw_design, designer_attempts = call_llm_with_metadata(
            designer_config,
            designer_prompt,
            timeout_seconds=timeout_seconds,
            agent_role="designer",
        )
        designer_provider_request = getattr(LLM_CALL_STATE, "last_request_evidence", None)
        with open(designer_request_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "role": "designer",
                    "prompt_role": "designer",
                    "model": designer_config.get("model"),
                    "api_type": designer_config.get("api_type"),
                    "endpoint": designer_config.get("endpoint"),
                    "api_key": "[REDACTED]",
                    "prompt_path": designer_prompt_artifact_path,
                    "rendered_prompt": designer_prompt,
                    "prompt_skeleton": designer_prompt_skeleton,
                    "provider_request": designer_provider_request,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        with open(designer_response_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "role": "designer",
                    "prompt_role": "designer",
                    "model": designer_config.get("model"),
                    "response_text": raw_design,
                    "attempt_count": len(designer_attempts) or 1,
                    "retry_attempts": designer_attempts,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        # Save raw response to file
        raw_path = os.path.join(output_dir, "design_principle_raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_design)

        # Extract JSON design principle
        try:
            design_json = extract_json(raw_design)
            design_json_str = json.dumps(design_json, ensure_ascii=False, indent=2)
        except json.JSONDecodeError as e:
            log.error("Failed to parse Designer JSON: %s", e)
            # Use raw extracted text as fallback
            design_json_str = extract_fenced_block(raw_design, "json")
            log.warning("Using raw extracted text as design principle (may not be valid JSON)")

        # Save design principle JSON to file
        json_path = os.path.join(output_dir, "design_principle.json")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(design_json_str)
        log.info("Design principle saved: %s", json_path)

        # Update run with design principle
        dbmod.update_run(
            run_id,
            design_principle_raw=raw_design,
            design_principle_json=design_json_str,
        )

        # ── Stage 2: HTML Agent (per slide) ──
        log.info("── Stage 2: HTML Agent (per slide) ──")

        def process_html_slide(rs: dict) -> None:
            slide_position = rs["position"]
            slide_title = rs["slide_title"]
            slide_content = rs["slide_content"]

            log.info("Processing slide %d: %s", slide_position, slide_title)

            # Mark run_slide as running
            dbmod.update_run_slide(rs["id"], status="running")

            try:
                html_vars = {
                    "Deck-Design-principle": design_json_str,
                    "Deck-User-Requirement": requirement["content"],
                    "Slide-Content": slide_content,
                }
                html_rendered_prompt = render_canonical_prompt("html_agent", html_vars)
                html_prompt = html_rendered_prompt.prompt
                html_prompt_skeleton = prompt_skeleton_evidence(html_rendered_prompt)

                safe_title = sanitize_filename(slide_title)
                html_prompt_path = os.path.join(output_dir, f"{safe_title}_html_agent_prompt.txt")
                html_request_path = os.path.join(output_dir, f"{safe_title}_html_agent_request.json")
                html_response_path = os.path.join(output_dir, f"{safe_title}_html_agent_response.json")
                with open(html_prompt_path, "w", encoding="utf-8") as f:
                    f.write(html_prompt)

                raw_html_response, html_attempts = call_llm_with_metadata(
                    html_agent_config,
                    html_prompt,
                    timeout_seconds=timeout_seconds,
                    agent_role="html_agent",
                )
                html_provider_request = getattr(LLM_CALL_STATE, "last_request_evidence", None)
                with open(html_request_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "role": "html_agent",
                            "prompt_role": "html_agent",
                            "model": html_agent_config.get("model"),
                            "api_type": html_agent_config.get("api_type"),
                            "endpoint": html_agent_config.get("endpoint"),
                            "api_key": "[REDACTED]",
                            "prompt_path": html_prompt_path,
                            "rendered_prompt": html_prompt,
                            "prompt_skeleton": html_prompt_skeleton,
                            "provider_request": html_provider_request,
                        },
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )
                with open(html_response_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "role": "html_agent",
                            "prompt_role": "html_agent",
                            "model": html_agent_config.get("model"),
                            "response_text": raw_html_response,
                            "attempt_count": len(html_attempts) or 1,
                            "retry_attempts": html_attempts,
                        },
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )

                # Save raw response to file
                raw_html_path = os.path.join(output_dir, f"{safe_title}_raw.txt")
                with open(raw_html_path, "w", encoding="utf-8") as f:
                    f.write(raw_html_response)

                # Extract clean HTML
                clean_html = extract_html(raw_html_response)
                html_path = os.path.join(output_dir, f"{safe_title}.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(clean_html)
                log.info("Saved HTML: %s", html_path)

                screenshot_path = None
                try:
                    screenshot_path = screenshot_html_file(html_path, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
                except Exception as screenshot_err:
                    log.warning("Slide %d screenshot failed (non-fatal): %s", slide_position, screenshot_err)

                slide_artifacts = {
                    "designer": {
                        "prompt_path": designer_prompt_artifact_path,
                        "request_path": designer_request_path,
                        "response_path": designer_response_path,
                        "raw_path": raw_path,
                        "json_path": json_path,
                        "design_principle_length": len(design_json_str),
                        "prompt_skeleton": designer_prompt_skeleton,
                    },
                    "html_agent": {
                        "prompt_path": html_prompt_path,
                        "request_path": html_request_path,
                        "response_path": html_response_path,
                        "raw_html_path": raw_html_path,
                        "html_path": html_path,
                        "screenshot_path": screenshot_path,
                        "prompt_skeleton": html_prompt_skeleton,
                    },
                    "request_chain": request_chain_evidence(
                        strategy=run.get("strategy") or "html_default",
                        slide=rs,
                        prompt_role="html_agent",
                        planned_chain=["designer", "html_agent", "screenshot"],
                        actual_evidence={
                            "design_principle_raw_path": raw_path,
                            "design_principle_json_path": json_path,
                            "design_principle_prompt_path": designer_prompt_artifact_path,
                            "design_principle_request_path": designer_request_path,
                            "design_principle_response_path": designer_response_path,
                            "html_prompt_path": html_prompt_path,
                            "html_request_path": html_request_path,
                            "html_response_path": html_response_path,
                            "html_raw_path": raw_html_path,
                            "html_path": html_path,
                            "screenshot_path": screenshot_path,
                        },
                        model=html_agent_config.get("model"),
                        stages=[
                            request_chain_stage_evidence(
                                stage_id="design-principle-generation",
                                stage_name="Design Principle Generation",
                                role="designer",
                                prompt_role="designer",
                                model_config=designer_config,
                                prompt_path=designer_prompt_artifact_path,
                                request_path=designer_request_path,
                                response_path=designer_response_path,
                                artifact_path=json_path,
                                provider_request=designer_provider_request,
                                health="complete",
                                extra={"prompt_skeleton": designer_prompt_skeleton},
                            ),
                            request_chain_stage_evidence(
                                stage_id="html-generation",
                                stage_name="HTML Generation",
                                role="html_agent",
                                prompt_role="html_agent",
                                model_config=html_agent_config,
                                prompt_path=html_prompt_path,
                                request_path=html_request_path,
                                response_path=html_response_path,
                                artifact_path=html_path,
                                provider_request=html_provider_request,
                                health="complete",
                                extra={"prompt_skeleton": html_prompt_skeleton},
                            ),
                            request_chain_stage_evidence(
                                stage_id="screenshot",
                                stage_name="Screenshot",
                                role="screenshot",
                                prompt_role=None,
                                model_config={},
                                artifact_path=screenshot_path,
                                health="complete" if screenshot_path else "skipped",
                            ),
                        ],
                        health="complete",
                    ),
                    "dependencies": slide_dependency_evidence(
                        deck=deck,
                        requirement=requirement,
                        color=color,
                        slide=rs,
                        prompt_role="html_agent",
                        model_config=html_agent_config,
                        extra={
                            "designer": {
                                "model": designer_config.get("model"),
                                "prompt_path": designer_prompt_artifact_path,
                                "request_path": designer_request_path,
                                "response_path": designer_response_path,
                                "raw_path": raw_path,
                                "json_path": json_path,
                            },
                            "html_agent": {
                                "model": html_agent_config.get("model"),
                                "prompt_path": html_prompt_path,
                                "request_path": html_request_path,
                                "response_path": html_response_path,
                                "raw_html_path": raw_html_path,
                                "html_path": html_path,
                                "screenshot_path": screenshot_path,
                            },
                        },
                    ),
                    "evidence_health": "complete",
                }

                # Update run_slide with results as soon as this slide is displayable.
                dbmod.update_run_slide(
                    rs["id"],
                    raw_response=raw_html_response,
                    clean_html=clean_html,
                    html_path=html_path,
                    screenshot_path=screenshot_path,
                    status="completed",
                    stage_artifacts=json.dumps(slide_artifacts, ensure_ascii=False),
                )

            except Exception as slide_err:
                log.error("Slide %d failed: %s", slide_position, slide_err)
                failure_artifacts = {
                    "error": str(slide_err),
                    "request_chain": request_chain_evidence(
                        strategy=run.get("strategy") or "html_default",
                        slide=rs,
                        prompt_role="html_agent",
                        planned_chain=["designer", "html_agent", "screenshot"],
                        actual_evidence={"error": str(slide_err)},
                        model=html_agent_config.get("model"),
                        health="failed",
                    ),
                    "dependencies": slide_dependency_evidence(
                        deck=deck,
                        requirement=requirement,
                        color=color,
                        slide=rs,
                        prompt_role="html_agent",
                        model_config=html_agent_config,
                    ),
                    "evidence_health": "failed",
                }
                dbmod.update_run_slide(
                    rs["id"],
                    status="failed",
                    error_message=str(slide_err),
                    stage_artifacts=json.dumps(failure_artifacts, ensure_ascii=False),
                )

        def run_concurrent_html_slides(slides: list[dict]) -> None:
            if not slides:
                return
            max_workers = max(1, min(len(slides), provider_limit_for_config(html_agent_config)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(process_html_slide, slide) for slide in slides]
                for future in as_completed(futures):
                    future.result()

        run_concurrent_html_slides(list(run_slides))

        # ── Stage 3: Screenshots ──
        log.info("── Stage 3: Playwright Screenshot Backfill ──")
        try:
            conn = dbmod.get_db()
            missing_rows = conn.execute(
                """SELECT id, position, html_path
                   FROM run_slides
                   WHERE run_id = ?
                     AND COALESCE(html_path, '') != ''
                     AND COALESCE(screenshot_path, '') = ''
                   ORDER BY position""",
                (run_id,),
            ).fetchall()
            conn.close()

            for missing in missing_rows:
                png_path = screenshot_html_file(missing["html_path"], VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
                dbmod.update_run_slide(missing["id"], screenshot_path=png_path)

            log.info("Screenshot backfill completed: %d missing file(s)", len(missing_rows))

        except Exception as screenshot_err:
            log.warning("Screenshot stage failed (non-fatal): %s", screenshot_err)

        # ── Mark run as completed ──
        conn = dbmod.get_db()
        conn.execute(
            "UPDATE runs SET status = 'completed', completed_at = datetime('now') WHERE id = ?",
            (run_id,),
        )
        conn.commit()
        conn.close()
        log.info("=== Pipeline run %d completed ===", run_id)

    except asyncio.CancelledError:
        if "context" in locals() and _is_native_image_context(context):
            _terminalize_cancelled_native_writer(run_id, "native_image_backend_cancelled")
            return
        raise
    except Exception as e:
        log.error("Pipeline run %d failed: %s", run_id, e, exc_info=True)
        try:
            import db as dbmod
            conn = dbmod.get_db()
            conn.execute(
                "UPDATE runs SET status = 'failed', error_message = ?, completed_at = datetime('now') WHERE id = ?",
                (str(e), run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            log.error("Failed to update run status to 'failed'", exc_info=True)
