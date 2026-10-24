# OwO *sniffs* this is valor/agent/__init__.py, the doorway to the range agent!
# The "range agent" is the set of tools the AI may use inside a range chat (run a command in a VM, read a file,
# take a snapshot, ...). The docstring below says the rules: read-only things run at once, everything else
# waits for a human to approve it in the chat ^w^ *wags tail*
# This file just collects the public names from the two modules so callers can `from valor.agent import ...`.
"""The range agent: what the AI may do in a range chat.

Reads run at once. Everything else needs the person's approval in the chat (unless they allowed all actions for that
chat), and Question mode gets the read-only tools only.

    tools.py      the tool definitions, their checks and how VALOR runs them
    readonly.py   which calls only read (the command allowlists for Linux and PowerShell)
"""

# From readonly.py: the checks that decide if a command only READS (is_readonly), with one allowlist for Linux
# shell commands (linux_readonly) and one for PowerShell (powershell_readonly) >w<
from .readonly import is_readonly, linux_readonly, powershell_readonly
# From tools.py: READ_TOOLS (the tools Question mode may use), TOOLS (all of them), Context (what a tool call
# needs to know: the range, the person, the chat...), execute (actually run a call), summary (one-line text
# for the approval card), tool_specs (the JSON schema sent to the model) and validate (check a call's arguments
# before anything happens) UwU *purrs*
from .tools import READ_TOOLS, TOOLS, Context, execute, summary, tool_specs, validate

# The public names of the package, so imports from outside stay tidy and explicit ^w^
__all__ = ["READ_TOOLS", "TOOLS", "Context", "execute", "is_readonly", "linux_readonly", "powershell_readonly",
           "summary", "tool_specs", "validate"]
