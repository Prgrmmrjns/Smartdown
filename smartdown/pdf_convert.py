"""PDF → Markdown conversion and cleanup helpers."""
import base64
import os
import re
import tempfile
from typing import Literal

import fitz

from smartdown.config import IMAGES_DIR, SMARTDOWN_PDF_USE_OCR
from smartdown.fs_utils import cleanup_paths, temp_root

_PAGE_LINES = (
    re.compile(r"^\s*(?:Page|PAGE|Pg\.?)\s+\d+(?:\s+of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*-\s*\d{1,4}\s*-\s*$"),
    re.compile(r"^\s*[–—]\s*\d{1,4}\s*[–—]\s*$"),
    re.compile(r"^\s*\d{1,4}\s*$"),
)
_CITATIONS = re.compile(r"\[\s*[\d\s,;–\-]{1,}\s*\](?!\s*\()")
_REF_HEADINGS = (
    re.compile(
        r"^#{1,6}\s*(?:References|Bibliography|Works\s+Cited)\b[^\n]*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(
        r"^\*{1,2}\s*References\s*\*{1,2}\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
)
_OMITTED_PIC = re.compile(
    r"(?:\*\*)?\s*==>\s*picture\s*\[[^\]]+\]\s*intentionally omitted\s*<==(?:\*\*)?\s*",
    re.IGNORECASE,
)
_SINGLE_LETTER_ITALIC = re.compile(r"(?<!\*)\*([a-zA-Z])\*(?!\*)")
_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def parse_form_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).lower() in ("true", "1", "on", "yes")


def _truncate_at_references(md: str) -> str:
    best: re.Match[str] | None = None
    for pat in _REF_HEADINGS:
        m = pat.search(md)
        if m and (best is None or m.start() < best.start()):
            best = m
    return md[: best.start()].rstrip() if best else md


def _clean_markdown(
    md: str,
    *,
    strip_page_numbers: bool,
    strip_citations: bool,
) -> str:
    md = _OMITTED_PIC.sub("", md)
    if strip_citations:
        md = _CITATIONS.sub("", md)
        md = _truncate_at_references(md)
    if strip_page_numbers:
        md = "\n".join(
            ln for ln in md.splitlines() if not any(p.match(ln) for p in _PAGE_LINES)
        )
    s = re.sub(r"\n{3,}", "\n\n", md).strip()
    return s + "\n" if s else ""


def _rewrite_image_paths(md: str, img_dir: str) -> str:
    if not md or not os.path.isdir(img_dir):
        return md
    names = {n for n in os.listdir(img_dir) if os.path.isfile(os.path.join(img_dir, n))}
    if not names:
        return md

    def repl(m: re.Match[str]) -> str:
        alt, path = m.group(1), m.group(2).strip().split("?", 1)[0].strip()
        base = os.path.basename(path.replace("\\", "/"))
        return f"![{alt}]({IMAGES_DIR}/{base})" if base in names else m.group(0)

    return _IMG.sub(repl, md)


def _pymupdf_to_md(
    pdf_path: str, img_dir: str, *, extract_images: bool, use_ocr: bool
) -> str:
    import pymupdf4llm

    stem = os.path.basename(pdf_path).replace(" ", "-")
    kw: dict = {
        "filename": stem,
        "page_separators": False,
        "header": False,
        "footer": False,
        "force_text": True,
        "use_ocr": use_ocr,
        "show_progress": False,
    }
    if extract_images:
        kw.update(write_images=True, image_path=img_dir, image_format="png")
    else:
        kw.update(write_images=False, embed_images=False)
    out = pymupdf4llm.to_markdown(pdf_path, **kw)
    return out if isinstance(out, str) else str(out)


def convert_pdf(
    pdf_path: str,
    output_dir: str,
    *,
    strip_page_numbers: bool = True,
    strip_citations: bool = True,
    equation_handling: Literal["markdown", "image"] = "markdown",
    math_inline_code: bool = True,
    mistral_api_key: str | None = None,
) -> tuple[str, str]:
    """Convert PDF with pymupdf4llm; returns (markdown file path, images directory)."""
    _ = mistral_api_key
    if os.path.getsize(pdf_path) == 0:
        raise ValueError("Empty PDF file.")

    img_dir = os.path.join(output_dir, IMAGES_DIR)
    os.makedirs(img_dir, exist_ok=True)

    md = _pymupdf_to_md(
        pdf_path,
        img_dir,
        extract_images=(equation_handling == "image"),
        use_ocr=SMARTDOWN_PDF_USE_OCR,
    )
    md = _rewrite_image_paths(md, img_dir)

    alive = set(os.listdir(img_dir))
    md = re.sub(
        r"!\[.*?\]\(images/([^)]+)\)",
        lambda m: m.group(0) if m.group(1) in alive else "",
        md,
    )
    if equation_handling == "markdown" and math_inline_code:
        md = _SINGLE_LETTER_ITALIC.sub(r"`\1`", md)
    md = _clean_markdown(
        md,
        strip_page_numbers=strip_page_numbers,
        strip_citations=strip_citations,
    )

    md_path = os.path.join(output_dir, "converted.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    return md_path, img_dir


def extract_plain_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    try:
        pages = [p.get_text("text") for p in doc]
    finally:
        doc.close()
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _convert_pdf_at_path_to_markdown_and_images(
    pdf_path: str,
    *,
    strip_page_numbers: bool = True,
    strip_citations: bool = True,
    equation_handling: Literal["markdown", "image"] = "markdown",
    math_inline_code: bool = True,
    mistral_api_key: str | None = None,
) -> tuple[str, dict[str, str]]:
    work_dir = tempfile.mkdtemp(dir=temp_root())
    try:
        md_path, img_dir = convert_pdf(
            pdf_path,
            work_dir,
            strip_page_numbers=strip_page_numbers,
            strip_citations=strip_citations,
            equation_handling=equation_handling,
            math_inline_code=math_inline_code,
            mistral_api_key=mistral_api_key,
        )
        with open(md_path, encoding="utf-8") as f:
            markdown_text = f.read()
        images: dict[str, str] = {}
        if os.path.isdir(img_dir):
            for img_name in sorted(os.listdir(img_dir)):
                ip = os.path.join(img_dir, img_name)
                if not os.path.isfile(ip):
                    continue
                key = f"{IMAGES_DIR}/{img_name.replace(chr(92), '/')}"
                with open(ip, "rb") as bf:
                    images[key] = base64.standard_b64encode(bf.read()).decode("ascii")
        return markdown_text, images
    finally:
        cleanup_paths([work_dir])
