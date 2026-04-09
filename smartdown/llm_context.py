"""Markdown shrinking, image bookkeeping, and JSON parse helpers for LLM calls."""
import json
import os
import re
from collections import Counter, defaultdict

from fastapi import HTTPException

from smartdown.config import (
    MISTRAL_CHARS_PER_TOKEN_EST,
    MISTRAL_CONTEXT_MAX_TOKENS,
    MISTRAL_MAX_DOC_CHARS_CAP,
    MISTRAL_PROMPT_OVERHEAD_TOKENS,
)
from smartdown.models import AgentChatMessage

_IMG_LINK_IN_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
# Base64 image URLs in Markdown explode token counts; strip for the API prompt only.
_DATA_URI_MD_RE = re.compile(
    r"!\[([^\]]*)\]\(data:image/[a-z0-9.+-]+;base64,([^)]+)\)",
    re.IGNORECASE,
)


def _images_path_key(url: str) -> str | None:
    u = url.strip().strip('"').strip("'").split("?", 1)[0].replace("\\", "/")
    low = u.lower()
    i = low.find("images/")
    if i < 0:
        return None
    return u[i:].replace("\\", "/").lower()


def _extract_image_blocks(md: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _IMG_LINK_IN_MD_RE.finditer(md):
        full = m.group(0)
        path_m = re.search(r"\(([^)]+)\)", full)
        if not path_m:
            continue
        key = _images_path_key(path_m.group(1))
        if key:
            out.append((key, full))
    return out


def _user_requests_image_removal(text: str) -> bool:
    t = text.lower()
    patterns = (
        r"\b(no|without|remove|delete|drop|omit|exclude|strip)\b[\s\S]{0,100}\b(images?|figures?|photos?|pictures?|diagrams?|screenshots?)\b",
        r"\b(remove|delete|drop)\b[\s\S]{0,60}\b(all\b[\s\S]{0,20})?(images?|figures?)\b",
        r"\bno\b[\s\S]{0,40}\b(images?|figures?|pictures?)\b",
    )
    return any(re.search(p, t) for p in patterns)


def _conversation_requests_image_removal(
    prior: list[AgentChatMessage],
    last_user: str,
    instruction_prefix: str = "",
) -> bool:
    if instruction_prefix and _user_requests_image_removal(instruction_prefix):
        return True
    if _user_requests_image_removal(last_user):
        return True
    for m in prior:
        if m.role == "user" and _user_requests_image_removal(m.content):
            return True
    return False


def _merge_dropped_images(original_md: str, model_md: str) -> str:
    if not model_md.strip():
        return model_md
    pairs = _extract_image_blocks(original_md)
    if not pairs:
        return model_md
    orig_c = Counter(k for k, _ in pairs)
    new_c = Counter()
    for m in _IMG_LINK_IN_MD_RE.finditer(model_md):
        path_m = re.search(r"\(([^)]+)\)", m.group(0))
        if path_m:
            k = _images_path_key(path_m.group(1))
            if k:
                new_c[k] += 1
    deficit = orig_c - new_c
    if not deficit:
        return model_md
    q: dict[str, list[str]] = defaultdict(list)
    for k, full in pairs:
        q[k].append(full)
    extras: list[str] = []
    for k, need in deficit.items():
        for _ in range(need):
            if q[k]:
                extras.append(q[k].pop(0))
    if not extras:
        return model_md
    block = (
        "\n\n---\n\n## Preserved figures and images\n\n"
        "*(These image links were restored from the source; keep them in the document.)*\n\n"
        + "\n\n".join(extras)
    )
    return model_md.rstrip() + block


def _replace_data_uri_images_with_stubs(md: str) -> tuple[str, int]:
    """Swap data-URL image markdown for tiny stub paths (sent to the model only)."""
    n = 0

    def repl(_m: re.Match) -> str:
        nonlocal n
        n += 1
        alt = _m.group(1)
        return f"![{alt}](images/__prompt_stub_image_{n}.png)"

    return _DATA_URI_MD_RE.sub(repl, md), n


def _max_doc_chars_for_model() -> int:
    budget = (MISTRAL_CONTEXT_MAX_TOKENS - MISTRAL_PROMPT_OVERHEAD_TOKENS) * max(
        MISTRAL_CHARS_PER_TOKEN_EST, 0.5
    )
    return max(32_000, min(int(budget), MISTRAL_MAX_DOC_CHARS_CAP))


def _truncate_md_middle(md: str, max_chars: int) -> tuple[str, bool]:
    if len(md) <= max_chars:
        return md, False
    half = (max_chars // 2) - 120
    tail = max_chars - half - 120
    if half < 1000 or tail < 1000:
        return md[:max_chars], True
    omitted = len(md) - half - tail
    mid = (
        "\n\n---\n\n**[Smartdown: omitted "
        f"{omitted} characters from the middle — document too long for the AI context. "
        "Your full text is unchanged in the editor and exports.]**\n\n---\n\n"
    )
    return md[:half] + mid + md[-tail:], True


def _shrink_text_for_prompt(text: str) -> tuple[str, str]:
    """Shrink arbitrary text (e.g. raw PDF plain text) for model prompt. Returns (text, extra_notes)."""
    max_chars = _max_doc_chars_for_model()
    if len(text) <= max_chars:
        return text, ""
    allow_trunc = os.environ.get("MISTRAL_ALLOW_MIDDLE_TRUNCATION", "").lower() in (
        "1", "true", "yes",
    )
    if allow_trunc:
        work, did = _truncate_md_middle(text, max_chars)
        note = ""
        if did:
            note = (
                "\n\n### Notes for this request\n"
                "- The PDF text below is head+tail only; the middle is omitted for context limits.\n"
            )
        return work, note
    raise HTTPException(
        status_code=400,
        detail=(
            f"This PDF text is too large for one AI request "
            f"({len(text)} characters; safe limit about {max_chars}). "
            "Try a shorter PDF, or set MISTRAL_ALLOW_MIDDLE_TRUNCATION=true."
        ),
    )


def _shrink_markdown_for_mistral_prompt(md: str) -> tuple[str, str]:
    """Return (markdown_for_model, extra_instructions_block)."""
    notes: list[str] = []
    work, n_stub = _replace_data_uri_images_with_stubs(md)
    if n_stub:
        notes.append(
            f"{n_stub} image(s) used `data:` URLs in the source (very large). They were replaced with "
            "`images/__prompt_stub_image_N.png` **only in this prompt**. In your `markdown` output use normal "
            "`![](images/...png)` paths consistent with the document (real filenames from the user's file, "
            "not `__prompt_stub__`)."
        )
    max_chars = _max_doc_chars_for_model()
    allow_trunc = os.environ.get("MISTRAL_ALLOW_MIDDLE_TRUNCATION", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if len(work) > max_chars:
        if allow_trunc:
            work, did_trunc = _truncate_md_middle(work, max_chars)
            if did_trunc:
                notes.append(
                    "The document below is head+tail only; the middle is omitted for context limits. "
                    "Improve what you see; the user's editor still has the full file."
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This document is too large for one AI request after shrinking images "
                    f"({len(work)} characters; safe limit about {max_chars}). "
                    "Try a shorter PDF, or set environment variable MISTRAL_ALLOW_MIDDLE_TRUNCATION=true "
                    "to send only the beginning and end of the text (the model still cannot edit the hidden "
                    "middle in one shot). You can tune MISTRAL_MAX_DOC_CHARS_CAP or MISTRAL_CONTEXT_MAX_TOKENS."
                ),
            )
    extra = ""
    if notes:
        extra = "\n\n### Notes for this request\n" + "\n".join(f"- {x}" for x in notes) + "\n"
    return work, extra


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _repair_json_invalid_escapes(raw: str) -> str:
    """Fix backslashes inside JSON string literals so json.loads accepts LLM output."""
    out: list[str] = []
    i = 0
    in_string = False
    valid_single = frozenset('"\\/bfnrt')
    while i < len(raw):
        ch = raw[i]
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            if i + 1 >= len(raw):
                out.append("\\\\")
                i += 1
                continue
            n = raw[i + 1]
            if n == "u":
                hexd = raw[i + 2 : i + 6]
                if len(hexd) == 4 and all(
                    c in "0123456789abcdefABCDEF" for c in hexd
                ):
                    out.append(raw[i : i + 6])
                    i += 6
                else:
                    out.append("\\\\")
                    out.append("u")
                    i += 2
                continue
            if n in valid_single:
                out.append("\\")
                out.append(n)
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        if ch == '"':
            in_string = False
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_llm_json_object(raw: str) -> dict:
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as first_err:
        try:
            data = json.loads(_repair_json_invalid_escapes(raw))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Model did not return valid JSON: {e} (after escape repair: {first_err})"
            ) from e
    if not isinstance(data, dict):
        raise ValueError("Model JSON must be an object")
    return data


# Local models often use different keys or leave assistant_message empty.
_ASSISTANT_JSON_KEYS = frozenset(
    {
        "assistant_message",
        "assistantmessage",
        "message",
        "answer",
        "reply",
        "response",
        "text",
        "content",
        "output",
    }
)


def _json_assistant_string(data: dict, *, skip_keys_ci: frozenset[str]) -> str | None:
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        if k.lower() in skip_keys_ci:
            continue
        if k.lower() not in _ASSISTANT_JSON_KEYS:
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _longest_string_value_excluding(
    data: dict, exclude_keys_ci: frozenset[str]
) -> str | None:
    best = ""
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        if k.lower() in exclude_keys_ci:
            continue
        if isinstance(v, str) and len(v.strip()) > len(best):
            best = v.strip()
    return best or None


def _parse_agent_payload(content: str) -> tuple[str, str]:
    raw = _strip_json_fence(content)
    data = _loads_llm_json_object(raw)
    md = data.get("markdown")
    if not isinstance(md, str) or not md.strip():
        raise ValueError('JSON must include non-empty string "markdown"')
    skip = frozenset({"markdown", "md"})
    msg = _json_assistant_string(data, skip_keys_ci=skip)
    if not msg:
        msg = _longest_string_value_excluding(data, skip) or "Updated the document."
    return md.strip(), msg.strip()


def _parse_block_note_payload(content: str) -> tuple[str, str]:
    raw = _strip_json_fence(content)
    data = _loads_llm_json_object(raw)
    note = data.get("note_markdown")
    if not isinstance(note, str) or not note.strip():
        raise ValueError('JSON must include non-empty string "note_markdown"')
    note = note.strip()
    msg = data.get("assistant_message")
    if isinstance(msg, str) and msg.strip():
        return note, msg.strip()
    alt = _json_assistant_string(
        data, skip_keys_ci=frozenset({"note_markdown", "notemarkdown"})
    )
    if alt:
        return note, alt
    return note, "Added note bullet."


def _parse_qa_payload(content: str) -> str:
    raw = _strip_json_fence(content)
    data = _loads_llm_json_object(raw)
    skip = frozenset({"markdown", "md"})
    msg = _json_assistant_string(data, skip_keys_ci=skip)
    if not msg:
        msg = _longest_string_value_excluding(data, skip)
    if not msg:
        raise ValueError(
            'JSON must include a non-empty reply string (e.g. "assistant_message").'
        )
    return msg.strip()


def _parse_explain_replace_payload(content: str) -> str:
    raw = _strip_json_fence(content)
    data = _loads_llm_json_object(raw)
    md = data.get("replacement_markdown")
    if not isinstance(md, str) or not md.strip():
        raise ValueError('JSON must include non-empty string "replacement_markdown"')
    return md.strip()


def _parse_notion_suggest_payload(content: str) -> tuple[dict[str, str], str]:
    raw = _strip_json_fence(content)
    data = _loads_llm_json_object(raw)
    pv = data.get("property_values")
    if not isinstance(pv, dict):
        raise ValueError('JSON must include object "property_values"')
    out: dict[str, str] = {}
    for k, v in pv.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if v is None:
            out[k.strip()] = ""
        elif isinstance(v, str):
            out[k.strip()] = v
        elif isinstance(v, bool):
            out[k.strip()] = "true" if v else "false"
        elif isinstance(v, (int, float)):
            out[k.strip()] = str(v)
        else:
            out[k.strip()] = str(v)
    msg = data.get("assistant_message")
    if not isinstance(msg, str) or not msg.strip():
        raise ValueError("assistant_message must be a non-empty string")
    return out, msg.strip()
