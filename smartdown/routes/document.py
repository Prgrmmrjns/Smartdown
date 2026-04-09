"""PDF upload, URL import, session PDF, and markdown extraction."""

import os
import tempfile
import time
import uuid
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from smartdown.fs_utils import (
    cleanup_paths,
    filename_base_from_url,
    safe_filename_base,
    temp_root,
)
from smartdown.models import ConvertFromUrlBody, ExtractMarkdownBody
from smartdown.pdf_convert import (
    _convert_pdf_at_path_to_markdown_and_images,
    extract_plain_text,
    parse_form_bool,
)
from smartdown.pdf_url import _download_pdf_from_url
from smartdown.routes.helpers import _local_pdf_image_extract_kwargs
from smartdown.session import DocumentSession, _purge_expired_sessions, _session_lock, _sessions

_CONVERT_FIXED = {"strip_page_numbers": True, "strip_citations": True}


async def _ingest_temp_pdf(
    path: str,
    *,
    filename_base: str,
    extract_markdown: bool,
    mistral_api_key: str | None,
) -> dict:
    doc_id = str(uuid.uuid4())
    if extract_markdown:
        mk = (mistral_api_key or "").strip() or None
        raw_md, imgs = _convert_pdf_at_path_to_markdown_and_images(
            path,
            mistral_api_key=mk,
            **_local_pdf_image_extract_kwargs(),
            **_CONVERT_FIXED,
        )
        deferred = False
    else:
        raw_md, imgs = "", {}
        deferred = True
    plain = extract_plain_text(path)
    async with _session_lock:
        _purge_expired_sessions()
        _sessions[doc_id] = DocumentSession(
            raw_markdown=raw_md,
            images=imgs,
            filename_base=filename_base,
            source_pdf_path=path,
            pdf_plain_text=plain,
        )
    return {
        "document_id": doc_id,
        "markdown": raw_md,
        "images": imgs,
        "filename_base": filename_base,
        "pdf_via_server": True,
        "extract_deferred": deferred,
    }


def register(app: FastAPI) -> None:
    @app.post("/api/convert")
    async def api_convert(
        file: UploadFile = File(...),
        extract_markdown: Annotated[str, Form()] = "false",
        mistral_api_key: Annotated[str | None, Form()] = None,
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
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=tmp) as tf:
                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail="Empty file.")
                tf.write(content)
                temp_pdf_path = tf.name
            out = await _ingest_temp_pdf(
                temp_pdf_path,
                filename_base=safe_filename_base(file.filename or "document.pdf"),
                extract_markdown=parse_form_bool(extract_markdown, False),
                mistral_api_key=mistral_api_key,
            )
            pdf_kept = True
            return out
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        finally:
            if temp_pdf_path and not pdf_kept:
                cleanup_paths([temp_pdf_path])

    @app.post("/api/convert-from-url")
    async def api_convert_from_url(body: ConvertFromUrlBody):
        temp_pdf_path = None
        pdf_kept = False
        tmp = temp_root()
        try:
            pdf_bytes, cd = await _download_pdf_from_url(body.url)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=tmp) as tf:
                tf.write(pdf_bytes)
                temp_pdf_path = tf.name
            out = await _ingest_temp_pdf(
                temp_pdf_path,
                filename_base=filename_base_from_url(body.url, cd),
                extract_markdown=body.extract_markdown,
                mistral_api_key=body.mistral_api_key,
            )
            pdf_kept = True
            return out
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
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"{session.filename_base}.pdf",
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
            mk = (body.mistral_api_key or "").strip() or None
            markdown_text, images = _convert_pdf_at_path_to_markdown_and_images(
                path,
                mistral_api_key=mk,
                **_local_pdf_image_extract_kwargs(),
                **_CONVERT_FIXED,
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
        return {
            "markdown": markdown_text,
            "images": images,
            "filename_base": session.filename_base,
            "extract_deferred": False,
        }
