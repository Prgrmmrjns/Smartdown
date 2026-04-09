"""Register all HTTP routes on the FastAPI app."""

from fastapi import FastAPI

from smartdown.routes import agent, document, export_routes, notion_routes, pages


def setup_routes(app: FastAPI) -> None:
    pages.register(app)
    document.register(app)
    export_routes.register(app)
    agent.register(app)
    notion_routes.register(app)
