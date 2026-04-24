"""Bridge — hosted MCP proxy for any public GraphQL API."""

from bridge.app import app, create_app

__all__ = ["app", "create_app"]
