"""FastAPI application factory."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles

from smartdown.config import BASE_DIR
from smartdown.routes import setup_routes

app = FastAPI(title="Smartdown", default_response_class=ORJSONResponse)
setup_routes(app)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
