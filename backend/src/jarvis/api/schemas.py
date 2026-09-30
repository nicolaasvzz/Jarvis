"""Request/response bodies for the API server.

Kept separate from the core models so the wire format can evolve without
touching domain types. Responses expose exactly what the phone needs and
nothing internal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.models import (
    ApprovalRequest,
    PlanStep,
    Task,
)


class SubmitTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=10_000)


class StepOut(BaseModel):
    id: str
    description: str
    tool: str | None
    status: str
    risk: str
    error: str | None

    @classmethod
    def from_step(cls, step: PlanStep) -> StepOut:
        return cls(
            id=step.id,
            description=step.description,
            tool=step.tool,
            status=step.status.value,
            risk=step.risk.value,
            error=step.error,
        )


class TaskOut(BaseModel):
    id: str
    request: str
    status: str
    goal: str | None
    steps: list[StepOut]
    result: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_task(cls, task: Task) -> TaskOut:
        return cls(
            id=task.id,
            request=task.request,
            status=task.status.value,
            goal=task.plan.goal if task.plan else None,
            steps=[StepOut.from_step(s) for s in task.plan.steps] if task.plan else [],
            result=task.result,
            error=task.error,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class ApprovalOut(BaseModel):
    id: str
    task_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    created_at: datetime

    @classmethod
    def from_request(cls, request: ApprovalRequest) -> ApprovalOut:
        return cls(
            id=request.id,
            task_id=request.task_id,
            tool=request.tool,
            arguments=request.arguments,
            reason=request.reason,
            created_at=request.created_at,
        )


class DecideApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(allow|deny)$")


class NotificationOut(BaseModel):
    type: str
    message: str
    task_id: str | None
    created_at: datetime


class UploadOut(BaseModel):
    path: str
    size: int


class MessageOut(BaseModel):
    detail: str
