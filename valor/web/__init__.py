# OwO *pads into the web package and sniffs around* hewwo!
# This is valor/web/__init__.py, the fwont door of the whole web sewvice package ^w^
# It doesn't hold any code of its own, just the docstring right below, which tells you (and Python's help())
# what the package is for: the FastAPI app (app.py), sign-in with MFA (auth.py, security.py), the audit log,
# settings, and the HTTP API that wraps the engine. *wags tail* The important rule it states: the web side
# never talks to Proxmox by itself, it only starts engine jobs (worker.py runs them) and reads their results UwU
"""VALOR web service: accounts with MFA, audit log, settings and an API around the engine.

Runs inside the VALOR VM behind nginx (TLS). The engine stays the only component that calls the Proxmox API;
this package only starts engine jobs and reads their results.
"""
