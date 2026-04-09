"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from smartdown.config import BASE_DIR
from smartdown.routes import setup_routes


class _RestoreApiPrefixMiddleware(BaseHTTPMiddleware):
    """If a reverse proxy forwards the app without the `/api` segment, restore it for our routes."""

    @staticmethod
    def _fixed_path(path: str) -> str | None:
        if path.startswith("/api/") or path.startswith("/static"):
            return None
        if path in ("/", ""):
            return None
        if (
            path.startswith("/agent-block-")
            or path.startswith("/convert")
            or path.startswith("/document/")
            or path.startswith("/extract-markdown")
            or path.startswith("/export-")
            or path.startswith("/notion/")
            or path == "/llm-options"
        ):
            return "/api" + path
        return None

    async def dispatch(self, request: Request, call_next):
        path = request.scope.get("path") or ""
        new_path = self._fixed_path(path)
        if new_path is not None:
            request.scope["path"] = new_path
            rp = request.scope.get("raw_path")
            if isinstance(rp, (bytes, bytearray)):
                request.scope["raw_path"] = new_path.encode("ascii")
        return await call_next(request)


app = FastAPI(title="Smartdown", default_response_class=ORJSONResponse)
app.add_middleware(_RestoreApiPrefixMiddleware)
setup_routes(app)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
