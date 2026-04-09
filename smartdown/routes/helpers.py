"""Shared helpers for HTTP routes."""

from collections.abc import Callable

from fastapi import HTTPException

from smartdown.config import (
    MISTRAL_OCR_EXTRACT_IMAGES,
    NOTION_INTEGRATION_SECRET_DEFAULT,
    NOTION_PAGE_ID_DEFAULT,
)
from smartdown.notion_tokens import normalize_notion_integration_token


def _local_pdf_image_extract_kwargs() -> dict[str, bool | str]:
    if MISTRAL_OCR_EXTRACT_IMAGES:
        return {"equation_handling": "image", "math_inline_code": False}
    return {"equation_handling": "markdown", "math_inline_code": True}


def _pick_secret_or_env(raw: str | None, default: str, normalize: Callable[[str], str], detail: str) -> str:
    t = normalize(raw or "")
    if len(t) >= 8:
        return t
    t2 = normalize(default)
    if len(t2) >= 8:
        return t2
    raise HTTPException(status_code=400, detail=detail)


def _pick_str_or_env(raw: str | None, default: str | None, detail: str) -> str:
    u = (raw or "").strip()
    if len(u) >= 8:
        return u
    u2 = (default or "").strip()
    if len(u2) >= 8:
        return u2
    raise HTTPException(status_code=400, detail=detail)


def _notion_token_from_request(raw: str | None) -> str:
    return _pick_secret_or_env(
        raw,
        NOTION_INTEGRATION_SECRET_DEFAULT,
        normalize_notion_integration_token,
        "Paste your Notion integration secret, or set NOTION_INTEGRATION_SECRET in the "
        "server environment.",
    )


def _notion_page_url_from_request(raw: str | None) -> str:
    return _pick_str_or_env(
        raw,
        NOTION_PAGE_ID_DEFAULT,
        "Paste the Notion page URL or ID, or set NOTION_PAGE_ID in the server environment.",
    )
