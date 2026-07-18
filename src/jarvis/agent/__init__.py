"""The agent orchestrator: Jarvis's main loop.

Takes a submitted request through the full lifecycle:

    understand → plan → execute one step at a time → observe →
    retry / revise on failure → summarise → remember

The orchestrator owns task state and drives the other modules; it contains
no tool logic, no LLM prompts beyond the summary, and no I/O of its own.
"""

from jarvis.agent.orchestrator import Orchestrator

__all__ = ["Orchestrator"]
