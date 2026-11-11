"""Local web UI for entity search and corrections."""
from .server import AppState, make_handler, serve

__all__ = ["AppState", "make_handler", "serve"]
