"""Prompt variable analysis for the guided prompt wizard."""

from __future__ import annotations

import re
from typing import Any

import db as dbmod
from backend.services.prompt_integrity import evaluate_prompt_integrity
from backend.services.system_variables import list_system_variables

REQUIRED_VARIABLES = {
    "designer": [
        "Deck-Full-Content",
        "Deck-User-Requirement",
        "Deck-Required-color",
    ],
    "html_agent": [
        "Deck-Design-principle",
        "Deck-User-Requirement",
        "Slide-Content",
    ],
    "evaluation_visual_qa": [],
}

VARIABLE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
CONFIRMATION_THRESHOLD = 0.8


class EmptyPromptContentError(ValueError):
    """Raised when prompt content is empty after trimming whitespace."""


def validate_prompt_content(content: str) -> str:
    normalized = content if isinstance(content, str) else ""
    if not normalized.strip():
        raise EmptyPromptContentError("Prompt content cannot be empty")
    return normalized


def extract_variables(content: str) -> list[str]:
    """Return explicit {{variable}} references in first-seen order."""
    variables: list[str] = []
    seen = set()
    for match in VARIABLE_RE.finditer(content or ""):
        variable = match.group(1).strip()
        if variable and variable not in seen:
            variables.append(variable)
            seen.add(variable)
    return variables


def llm_map_variables(
    *,
    agent_type: str,
    content: str,
    required_variables: list[str],
    present_variables: list[str],
    baseline_prompt: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantic mapping hook.

    The production default is deterministic and intentionally empty. Tests and
    later LLM wiring can monkeypatch this hook without changing the route.
    """
    return []


def analyze_prompt_variables(
    agent_type: str,
    content: str,
    baseline_prompt_id: int | None = None,
    baseline_prompt_content: str | None = None,
) -> dict[str, Any]:
    content = validate_prompt_content(content)
    configured_variables = list_system_variables(agent_type=agent_type)
    if not configured_variables and agent_type not in REQUIRED_VARIABLES:
        raise ValueError(f"Unsupported agent_type '{agent_type}'")
    disabled_variables = {
        variable["name"]
        for variable in configured_variables
        if variable["status"] == "disabled"
    }
    image_prompt_required_variables = dbmod.get_image_prompt_required_variables(agent_type)
    if image_prompt_required_variables is not None:
        required_variables = [
            variable
            for variable in image_prompt_required_variables
            if variable not in disabled_variables
        ]
    else:
        required_variables = [
            variable["name"]
            for variable in configured_variables
            if variable["status"] == "active"
        ] or REQUIRED_VARIABLES.get(agent_type, [])

    present_variables = extract_variables(content)
    present_set = set(present_variables)
    disabled_present = sorted(present_set & disabled_variables)
    baseline_prompt = dbmod.get_prompt(baseline_prompt_id) if baseline_prompt_id else None
    baseline_content = baseline_prompt_content
    if baseline_content is None and baseline_prompt:
        baseline_content = baseline_prompt.get("publish_baseline_content") or baseline_prompt["content"]
    if baseline_prompt_content is not None and baseline_prompt is None:
        baseline_prompt = {"content": baseline_prompt_content}
    llm_mappings = llm_map_variables(
        agent_type=agent_type,
        content=content,
        required_variables=required_variables,
        present_variables=present_variables,
        baseline_prompt=baseline_prompt,
    )
    llm_by_variable = {
        str(mapping.get("variable")): mapping
        for mapping in llm_mappings
        if mapping.get("variable")
    }

    mappings = []
    for variable in disabled_present:
        mappings.append(
            {
                "variable": variable,
                "target": variable,
                "confidence": 1.0,
                "status": "disabled",
                "source": "explicit",
            }
        )
    for variable in required_variables:
        if variable in present_set:
            mappings.append(
                {
                    "variable": variable,
                    "target": variable,
                    "confidence": 1.0,
                    "status": "ready",
                    "source": "explicit",
                }
            )
            continue

        llm_mapping = llm_by_variable.get(variable)
        if llm_mapping:
            confidence = float(llm_mapping.get("confidence", 0) or 0)
            status = llm_mapping.get("status") or (
                "ready" if confidence >= CONFIRMATION_THRESHOLD else "needs_confirmation"
            )
            if status == "ready" and confidence < CONFIRMATION_THRESHOLD:
                status = "needs_confirmation"
            mappings.append(
                {
                    "variable": variable,
                    "target": llm_mapping.get("target"),
                    "confidence": confidence,
                    "status": status,
                    "source": "llm",
                }
            )
            continue

        mappings.append(
            {
                "variable": variable,
                "target": None,
                "confidence": 0.0,
                "status": "missing",
                "source": "deterministic",
            }
        )

    can_save = not disabled_present and all(mapping["status"] == "ready" for mapping in mappings)
    integrity = evaluate_prompt_integrity(
        content,
        baseline_content=baseline_content,
        variable_can_save=can_save,
    )

    return {
        "agent_type": agent_type,
        "required_variables": required_variables,
        "present_variables": present_variables,
        "disabled_variables": disabled_present,
        "mappings": mappings,
        "can_save": can_save,
        **integrity,
    }
