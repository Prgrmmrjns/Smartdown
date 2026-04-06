"""FastAPI application factory."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from smartdown.config import BASE_DIR
from smartdown.routes import setup_routes

app = FastAPI(title="Smartdown")
setup_routes(app)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
