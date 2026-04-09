"""LLM-backed block note and clarify endpoints."""

from typing import Protocol

from fastapi import FastAPI, HTTPException

from smartdown.config import (
    OLLAMA_SCHEMA_BLOCK_NOTE,
    OLLAMA_SCHEMA_EXPLAIN_REPLACE,
    OLLAMA_SCHEMA_QA,
)
from smartdown.llm_context import (
    _max_doc_chars_for_model,
    _merge_dropped_images,
    _parse_block_note_payload,
    _parse_explain_replace_payload,
    _parse_qa_payload,
    _shrink_markdown_for_mistral_prompt,
)
from smartdown.llm_providers import _llm_chat_json, _resolve_llm
from smartdown.models import (
    AgentBlockBeautifyRequest,
    AgentBlockClarifyRequest,
    AgentBlockExcerpt,
    AgentBlockExplainReplaceRequest,
    AgentBlockNoteRequest,
)
from smartdown.prompts import (
    AGENT_BLOCK_BEAUTIFY_SYSTEM_PROMPT,
    AGENT_BLOCK_EXPLAIN_REPLACE_SYSTEM_PROMPT,
    AGENT_BLOCK_NOTE_SYSTEM_PROMPT,
    AGENT_BLOCK_QA_SYSTEM_PROMPT,
)
from smartdown.session import _purge_expired_sessions, _session_lock, _sessions

_DEFAULT_NOTE_FMT = (
    "- Output **exactly one** Markdown list item: a single line starting with `- `.\n"
    "- Immediately after `- `, begin with a **bold** word or short label using `**…**` "
    "(e.g. `- **Result**: …` or `- **Setup**: …`).\n"
    "- Summarize the excerpt faithfully in that one line; no second bullet, no headings."
)


class _BlockSelection(Protocol):
    block: AgentBlockExcerpt | None
    blocks: list[AgentBlockExcerpt] | None


def _excerpts(body: _BlockSelection) -> list[AgentBlockExcerpt]:
    if body.blocks:
        return body.blocks
    assert body.block is not None
    return [body.block]


def _cap_excerpt(md: str, max_chars: int = 14_000) -> str:
    t = md.strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 24].rstrip() + "\n\n[…truncated for model]"


def _combined_excerpts_markdown(items: list[AgentBlockExcerpt]) -> str:
    parts = [
        f"### Selection {i + 1} (document block index {e.index})\n\n{e.markdown.strip()}"
        for i, e in enumerate(items)
    ]
    return _cap_excerpt("\n\n---\n\n".join(parts))


async def _assert_session(document_id: str) -> None:
    async with _session_lock:
        _purge_expired_sessions()
        ok = document_id in _sessions
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Unknown or expired document session. Upload the PDF again.",
        )


async def _pdf_plain_for_session(document_id: str) -> str:
    async with _session_lock:
        _purge_expired_sessions()
        s = _sessions.get(document_id)
    if not s or not s.pdf_plain_text:
        return ""
    return s.pdf_plain_text.strip()


def _mistral_shrink(prov: str, text: str) -> tuple[str, str]:
    if prov == "mistral":
        return _shrink_markdown_for_mistral_prompt(text)
    return text, ""


def _user_instructions_block(instructions: str) -> str:
    t = (instructions or "").strip()
    if not t:
        return ""
    return f"## User instructions (apply to this request)\n\n{t}\n\n"


def register(app: FastAPI) -> None:
    @app.post("/api/agent-block-note")
    async def api_agent_block_note(body: AgentBlockNoteRequest):
        prov, model_id = await _resolve_llm(body.provider, body.model)
        await _assert_session(body.document_id)
        fmt = (body.format_instructions or "").strip()
        if (prefix := (body.instruction_prefix or "").strip()):
            fmt = (prefix + "\n\n" + fmt).strip()
        if not fmt:
            fmt = _DEFAULT_NOTE_FMT
        combined = _combined_excerpts_markdown(_excerpts(body))
        combined, shrink_extra = _mistral_shrink(prov, combined)
        n = len(combined)
        scale = (
            f"\n\n### Selection size\nAbout **{n} characters** — still **exactly one** `-` bullet; "
            "compress the main takeaway into one line with a bold-leading label.\n"
            if n > 500
            else ""
        )
        user_content = (
            _user_instructions_block(body.instructions)
            + f"## Note format instructions\n\n{fmt}\n{scale}\n{shrink_extra}"
            f"## Selected excerpt(s)\n\n{combined}\n"
        )
        msgs = [
            {"role": "system", "content": AGENT_BLOCK_NOTE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = await _llm_chat_json(
                prov,
                model_id,
                msgs,
                ollama_schema=OLLAMA_SCHEMA_BLOCK_NOTE,
                mistral_api_key=body.mistral_api_key,
            )
            note_md, assistant_message = _parse_block_note_payload(raw)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"note_markdown": note_md, "assistant_message": assistant_message}

    @app.post("/api/agent-block-clarify")
    async def api_agent_block_clarify(body: AgentBlockClarifyRequest):
        prov, model_id = await _resolve_llm(body.provider, body.model)
        await _assert_session(body.document_id)
        msgs = body.messages
        if msgs[-1].role != "user":
            raise HTTPException(
                status_code=400, detail="The last chat message must be from the user."
            )
        last_user = msgs[-1].content.strip()
        if not last_user:
            raise HTTPException(status_code=400, detail="Message content cannot be empty.")
        conv_lines = [
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content.strip()}"
            for m in msgs[:-1]
        ]
        conv_block = "\n".join(conv_lines) if conv_lines else "(no prior messages)"
        combined, shrink_extra = _mistral_shrink(prov, _combined_excerpts_markdown(_excerpts(body)))
        user_content = (
            _user_instructions_block(body.instructions)
            + f"## Prior conversation\n{conv_block}\n\n{shrink_extra}"
            f"## Selected excerpt(s)\n\n{combined}\n\n## User question\n\n{last_user}\n\n"
            'Reply with JSON only: {"assistant_message": "your answer here"}'
        )
        api_messages = [
            {"role": "system", "content": AGENT_BLOCK_QA_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = await _llm_chat_json(
                prov,
                model_id,
                api_messages,
                ollama_schema=OLLAMA_SCHEMA_QA,
                mistral_api_key=body.mistral_api_key,
            )
            assistant_message = _parse_qa_payload(raw)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"assistant_message": assistant_message}

    @app.post("/api/agent-block-explain-replace")
    async def api_agent_block_explain_replace(body: AgentBlockExplainReplaceRequest):
        prov, model_id = await _resolve_llm(body.provider, body.model)
        await _assert_session(body.document_id)
        task = (body.user_task or "").strip()
        if not task:
            task = (
                "Explain the selected excerpt(s) clearly in Markdown suitable for study notes. "
                "Replace the original block content conceptually with this explanation."
            )
        combined, shrink_extra = _mistral_shrink(prov, _combined_excerpts_markdown(_excerpts(body)))
        user_content = (
            _user_instructions_block(body.instructions)
            + f"## Task\n\n{task}\n\n{shrink_extra}"
            f"## Selected excerpt(s)\n\n{combined}\n\n"
            'Reply with JSON only: {"replacement_markdown": "…"}'
        )
        api_messages = [
            {"role": "system", "content": AGENT_BLOCK_EXPLAIN_REPLACE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = await _llm_chat_json(
                prov,
                model_id,
                api_messages,
                ollama_schema=OLLAMA_SCHEMA_EXPLAIN_REPLACE,
                mistral_api_key=body.mistral_api_key,
            )
            replacement_md = _parse_explain_replace_payload(raw)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"replacement_markdown": replacement_md}

    @app.post("/api/agent-block-beautify")
    async def api_agent_block_beautify(body: AgentBlockBeautifyRequest):
        prov, model_id = await _resolve_llm(body.provider, body.model)
        await _assert_session(body.document_id)
        pdf_plain = await _pdf_plain_for_session(body.document_id)
        if not pdf_plain:
            raise HTTPException(
                status_code=400,
                detail="No plain-text source is available for this PDF. Import the document again.",
            )
        items = _excerpts(body)
        original_for_images = "\n\n---\n\n".join(e.markdown.strip() for e in items)
        combined, shrink_extra = _mistral_shrink(prov, _combined_excerpts_markdown(items))
        overhead = 8000
        budget = _max_doc_chars_for_model() - len(combined) - len(shrink_extra) - overhead
        budget = max(12_000, min(budget, 200_000))
        pdf_part = _cap_excerpt(pdf_plain, max_chars=budget)
        pdf_note = ""
        if len(pdf_plain) > budget:
            pdf_note = (
                "\n\n### Note\nThe source PDF text is truncated to fit the model context; "
                "fix formatting using the visible portion.\n"
            )
        task = (body.user_task or "").strip()
        if not task:
            task = (
                "Compare the PDF source text with the converted Markdown selection and output beautified Markdown "
                "that replaces the selection (fix math, code, and structure; merge or split sections when justified)."
            )
        user_content = (
            _user_instructions_block(body.instructions)
            + pdf_note
            + shrink_extra
            + f"## Task\n\n{task}\n\n"
            + "## Source PDF (plain text)\n\n"
            + pdf_part
            + "\n\n## Selection (converted Markdown)\n\n"
            + combined
            + "\n\n"
            + 'Reply with JSON only: {"replacement_markdown": "…"}'
        )
        api_messages = [
            {"role": "system", "content": AGENT_BLOCK_BEAUTIFY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = await _llm_chat_json(
                prov,
                model_id,
                api_messages,
                ollama_schema=OLLAMA_SCHEMA_EXPLAIN_REPLACE,
                mistral_api_key=body.mistral_api_key,
            )
            replacement_md = _parse_explain_replace_payload(raw)
            replacement_md = _merge_dropped_images(original_for_images, replacement_md)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"replacement_markdown": replacement_md}
