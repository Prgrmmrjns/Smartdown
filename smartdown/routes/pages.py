"""HTML index and lightweight JSON endpoints."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from smartdown.config import BASE_DIR, MISTRAL_MODEL, MISTRAL_MODEL_IDS, OLLAMA_HOST
from smartdown.llm_providers import _fetch_ollama_model_names


def register(app: FastAPI) -> None:
    @app.get("/api/llm-options")
    async def api_llm_options():
        ollama_models = await _fetch_ollama_model_names()
        default_mistral = (
            MISTRAL_MODEL if MISTRAL_MODEL in MISTRAL_MODEL_IDS else "mistral-small-latest"
        )
        return {
            "ollama_host": OLLAMA_HOST,
            "mistral_models": [
                {"id": "mistral-small-latest", "label": "Mistral Small (latest)"},
                {"id": "mistral-large-latest", "label": "Mistral Large (latest)"},
            ],
            "ollama_models": [{"id": n, "label": n} for n in ollama_models],
            "defaults": {"provider": "mistral", "model": default_mistral},
            "mistral_api_key_from_client": True,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(
            (BASE_DIR / "templates" / "upload.html").read_text(encoding="utf-8"),
            media_type="text/html; charset=utf-8",
        )
