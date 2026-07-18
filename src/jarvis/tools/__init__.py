"""Tool Manager: the single gateway through which all actions execute.

A :class:`~jarvis.tools.base.Tool` is one capability — read a file, click a
button, open a URL. Each declares a name, a description, a JSON-schema for
its arguments (so the Brain knows how to call it), and a *risk category*
that the permission policy maps to SAFE or CONFIRM.

The :class:`~jarvis.tools.manager.ToolManager` is the only place that runs
tools. It validates arguments, asks the permission policy whether a
confirmation is needed (and waits for it), logs every invocation with timing,
turns exceptions into structured :class:`~jarvis.core.models.ToolResult`
failures, and publishes lifecycle events. Nothing else in the system touches
the OS directly — this one choke point is what makes the assistant auditable
and safe.
"""

from jarvis.tools.base import Tool, ToolContext, tool
from jarvis.tools.manager import ToolManager
from jarvis.tools.registry import ToolRegistry

__all__ = ["Tool", "ToolContext", "ToolManager", "ToolRegistry", "tool"]
