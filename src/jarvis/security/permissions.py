"""The permission policy: safe vs. confirm, and the approval lifecycle.

A tool declares a *risk category* string (e.g. ``"delete_files"``). The
policy checks that category against the configured
``security.require_confirmation`` list. If it is listed, the action is
:class:`~jarvis.core.models.RiskLevel.CONFIRM` and must be approved by the
user before it runs; otherwise it is ``SAFE``.

The policy also owns the store of pending :class:`ApprovalRequest` objects
so the API server, the executor, and the notifier all talk to one place
when an approval is raised, waited on, and resolved.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.config.schema import SecurityConfig
from jarvis.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    RiskLevel,
)
from jarvis.logging import get_logger

_log = get_logger(__name__)


class PermissionPolicy:
    """Classifies actions by risk and tracks pending approvals."""

    def __init__(self, config: SecurityConfig) -> None:
        self._require_confirmation = set(config.require_confirmation)
        self._pending: dict[str, ApprovalRequest] = {}
        self._events: dict[str, asyncio.Event] = {}

    def risk_for(self, category: str | None) -> RiskLevel:
        """Return the risk level for a tool's declared category."""
        if category and category in self._require_confirmation:
            return RiskLevel.CONFIRM
        return RiskLevel.SAFE

    def requires_confirmation(self, category: str | None) -> bool:
        return self.risk_for(category) is RiskLevel.CONFIRM

    def create_request(
        self,
        *,
        task_id: str,
        tool: str,
        arguments: dict[str, Any],
        reason: str,
        step_id: str | None = None,
    ) -> ApprovalRequest:
        """Register a pending approval and return it."""
        request = ApprovalRequest(
            task_id=task_id,
            step_id=step_id,
            tool=tool,
            arguments=arguments,
            reason=reason,
        )
        self._pending[request.id] = request
        self._events[request.id] = asyncio.Event()
        _log.info(
            "approval required",
            extra={"approval_id": request.id, "tool": tool, "task_id": task_id},
        )
        return request

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRequest:
        """Record the user's decision and wake anyone awaiting it."""
        request = self._pending.get(approval_id)
        if request is None:
            raise KeyError(f"Unknown approval request: {approval_id}")
        if not request.is_pending:
            raise ValueError(f"Approval {approval_id} already decided.")
        request.resolve(decision)
        self._events[approval_id].set()
        _log.info(
            "approval resolved",
            extra={"approval_id": approval_id, "decision": decision.value},
        )
        return request

    async def wait_for(self, approval_id: str) -> ApprovalDecision:
        """Block until the given approval request is decided."""
        event = self._events.get(approval_id)
        if event is None:
            raise KeyError(f"Unknown approval request: {approval_id}")
        await event.wait()
        decision = self._pending[approval_id].decision
        assert decision is not None  # set() is only called after resolve()
        return decision

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._pending.get(approval_id)

    def pending(self) -> list[ApprovalRequest]:
        """All approval requests still awaiting a decision, oldest first."""
        return [r for r in self._pending.values() if r.is_pending]
