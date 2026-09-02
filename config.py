import os


def _runtime_path(env_name: str, default_path: str) -> str:
    configured = os.environ.get(env_name)
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return default_path


# --- Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLE_DIR = os.path.join(BASE_DIR, "example")
DATABASE_DIR = os.path.join(EXAMPLE_DIR, "Database")
PROMPT_DIR = os.path.join(EXAMPLE_DIR, "Prompt")
ARTIFACTS_DIR = _runtime_path("PPT_ARTIFACTS_DIR", os.path.join(BASE_DIR, "artifacts"))

# --- CSV file paths ---
CSV_FULL_CONTENT = os.path.join(DATABASE_DIR, "Deck-Full-Content.csv")
CSV_USER_REQUIREMENT = os.path.join(DATABASE_DIR, "Deck-User-Requirement.csv")
CSV_REQUIRED_COLOR = os.path.join(DATABASE_DIR, "Deck-Required-color.csv")
CSV_SLIDE_DATA = os.path.join(DATABASE_DIR, "Slide-data.csv")

# --- Prompt template paths ---
DESIGNER_PROMPT = os.path.join(PROMPT_DIR, "Designer-agent_v5.3.prompt.md")
HTML_AGENT_PROMPT = os.path.join(PROMPT_DIR, "HTML-agent_v5.3.prompt.md")

# --- Model configs ---
MODEL_CONFIGS = {
    "test": {
        "designer": {
            "api_type": "gemini",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models",
            "model": "google/gemini-3.1-flash-lite-preview",
            "api_key": os.environ.get("GEMINI_API_KEY", ""),
            "temperature": 1,
            "thinking": None,
        },
        "html_agent": {
            "api_type": "gemini",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models",
            "model": "google/gemini-3.1-flash-lite-preview",
            "api_key": os.environ.get("GEMINI_API_KEY", ""),
            "temperature": 1,
            "thinking": None,
        },
    },
    "production": {
        "designer": {
            "api_type": "openai",
            "endpoint": "https://zenmux.ai/api/v1/chat/completions",
            "model": "openai/gpt-5.1",
            "api_key": os.environ.get("ZENMUX_API_KEY", ""),
            "temperature": 1,
            "thinking": "high",
        },
        "html_agent": {
            "api_type": "gemini",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models",
            "model": "gemini-3.1-pro-preview",
            "api_key": os.environ.get("GEMINI_API_KEY", ""),
            "temperature": 1,
            "thinking": "high",
        },
    },
}

# --- Active config ---
ACTIVE_PROFILE = os.environ.get("PPT_PROFILE", "test")

# --- Playwright ---
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720
