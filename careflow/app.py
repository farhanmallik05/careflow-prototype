"""Compatibility import for deployment servers and API clients."""

from careflow.api import app, create_app

__all__ = ["app", "create_app"]
