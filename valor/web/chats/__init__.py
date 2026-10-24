# *ears perk up* OwO this is valor/web/chats/__init__.py, the entrance to the chat package!
# The docstring right below maps out its three files (routes, turn, autofix) and how they hold paws ^w^
# Everything else in VALOR imports chat things from HERE, so the package re-exports just the three names
# other code needs, and hides the rest as internal details. *wags tail*
"""Chats with the AI: the chat builder and the range chats.

    routes.py    the API: saved chats per user, messages, approvals, stop; handing a failed build to the assistant
    turn.py      the background turn: the live feed, the chat builder, Plan mode and the range agent's tool loop
    autofix.py   failed builds go to the assistant, which fixes them (and, in mode 'auto', rebuilds the range)

turn and autofix call each other: a failed build starts a turn, and a finished fix turn may start the rebuild.
"""

# `fix_failed_builds` = hand a failed build to the AI assistant so it can fix it;
# `watch_failed_builds` = the background watcher (started by the web app) that notices failed builds on its own >w<
from .autofix import fix_failed_builds, watch_failed_builds
# `router` = the FastAPI router with every /api/chats... endpoint; app.py plugs it into the web app UwU
from .routes import router

# `__all__` lists the public names, so `from valor.web.chats import *` (and linters) know what's meant to be
# used from outside. Anything not listed here is private to the package *hides other names behind fluffy tail*
__all__ = ["fix_failed_builds", "router", "watch_failed_builds"]
