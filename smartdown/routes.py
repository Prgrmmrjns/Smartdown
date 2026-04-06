"""HTTP route handlers."""
import os
import tempfile
import time
import uuid
from typing import Annotated

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

from smartdown.config import (
    BASE_DIR,
    MISTRAL_MODEL,
    MISTRAL_MODEL_IDS,
    NOTION_API_VERSION,
    OLLAMA_HOST,
    OLLAMA_SCHEMA_AGENT,
    OLLAMA_SCHEMA_NOTION_PROPS,
    OLLAMA_SCHEMA_QA,
)
from smartdown.fs_utils import (
    cleanup_paths,
    filename_base_from_url,
    safe_filename_base,
    temp_root,
)
from smartdown.llm_context import (
    _extract_image_blocks,
    _parse_agent_payload,
    _parse_notion_suggest_payload,
    _parse_qa_payload,
    _shrink_markdown_for_mistral_prompt,
)
from smartdown.llm_providers import (
    _agent_apply_stream,
    _fetch_ollama_model_names,
    _llm_chat_json,
    _resolve_llm,
)
from smartdown.models import (
    AgentRequest,
    ConvertFromUrlBody,
    DocxExportRequest,
    ExtractMarkdownBody,
    NotionExportRequest,
    NotionInspectRequest,
    NotionSuggestPropertiesRequest,
)
from smartdown.notion import (
    _default_notion_page_title,
    _notion_api_error,
    _notion_markdown_without_images,
    _notion_merge_extra_properties,
    _notion_normalize_database_id,
    _notion_page_url,
    _notion_properties_for_client_ui,
    _notion_resolve_database_export,
    _notion_segments_to_api_blocks,
    _parse_markdown_to_notion_segments,
    _notion_suggested_defaults_for_properties,
)
from smartdown.pdf_convert import (
    _convert_pdf_at_path_to_markdown_and_images,
    extract_plain_text,
    parse_equation_handling,
    parse_form_bool,
)
from smartdown.pdf_url import _download_pdf_from_url
from smartdown.agent_fragment import prepend_fragment_to_document, wants_fragment_only_apply
from smartdown.prompts import (
    AGENT_FRAGMENT_SYSTEM_PROMPT,
    AGENT_QA_SYSTEM_PROMPT,
    AGENT_SYSTEM_PROMPT,
    NOTION_SUGGEST_SYSTEM_PROMPT,
)
from smartdown.session import DocumentSession, _purge_expired_sessions, _session_lock, _sessions
from smartdown.docx_export import markdown_to_docx_bytes


def setup_routes(app: FastAPI) -> None:
    @app.get("/api/llm-options")
    async def api_llm_options():
        ollama_models = await _fetch_ollama_model_names()
        default_mistral = (
            MISTRAL_MODEL if MISTRAL_MODEL in MISTRAL_MODEL_IDS else "mistral-small-latest"
        )
        return JSONResponse(
            {
                "ollama_host": OLLAMA_HOST,
                "mistral_models": [
                    {"id": "mistral-small-latest", "label": "Mistral Small (latest)"},
                    {"id": "mistral-large-latest", "label": "Mistral Large (latest)"},
                ],
                "ollama_models": [{"id": n, "label": n} for n in ollama_models],
                "defaults": {"provider": "mistral", "model": default_mistral},
                "mistral_api_key_from_client": True,
            }
        )


    @app.get("/", response_class=HTMLResponse)
    async def index():
        # Static HTML (no Jinja). Avoids Jinja2/Starlette template cache issues on Vercel.
        html = (BASE_DIR / "templates" / "upload.html").read_text(encoding="utf-8")
        return HTMLResponse(
            content=html,
            media_type="text/html; charset=utf-8",
        )


    @app.post("/api/convert")
    async def api_convert(
        file: UploadFile = File(...),
        strip_page_numbers: Annotated[str, Form()] = "true",
        strip_citations: Annotated[str, Form()] = "false",
        equation_handling: Annotated[str, Form()] = "markdown",
        math_inline_code: Annotated[str, Form()] = "true",
        extract_markdown: Annotated[str, Form()] = "false",
    ):
        name = (file.filename or "").lower()
        if not name.endswith(".pdf"):
            raise HTTPException(
                status_code=400, detail="Please upload a file with a .pdf extension."
            )

        temp_pdf_path = None
        pdf_kept = False
        tmp = temp_root()
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".pdf", dir=tmp
            ) as temp_pdf:
                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail="Empty file.")
                temp_pdf.write(content)
                temp_pdf_path = temp_pdf.name

            do_extract = parse_form_bool(extract_markdown, False)
            doc_id = str(uuid.uuid4())
            base_name = safe_filename_base(file.filename or "document.pdf")

            if not do_extract:
                plain = extract_plain_text(temp_pdf_path)
                async with _session_lock:
                    _purge_expired_sessions()
                    _sessions[doc_id] = DocumentSession(
                        raw_markdown="",
                        images={},
                        filename_base=base_name,
                        source_pdf_path=temp_pdf_path,
                        pdf_plain_text=plain,
                    )
                pdf_kept = True
                return JSONResponse(
                    {
                        "document_id": doc_id,
                        "markdown": "",
                        "images": {},
                        "filename_base": base_name,
                        "pdf_via_server": True,
                        "extract_deferred": True,
                    }
                )

            markdown_text, images = _convert_pdf_at_path_to_markdown_and_images(
                temp_pdf_path,
                strip_page_numbers=parse_form_bool(strip_page_numbers, True),
                strip_citations=parse_form_bool(strip_citations, False),
                equation_handling=parse_equation_handling(equation_handling),
                math_inline_code=parse_form_bool(math_inline_code, True),
            )

            async with _session_lock:
                _purge_expired_sessions()
                _sessions[doc_id] = DocumentSession(
                    raw_markdown=markdown_text,
                    images=images,
                    filename_base=base_name,
                )

            return JSONResponse(
                {
                    "document_id": doc_id,
                    "markdown": markdown_text,
                    "images": images,
                    "filename_base": base_name,
                    "extract_deferred": False,
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        finally:
            if temp_pdf_path and not pdf_kept:
                cleanup_paths([temp_pdf_path])


    @app.post("/api/convert-from-url")
    async def api_convert_from_url(body: ConvertFromUrlBody):
        temp_pdf_path: str | None = None
        pdf_kept = False
        tmp = temp_root()
        try:
            pdf_bytes, cd = await _download_pdf_from_url(body.url)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=tmp) as tf:
                tf.write(pdf_bytes)
                temp_pdf_path = tf.name

            doc_id = str(uuid.uuid4())
            base_name = filename_base_from_url(body.url, cd)

            if not body.extract_markdown:
                plain = extract_plain_text(temp_pdf_path)
                async with _session_lock:
                    _purge_expired_sessions()
                    _sessions[doc_id] = DocumentSession(
                        raw_markdown="",
                        images={},
                        filename_base=base_name,
                        source_pdf_path=temp_pdf_path,
                        pdf_plain_text=plain,
                    )
                    pdf_kept = True
                return JSONResponse(
                    {
                        "document_id": doc_id,
                        "markdown": "",
                        "images": {},
                        "filename_base": base_name,
                        "pdf_via_server": True,
                        "extract_deferred": True,
                    }
                )

            markdown_text, images = _convert_pdf_at_path_to_markdown_and_images(
                temp_pdf_path,
                strip_page_numbers=body.strip_page_numbers,
                strip_citations=body.strip_citations,
                equation_handling=body.equation_handling,
                math_inline_code=body.math_inline_code,
            )

            async with _session_lock:
                _purge_expired_sessions()
                _sessions[doc_id] = DocumentSession(
                    raw_markdown=markdown_text,
                    images=images,
                    filename_base=base_name,
                    source_pdf_path=temp_pdf_path,
                )
                pdf_kept = True

            return JSONResponse(
                {
                    "document_id": doc_id,
                    "markdown": markdown_text,
                    "images": images,
                    "filename_base": base_name,
                    "pdf_via_server": True,
                    "extract_deferred": False,
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        finally:
            if temp_pdf_path and not pdf_kept:
                cleanup_paths([temp_pdf_path])


    @app.get("/api/document/{document_id}/pdf")
    async def api_document_pdf(document_id: str):
        async with _session_lock:
            _purge_expired_sessions()
            session = _sessions.get(document_id)
        if not session or not session.source_pdf_path:
            raise HTTPException(
                status_code=404,
                detail="No server-stored PDF for this document (upload flows use your local file).",
            )
        path = session.source_pdf_path
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="PDF file is no longer available.")
        fname = f"{session.filename_base}.pdf"
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=fname,
            content_disposition_type="inline",
        )


    @app.post("/api/extract-markdown")
    async def api_extract_markdown(body: ExtractMarkdownBody):
        async with _session_lock:
            _purge_expired_sessions()
            session = _sessions.get(body.document_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail="Unknown or expired document session. Import the PDF again.",
            )
        path = session.source_pdf_path
        if not path or not os.path.isfile(path):
            raise HTTPException(
                status_code=400,
                detail="No server-stored PDF for this session, or the file expired. Re-import the PDF.",
            )
        try:
            markdown_text, images = _convert_pdf_at_path_to_markdown_and_images(
                path,
                strip_page_numbers=body.strip_page_numbers,
                strip_citations=body.strip_citations,
                equation_handling=body.equation_handling,
                math_inline_code=body.math_inline_code,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        async with _session_lock:
            s = _sessions.get(body.document_id)
            if s:
                s.raw_markdown = markdown_text
                s.images = images
                s.beautified_markdown = None
                s.created = time.time()
        return JSONResponse(
            {
                "markdown": markdown_text,
                "images": images,
                "filename_base": session.filename_base,
                "extract_deferred": False,
            }
        )


    @app.post("/api/export-docx")
    async def api_export_docx(body: DocxExportRequest):
        try:
            data = markdown_to_docx_bytes(
                body.markdown,
                images=body.images,
                include_images=body.include_images,
                title=body.title,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        base = safe_filename_base(body.filename_base or "document")
        fn = f"{base}.docx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{fn}"'},
        )


    @app.post("/api/agent")
    async def api_agent(body: AgentRequest):
        prov, model_id = await _resolve_llm(body.provider, body.model)
        async with _session_lock:
            _purge_expired_sessions()
            session = _sessions.get(body.document_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail="Unknown or expired document session. Upload the PDF again.",
            )

        msgs = body.messages
        if msgs[-1].role != "user":
            raise HTTPException(
                status_code=400, detail="The last chat message must be from the user."
            )

        prior = msgs[:-1]
        last_user = msgs[-1].content.strip()
        if not last_user:
            raise HTTPException(status_code=400, detail="Message content cannot be empty.")

        prefix = (body.instruction_prefix or "").strip()
        last_user_for_model = f"{prefix}\n\n{last_user}" if prefix else last_user

        context_md_full = (
            (body.current_markdown or "").strip()
            or session.beautified_markdown
            or session.raw_markdown
            or ""
        )
        context_md_model, shrink_extra = _shrink_markdown_for_mistral_prompt(context_md_full)

        conv_lines: list[str] = []
        for m in prior:
            label = "User" if m.role == "user" else "Assistant"
            conv_lines.append(f"{label}: {m.content.strip()}")
        conv_block = "\n".join(conv_lines) if conv_lines else "(no prior messages)"

        if not body.apply_to_document:
            qa_user = (
                f"## Prior conversation\n{conv_block}\n"
                f"{shrink_extra}"
                f"## Document (Markdown)\n\n{context_md_model}\n\n"
                f"## User question\n\n{last_user}\n\n"
                'Reply with JSON only: {"assistant_message": "your answer here"}. '
                "Do not include a markdown key."
            )
            api_messages = [
                {"role": "system", "content": AGENT_QA_SYSTEM_PROMPT},
                {"role": "user", "content": qa_user},
            ]
            try:
                raw_content = await _llm_chat_json(
                    prov,
                    model_id,
                    api_messages,
                    ollama_schema=OLLAMA_SCHEMA_QA,
                    mistral_api_key=body.mistral_api_key,
                )
                assistant_message = _parse_qa_payload(raw_content)
            except HTTPException:
                raise
            except ValueError as e:
                raise HTTPException(status_code=502, detail=str(e)) from e
            return JSONResponse(
                {
                    "markdown": None,
                    "assistant_message": assistant_message,
                }
            )

        fragment_apply = wants_fragment_only_apply(last_user, prefix, force_full=False)

        if fragment_apply:
            user_content_frag = (
                f"## Prior conversation\n{conv_block}\n"
                f"{shrink_extra}"
                f"## Current markdown\n\n{context_md_model}\n\n"
                f"## Latest instruction\n\n{last_user_for_model}\n\n"
                "### Mandatory behavior\n"
                "- Return JSON with `markdown` (new snippet only) and `assistant_message`.\n"
                "- The app prepends `markdown` to the user's editor (with a `---` separator).\n"
            )
            api_messages_frag = [
                {"role": "system", "content": AGENT_FRAGMENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_content_frag},
            ]
            try:
                raw_content = await _llm_chat_json(
                    prov,
                    model_id,
                    api_messages_frag,
                    ollama_schema=OLLAMA_SCHEMA_AGENT,
                    mistral_api_key=body.mistral_api_key,
                )
                frag_md, assistant_message = _parse_agent_payload(raw_content)
            except HTTPException:
                raise
            except ValueError as e:
                raise HTTPException(status_code=502, detail=str(e)) from e
            new_md = prepend_fragment_to_document(frag_md, context_md_full)
            async with _session_lock:
                s = _sessions.get(body.document_id)
                if s:
                    s.beautified_markdown = new_md
                    s.created = time.time()
            return JSONResponse({"markdown": new_md, "assistant_message": assistant_message})

        img_pairs = _extract_image_blocks(context_md_full)
        img_requirement = ""
        if img_pairs:
            img_requirement = (
                f"\n\n### Mandatory image lines ({len(img_pairs)} total)\n"
                "Include **every** `![](images/...)` line from the source in your output, same paths.\n"
            )

        if body.full_rewrite:
            mandatory = (
                "### Mandatory behavior\n"
                "- This is a **full structural transformation**. Completely restructure the document per the instruction.\n"
                "- Do NOT copy the original structure; produce the requested output from scratch.\n"
                "- Return the complete new document in the `markdown` field.\n\n"
                "Return the result in JSON as specified."
            )
        else:
            mandatory = (
                "### Mandatory behavior\n"
                "- The markdown above is the **authoritative source**.\n"
                "- Your `markdown` field must equal that text **except** where Latest instruction requires a change.\n"
                "- Narrow asks (e.g. remove References only): **copy** all other lines character-for-character.\n\n"
                "Return the full updated document in JSON as specified."
            )

        user_content = (
            f"## Prior conversation\n{conv_block}\n"
            f"{shrink_extra}"
            f"{img_requirement}\n"
            f"## Current markdown\n\n{context_md_model}\n\n"
            f"## Latest instruction\n\n{last_user_for_model}\n\n"
            f"{mandatory}"
        )
        api_messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            raw_content = await _llm_chat_json(
                prov,
                model_id,
                api_messages,
                ollama_schema=OLLAMA_SCHEMA_AGENT,
                mistral_api_key=body.mistral_api_key,
            )
            new_md, assistant_message = _parse_agent_payload(raw_content)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        async with _session_lock:
            s = _sessions.get(body.document_id)
            if s:
                s.beautified_markdown = new_md
                s.created = time.time()
        return JSONResponse({"markdown": new_md, "assistant_message": assistant_message})


    @app.post("/api/agent-stream")
    async def api_agent_stream(body: AgentRequest):
        """Server-Sent Events: `token` deltas (`d`), then `done` with final `markdown` + `assistant_message`."""
        return StreamingResponse(
            _agent_apply_stream(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )


    @app.post("/api/notion/inspect")
    async def api_notion_inspect(body: NotionInspectRequest):
        """Validate token, resolve database parent + schema; optional URL hints from markdown."""
        db_id = _notion_normalize_database_id(body.database_id)
        token = body.notion_token
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
            title_prop, parent, schema = await _notion_resolve_database_export(
                client, token, db_id
            )
        rows = _notion_properties_for_client_ui(schema)
        suggested = _notion_suggested_defaults_for_properties(body.markdown, rows)
        return JSONResponse(
            {
                "ok": True,
                "notion_user": {
                    "id": me.get("id"),
                    "name": me.get("name"),
                    "type": me.get("type"),
                },
                "title_property": title_prop,
                "parent": parent,
                "properties": rows,
                "suggested_defaults": suggested,
            }
        )


    @app.post("/api/notion/suggest-properties")
    async def api_notion_suggest_properties(body: NotionSuggestPropertiesRequest):
        """Use configured LLM to propose string values for Notion columns from markdown."""
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
        return JSONResponse(
            {"property_values": values, "assistant_message": assistant_message}
        )


    @app.post("/api/notion/export")
    async def api_notion_export(body: NotionExportRequest):
        """Create a row/page in a Notion database and append Markdown as blocks; images uploaded to Notion."""
        db_id = _notion_normalize_database_id(body.database_id)
        token = body.notion_token
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
        title = (body.title or "").strip() or _default_notion_page_title(md)
        title = title[:2000]

        export_warnings: list[str] = []
        segments = _parse_markdown_to_notion_segments(md)
        timeout = httpx.Timeout(300.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            title_prop, page_parent, props_schema = await _notion_resolve_database_export(
                client, token, db_id
            )
            props_payload: dict[str, dict] = {
                title_prop: {
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": title},
                        }
                    ],
                },
            }
            extra_map = dict(body.extra_properties) if body.extra_properties else {}
            props_payload.update(
                _notion_merge_extra_properties(
                    props_schema, title_prop, extra_map, warnings=export_warnings
                )
            )
            r = await client.post(
                "https://api.notion.com/v1/pages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": NOTION_API_VERSION,
                    "Content-Type": "application/json",
                },
                json={
                    "parent": page_parent,
                    "properties": props_payload,
                },
            )
            if r.status_code != 200:
                _notion_api_error(r, "creating the database page")
            page_id = r.json().get("id")
            if not isinstance(page_id, str):
                raise HTTPException(status_code=502, detail="Notion create page response missing id.")

            blocks = await _notion_segments_to_api_blocks(
                client, token, segments, images_b64, export_warnings
            )
            for off in range(0, len(blocks), 100):
                chunk = blocks[off : off + 100]
                ra = await client.patch(
                    f"https://api.notion.com/v1/blocks/{page_id}/children",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Notion-Version": NOTION_API_VERSION,
                        "Content-Type": "application/json",
                    },
                    json={"children": chunk},
                )
                if ra.status_code != 200:
                    _notion_api_error(ra, "adding page content blocks")

        return JSONResponse(
            {
                "page_id": page_id,
                "notion_url": _notion_page_url(page_id),
                "warnings": export_warnings,
            }
        )
