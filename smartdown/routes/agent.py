"""LLM-backed block note and clarify endpoints."""

from typing import Protocol

from fastapi import FastAPI, HTTPException

from smartdown.config import OLLAMA_SCHEMA_BLOCK_NOTE, OLLAMA_SCHEMA_QA
from smartdown.llm_context import (
    _parse_block_note_payload,
    _parse_qa_payload,
    _shrink_markdown_for_mistral_prompt,
)
from smartdown.llm_providers import _llm_chat_json, _resolve_llm
from smartdown.models import AgentBlockClarifyRequest, AgentBlockExcerpt, AgentBlockNoteRequest
from smartdown.prompts import AGENT_BLOCK_NOTE_SYSTEM_PROMPT, AGENT_BLOCK_QA_SYSTEM_PROMPT
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


def _mistral_shrink(prov: str, text: str) -> tuple[str, str]:
    if prov == "mistral":
        return _shrink_markdown_for_mistral_prompt(text)
    return text, ""


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
            f"## Note format instructions\n\n{fmt}\n{scale}\n{shrink_extra}"
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
            f"## Prior conversation\n{conv_block}\n\n{shrink_extra}"
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
