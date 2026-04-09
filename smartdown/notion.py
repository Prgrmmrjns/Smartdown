"""Notion API: inspect page properties, sync markdown into an existing page."""
import base64
import binascii
import os
import re
import warnings
from pathlib import Path
from typing import Literal

import fitz
import httpx
from fastapi import HTTPException

from smartdown.config import IMAGES_DIR, NOTION_API_VERSION

_NOTION_IMG_INLINE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

_NOTION_DATA_URI_IMG_RE = re.compile(
    r"!\[([^\]]*)\]\(data:image/[a-z0-9.+-]+;base64,([^)]+)\)",
    re.IGNORECASE,
)


def _notion_restore_data_uri_images(md: str, images_b64: dict[str, str]) -> str:
    """Mirror upload.html restoreDataUriImagesToPaths — keeps payloads small and paths consistent."""
    if not md.strip() or not images_b64:
        return md

    def norm_b64(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    inv: dict[str, str] = {}
    for k, v in images_b64.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            nb = norm_b64(v)
            if nb not in inv:
                inv[nb] = k

    def repl(m: re.Match[str]) -> str:
        alt, b64_part = m.group(1), m.group(2)
        key = inv.get(norm_b64(b64_part))
        if key:
            return f"![{alt}]({key})"
        return f"![{alt}](images/_unknown_inline.png)"

    return _NOTION_DATA_URI_IMG_RE.sub(repl, md)


def _notion_markdown_without_images(md: str) -> str:
    t = _NOTION_IMG_INLINE_RE.sub("", md)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _notion_normalize_page_id(raw: str) -> str:
    s = raw.strip()
    m = re.search(
        r"([0-9a-f]{8})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{12})",
        s,
        re.I,
    )
    if not m:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not find a Notion page ID. Paste the full page URL from the browser "
                "(open the page → copy link) or the 32-character hex ID (with or without dashes)."
            ),
        )
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}-{m.group(4)}-{m.group(5)}".lower()


def _notion_err_detail(r: httpx.Response) -> str:
    try:
        j = r.json()
        msg = j.get("message", "")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:1200]
    except Exception:
        pass
    t = (r.text or "").strip()
    return (t[:1200] if t else r.reason_phrase or "Notion API error")


def _notion_ok_json(r: httpx.Response, doing: str) -> dict:
    """Parse a successful Notion JSON body; avoid bare r.json() raising and causing HTTP 500."""
    try:
        data = r.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Notion returned invalid JSON while {doing}.",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail=f"Notion returned unexpected JSON while {doing}.",
        )
    return data


def _notion_api_error(r: httpx.Response, doing: str) -> None:
    """Raise HTTPException with a clear message; map 401/403 to token vs sharing."""
    extra = _notion_err_detail(r)
    code = r.status_code
    if code == 401:
        raise HTTPException(
            status_code=400,
            detail=(
                "Notion returned HTTP 401 (unauthorized) — it does not accept this token. "
                "Use the **Internal Integration Secret** from https://www.notion.so/my-integrations "
                "(open your integration → **Secrets**). Do not paste an OAuth client secret from another product, "
                "and do not include the word `Bearer` or quotes. "
                f"Notion message: {extra}"
            ),
        )
    if code == 403:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Notion returned HTTP 403 while {doing}. "
                "Share the page with the integration (**⋯** → **Connections**), and in "
                "https://www.notion.so/my-integrations ensure **Update** and **Insert content** "
                "capabilities are enabled if you use this API for properties and blocks. "
                f"Notion message: {extra}"
            ),
        )
    raise HTTPException(
        status_code=400,
        detail=f"Notion API error while {doing} (HTTP {code}): {extra}",
    )


def _notion_page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


_NOTION_URL_IN_TEXT_RE = re.compile(
    r'(?i)\b(https?://[^\s<>"\']+|www\.[^\s<>"\']+)'
)

_NOTION_READONLY_PROP_TYPES = frozenset(
    {
        "formula",
        "rollup",
        "unique_id",
        "created_time",
        "created_by",
        "last_edited_time",
        "last_edited_by",
        "last_edited_by_property",
        "button",
    }
)


def _notion_first_url_from_text(text: str) -> str | None:
    m = _NOTION_URL_IN_TEXT_RE.search(text or "")
    if not m:
        return None
    u = m.group(0).rstrip(".,;:\"'")
    if u.lower().startswith("www."):
        u = "https://" + u
    return u


def _notion_property_looks_like_url_field(name: str) -> bool:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not n:
        return False
    if n in (
        "url",
        "uri",
        "link",
        "href",
        "web",
        "website",
        "web page",
        "page link",
        "page",
        "source",
        "source url",
        "article url",
        "article",
        "pdf url",
        "document url",
        "doc url",
    ):
        return True
    return bool(re.search(r"\b(url|uri|link|href|website|web\s*page)\b", n))


def _notion_select_like_options(spec: dict, typ: str) -> list[str]:
    key = typ if typ in spec else None
    if key is None:
        for k in ("select", "multi_select", "status"):
            if k in spec:
                key = k
                break
    if not key:
        return []
    block = spec.get(key)
    if not isinstance(block, dict):
        return []
    opts = block.get("options") or []
    out: list[str] = []
    for o in opts:
        if isinstance(o, dict):
            nm = o.get("name")
            if isinstance(nm, str) and nm.strip():
                out.append(nm.strip())
    return out


def _notion_properties_for_client_ui(raw: dict | None) -> list[dict]:
    if not isinstance(raw, dict):
        return []
    rows: list[dict] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        typ = spec.get("type")
        if not isinstance(typ, str) or typ in _NOTION_READONLY_PROP_TYPES:
            continue
        if typ == "title":
            continue
        row: dict = {"name": name, "type": typ}
        if typ in ("select", "multi_select", "status"):
            row["options"] = _notion_select_like_options(spec, typ)
        rows.append(row)
    rows.sort(key=lambda r: (r["name"] or "").lower())
    return rows


def _notion_suggested_defaults_for_properties(
    md: str | None, prop_rows: list[dict]
) -> dict[str, str]:
    if not md or not (md := md.strip()):
        return {}
    url = _notion_first_url_from_text(md)
    if not url:
        return {}
    out: dict[str, str] = {}
    for row in prop_rows:
        nm = row.get("name")
        typ = row.get("type")
        if not isinstance(nm, str) or typ != "url":
            continue
        if _notion_property_looks_like_url_field(nm):
            out[nm] = url
    return out


def _notion_string_to_property_payload(
    prop_name: str,
    raw: str,
    spec: dict,
    *,
    warnings: list[str],
) -> dict | None:
    typ = spec.get("type")
    if typ == "title":
        return None
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if typ == "rich_text":
            return {"rich_text": _notion_plain_rich_text(s)}
        if typ == "url":
            return {"url": s}
        if typ == "email":
            return {"email": s}
        if typ == "phone_number":
            return {"phone_number": s}
        if typ == "number":
            if "." in s:
                return {"number": float(s.replace(",", ""))}
            return {"number": int(s.replace(",", ""))}
        if typ == "checkbox":
            low = s.lower()
            return {"checkbox": low in ("1", "true", "yes", "y", "on")}
        if typ == "date":
            return {"date": {"start": s, "end": None, "time_zone": None}}
        if typ == "select":
            return {"select": {"name": s}}
        if typ == "multi_select":
            names = [x.strip() for x in re.split(r"[,;]", s) if x.strip()]
            if not names:
                return None
            return {"multi_select": [{"name": n} for n in names]}
        if typ == "status":
            return {"status": {"name": s}}
    except (TypeError, ValueError) as e:
        warnings.append(f"Could not set {prop_name!r}: {e}")
        return None
    warnings.append(f"Unsupported Notion property type for export: {typ!r} ({prop_name})")
    return None


def _notion_merge_extra_properties(
    schema: dict,
    title_prop: str,
    extra: dict[str, str],
    *,
    warnings: list[str],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(schema, dict):
        return out
    for key, val in extra.items():
        if not isinstance(key, str) or not key.strip():
            continue
        name = key.strip()
        if name == title_prop:
            continue
        spec = schema.get(name)
        if not isinstance(spec, dict):
            warnings.append(f"Ignored unknown property {name!r}.")
            continue
        payload = _notion_string_to_property_payload(
            name, str(val), spec, warnings=warnings
        )
        if payload:
            out[name] = payload
    return out


def _default_notion_page_title(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#"):
            t = s.lstrip("#").strip()
            if t:
                return t[:2000]
    return "Document"


def _normalize_notion_image_rel_path(path: str) -> str:
    p = path.strip().strip('"').strip("'").split("?", 1)[0].replace("\\", "/")
    low = p.lower()
    idx = low.find("images/")
    if idx >= 0:
        p = p[idx:]
    elif not low.startswith("images/"):
        p = f"{IMAGES_DIR}/{p.lstrip('/')}"
    return p.replace("\\", "/")


def _notion_b64_to_bytes(raw: str) -> bytes:
    """Decode image base64 from API/JSON; avoid standard_b64decode(..., validate=) (breaks on some Python builds)."""
    s = (raw or "").strip()
    low = s.lower()
    if ";base64," in low:
        s = s[low.index(";base64,") + len(";base64,") :]
    s = re.sub(r"\s+", "", s)
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    return base64.b64decode(s)


def _lookup_notion_image_bytes(
    images_b64: dict[str, str], norm_key: str
) -> tuple[bytes, str, str]:
    nk = norm_key.replace("\\", "/").lower()
    found: str | None = None
    for k in images_b64:
        if k.replace("\\", "/").lower() == nk:
            found = k
            break
    if not found:
        raise KeyError(norm_key)
    raw = _notion_b64_to_bytes(images_b64[found])
    name = Path(found).name
    ext = Path(name).suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/png")
    return raw, mime, name


def _notion_plain_rich_text(s: str) -> list[dict]:
    s = s or ""
    if not s:
        return [
            {
                "type": "text",
                "text": {"content": " "},
            }
        ]
    parts: list[dict] = []
    i = 0
    while i < len(s):
        chunk = s[i : i + 2000]
        parts.append({"type": "text", "text": {"content": chunk}})
        i += 2000
    return parts


def _notion_paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _notion_plain_rich_text(text)},
    }


def _notion_heading_visible_text(text: str) -> str:
    """Strip accidental leading ATX markers so blocks don't show '## Title' twice."""
    t = (text or "").strip()
    while True:
        m = re.match(r"^#{1,6}\s+(.+)$", t)
        if not m:
            break
        t = m.group(1).strip()
    return t


def _notion_heading_block(level: int, text: str) -> dict:
    typ = {1: "heading_1", 2: "heading_2", 3: "heading_3"}[level]
    visible = _notion_heading_visible_text(text)
    return {
        "object": "block",
        "type": typ,
        typ: {"rich_text": _notion_plain_rich_text(visible)},
    }


def _notion_bullet_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _notion_plain_rich_text(text)},
    }


def _notion_numbered_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": _notion_plain_rich_text(text)},
    }


def _notion_code_block(lang: str, text: str) -> dict:
    safe_lang = (lang or "plain text").strip()[:48] or "plain text"
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": _notion_plain_rich_text(text),
            "language": safe_lang,
        },
    }


def _notion_image_block(file_upload_id: str, caption: str) -> dict:
    cap = _notion_plain_rich_text(caption) if (caption or "").strip() else []
    return {
        "object": "block",
        "type": "image",
        "image": {
            "type": "file_upload",
            "file_upload": {"id": file_upload_id},
            "caption": cap,
        },
    }


def _split_markdown_text_and_images(chunk: str) -> list[tuple[str, str | None, str | None]]:
    """Segments: (text, image_alt_or_None, image_path_or_None)."""
    out: list[tuple[str, str | None, str | None]] = []
    pos = 0
    for m in _NOTION_IMG_INLINE_RE.finditer(chunk):
        if m.start() > pos:
            out.append((chunk[pos : m.start()], None, None))
        alt, path = m.group(1), m.group(2)
        out.append(("", alt, path.strip().strip('"').strip("'")))
        pos = m.end()
    if pos < len(chunk):
        out.append((chunk[pos:], None, None))
    return out


def _flush_notion_inline_segments_core(
    segs: list[dict], content: str, *, list_kind: str | None
) -> None:
    for txt, alt, path in _split_markdown_text_and_images(content):
        if path is not None:
            t = txt.strip()
            if t:
                if list_kind == "bullet":
                    segs.append({"type": "bullet", "text": t})
                elif list_kind == "numbered":
                    segs.append({"type": "numbered", "text": t})
                else:
                    segs.append({"type": "paragraph", "text": t})
            segs.append({"type": "image", "alt": alt or "", "path": path})
        else:
            t2 = txt.strip()
            if not t2:
                continue
            if list_kind == "bullet":
                segs.append({"type": "bullet", "text": t2})
            elif list_kind == "numbered":
                segs.append({"type": "numbered", "text": t2})
            else:
                segs.append({"type": "paragraph", "text": t2})


def _flush_notion_inline_segments(
    segs: list[dict], content: str, *, list_kind: str | None
) -> None:
    """Split markdown so ATX headings inside a paragraph buffer become real heading blocks."""
    content = (content or "").replace("\r\n", "\n")
    if not content.strip():
        return
    # One-line list item that is only a heading: "- ## Topic"
    if list_kind and "\n" not in content.rstrip("\n"):
        st = content.strip()
        m_h = re.match(r"^(#+)\s+(.+)$", st)
        if m_h and 1 <= len(m_h.group(1)) <= 3:
            segs.append(
                {"type": f"h{len(m_h.group(1))}", "text": m_h.group(2).strip()}
            )
            return
    lines = content.split("\n")
    buf: list[str] = []

    def flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        chunk = "\n".join(buf).rstrip("\n")
        buf = []
        if chunk.strip():
            _flush_notion_inline_segments_core(segs, chunk, list_kind=list_kind)

    for line in lines:
        st = line.strip()
        if st:
            m_h = re.match(r"^(#+)\s+(.+)$", st)
            if m_h and 1 <= len(m_h.group(1)) <= 3:
                flush_buf()
                segs.append(
                    {
                        "type": f"h{len(m_h.group(1))}",
                        "text": m_h.group(2).strip(),
                    }
                )
                continue
        buf.append(line)
    flush_buf()


def _parse_markdown_to_notion_segments(md: str) -> list[dict]:
    lines = md.replace("\r\n", "\n").split("\n")
    segs: list[dict] = []
    i = 0
    in_code = False
    code_lang = "plain text"
    code_buf: list[str] = []
    para_buf: list[str] = []

    def flush_para() -> None:
        nonlocal para_buf
        if not para_buf:
            return
        text = "\n".join(para_buf)
        para_buf = []
        if not text.strip():
            return
        _flush_notion_inline_segments(segs, text, list_kind=None)

    while i < len(lines):
        line = lines[i]
        if in_code:
            if line.strip().startswith("```"):
                segs.append(
                    {"type": "code", "lang": code_lang, "text": "\n".join(code_buf)}
                )
                code_buf = []
                in_code = False
            else:
                code_buf.append(line)
            i += 1
            continue

        st = line.strip()
        if st.startswith("```"):
            flush_para()
            in_code = True
            code_lang = st[3:].strip() or "plain text"
            i += 1
            continue

        if not st:
            flush_para()
            i += 1
            continue

        if st in ("---", "***", "___"):
            flush_para()
            segs.append({"type": "divider"})
            i += 1
            continue

        if st.startswith("#"):
            flush_para()
            level = 0
            for c in st:
                if c == "#":
                    level += 1
                else:
                    break
            rest = st[level:].strip()
            if 1 <= level <= 3 and rest:
                segs.append({"type": f"h{level}", "text": rest})
            else:
                para_buf.append(line)
            i += 1
            continue

        m_bullet = re.match(r"^[\-\*]\s+(.*)$", st)
        if m_bullet:
            flush_para()
            _flush_notion_inline_segments(segs, m_bullet.group(1), list_kind="bullet")
            i += 1
            continue

        m_num = re.match(r"^\d+\.\s+(.*)$", st)
        if m_num:
            flush_para()
            _flush_notion_inline_segments(segs, m_num.group(1), list_kind="numbered")
            i += 1
            continue

        para_buf.append(line)
        i += 1

    flush_para()
    if in_code:
        segs.append(
            {"type": "code", "lang": code_lang, "text": "\n".join(code_buf)}
        )
    return segs


async def _notion_create_file_upload(
    client: httpx.AsyncClient, token: str, filename: str, content_type: str
) -> tuple[str, str]:
    r = await client.post(
        "https://api.notion.com/v1/file_uploads",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        },
        json={
            "mode": "single_part",
            "filename": filename,
            "content_type": content_type,
        },
    )
    if r.status_code != 200:
        _notion_api_error(r, "starting an image upload")
    data = _notion_ok_json(r, "starting an image upload")
    fid = data.get("id")
    upload_url = data.get("upload_url")
    if not isinstance(fid, str) or not isinstance(upload_url, str):
        raise HTTPException(
            status_code=502, detail="Notion file_upload response missing id or upload_url."
        )
    return fid, upload_url


async def _notion_send_file_bytes(
    client: httpx.AsyncClient,
    token: str,
    upload_url: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    r = await client.post(
        upload_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
        },
        files={"file": (filename, content, content_type)},
    )
    if r.status_code != 200:
        _notion_api_error(r, "uploading an image file")
    try:
        data = r.json()
    except Exception:
        return
    if data.get("status") != "uploaded":
        raise HTTPException(
            status_code=502,
            detail=f"Notion file upload incomplete: {str(data)[:400]}",
        )


def _notion_title_property_from_schema(props: object) -> str | None:
    if not isinstance(props, dict):
        return None
    for name, spec in props.items():
        if isinstance(spec, dict) and spec.get("type") == "title":
            return str(name)
    return None


async def _notion_resolve_database_export(
    client: httpx.AsyncClient, token: str, database_id: str
) -> tuple[str, dict, dict]:
    """Return (title_property_name, parent_object, properties_schema) for POST /v1/pages.

    Current Notion APIs require creating database rows under a **data source**
    (`data_source_id`) whenever the database lists `data_sources`. Using
    `database_id` as parent often returns HTTP 400 (“use … data source …”) even
    when GET /databases still echoes legacy top-level `properties`.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
    }
    r = await client.get(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=headers,
    )
    if r.status_code != 200:
        _notion_api_error(r, "reading your database")
    data = _notion_ok_json(r, "reading your database")

    sources = data.get("data_sources") or []
    ds_ids: list[str] = []
    for item in sources:
        if isinstance(item, dict):
            sid = item.get("id")
            if isinstance(sid, str) and sid.strip():
                ds_ids.append(sid.strip())
        elif isinstance(item, str) and item.strip():
            ds_ids.append(item.strip())

    for ds_id in ds_ids:
        rd = await client.get(
            f"https://api.notion.com/v1/data_sources/{ds_id}",
            headers=headers,
        )
        if rd.status_code != 200:
            if rd.status_code in (401, 403):
                _notion_api_error(rd, "reading the database schema (data source)")
            continue
        body = _notion_ok_json(rd, "reading the database schema (data source)")
        dsp = body.get("properties")
        if not isinstance(dsp, dict):
            dsp = {}
        title_name = _notion_title_property_from_schema(dsp)
        if title_name:
            return title_name, {"type": "data_source_id", "data_source_id": ds_id}, dsp

    props = data.get("properties")
    if not isinstance(props, dict):
        props = {}
    title_name = _notion_title_property_from_schema(props)
    if title_name:
        return title_name, {"type": "database_id", "database_id": database_id}, props

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not read this database’s columns from Notion. "
            "Use a full database the integration can access (share it via ⋯ → Connections), "
            "not only a linked view on another page. "
            "The database must include a Title property (default in new databases). "
            "If it still fails, open the database in Notion → ⋯ → Manage data sources and ensure "
            "a data source exists."
        ),
    )


def _notion_schema_from_page_properties(props: dict) -> dict:
    """Minimal property schema from GET /pages/{id} `properties` (for merge + UI)."""
    schema: dict[str, dict] = {}
    for name, val in props.items():
        if not isinstance(val, dict):
            continue
        typ = val.get("type")
        if not isinstance(typ, str) or typ in _NOTION_READONLY_PROP_TYPES:
            continue
        if typ == "title":
            schema[name] = {"type": "title"}
        elif typ in ("select", "multi_select", "status"):
            schema[name] = {"type": typ, typ: {"options": []}}
        else:
            schema[name] = {"type": typ}
    return schema


async def _notion_resolve_page_inspect(
    client: httpx.AsyncClient, token: str, page_id: str
) -> tuple[str, dict]:
    """Title column name + full property schema for optional fields and PATCH."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
    }
    r = await client.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=headers,
    )
    if r.status_code != 200:
        _notion_api_error(r, "reading your Notion page")
    page = _notion_ok_json(r, "reading your Notion page")
    props = page.get("properties")
    if not isinstance(props, dict):
        props = {}
    title_prop = _notion_title_property_from_schema(props)
    if not title_prop:
        raise HTTPException(
            status_code=400,
            detail="This page has no Title property the API can set.",
        )
    parent = page.get("parent")
    if isinstance(parent, dict):
        if parent.get("type") == "database_id":
            db_id = parent.get("database_id")
            if isinstance(db_id, str) and db_id.strip():
                try:
                    _, _, db_schema = await _notion_resolve_database_export(
                        client, token, db_id.strip()
                    )
                    return title_prop, db_schema
                except HTTPException:
                    pass
        if parent.get("type") == "data_source_id":
            ds_id = parent.get("data_source_id")
            if isinstance(ds_id, str) and ds_id.strip():
                rd = await client.get(
                    f"https://api.notion.com/v1/data_sources/{ds_id.strip()}",
                    headers=headers,
                )
                if rd.status_code == 200:
                    body = _notion_ok_json(rd, "reading the data source schema")
                    dsp = body.get("properties")
                    if isinstance(dsp, dict) and _notion_title_property_from_schema(dsp):
                        return title_prop, dsp
    return title_prop, _notion_schema_from_page_properties(props)


async def _notion_list_block_children_all(
    client: httpx.AsyncClient, token: str, block_id: str
) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
    }
    out: list[dict] = []
    cursor: str | None = None
    url = f"https://api.notion.com/v1/blocks/{block_id}/children"
    while True:
        params: dict[str, str | int] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = await client.get(url, headers=headers, params=params)
        if r.status_code != 200:
            _notion_api_error(r, "listing page blocks")
        data = _notion_ok_json(r, "listing page blocks")
        for item in data.get("results") or []:
            if isinstance(item, dict):
                out.append(item)
        if not data.get("has_more"):
            break
        nxt = data.get("next_cursor")
        if not isinstance(nxt, str) or not nxt.strip():
            break
        cursor = nxt
    return out


async def _notion_delete_block(
    client: httpx.AsyncClient, token: str, block_id: str
) -> None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
    }
    r = await client.delete(f"https://api.notion.com/v1/blocks/{block_id}", headers=headers)
    if r.status_code not in (200, 404):
        _notion_api_error(r, "removing old page content")


async def _notion_clear_page_children(
    client: httpx.AsyncClient,
    token: str,
    page_id: str,
    *,
    warnings: list[str],
) -> None:
    children = await _notion_list_block_children_all(client, token, page_id)
    for b in children:
        bid = b.get("id")
        if not isinstance(bid, str):
            continue
        if b.get("has_children"):
            await _notion_clear_page_children(client, token, bid, warnings=warnings)
        await _notion_delete_block(client, token, bid)
    if children:
        warnings.append("Existing block content under this page was removed and replaced.")


async def _notion_segments_to_api_blocks(
    client: httpx.AsyncClient,
    token: str,
    segments: list[dict],
    images_b64: dict[str, str],
    warnings: list[str],
) -> list[dict]:
    upload_by_key: dict[str, str] = {}
    blocks: list[dict] = []

    async def upload_for_path(rel_path: str) -> str | None:
        key = _normalize_notion_image_rel_path(rel_path)
        if key in upload_by_key:
            return upload_by_key[key]
        try:
            data, mime, fname = _lookup_notion_image_bytes(images_b64, key)
        except (KeyError, ValueError, binascii.Error):
            warnings.append(f"Missing or invalid image data for {rel_path}")
            return None
        if len(data) > 20 * 1024 * 1024:
            warnings.append(f"Image too large for Notion direct upload (20 MB): {rel_path}")
            return None
        fid, upload_url = await _notion_create_file_upload(client, token, fname, mime)
        await _notion_send_file_bytes(client, token, upload_url, fname, data, mime)
        upload_by_key[key] = fid
        return fid

    for seg in segments:
        t = seg.get("type")
        if t == "paragraph":
            blocks.append(_notion_paragraph_block(str(seg["text"])))
        elif t == "h1":
            blocks.append(_notion_heading_block(1, str(seg["text"])))
        elif t == "h2":
            blocks.append(_notion_heading_block(2, str(seg["text"])))
        elif t == "h3":
            blocks.append(_notion_heading_block(3, str(seg["text"])))
        elif t == "bullet":
            blocks.append(_notion_bullet_block(str(seg["text"])))
        elif t == "numbered":
            blocks.append(_notion_numbered_block(str(seg["text"])))
        elif t == "code":
            blocks.append(_notion_code_block(str(seg.get("lang") or ""), str(seg["text"])))
        elif t == "divider":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif t == "image":
            uid = await upload_for_path(str(seg["path"]))
            if uid:
                blocks.append(_notion_image_block(uid, str(seg.get("alt") or "")))
            else:
                blocks.append(
                    _notion_paragraph_block(f"[Image not exported: {seg['path']}]")
                )
    return blocks


def _notion_blocks_sdk_shape(blocks: list[dict]) -> list[dict]:
    """Omit top-level `object` on blocks (matches JS SDK / some API gateways)."""
    out: list[dict] = []
    for b in blocks:
        if isinstance(b, dict) and "object" in b:
            out.append({k: v for k, v in b.items() if k != "object"})
        elif isinstance(b, dict):
            out.append(b)
    return out


async def _notion_append_block_children_chunks(
    client: httpx.AsyncClient,
    token: str,
    page_id: str,
    blocks: list[dict],
    notion_version: str,
    *,
    warnings: list[str],
) -> None:
    """Append blocks to an existing page (chunked; retry without `object` if needed)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": notion_version,
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    for off in range(0, len(blocks), 100):
        chunk = blocks[off : off + 100]
        ra = await client.patch(url, headers=headers, json={"children": chunk})
        if ra.status_code != 200:
            chunk2 = _notion_blocks_sdk_shape(chunk)
            ra = await client.patch(url, headers=headers, json={"children": chunk2})
            if ra.status_code == 200:
                warnings.append(
                    "Page content was appended using a compatible block shape (retried without "
                    "`object` on blocks)."
                )
            else:
                _notion_api_error(ra, "adding page content blocks")


async def export_notion_page_markdown(
    client: httpx.AsyncClient,
    token: str,
    page_id: str,
    md: str,
    *,
    resolved_title: str,
    images_b64: dict[str, str],
    extra_properties: dict[str, str],
) -> list[str]:
    """PATCH title/properties, clear block tree, append Markdown → Notion blocks."""
    export_warnings: list[str] = []
    md = (md or "").strip()
    if images_b64:
        md = _notion_restore_data_uri_images(md, images_b64)
    segments = _parse_markdown_to_notion_segments(md)
    if not segments and md.strip():
        segments = [{"type": "paragraph", "text": md.strip()}]

    title_prop, props_schema = await _notion_resolve_page_inspect(client, token, page_id)
    props_payload: dict[str, dict] = {
        title_prop: {
            "title": [
                {
                    "type": "text",
                    "text": {"content": resolved_title[:2000]},
                }
            ],
        },
    }
    props_payload.update(
        _notion_merge_extra_properties(
            props_schema,
            title_prop,
            dict(extra_properties),
            warnings=export_warnings,
        )
    )

    blocks = await _notion_segments_to_api_blocks(
        client, token, segments, images_b64, export_warnings
    )
    if not blocks and md.strip():
        blocks = [_notion_paragraph_block(md.strip())]

    r = await client.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        },
        json={"properties": props_payload},
    )
    if r.status_code != 200:
        _notion_api_error(r, "updating the page")

    await _notion_clear_page_children(client, token, page_id, warnings=export_warnings)
    await _notion_append_block_children_chunks(
        client,
        token,
        page_id,
        blocks,
        NOTION_API_VERSION,
        warnings=export_warnings,
    )
    return export_warnings

