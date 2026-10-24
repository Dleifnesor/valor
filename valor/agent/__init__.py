"""The range agent: what the AI may do in a range chat.

Reads run at once. Everything else needs the person's approval in the chat (unless they allowed all actions for that
chat), and Question mode gets the read-only tools only.

    tools.py      the tool definitions, their checks and how VALOR runs them
    readonly.py   which calls only read (the command allowlists for Linux and PowerShell)
"""

from .readonly import is_readonly, linux_readonly, powershell_readonly
from .tools import READ_TOOLS, TOOLS, Context, execute, summary, tool_specs, validate

__all__ = ["READ_TOOLS", "TOOLS", "Context", "execute", "is_readonly", "linux_readonly", "powershell_readonly",
           "summary", "tool_specs", "validate"]
