"""Environment, API constants, and paths."""
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv()

FIGURE_MIN_WIDTH = 200
FIGURE_MIN_HEIGHT = 150
IMAGES_DIR = "images"

SESSION_TTL_SEC = int(os.environ.get("SMARTDOWN_SESSION_TTL", "3600"))
SMARTDOWN_MAX_URL_PDF_BYTES = int(
    os.environ.get("SMARTDOWN_MAX_URL_PDF_BYTES", str(50 * 1024 * 1024))
)
URL_FETCH_TIMEOUT = httpx.Timeout(120.0, connect=30.0)
URL_FETCH_MAX_REDIRECTS = 8
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_MODEL_IDS = frozenset({"mistral-small-latest", "mistral-large-latest"})
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_CHAT_TIMEOUT = float(os.environ.get("OLLAMA_CHAT_TIMEOUT", "600"))
OLLAMA_MODELS_CACHE_SEC = float(os.environ.get("OLLAMA_MODELS_CACHE_SEC", "30"))
OLLAMA_USE_STRUCTURED_FORMAT = os.environ.get(
    "OLLAMA_USE_STRUCTURED_FORMAT", "true"
).lower() in ("1", "true", "yes")
OLLAMA_SCHEMA_AGENT: dict = {
    "type": "object",
    "properties": {
        "markdown": {
            "type": "string",
            "description": "The complete updated Markdown document.",
        },
        "assistant_message": {
            "type": "string",
            "description": "Short summary of changes for the chat UI.",
        },
    },
    "required": ["markdown", "assistant_message"],
}
OLLAMA_SCHEMA_QA: dict = {
    "type": "object",
    "properties": {
        "assistant_message": {
            "type": "string",
            "description": "Direct answer to the user from the document.",
        },
    },
    "required": ["assistant_message"],
}
OLLAMA_SCHEMA_NOTION_PROPS: dict = {
    "type": "object",
    "properties": {
        "property_values": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Suggested values per Notion property name; use empty string to skip.",
        },
        "assistant_message": {
            "type": "string",
            "description": "Brief note on how properties were inferred.",
        },
    },
    "required": ["property_values", "assistant_message"],
}
MISTRAL_CONTEXT_MAX_TOKENS = int(os.environ.get("MISTRAL_CONTEXT_MAX_TOKENS", "262144"))
MISTRAL_PROMPT_OVERHEAD_TOKENS = int(
    os.environ.get("MISTRAL_PROMPT_OVERHEAD_TOKENS", "22000")
)
MISTRAL_CHARS_PER_TOKEN_EST = float(
    os.environ.get("MISTRAL_CHARS_PER_TOKEN_EST", "2.8")
)
MISTRAL_MAX_DOC_CHARS_CAP = int(os.environ.get("MISTRAL_MAX_DOC_CHARS_CAP", "720000"))

NOTION_API_VERSION = os.environ.get("NOTION_API_VERSION", "2026-03-11")
