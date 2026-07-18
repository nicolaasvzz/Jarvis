"""Core domain models: tasks, plans, steps, tool calls, approvals.

These are the objects that move between modules. They are plain Pydantic
models — serialisable (for the API and for persistence), validated, and
free of behaviour so any module can hold them without depending on another
module's logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.ids import new_id


def _now() -> datetime:
    return datetime.now(tz=UTC)


class RiskLevel(StrEnum):
    """How dangerous an action is, and therefore how it must be gated.

    ``SAFE`` runs freely; ``CONFIRM`` requires explicit user approval before
    the Tool Manager will execute it.
    """

    SAFE = "safe"
    CONFIRM = "confirm"


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


_TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


class ToolCall(BaseModel):
    """A request to run one tool with a set of arguments."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The outcome of executing a :class:`ToolCall`."""

    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime = Field(default_factory=_now)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @classmethod
    def success(cls, tool: str, output: Any, started_at: datetime) -> ToolResult:
        return cls(tool=tool, ok=True, output=output, started_at=started_at)

    @classmethod
    def failure(cls, tool: str, error: str, started_at: datetime) -> ToolResult:
        return cls(tool=tool, ok=False, error=error, started_at=started_at)


class PlanStep(BaseModel):
    """A single unit of work inside a :class:`Plan`."""

    id: str = Field(default_factory=lambda: new_id("step"))
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.SAFE
    status: StepStatus = StepStatus.PENDING
    result: ToolResult | None = None
    error: str | None = None
    attempts: int = 0


class Plan(BaseModel):
    """An ordered list of steps produced for a task before execution begins."""

    id: str = Field(default_factory=lambda: new_id("plan"))
    task_id: str
    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)

    def next_pending(self) -> PlanStep | None:
        """Return the first step still waiting to run, or ``None`` if done."""
        return next(
            (s for s in self.steps if s.status == StepStatus.PENDING),
            None,
        )


class Task(BaseModel):
    """A user request and everything Jarvis tracks while working on it."""

    id: str = Field(default_factory=lambda: new_id("task"))
    request: str
    status: TaskStatus = TaskStatus.PENDING
    plan: Plan | None = None
    result: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def touch(self, status: TaskStatus | None = None) -> None:
        """Update ``updated_at`` and, optionally, the status in one call."""
        if status is not None:
            self.status = status
        self.updated_at = _now()

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_TASK_STATUSES


class ApprovalDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ApprovalRequest(BaseModel):
    """A pending request for the user to approve a dangerous action."""

    id: str = Field(default_factory=lambda: new_id("appr"))
    task_id: str
    step_id: str | None = None
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str
    created_at: datetime = Field(default_factory=_now)
    decision: ApprovalDecision | None = None
    decided_at: datetime | None = None

    @property
    def is_pending(self) -> bool:
        return self.decision is None

    def resolve(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.decided_at = _now()
