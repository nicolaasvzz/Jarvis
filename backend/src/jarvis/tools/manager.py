"""The Tool Manager: validate, gate, log, execute, report.

Every action in Jarvis flows through :meth:`ToolManager.execute`. The
sequence for each call is deliberate and always the same:

1. Look the tool up in the registry (unknown tool → structured failure).
2. Ask the permission policy whether the tool's risk category needs
   confirmation. If so, raise an approval request and *wait* for the user's
   decision; a denial becomes a structured failure, not a crash.
3. Log the invocation and publish a ``STEP_STARTED`` event.
4. Run the tool, timing it, converting any exception into a
   :class:`ToolResult` failure so one bad tool never takes down the loop.
5. Log the outcome and publish ``STEP_COMPLETED`` / ``STEP_FAILED``.

Because this is the only path to execution, it is also the only place
permission checks and audit logging need to live.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jarvis.core.errors import ApprovalDenied, ToolError, ToolNotFound
from jarvis.core.events import EventBus, EventType
from jarvis.core.models import ApprovalDecision, ToolResult
from jarvis.core.redaction import redact_arguments
from jarvis.logging import get_logger, log_context
from jarvis.security.permissions import PermissionPolicy
from jarvis.tools.base import ToolContext
from jarvis.tools.registry import ToolRegistry

_log = get_logger(__name__)


class ToolManager:
    """Runs tools through the security policy with full logging."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        bus: EventBus,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._bus = bus

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        step_id: str | None = None,
        agent_id: str | None = None,
        require_approval: bool = True,
    ) -> ToolResult:
        """Execute one tool and always return a :class:`ToolResult`.

        Errors are captured in the result rather than raised, so the caller
        (the executor) can decide whether to retry, route around, or report.

        ``agent_id`` identifies which agent in the pool is running this call.
        It is carried on every event purely so observers — the dashboard,
        the logs — can attribute the work; execution does not depend on it.
        """
        arguments = dict(arguments or {})
        started = datetime.now(tz=UTC)
        # Summarised once and reused: the raw arguments go to the tool, but
        # only this safe version is ever published or logged.
        shown = redact_arguments(arguments)

        with log_context(tool=tool_name, task_id=task_id):
            try:
                tool = self._registry.get(tool_name)
            except ToolNotFound as exc:
                return self._fail(tool_name, str(exc), started)

            if require_approval and self._policy.requires_confirmation(
                tool.risk_category
            ):
                try:
                    await self._await_approval(
                        tool_name,
                        arguments,
                        task_id,
                        step_id,
                        tool.risk_category,
                        agent_id,
                    )
                except ApprovalDenied as exc:
                    return self._fail(tool_name, str(exc), started)

            await self._bus.emit(
                EventType.STEP_STARTED,
                f"Running {tool_name}",
                task_id=task_id,
                tool=tool_name,
                step_id=step_id,
                agent_id=agent_id,
                arguments=shown,
            )
            _log.info("executing tool", extra={"arguments": shown})

            try:
                output = await tool(arguments, ToolContext(task_id=task_id))
            except ToolError as exc:
                return await self._report_failure(
                    tool_name, str(exc), started, task_id, step_id, agent_id
                )
            except Exception as exc:  # noqa: BLE001 — one tool must not crash Jarvis
                _log.exception("tool raised an unexpected error")
                return await self._report_failure(
                    tool_name,
                    f"{type(exc).__name__}: {exc}",
                    started,
                    task_id,
                    step_id,
                    agent_id,
                )

            result = ToolResult.success(tool_name, output, started)
            _log.info(
                "tool succeeded",
                extra={"duration_s": round(result.duration_seconds, 3)},
            )
            await self._bus.emit(
                EventType.STEP_COMPLETED,
                f"Finished {tool_name}",
                task_id=task_id,
                tool=tool_name,
                step_id=step_id,
                agent_id=agent_id,
                arguments=shown,
                duration_s=round(result.duration_seconds, 3),
            )
            return result

    async def _await_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        task_id: str | None,
        step_id: str | None,
        category: str | None,
        agent_id: str | None = None,
    ) -> None:
        request = self._policy.create_request(
            task_id=task_id or "",
            step_id=step_id,
            tool=tool_name,
            arguments=arguments,
            reason=f"{tool_name} is a {category!r} action and needs your approval.",
        )
        await self._bus.emit(
            EventType.APPROVAL_REQUIRED,
            f"Approval needed to run {tool_name}.",
            task_id=task_id,
            approval_id=request.id,
            tool=tool_name,
            step_id=step_id,
            agent_id=agent_id,
            arguments=redact_arguments(arguments),
        )
        decision = await self._policy.wait_for(request.id)
        await self._bus.emit(
            EventType.APPROVAL_RESOLVED,
            f"Approval {decision.value} for {tool_name}.",
            task_id=task_id,
            approval_id=request.id,
            tool=tool_name,
            step_id=step_id,
            agent_id=agent_id,
            decision=decision.value,
        )
        if decision is ApprovalDecision.DENY:
            raise ApprovalDenied(f"User denied running {tool_name}.")

    async def _report_failure(
        self,
        tool_name: str,
        error: str,
        started: datetime,
        task_id: str | None,
        step_id: str | None = None,
        agent_id: str | None = None,
    ) -> ToolResult:
        _log.warning("tool failed", extra={"error": error})
        await self._bus.emit(
            EventType.STEP_FAILED,
            f"{tool_name} failed: {error}",
            task_id=task_id,
            tool=tool_name,
            step_id=step_id,
            agent_id=agent_id,
            error=error,
        )
        return ToolResult.failure(tool_name, error, started)

    @staticmethod
    def _fail(tool_name: str, error: str, started: datetime) -> ToolResult:
        _log.warning("tool call rejected", extra={"error": error})
        return ToolResult.failure(tool_name, error, started)
