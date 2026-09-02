"""Prompt variable insertion assistant.

The LLM path is the preferred product experience. The deterministic fallback
keeps the editor usable when no assistant profile or live credentials exist.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services import model_profiles
from backend.services.prompt_integrity import build_change_report
from backend.services.prompt_variables import (
    EmptyPromptContentError,
    analyze_prompt_variables,
    validate_prompt_content,
)
from pipeline import call_llm

log = logging.getLogger("ppt-prompt-assistant")

ASSISTANT_BLOCK_HEADER = "<!-- Prompt Adding Assistant: required system variables -->"
ASSISTANT_BLOCK_FOOTER = "<!-- End Prompt Adding Assistant variables -->"


def _missing_variables(analysis: dict[str, Any]) -> list[str]:
    return [
        mapping["variable"]
        for mapping in analysis.get("mappings", [])
        if mapping.get("status") == "missing"
    ]


def deterministic_insert(content: str, missing_variables: list[str]) -> str:
    if not missing_variables:
        return content
    lines = [
        content.rstrip(),
        "",
        ASSISTANT_BLOCK_HEADER,
        *[f"{{{{{variable}}}}}" for variable in missing_variables],
        ASSISTANT_BLOCK_FOOTER,
    ]
    return "\n".join(lines).rstrip() + "\n"


def _change_report_is_safe(report: dict[str, Any]) -> bool:
    return report.get("risk_level") != "high"


def _assistant_prompt(agent_type: str, content: str, missing_variables: list[str]) -> str:
    variables = "\n".join(f"- {{{{{variable}}}}}" for variable in missing_variables)
    return f"""You are a prompt editing assistant for HTML-PPT-Gen.

Task:
Insert the missing system variable placeholders into the user's prompt.

Rules:
- Return the full edited prompt only.
- Preserve the user's original wording, order, headings, and formatting as much as possible.
- Insert each required placeholder exactly once unless the prompt already contains it.
- Do not wrap the answer in markdown fences.
- Do not explain your edits.
- Required placeholders for role {agent_type}:
{variables}

User prompt:
{content}
"""


def select_prompt_assistant_profile() -> dict[str, Any] | None:
    profiles = model_profiles.list_profiles(role="prompt_assistant", status="active")
    return profiles[0] if profiles else None


def llm_insert_variables(
    *,
    agent_type: str,
    content: str,
    missing_variables: list[str],
    profile: dict[str, Any],
) -> str:
    prompt = _assistant_prompt(agent_type, content, missing_variables)
    return call_llm(
        model_profiles.profile_to_agent_config(profile["id"]),
        prompt,
        timeout_seconds=90,
        agent_role="prompt_assistant",
    )


def assist_prompt_variables(
    agent_type: str,
    content: str,
    *,
    prefer_llm: bool = True,
) -> dict[str, Any]:
    content = validate_prompt_content(content)
    initial_analysis = analyze_prompt_variables(agent_type, content)
    missing = _missing_variables(initial_analysis)
    if not missing and initial_analysis.get("can_save"):
        return {
            "agent_type": agent_type,
            "content": content,
            "inserted_variables": [],
            "mode": "already_ready",
            "requires_review": False,
            "change_report": build_change_report(content, content, []),
            "analysis": initial_analysis,
        }

    mode = "deterministic_review_block"
    next_content = deterministic_insert(content, missing)
    assistant_error: str | None = None

    if prefer_llm and missing:
        profile = select_prompt_assistant_profile()
        if profile:
            try:
                candidate = llm_insert_variables(
                    agent_type=agent_type,
                    content=content,
                    missing_variables=missing,
                    profile=profile,
                )
                candidate_analysis = analyze_prompt_variables(agent_type, candidate)
                candidate_report = build_change_report(content, candidate, missing)
                if candidate_analysis.get("can_save") and _change_report_is_safe(candidate_report):
                    next_content = candidate
                    mode = "llm"
                elif candidate_analysis.get("can_save"):
                    assistant_error = "LLM output rewrote too much of the original prompt; deterministic fallback used."
                else:
                    assistant_error = "LLM output did not pass variable analysis; deterministic fallback used."
            except Exception as exc:  # pragma: no cover - exercised by live/provider smoke, not unit tests.
                assistant_error = str(exc)
                log.warning("Prompt assistant LLM failed; using fallback: %s", exc)

    final_analysis = analyze_prompt_variables(agent_type, next_content)
    change_report = build_change_report(content, next_content, missing)
    return {
        "agent_type": agent_type,
        "content": next_content,
        "inserted_variables": missing,
        "mode": mode,
        "requires_review": bool(missing),
        "change_report": change_report,
        "assistant_error": assistant_error,
        "analysis": final_analysis,
    }


__all__ = [
    "EmptyPromptContentError",
    "assist_prompt_variables",
    "build_change_report",
    "deterministic_insert",
]
