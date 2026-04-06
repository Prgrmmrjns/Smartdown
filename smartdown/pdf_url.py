"""Fetch PDFs from HTTP(S) URLs with SSRF guards."""
import asyncio
import ipaddress
import os
import socket
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException

from smartdown.config import SMARTDOWN_MAX_URL_PDF_BYTES, URL_FETCH_MAX_REDIRECTS, URL_FETCH_TIMEOUT
from smartdown.fs_utils import filename_base_from_url, filename_from_content_disposition

def _literal_host_blocked(hostname: str) -> bool:
    h = (hostname or "").strip().lower().rstrip(".")
    if h in ("localhost", "0.0.0.0", "127.0.0.1", "::1", "[::1]"):
        return True
    if h.endswith(".local") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def _ensure_host_resolves_to_public_ip(hostname: str) -> None:
    if _literal_host_blocked(hostname):
        raise HTTPException(
            status_code=400,
            detail="That host is not allowed (private, loopback, or local addresses are blocked).",
        )
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise HTTPException(
            status_code=400, detail=f"Could not resolve host: {e}"
        ) from e
    for _fam, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        if not isinstance(ip_str, str):
            continue
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail="URL resolves to a non-public address (blocked for security).",
            )


def _validate_http_url_for_fetch(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="URL is required.")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail="Only http and https URLs are allowed."
        )
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid URL (no host).")
    if parsed.username is not None:
        raise HTTPException(
            status_code=400, detail="URLs with embedded credentials are not allowed."
        )
    return raw


async def _download_pdf_from_url(initial_url: str) -> tuple[bytes, str | None]:
    current = _validate_http_url_for_fetch(initial_url)
    content_disposition: str | None = None
    async with httpx.AsyncClient(timeout=URL_FETCH_TIMEOUT, follow_redirects=False) as client:
        for _ in range(URL_FETCH_MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https"):
                raise HTTPException(status_code=400, detail="Invalid redirect URL.")
            host = parsed.hostname
            if not host:
                raise HTTPException(status_code=400, detail="Invalid redirect URL.")
            await asyncio.to_thread(_ensure_host_resolves_to_public_ip, host)
            r = await client.get(
                current,
                headers={"User-Agent": "Smartdown/1.0 (+https://github.com/) PDF fetch"},
            )
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                if not loc:
                    raise HTTPException(
                        status_code=400, detail="Redirect response missing Location header."
                    )
                current = urljoin(current, loc)
                continue
            if r.status_code >= 400:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not download PDF (HTTP {r.status_code}).",
                )
            content_disposition = r.headers.get("content-disposition")
            total = 0
            chunks: list[bytes] = []
            async for chunk in r.aiter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > SMARTDOWN_MAX_URL_PDF_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Downloaded file exceeds maximum size "
                            f"({SMARTDOWN_MAX_URL_PDF_BYTES // (1024 * 1024)} MB)."
                        ),
                    )
                chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                raise HTTPException(status_code=400, detail="Downloaded file is empty.")
            if not data.startswith(b"%PDF"):
                raise HTTPException(
                    status_code=400,
                    detail="Downloaded content is not a PDF (missing %PDF signature).",
                )
            return data, content_disposition
    raise HTTPException(status_code=400, detail="Too many redirects.")

