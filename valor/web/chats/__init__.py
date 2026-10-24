"""Chats with the AI: the chat builder and the range chats.

    routes.py    the API: saved chats per user, messages, approvals, stop; handing a failed build to the assistant
    turn.py      the background turn: the live feed, the chat builder, Plan mode and the range agent's tool loop
    autofix.py   failed builds go to the assistant, which fixes them (and, in mode 'auto', rebuilds the range)

turn and autofix call each other: a failed build starts a turn, and a finished fix turn may start the rebuild.
"""

from .autofix import fix_failed_builds, watch_failed_builds
from .routes import router

__all__ = ["fix_failed_builds", "router", "watch_failed_builds"]
