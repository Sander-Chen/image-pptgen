"""Canonical HTML prompt skeleton rendering and parity checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


class PromptSkeletonMismatch(ValueError):
    """Raised when a rendered prompt drifts from the canonical skeleton."""


@dataclass(frozen=True)
class PromptSkeletonSpec:
    role: str
    template_path: str
    template_sha256: str
    slot_names: list[str]


@dataclass(frozen=True)
class RenderedPrompt:
    role: str
    prompt: str
    prompt_sha256: str
    template_path: str
    template_sha256: str
    slot_names: list[str]
    slot_sha256: dict[str, str]


@dataclass(frozen=True)
class PromptParityReport:
    ok: bool
    role: str
    template_path: str
    template_sha256: str
    rendered_prompt_sha256: str
    slot_names: list[str]
    slot_sha256: dict[str, str]


REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_ROOT = REPO_ROOT / "example" / "Prompt"

_PROMPT_SPECS = {
    "designer": (
        PROMPT_ROOT / "Designer-agent_v5.3.prompt.md",
        ["Deck-Full-Content", "Deck-User-Requirement", "Deck-Required-color"],
    ),
    "html_agent": (
        PROMPT_ROOT / "HTML-agent_v5.3.prompt.md",
        ["Deck-Design-principle", "Deck-User-Requirement", "Slide-Content"],
    ),
}

_ROLE_ALIASES = {
    "design": "designer",
    "designer": "designer",
    "html": "html_agent",
    "html_agent": "html_agent",
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_role(role: str) -> str:
    try:
        return _ROLE_ALIASES[role]
    except KeyError as exc:
        raise ValueError(f"unknown prompt skeleton role: {role}") from exc


def _render_template(template: str, values: dict[str, str], slot_names: list[str]) -> str:
    rendered = template
    for slot_name in slot_names:
        rendered = rendered.replace("{{" + slot_name + "}}", str(values.get(slot_name, "")))
    return rendered


def _normalize_final_newline(value: str) -> str:
    return value[:-1] if value.endswith("\n") else value


def canonical_prompt_spec(role: str) -> PromptSkeletonSpec:
    canonical_role = _canonical_role(role)
    template_path, slot_names = _PROMPT_SPECS[canonical_role]
    return PromptSkeletonSpec(
        role=canonical_role,
        template_path=str(template_path),
        template_sha256=_sha256_file(template_path),
        slot_names=list(slot_names),
    )


def render_canonical_prompt(role: str, values: dict[str, str]) -> RenderedPrompt:
    spec = canonical_prompt_spec(role)
    template_path = Path(spec.template_path)
    template = template_path.read_text(encoding="utf-8")
    prompt = _render_template(template, values, spec.slot_names)
    return RenderedPrompt(
        role=spec.role,
        prompt=prompt,
        prompt_sha256=_sha256_text(prompt),
        template_path=spec.template_path,
        template_sha256=spec.template_sha256,
        slot_names=list(spec.slot_names),
        slot_sha256={slot_name: _sha256_text(str(values.get(slot_name, ""))) for slot_name in spec.slot_names},
    )


def assert_prompt_skeleton_parity(
    role: str,
    rendered_prompt: str,
    values: dict[str, str],
) -> PromptParityReport:
    expected = render_canonical_prompt(role, values)
    if _normalize_final_newline(rendered_prompt) != _normalize_final_newline(expected.prompt):
        raise PromptSkeletonMismatch(f"{expected.role} rendered prompt does not match canonical skeleton")
    return PromptParityReport(
        ok=True,
        role=expected.role,
        template_path=expected.template_path,
        template_sha256=expected.template_sha256,
        rendered_prompt_sha256=_sha256_text(rendered_prompt),
        slot_names=list(expected.slot_names),
        slot_sha256=dict(expected.slot_sha256),
    )
