"""ASGI entrypoint for Vercel (discovers app.py at repo root)."""
from smartdown.app import app

__all__ = ["app"]
