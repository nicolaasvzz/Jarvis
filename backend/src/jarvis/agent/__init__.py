"""The agent orchestrator: Jarvis's main loop.

Takes a submitted request through the full lifecycle:

    understand → plan → execute the unblocked steps → observe →
    retry / revise on failure → summarise → remember

The orchestrator owns task state and the failure policy; the
:class:`~jarvis.agent.pool.AgentPool` owns concurrency, running every step
whose dependencies are met across a roster of named agents. Neither contains
tool logic, LLM prompts beyond the summary, or I/O of its own.
"""

from jarvis.agent.orchestrator import Orchestrator
from jarvis.agent.pool import AgentPool, AgentState, StepOutcome
from jarvis.agent.roster import AgentProfile, build_roster

__all__ = [
    "AgentPool",
    "AgentProfile",
    "AgentState",
    "Orchestrator",
    "StepOutcome",
    "build_roster",
]
