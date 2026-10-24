# *pounces onto the top of the package* OwO hewwo fwiend! This is valor/__init__.py, the vewy first file Python
# runs when anything does `import valor` >w<
# It does two smol but impowtant things:
#   1. The docstring below describes the engine as a whole: validate -> plan -> apply -> verify -> destroy,
#      for test ranges (groups of VMs + networks) on Proxmox VE. *wags tail*
#   2. It sets `__version__`, the version string that the CLI and the web API report back to you ^w^
# The big safety idea is in the docstring too: the AI parts (ai/, agent/) only ever WRITE specs and ASK for
# tool calls; pve.py is the one and only place that actually talks to the Proxmox API. *nuzzles the boundary*
"""V.A.L.O.R. engine: validate, plan, apply, verify and destroy test ranges on Proxmox VE.

The AI only writes range specifications and asks for checked tool calls (ai/, agent/); this package is the only
component that talks to the Proxmox API (pve.py). Map of the modules: docs/ARCHITECTURE.md.
"""

# The package version, PEP 440 style: "0.2.0" plus ".dev0" which means "a development build before 0.2.0" UwU
# `valor --version`-style output and the web API's status page read this one value. *boops the number*
__version__ = "0.2.0.dev0"
