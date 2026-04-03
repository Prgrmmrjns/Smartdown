"""Vercel entrypoint: re-export the FastAPI application."""

from pdf_to_md import app

__all__ = ["app"]
