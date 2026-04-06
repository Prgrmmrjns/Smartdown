"""PDF → Markdown conversion and cleanup helpers."""
import base64
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

import fitz
import pymupdf4llm

# pymupdf4llm ≥1.27 enables pymupdf.layout + OCR by default. That runs Tesseract on
# small regions (stderr: "Image too small to scale", "Line cannot be recognized") and
# often yields empty or broken Markdown for normal text PDFs. Legacy rag mode matches
# this module's figure/math post-processing.
try:
    pymupdf4llm.use_layout(False)
except Exception:
    pass

from smartdown.config import FIGURE_MIN_HEIGHT, FIGURE_MIN_WIDTH, IMAGES_DIR
from smartdown.fs_utils import cleanup_paths, temp_root

def _extract_figure_images(
    doc: fitz.Document, page: fitz.Page, img_dir: str, page_num: int
) -> list[tuple[str, str]]:
    """Extract large raster images from a page, save to disk, return (filename, md_ref) pairs."""
    results = []
    fig_idx = 0
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            pix = fitz.Pixmap(doc, xref)
            if pix.width < FIGURE_MIN_WIDTH or pix.height < FIGURE_MIN_HEIGHT:
                continue
            if pix.alpha:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            fname = f"page{page_num + 1}_fig{fig_idx}.png"
            pix.save(os.path.join(img_dir, fname))
            md_ref = f"![image]({IMAGES_DIR}/{fname})"
            results.append((fname, md_ref))
            fig_idx += 1
        except Exception:
            continue
    return results


def _cleanup_images(img_dir: str, min_bytes: int = 2048):
    """Remove tiny junk images from the output."""
    for name in list(os.listdir(img_dir)):
        path = os.path.join(img_dir, name)
        if os.path.getsize(path) < min_bytes:
            os.remove(path)


def _char_is_math_symbol(ch: str) -> bool:
    o = ord(ch)
    if ch.isspace():
        return False
    if 0x1D400 <= o <= 0x1D7FF:
        return True
    if 0x2070 <= o <= 0x209F:
        return True
    if 0x2200 <= o <= 0x23FF:
        return True
    if 0x2A00 <= o <= 0x2AFF:
        return True
    if 0x27C0 <= o <= 0x27FF:
        return True
    if ch in "∂∇∞±×·÷≤≥≠≈∈∑∫√":
        return True
    if 0x03B1 <= o <= 0x03C9 or 0x0391 <= o <= 0x03A9:
        return True
    return False


def _span_looks_mathy(text: str) -> bool:
    if not text or not text.strip():
        return False
    sym = sum(1 for c in text if _char_is_math_symbol(c))
    return sym >= max(2, int(len(text) * 0.25))


def _extract_math_equation_images(
    page: fitz.Page, img_dir: str, page_num: int, max_per_page: int = 12
) -> list[str]:
    """Rasterize tight crops around math-heavy text spans; return markdown image lines."""
    d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    groups: list[list[fitz.Rect]] = []
    current: list[fitz.Rect] = []

    def flush():
        nonlocal current
        if len(current) >= 1:
            groups.append(current)
        current = []

    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span.get("text") or ""
                bbox = span.get("bbox")
                if not bbox:
                    continue
                r = fitz.Rect(bbox)
                if _span_looks_mathy(t):
                    current.append(r)
                else:
                    flush()
            flush()

    md_lines: list[str] = []
    zoom = fitz.Matrix(2, 2)
    for i, rects in enumerate(groups):
        if i >= max_per_page:
            break
        clip = rects[0]
        for r in rects[1:]:
            clip = clip | r
        clip += (-3, -3, 3, 3)
        clip &= page.rect
        if clip.width < 4 or clip.height < 4:
            continue
        if (clip.width * clip.height) > 0.45 * page_area:
            continue
        try:
            pix = page.get_pixmap(matrix=zoom, clip=clip, alpha=False)
            if pix.width < 8 or pix.height < 8:
                continue
            fname = f"page{page_num + 1}_math{i}.png"
            pix.save(os.path.join(img_dir, fname))
            md_lines.append(f"![equation]({IMAGES_DIR}/{fname})")
        except Exception:
            continue
    return md_lines


_SINGLE_LETTER_ITALIC_FOR_CODE_RE = re.compile(
    r"(?<!\*)\*([a-zA-Z])\*(?!\*)",
)


def _math_single_italic_letters_to_inline_code(md: str) -> str:
    """Turn *x* (single Latin letter) into `x` for mathy prose (see e.g. paper .md exports)."""
    return _SINGLE_LETTER_ITALIC_FOR_CODE_RE.sub(r"`\1`", md)


_OMITTED_PICTURE_PLACEHOLDER_RE = re.compile(
    r"(?:\*\*)?\s*==>\s*picture\s*\[[^\]]+\]\s*intentionally omitted\s*<==(?:\*\*)?\s*",
    re.IGNORECASE,
)

_MD_IMG_LINK_RE = re.compile(r"!\[([^\]]*)\]\(images/([^)]+)\)", re.IGNORECASE)


def _strip_pymupdf_picture_omitted_placeholders(md: str) -> str:
    """Remove PyMuPDF4LLM placeholders when raster output was disabled (we avoid that path)."""
    return _OMITTED_PICTURE_PLACEHOLDER_RE.sub("", md)


def _looks_like_equation_raster(w: int, h: int) -> bool:
    """Shallow wide strips or small blocks typical of layout 'formula' crops."""
    if w < 16 or h < 10:
        return True
    if h <= 95 and w >= 72:
        return True
    if h <= 240 and w >= h * 2:
        return True
    return False


def _strip_equation_like_raster_refs(md: str, img_dir: str) -> str:
    """Drop markdown links to equation-sized PNGs (keep large figure extracts)."""

    def repl(m: re.Match[str]) -> str:
        fname = m.group(2).replace("\\", "/").split("/")[-1]
        if "_math" in fname:
            try:
                os.remove(os.path.join(img_dir, fname))
            except OSError:
                pass
            return ""
        if fname.startswith("page") and "_fig" in fname:
            return m.group(0)
        path = os.path.join(img_dir, fname)
        if not os.path.isfile(path):
            return m.group(0)
        try:
            pix = fitz.Pixmap(path)
            w, h = pix.width, pix.height
        except Exception:
            return m.group(0)
        if w >= FIGURE_MIN_WIDTH and h >= FIGURE_MIN_HEIGHT:
            return m.group(0)
        if _looks_like_equation_raster(w, h):
            try:
                os.remove(path)
            except OSError:
                pass
            return ""
        return m.group(0)

    return _MD_IMG_LINK_RE.sub(repl, md)


def _strip_math_only_lines(md: str) -> str:
    """Drop lines that are mostly Unicode math (replaced by equation images)."""
    out: list[str] = []
    for line in md.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
            continue
        if s.startswith("!["):
            out.append(line)
            continue
        if "#" in s[:2]:
            out.append(line)
            continue
        n = len(s)
        sym = sum(1 for c in s if _char_is_math_symbol(c))
        if sym >= 4 and sym >= n * 0.45:
            continue
        out.append(line)
    return "\n".join(out)


_EQ_NUM_TAIL_RE = re.compile(r"\(\d+\)\s*$")


def _line_is_display_equation_candidate(line: str) -> bool:
    """Heuristic: line is mostly math symbols / equation-like (for fenced ``` blocks)."""
    s = line.strip()
    if not s or len(s) < 2:
        return False
    if s.startswith("#") or s.startswith("![") or s.startswith("|"):
        return False
    if s.startswith("http://") or s.startswith("https://"):
        return False
    if s.startswith(">"):
        return False
    n = len(s)
    sym = sum(1 for c in s if _char_is_math_symbol(c))
    if sym >= 4 and sym >= n * 0.32:
        return True
    if _EQ_NUM_TAIL_RE.search(s) and ("=" in s or sym >= 2):
        return True
    if "=" in s and sym >= 2 and n <= 140:
        return True
    letters = sum(1 for c in s if c.isalpha())
    if letters > 55 and sym < 4:
        return False
    if sym >= 3 and n <= 110 and ("=" in s or "∑" in s or "Σ" in s or "∫" in s):
        return True
    return False


def wrap_display_equations_in_fenced_code(md: str) -> str:
    """Wrap consecutive display-style equation lines in plain ``` fences (no language tag)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        raw = lines[i]
        lstripped = raw.lstrip()
        if lstripped.startswith("```"):
            in_fence = not in_fence
            out.append(raw)
            i += 1
            continue
        if in_fence:
            out.append(raw)
            i += 1
            continue

        if not _line_is_display_equation_candidate(raw):
            out.append(raw)
            i += 1
            continue

        buf: list[str] = []
        while i < len(lines):
            r2 = lines[i]
            if r2.lstrip().startswith("```"):
                break
            if not r2.strip():
                break
            if not _line_is_display_equation_candidate(r2):
                break
            buf.append(r2.rstrip())
            i += 1
        if buf:
            out.append("```")
            out.extend(buf)
            out.append("```")
            continue

        out.append(raw)
        i += 1

    return "\n".join(out)


_PAGE_NUMBER_LINE_RES = (
    re.compile(r"^\s*(?:Page|PAGE|Pg\.?)\s+\d+(?:\s+of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*-\s*\d{1,4}\s*-\s*$"),
    re.compile(r"^\s*[–—]\s*\d{1,4}\s*[–—]\s*$"),
    re.compile(r"^\s*\d{1,4}\s*$"),
)

_CITATION_BRACKET_RE = re.compile(
    r"\[\s*[\d\s,;–\-]{1,}\s*\](?!\s*\()",
)

_REF_SECTION_HEADING_RES = (
    re.compile(
        r"^#{1,6}\s*(?:References|Bibliography|Works\s+Cited)\b[^\n]*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(
        r"^\*{1,2}\s*References\s*\*{1,2}\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
)


def _strip_page_number_lines(md: str) -> str:
    lines = md.splitlines()
    kept: list[str] = []
    for line in lines:
        if any(p.match(line) for p in _PAGE_NUMBER_LINE_RES):
            continue
        kept.append(line)
    return "\n".join(kept)


def _strip_citation_brackets(md: str) -> str:
    return _CITATION_BRACKET_RE.sub("", md)


def _remove_references_section(md: str) -> str:
    best: re.Match | None = None
    for p in _REF_SECTION_HEADING_RES:
        m = p.search(md)
        if m and (best is None or m.start() < best.start()):
            best = m
    if not best:
        return md
    return md[: best.start()].rstrip()


def _collapse_blank_lines(md: str) -> str:
    s = re.sub(r"\n{3,}", "\n\n", md).strip()
    return s + "\n" if s else ""


def apply_note_cleanups(
    md: str,
    *,
    strip_page_numbers: bool = True,
    strip_citations: bool = False,
) -> str:
    if strip_citations:
        md = _strip_citation_brackets(md)
        md = _remove_references_section(md)
    if strip_page_numbers:
        md = _strip_page_number_lines(md)
    return _collapse_blank_lines(md)


def parse_form_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).lower() in ("true", "1", "on", "yes")


def convert_pdf(
    pdf_path: str,
    output_dir: str,
    *,
    strip_page_numbers: bool = True,
    strip_citations: bool = False,
    equation_handling: Literal["markdown", "image"] = "markdown",
    math_inline_code: bool = True,
) -> tuple[str, str]:
    """Convert PDF to Markdown.

    PyMuPDF4LLM always uses write_images=True and force_text=True so formula regions are
    not replaced by "picture omitted" placeholders. equation_handling "image" keeps
    equation raster crops and formula PNGs; "markdown" strips equation-shaped images
    and applies optional *x* -> `x`.

    Returns (md_file_path, images_dir_path).
    """
    img_dir = os.path.join(output_dir, IMAGES_DIR)
    os.makedirs(img_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    math_as_images = equation_handling == "image"

    chunks = pymupdf4llm.to_markdown(
        pdf_path,
        write_images=True,
        image_path=img_dir,
        image_format="png",
        force_text=True,
        page_chunks=True,
    )

    figure_re = re.compile(
        r"^((?:Figure|Fig\.?)\s*\d+[^}\n]*?)$",
        re.MULTILINE | re.IGNORECASE,
    )

    result_parts = []
    for page_num, chunk in enumerate(chunks):
        page_md = chunk["text"]
        page = doc[page_num]

        page_md = re.sub(
            r"!\[(.*?)\]\([^)]*[/\\](images[/\\][^)]+)\)",
            r"![\1](\2)",
            page_md,
        )
        page_md = page_md.replace("\\", "/")

        figure_imgs = _extract_figure_images(doc, page, img_dir, page_num)

        if figure_imgs:
            captions = list(figure_re.finditer(page_md))
            if captions:
                for caption, (_, md_ref) in zip(
                    reversed(captions), reversed(figure_imgs)
                ):
                    pos = caption.start()
                    page_md = page_md[:pos] + md_ref + "\n\n" + page_md[pos:]
            else:
                page_md += "\n\n" + "\n\n".join(ref for _, ref in figure_imgs) + "\n\n"

        if math_as_images:
            math_imgs = _extract_math_equation_images(page, img_dir, page_num)
            if math_imgs:
                page_md = _strip_math_only_lines(page_md)
                page_md += "\n\n" + "\n\n".join(math_imgs) + "\n\n"

        result_parts.append(page_md)

    doc.close()

    md_text = "".join(result_parts)
    md_text = _strip_pymupdf_picture_omitted_placeholders(md_text)
    if not math_as_images:
        md_text = _strip_equation_like_raster_refs(md_text, img_dir)

    _cleanup_images(img_dir)

    surviving = set(os.listdir(img_dir))
    md_text = re.sub(
        r"!\[.*?\]\(images/([^)]+)\)",
        lambda m: m.group(0) if m.group(1) in surviving else "",
        md_text,
    )

    if equation_handling == "markdown" and math_inline_code:
        md_text = _math_single_italic_letters_to_inline_code(md_text)

    md_text = apply_note_cleanups(
        md_text,
        strip_page_numbers=strip_page_numbers,
        strip_citations=strip_citations,
    )
    md_text = wrap_display_equations_in_fenced_code(md_text)

    md_path = os.path.join(output_dir, "converted.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    return md_path, img_dir


def extract_plain_text(pdf_path: str) -> str:
    """Extract plain text from a PDF using PyMuPDF (no markdown conversion)."""
    doc = fitz.open(pdf_path)
    pages: list[str] = []
    for page in doc:
        pages.append(page.get_text("text"))
    doc.close()
    return "\n\n".join(p.strip() for p in pages if p.strip())


def parse_equation_handling(value: str | None) -> Literal["markdown", "image"]:
    v = (value or "markdown").strip().lower()
    if v == "markdown":
        return "markdown"
    return "image"


def _convert_pdf_at_path_to_markdown_and_images(
    pdf_path: str,
    *,
    strip_page_numbers: bool,
    strip_citations: bool,
    equation_handling: Literal["markdown", "image"] = "markdown",
    math_inline_code: bool = True,
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
