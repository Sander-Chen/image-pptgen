"""Provider-backed model profile test gate."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

import pipeline
from backend.services import model_profiles
from backend.services.codex_exec import materialize_codex_result_final_text, run_codex_exec_json

TEST_PROMPT = "Reply with OK if this model id is callable."
CODEX_PROFILE_TEST_PROMPT = "你好。请只回复 CODEX_PROFILE_TEST_OK，不要解释，不要使用工具。"
CODEX_SMOKE_TEST_MODE = "codex_smoke"
CODEX_PROFILE_TEST_MODE = "codex_profile"
IMAGE_TEST_XML = "<slide><title>Model test</title><text>Generate a minimal verification image.</text></slide>"
VISUAL_QA_TEST_PROMPT = "Inspect the attached 1x1 image and reply with OK."
PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
TOKEN_TTL_SECONDS = 1800
_TEST_TOKENS: dict[str, dict[str, Any]] = {}


def run_profile_test(data: dict[str, Any]) -> dict[str, Any]:
    profile = model_profiles.normalize_profile(data)
    test_mode = str(data.get("test_mode") or "").strip()
    try:
        if model_profiles.is_codex_profile(profile):
            result = _run_codex_profile_test(profile, smoke=test_mode == CODEX_SMOKE_TEST_MODE)
        elif profile["role"] == "image_generator":
            result = _run_image_profile_test(profile)
        elif profile["role"] == "evaluation_visual_qa":
            result = _run_visual_qa_profile_test(profile)
        else:
            result = _run_text_profile_test(profile)
    except Exception as exc:
        return {
            "ok": False,
            "response_detail": _redact_detail(f"model={profile['model']} error={exc}", profile),
        }

    response = {
        **result,
        "ok": True,
        "tested_model": result.get("tested_model", profile["model"]),
        "tested_role": profile["role"],
    }
    if result.get("test_mode") != CODEX_SMOKE_TEST_MODE:
        response["test_token"] = _issue_test_token(profile)
    return response


def consume_test_token(token: str | None, data: dict[str, Any]) -> None:
    if not token:
        raise ValueError("Test must pass before saving this model profile")
    profile = model_profiles.normalize_profile(data)
    record = _TEST_TOKENS.pop(str(token), None)
    now = time.time()
    if not record or record["expires_at"] < now or record["profile_hash"] != _profile_hash(profile):
        raise ValueError("Test must pass before saving this model profile")


def _run_text_profile_test(profile: dict[str, Any]) -> dict[str, Any]:
    response = pipeline.call_llm(profile, TEST_PROMPT, timeout_seconds=60, agent_role=profile["role"])
    return {
        "response_preview": str(response)[:1000],
        "response_detail": _redact_detail(str(response)[:2000], profile),
    }


def _run_codex_profile_test(profile: dict[str, Any], *, smoke: bool) -> dict[str, Any]:
    model = model_profiles.CODEX_SMOKE_MODEL if smoke else profile["model"]
    effort = "low" if smoke else (profile.get("thinking") or "low")
    with tempfile.TemporaryDirectory(prefix="codex-profile-test-") as temp_dir:
        root = Path(temp_dir)
        result = asyncio.run(
            run_codex_exec_json(
                stage_id="codex_profile_test",
                role=profile["role"],
                prompt=CODEX_PROFILE_TEST_PROMPT,
                work_dir=root / "scratch",
                artifact_dir=root / "artifacts",
                model=model,
                reasoning_effort=effort,
                sandbox="read-only",
                timeout_seconds=180,
            )
        )
    if result.exit_code != 0:
        raise ValueError(f"Codex profile test exited {result.exit_code}")
    final_text = materialize_codex_result_final_text(result)
    if "CODEX_PROFILE_TEST_OK" not in final_text:
        raise ValueError("Codex profile test did not return CODEX_PROFILE_TEST_OK")
    mode = CODEX_SMOKE_TEST_MODE if smoke else CODEX_PROFILE_TEST_MODE
    return {
        "test_mode": mode,
        "tested_model": model,
        "tested_effort": effort,
        "response_preview": final_text[:1000],
        "response_detail": _redact_detail(final_text[:2000], profile),
        "elapsed_ms": result.elapsed_ms,
    }


def _run_image_profile_test(profile: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="model-profile-test-") as temp_dir:
        output_path = Path(temp_dir) / "model-test.png"
        response = pipeline.generate_image_generator(
            profile,
            IMAGE_TEST_XML,
            str(output_path),
            timeout_seconds=90,
            image_prompt_mode="user_prompt_only",
            image_prompt="Generate a simple presentation-style verification image with one geometric shape.",
            prompt_role=profile["role"],
        )
        image_bytes = output_path.read_bytes()
        preview = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
    return {
        "response_preview": "Image generated",
        "response_detail": _redact_detail(json.dumps(response, ensure_ascii=False), profile),
        "temporary_image_preview": preview,
        "temporary_image_deleted": not output_path.exists(),
    }


def _run_visual_qa_profile_test(profile: dict[str, Any]) -> dict[str, Any]:
    image_path: Path
    with tempfile.TemporaryDirectory(prefix="visual-qa-profile-test-") as temp_dir:
        image_path = Path(temp_dir) / "qa-test.png"
        image_path.write_bytes(base64.b64decode(PNG_1X1))
        response = pipeline.call_llm(
            profile,
            VISUAL_QA_TEST_PROMPT,
            timeout_seconds=60,
            agent_role=profile["role"],
            image_paths=[str(image_path)],
        )
    return {
        "response_preview": str(response)[:1000],
        "response_detail": _redact_detail(str(response)[:2000], profile),
        "temporary_image_deleted": not image_path.exists(),
    }


def _issue_test_token(profile: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(24)
    _TEST_TOKENS[token] = {
        "profile_hash": _profile_hash(profile),
        "expires_at": time.time() + TOKEN_TTL_SECONDS,
    }
    return token


def _profile_hash(profile: dict[str, Any]) -> str:
    material = {
        "role": profile["role"],
        "name": profile["name"],
        "api_type": profile["api_type"],
        "endpoint": profile["endpoint"],
        "model": profile["model"],
        "api_key": profile["api_key"],
        "temperature": profile["temperature"],
        "thinking": profile["thinking"],
        "status": profile["status"],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _redact_detail(detail: str, profile: dict[str, Any]) -> str:
    redacted = str(detail or "")
    api_key = str(profile.get("api_key") or "")
    if api_key:
        redacted = redacted.replace(api_key, "<redacted>")
    patterns = [
        r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(authorization\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(secret\s*[=:]\s*)([^\s,;]+)",
    ]
    for pattern in patterns:
        redacted = re.sub(pattern, lambda match: f"{match.group(1)}<redacted>", redacted)
    return redacted[:4000]
