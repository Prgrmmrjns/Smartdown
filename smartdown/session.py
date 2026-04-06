"""In-memory document sessions between convert and agent/notion."""
import asyncio
import time
from dataclasses import dataclass, field

from smartdown.config import SESSION_TTL_SEC
from smartdown.fs_utils import cleanup_paths

_session_lock = asyncio.Lock()
_sessions: dict[str, "DocumentSession"] = {}


@dataclass
class DocumentSession:
    raw_markdown: str
    images: dict[str, str]
    filename_base: str
    beautified_markdown: str | None = None
    created: float = field(default_factory=lambda: time.time())
    source_pdf_path: str | None = None
    pdf_plain_text: str | None = None


def _purge_expired_sessions() -> None:
    cutoff = time.time() - SESSION_TTL_SEC
    dead = [k for k, v in _sessions.items() if v.created < cutoff]
    for k in dead:
        s = _sessions.pop(k, None)
        if s and s.source_pdf_path:
            cleanup_paths([s.source_pdf_path])
