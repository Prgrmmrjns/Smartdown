"""Mistral / Ollama API clients and agent streaming."""
import json
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from fastapi import HTTPException

from smartdown.config import (
    MISTRAL_API_URL,
    MISTRAL_MODEL,
    MISTRAL_MODEL_IDS,
    OLLAMA_CHAT_TIMEOUT,
    OLLAMA_HOST,
    OLLAMA_MODELS_CACHE_SEC,
    OLLAMA_USE_STRUCTURED_FORMAT,
)
from smartdown.llm_context import _shrink_markdown_for_mistral_prompt

_ollama_models_cache: tuple[float, list[str]] | None = None

async def _fetch_ollama_model_names() -> list[str]:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=3.0)
        ) as client:
            r = await client.get(f"{OLLAMA_HOST}/api/tags")
    except httpx.RequestError:
        return []
    if r.status_code >= 400:
        return []
    try:
        data = r.json()
    except json.JSONDecodeError:
        return []
    models = data.get("models") or []
    out: list[str] = []
    for m in models:
        name = m.get("name") if isinstance(m, dict) else None
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return sorted(set(out), key=str.lower)


async def _get_ollama_models_cached() -> list[str]:
    global _ollama_models_cache
    now = time.time()
    if _ollama_models_cache is not None:
        ts, names = _ollama_models_cache
        if now - ts < OLLAMA_MODELS_CACHE_SEC:
            return names
    names = await _fetch_ollama_model_names()
    _ollama_models_cache = (now, names)
    return names


async def _resolve_llm(provider: str, model: str | None) -> tuple[Literal["mistral", "ollama"], str]:
    if provider == "mistral":
        requested = (model or "").strip()
        if not requested:
            env_m = MISTRAL_MODEL.strip()
            mid = env_m if env_m in MISTRAL_MODEL_IDS else "mistral-small-latest"
        else:
            mid = requested
            if mid not in MISTRAL_MODEL_IDS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Invalid Mistral model. Use mistral-small-latest or mistral-large-latest. "
                        f"Got: {mid!r}."
                    ),
                )
        return "mistral", mid
    if provider == "ollama":
        mid = (model or "").strip()
        if not mid:
            raise HTTPException(
                status_code=400,
                detail="Select an Ollama model (from your local `ollama list`).",
            )
        allowed = await _get_ollama_models_cached()
        if mid not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model {mid!r} is not in the current Ollama catalog at {OLLAMA_HOST}. "
                    "Refresh the model list or run `ollama pull`."
                ),
            )
        return "ollama", mid
    raise HTTPException(status_code=400, detail="Invalid provider.")


async def _mistral_chat(
    model: str, messages: list[dict], *, api_key: str | None = None
) -> str:
    key = (api_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Mistral API key missing. Paste your key in the app (Model section) or use Ollama.",
        )
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
            r = await client.post(MISTRAL_API_URL, json=payload, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502, detail=f"Mistral API request failed: {e}"
        ) from e
    if r.status_code >= 400:
        raw = r.text[:4000] if r.text else r.reason_phrase
        try:
            err_j = r.json()
            msg = (err_j.get("message") or "").lower()
            code = str(err_j.get("code") or "")
            if (
                "too large" in msg
                or "context length" in msg
                or "maximum context" in msg
                or code == "3051"
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "The document is too large for the Mistral model context, even after shrinking "
                        "data-URL images and truncating long text. Try a smaller PDF, split the document, "
                        "or set a lower MISTRAL_MAX_DOC_CHARS_CAP / MISTRAL_CHARS_PER_TOKEN_EST in the server environment."
                    ),
                )
        except HTTPException:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        raise HTTPException(
            status_code=502,
            detail=f"Mistral API error {r.status_code}: {raw}",
        )
    try:
        body = r.json()
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502, detail="Invalid JSON from Mistral API"
        ) from e
    choices = body.get("choices") or []
    if not choices:
        raise HTTPException(status_code=502, detail="Empty response from Mistral API")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=502, detail="No message content from Mistral")
    return content


def _ollama_format_payload(schema: dict | None) -> dict | str:
    if schema is not None and OLLAMA_USE_STRUCTURED_FORMAT:
        return schema
    return "json"


def _ollama_think_request_value() -> bool | str | None:
    """Ollama /api/chat `think` (docs.ollama.com): when true, reasoning may omit from `content`."""
    if os.environ.get("OLLAMA_OMIT_THINK_KEY", "").lower() in ("1", "true", "yes"):
        return None
    raw = os.environ.get("OLLAMA_THINK", "false").strip().lower()
    if raw in ("high", "medium", "low"):
        return raw
    if raw in ("1", "true", "yes", "on"):
        return True
    return False


async def _ollama_chat(
    model: str,
    messages: list[dict],
    *,
    response_schema: dict | None,
) -> str:
    fmt = _ollama_format_payload(response_schema)
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": fmt,
        "options": {"temperature": 0.35},
    }
    tv = _ollama_think_request_value()
    if tv is not None:
        payload["think"] = tv
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(OLLAMA_CHAT_TIMEOUT, connect=15.0)
        ) as client:
            r = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach Ollama at {OLLAMA_HOST}. Is `ollama serve` running? ({e})",
        ) from e
    if r.status_code >= 400:
        raw = r.text[:4000] if r.text else r.reason_phrase
        raise HTTPException(
            status_code=502,
            detail=f"Ollama error {r.status_code}: {raw}",
        )
    try:
        body = r.json()
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail="Invalid JSON from Ollama") from e
    msg = body.get("message") or {}
    content = msg.get("content")
    thinking = msg.get("thinking")
    if not isinstance(content, str) or not content.strip():
        if (
            isinstance(thinking, str)
            and thinking.strip()
            and thinking.lstrip().startswith("{")
        ):
            content = thinking
        else:
            raise HTTPException(
                status_code=502,
                detail="No message content from Ollama (check model / `think` settings).",
            )
    return content


async def _llm_chat_json(
    provider: str,
    model: str,
    messages: list[dict],
    *,
    ollama_schema: dict | None = None,
    mistral_api_key: str | None = None,
) -> str:
    if provider == "mistral":
        return await _mistral_chat(model, messages, api_key=mistral_api_key)
    return await _ollama_chat(model, messages, response_schema=ollama_schema)


def _sse_bytes(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _strip_stream_markdown_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return text.strip()
    t = re.sub(r"^```(?:markdown|md)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


async def _mistral_stream_text(
    model: str, messages: list[dict], *, api_key: str | None = None
) -> AsyncIterator[str]:
    key = (api_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Mistral API key missing. Paste your key in the app (Model section) or use Ollama.",
        )
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=20.0)
        ) as client:
            async with client.stream(
                "POST", MISTRAL_API_URL, json=payload, headers=headers
            ) as r:
                if r.status_code >= 400:
                    raw = (await r.aread())[:4000]
                    raise HTTPException(
                        status_code=502,
                        detail=f"Mistral stream error {r.status_code}: {raw.decode(errors='replace')}",
                    )
                async for line in r.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    for ch in obj.get("choices") or []:
                        delta = ch.get("delta") or {}
                        c = delta.get("content")
                        if isinstance(c, str) and c:
                            yield c
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502, detail=f"Mistral streaming request failed: {e}"
        ) from e


async def _ollama_stream_text(model: str, messages: list[dict]) -> AsyncIterator[str]:
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.35},
    }
    tv = _ollama_think_request_value()
    if tv is not None:
        payload["think"] = tv
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(OLLAMA_CHAT_TIMEOUT, connect=15.0)
        ) as client:
            async with client.stream(
                "POST", f"{OLLAMA_HOST}/api/chat", json=payload
            ) as r:
                if r.status_code >= 400:
                    raw = (await r.aread())[:4000]
                    raise HTTPException(
                        status_code=502,
                        detail=f"Ollama stream error {r.status_code}: {raw.decode(errors='replace')}",
                    )
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = obj.get("message") or {}
                    c = msg.get("content")
                    if isinstance(c, str) and c:
                        yield c
                    if obj.get("done"):
                        break
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach Ollama at {OLLAMA_HOST}. Is `ollama serve` running? ({e})",
        ) from e


async def _stream_llm_markdown(
    provider: str,
    model: str,
    messages: list[dict],
    *,
    mistral_api_key: str | None = None,
) -> AsyncIterator[str]:
    if provider == "mistral":
        async for x in _mistral_stream_text(model, messages, api_key=mistral_api_key):
            yield x
    else:
        async for x in _ollama_stream_text(model, messages):
            yield x



