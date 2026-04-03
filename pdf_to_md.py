import base64
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

import fitz
import pymupdf4llm
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Smartdown")

FIGURE_MIN_WIDTH = 200
FIGURE_MIN_HEIGHT = 150
IMAGES_DIR = "images"


def cleanup_paths(paths: list):
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass


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
    strip_citations: bool = True,
) -> str:
    if strip_citations:
        md = _strip_citation_brackets(md)
        md = _remove_references_section(md)
    if strip_page_numbers:
        md = _strip_page_number_lines(md)
    return _collapse_blank_lines(md)


def _parse_form_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).lower() in ("true", "1", "on", "yes")


def convert_pdf(
    pdf_path: str,
    output_dir: str,
    *,
    strip_page_numbers: bool = True,
    strip_citations: bool = True,
) -> tuple[str, str]:
    """Convert PDF to Markdown with images saved to output_dir/images/.

    Returns (md_file_path, images_dir_path).
    """
    img_dir = os.path.join(output_dir, IMAGES_DIR)
    os.makedirs(img_dir, exist_ok=True)

    doc = fitz.open(pdf_path)

    chunks = pymupdf4llm.to_markdown(
        pdf_path,
        write_images=True,
        image_path=img_dir,
        image_format="png",
        force_text=False,
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

        math_imgs = _extract_math_equation_images(page, img_dir, page_num)
        if math_imgs:
            page_md = _strip_math_only_lines(page_md)
            page_md += "\n\n" + "\n\n".join(math_imgs) + "\n\n"

        result_parts.append(page_md)

    doc.close()

    _cleanup_images(img_dir)

    surviving = set(os.listdir(img_dir))
    md_text = "".join(result_parts)
    md_text = re.sub(
        r"!\[.*?\]\(images/([^)]+)\)",
        lambda m: m.group(0) if m.group(1) in surviving else "",
        md_text,
    )

    md_text = apply_note_cleanups(
        md_text,
        strip_page_numbers=strip_page_numbers,
        strip_citations=strip_citations,
    )

    md_path = os.path.join(output_dir, "converted.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    return md_path, img_dir


def _temp_root() -> str:
    return os.environ.get("TMPDIR") or tempfile.gettempdir()


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
    strip_citations: Annotated[str, Form()] = "true",
):
    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(
            status_code=400, detail="Please upload a file with a .pdf extension."
        )

    temp_pdf_path = None
    work_dir = None
    tmp = _temp_root()
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".pdf", dir=tmp
        ) as temp_pdf:
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Empty file.")
            temp_pdf.write(content)
            temp_pdf_path = temp_pdf.name

        work_dir = tempfile.mkdtemp(dir=tmp)
        md_path, img_dir = convert_pdf(
            temp_pdf_path,
            work_dir,
            strip_page_numbers=_parse_form_bool(strip_page_numbers, True),
            strip_citations=_parse_form_bool(strip_citations, True),
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

        return JSONResponse(
            {
                "markdown": markdown_text,
                "images": images,
                "filename_base": Path(file.filename or "document").stem,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        cleanup_paths([temp_pdf_path, work_dir])


app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
