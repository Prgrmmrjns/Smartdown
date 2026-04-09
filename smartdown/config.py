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
MISTRAL_API_KEY = (os.environ.get("MISTRAL_API_KEY") or "").strip()
# If true, local PDF→MD writes figure/table/formula regions as PNGs under images/.
MISTRAL_OCR_EXTRACT_IMAGES = os.environ.get(
    "MISTRAL_OCR_EXTRACT_IMAGES", ""
).lower() in ("1", "true", "yes")
SMARTDOWN_PDF_USE_OCR = os.environ.get(
    "SMARTDOWN_PDF_USE_OCR", "true"
).lower() in ("1", "true", "yes")
MISTRAL_STRUCTURE_MODEL = os.environ.get("MISTRAL_STRUCTURE_MODEL", "mistral-small-latest")
MISTRAL_STRUCTURE_TIMEOUT = float(os.environ.get("MISTRAL_STRUCTURE_TIMEOUT", "600"))
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_CHAT_TIMEOUT = float(os.environ.get("OLLAMA_CHAT_TIMEOUT", "600"))
OLLAMA_MODELS_CACHE_SEC = float(os.environ.get("OLLAMA_MODELS_CACHE_SEC", "30"))
OLLAMA_USE_STRUCTURED_FORMAT = os.environ.get(
    "OLLAMA_USE_STRUCTURED_FORMAT", "true"
).lower() in ("1", "true", "yes")
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
OLLAMA_SCHEMA_BLOCK_NOTE: dict = {
    "type": "object",
    "properties": {
        "note_markdown": {
            "type": "string",
            "description": (
                "Exactly one Markdown bullet: one line '- **Label**: …' or '- **word** …' with a bold span after the hyphen."
            ),
        },
        "assistant_message": {
            "type": "string",
            "description": "Very short note for the chat UI.",
        },
    },
    "required": ["note_markdown", "assistant_message"],
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

# Block append + data sources are well-tested on 2025-09-03; override if you need a newer release.
NOTION_API_VERSION = (os.environ.get("NOTION_API_VERSION") or "2025-09-03").strip()
# Optional single-tenant defaults (same vars as scripts/notion_smoke_test.py).
NOTION_INTEGRATION_SECRET_DEFAULT = (
    os.environ.get("NOTION_INTEGRATION_SECRET") or ""
).strip()
NOTION_PAGE_ID_DEFAULT = (os.environ.get("NOTION_PAGE_ID") or "").strip()
