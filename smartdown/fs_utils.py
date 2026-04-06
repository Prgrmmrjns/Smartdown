"""Filesystem helpers for temp paths and uploads."""
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse


def cleanup_paths(paths: list) -> None:
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


def temp_root() -> str:
    return os.environ.get("TMPDIR") or tempfile.gettempdir()


def safe_filename_base(name: str) -> str:
    stem = Path(name).stem if name else "document"
    out = re.sub(r"[^\w\-]+", "_", stem).strip("_")
    return out or "document"


def filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        part = part.strip()
        if part.lower().startswith("filename*="):
            _, _, value = part.partition("=")
            value = value.strip().strip('"').strip("'")
            if "''" in value:
                value = value.split("''", 1)[-1]
            return unquote(value) or None
        if part.lower().startswith("filename="):
            _, _, value = part.partition("=")
            v = value.strip().strip('"').strip("'")
            return unquote(v) or None
    return None


def filename_base_from_url(url: str, content_disposition: str | None) -> str:
    from_cd = filename_from_content_disposition(content_disposition)
    if from_cd and from_cd.lower().endswith(".pdf"):
        return safe_filename_base(from_cd)
    path = urlparse(url).path or ""
    seg = Path(path).name
    if seg.lower().endswith(".pdf"):
        return safe_filename_base(seg)
    return "from_url"
