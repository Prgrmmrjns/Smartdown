"""Notion inspect, property suggestion, and export."""

import httpx
from fastapi import FastAPI, HTTPException

from smartdown.config import (
    NOTION_API_VERSION,
    OLLAMA_SCHEMA_NOTION_PROPS,
)
from smartdown.llm_context import (
    _parse_notion_suggest_payload,
    _shrink_markdown_for_mistral_prompt,
)
from smartdown.llm_providers import _llm_chat_json, _resolve_llm
from smartdown.models import (
    NotionExportRequest,
    NotionInspectRequest,
    NotionSuggestPropertiesRequest,
)
from smartdown.notion import (
    _default_notion_page_title,
    _notion_api_error,
    _notion_markdown_without_images,
    _notion_normalize_page_id,
    _notion_page_url,
    _notion_properties_for_client_ui,
    _notion_resolve_page_inspect,
    _notion_suggested_defaults_for_properties,
    export_notion_page_markdown,
)
from smartdown.prompts import NOTION_SUGGEST_SYSTEM_PROMPT
from smartdown.routes.helpers import _notion_page_url_from_request, _notion_token_from_request


def _token_and_page_id(notion_token: str | None, page_url: str | None) -> tuple[str, str]:
    return (
        _notion_token_from_request(notion_token),
        _notion_normalize_page_id(_notion_page_url_from_request(page_url)),
    )


def register(app: FastAPI) -> None:
    @app.post("/api/notion/inspect")
    async def api_notion_inspect(body: NotionInspectRequest):
        token, page_id = _token_and_page_id(body.notion_token, body.page_url)
        timeout = httpx.Timeout(60.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            rm = await client.get(
                "https://api.notion.com/v1/users/me",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": NOTION_API_VERSION,
                },
            )
            if rm.status_code != 200:
                _notion_api_error(rm, "checking the integration token")
            me = rm.json()
            title_prop, schema = await _notion_resolve_page_inspect(
                client, token, page_id
            )
        rows = _notion_properties_for_client_ui(schema)
        suggested = _notion_suggested_defaults_for_properties(body.markdown, rows)
        return {
            "ok": True,
            "notion_user": {
                "id": me.get("id"),
                "name": me.get("name"),
                "type": me.get("type"),
            },
            "title_property": title_prop,
            "page_id": page_id,
            "properties": rows,
            "suggested_defaults": suggested,
        }

    @app.post("/api/notion/suggest-properties")
    async def api_notion_suggest_properties(body: NotionSuggestPropertiesRequest):
        prov, model_id = await _resolve_llm(body.provider, body.model)
        lines: list[str] = []
        for p in body.properties:
            line = f"- {p.name} ({p.type})"
            if p.options:
                line += f" — options: {', '.join(p.options[:40])}"
            lines.append(line)
        md_full = body.markdown.strip()
        md_model, shrink_extra = _shrink_markdown_for_mistral_prompt(md_full)
        hint = (body.user_hint or "").strip()
        user_msg = (
            f"## Columns\n{chr(10).join(lines)}\n\n"
            f"## User hint\n{hint or '(none)'}\n\n"
            f"## Document markdown\n{md_model}\n"
            f"{shrink_extra}\n"
            "Fill property_values for every column name; use empty string when unsure."
        )
        messages = [
            {"role": "system", "content": NOTION_SUGGEST_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        try:
            raw_content = await _llm_chat_json(
                prov,
                model_id,
                messages,
                ollama_schema=OLLAMA_SCHEMA_NOTION_PROPS,
                mistral_api_key=body.mistral_api_key,
            )
            values, assistant_message = _parse_notion_suggest_payload(raw_content)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"property_values": values, "assistant_message": assistant_message}

    @app.post("/api/notion/export")
    async def api_notion_export(body: NotionExportRequest):
        token, page_id = _token_and_page_id(body.notion_token, body.page_url)
        md = body.markdown.strip()
        if not md:
            raise HTTPException(status_code=400, detail="Markdown is empty.")
        images_b64: dict[str, str] = dict(body.images) if body.include_images else {}
        if not body.include_images:
            md = _notion_markdown_without_images(md)
            if not md:
                raise HTTPException(
                    status_code=400,
                    detail="Markdown is empty after removing images. Nothing to export.",
                )
        resolved_title = (body.title or "").strip() or _default_notion_page_title(md)
        resolved_title = resolved_title[:2000]
        extra_map = dict(body.extra_properties) if body.extra_properties else {}

        timeout = httpx.Timeout(300.0, connect=30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                export_warnings = await export_notion_page_markdown(
                    client,
                    token,
                    page_id,
                    md,
                    resolved_title=resolved_title,
                    images_b64=images_b64,
                    extra_properties=extra_map,
                )
        except HTTPException:
            raise
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach Notion (network): {e!s}",
            ) from e

        return {
            "page_id": page_id,
            "notion_url": _notion_page_url(page_id),
            "warnings": export_warnings,
        }
