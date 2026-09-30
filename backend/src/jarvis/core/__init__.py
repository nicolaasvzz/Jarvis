"""Core domain types shared across every Jarvis module.

This package holds the vocabulary the whole system is written in — tasks,
plan steps, tool calls and results, risk levels, and the event bus that
carries lifecycle notifications between modules. Nothing here imports from
the feature modules, so every other package can depend on ``jarvis.core``
without creating cycles.
"""

from jarvis.core.errors import (
    ApprovalDenied,
    ConfirmationRequired,
    JarvisError,
    PlanningError,
    ToolError,
    ToolNotFound,
)
from jarvis.core.events import Event, EventBus, EventType
from jarvis.core.ids import new_id
from jarvis.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    Plan,
    PlanStep,
    RiskLevel,
    StepStatus,
    Task,
    TaskStatus,
    ToolCall,
    ToolResult,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalDenied",
    "ApprovalRequest",
    "ConfirmationRequired",
    "Event",
    "EventBus",
    "EventType",
    "JarvisError",
    "Plan",
    "PlanStep",
    "PlanningError",
    "RiskLevel",
    "StepStatus",
    "Task",
    "TaskStatus",
    "ToolCall",
    "ToolError",
    "ToolNotFound",
    "ToolResult",
    "new_id",
]
