"""Build Word (.docx) files from Markdown for download."""
import re

import markdown
from html2docx import html2docx

from smartdown.notion import _default_notion_page_title, _notion_markdown_without_images

_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _normalize_images_path_key(raw_path: str) -> str | None:
    u = raw_path.strip().strip('"').strip("'").split("?", 1)[0].replace("\\", "/")
    low = u.lower()
    i = low.find("images/")
    if i < 0:
        return None
    return u[i:].replace("\\", "/")


def _inline_markdown_images(md: str, images: dict[str, str]) -> str:
    if not images:
        return md

    def repl(m: re.Match[str]) -> str:
        alt, raw_path = m.group(1), m.group(2)
        key = _normalize_images_path_key(raw_path)
        if not key:
            return m.group(0)
        b64 = images.get(key)
        if b64 is None:
            b64 = images.get(key.lower())
        if not b64:
            return m.group(0)
        return f"![{alt}](data:image/png;base64,{b64})"

    return _MD_IMG_RE.sub(repl, md)


def markdown_to_docx_bytes(
    md: str,
    *,
    images: dict[str, str] | None = None,
    include_images: bool = True,
    title: str | None = None,
) -> bytes:
    work = (md or "").strip()
    if not work:
        raise ValueError("Markdown is empty.")
    imgs = dict(images) if images else {}
    if not include_images:
        work = _notion_markdown_without_images(work)
        imgs = {}
    else:
        work = _inline_markdown_images(work, imgs)
    doc_title = (title or "").strip() or _default_notion_page_title(work) or "Document"
    html = markdown.markdown(
        work,
        extensions=["extra", "nl2br", "sane_lists"],
    )
    buf = html2docx(html, doc_title)
    return buf.getvalue()
