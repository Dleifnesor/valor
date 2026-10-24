"""V.A.L.O.R. engine: validate, plan, apply, verify and destroy test ranges on Proxmox VE.

The AI only writes range specifications and asks for checked tool calls (ai/, agent/); this package is the only
component that talks to the Proxmox API (pve.py). Map of the modules: docs/ARCHITECTURE.md.
"""

__version__ = "0.2.0.dev0"
