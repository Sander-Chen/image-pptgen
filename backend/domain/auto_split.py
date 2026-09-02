"""Immutable AutoSplit model and effort catalogue."""

from __future__ import annotations

from dataclasses import dataclass


GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
CODEX_EXEC_ENDPOINT = "codex://exec"


@dataclass(frozen=True)
class AutoSplitModel:
    label: str
    model: str
    api_type: str
    endpoint: str

    @property
    def profile_name(self) -> str:
        return f"AutoSplit · {self.label}"


AUTO_SPLIT_MODELS = (
    AutoSplitModel("Gemini 3.1 Pro", "gemini-3.1-pro-preview", "gemini", GEMINI_ENDPOINT),
    AutoSplitModel("Gemini 3 Flash", "gemini-3-flash-preview", "gemini", GEMINI_ENDPOINT),
    AutoSplitModel(
        "Gemini 3.1 Flash Lite",
        "gemini-3.1-flash-lite-preview",
        "gemini",
        GEMINI_ENDPOINT,
    ),
    AutoSplitModel("GPT-5.3 Codex Spark", "gpt-5.3-codex-spark", "codex_exec", CODEX_EXEC_ENDPOINT),
    AutoSplitModel("GPT-5.4 Mini", "gpt-5.4-mini", "codex_exec", CODEX_EXEC_ENDPOINT),
    AutoSplitModel("GPT-5.6 Luna", "gpt-5.6-luna", "codex_exec", CODEX_EXEC_ENDPOINT),
    AutoSplitModel("GPT-5.6 Terra", "gpt-5.6-terra", "codex_exec", CODEX_EXEC_ENDPOINT),
)
AUTO_SPLIT_MODEL_IDS = frozenset(item.model for item in AUTO_SPLIT_MODELS)
THINKING_EFFORTS = frozenset({"low", "medium", "high"})
DEFAULT_AUTO_SPLIT_MODEL = "gemini-3.1-pro-preview"

