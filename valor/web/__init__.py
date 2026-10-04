"""VALOR web service: accounts with MFA, audit log, settings and an API around the engine.

Runs inside the VALOR VM behind nginx (TLS). The engine stays the only component that calls the Proxmox API;
this package only starts engine jobs and reads their results.
"""
