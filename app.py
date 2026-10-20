"""Compatibility ASGI entry point; application code lives in backend.main."""

from backend.main import app

__all__ = ["app"]
