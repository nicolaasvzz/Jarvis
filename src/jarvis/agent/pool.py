"""A pool of agents that work through one plan together.

The plan is a dependency graph, not a queue: every step names the steps it
waits for. That turns "what runs next" into "what is unblocked right now",
and anything unblocked at the same moment can run at the same time. The pool
keeps up to ``pool_size`` steps in flight, handing each to a free agent.

Concurrency lives here and policy lives in the Orchestrator. The pool never
decides whether a failure should be retried, revised around, or fatal — it
runs a step, reports the result to a handler, and does what the handler
says. That split keeps this file free of planning concerns and keeps the
Orchestrator free of task-juggling.

A plan whose steps are fully chained (the Planner's default) puts exactly one
step in flight at a time, so sequential plans behave precisely as they did
before the pool existed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from jarvis.agent.roster import AgentProfile, build_roster
from jarvis.config.schema import AgentConfig
from jarvis.core.events import EventBus, EventType
from jarvis.core.models import Plan, PlanStep, StepStatus, Task, ToolResult
from jarvis.core.redaction import redact_arguments
from jarvis.logging import get_logger
from jarvis.tools.manager import ToolManager

_log = get_logger(__name__)


class StepOutcome(StrEnum):
    """What the pool should do after a handler has judged a step."""

    #: The step is settled. Carry on with the rest of the plan.
    CONTINUE = "continue"
    #: The handler reset the step to pending; run it again.
    RETRY = "retry"
    #: Stop the whole plan and cancel anything still running.
    ABORT = "abort"


#: Called once per finished step. Receives the step (with ``result`` already
#: attached) and decides what happens next. It owns the step's final status.
StepHandler = Callable[[PlanStep, ToolResult], Awaitable[StepOutcome]]


@dataclass
class AgentState:
    """What one agent is doing right now, for anyone watching."""

    profile: AgentProfile
    status: str = "idle"  # idle | working
    task_id: str | None = None
    step_id: str | None = None
    tool: str | None = None
    description: str | None = None
    arguments: dict[str, object] = field(default_factory=dict)
    since: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def as_dict(self) -> dict[str, object]:
        return {
            **self.profile.as_dict(),
            "status": self.status,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "tool": self.tool,
            "description": self.description,
            "arguments": self.arguments,
            "since": self.since.isoformat(),
        }


class AgentPool:
    """Runs the unblocked steps of a plan across several agents at once."""

    def __init__(
        self,
        *,
        tools: ToolManager,
        bus: EventBus,
        config: AgentConfig,
    ) -> None:
        self._tools = tools
        self._bus = bus
        self._config = config
        self._states: dict[str, AgentState] = {
            profile.id: AgentState(profile=profile)
            for profile in build_roster(config.pool_size)
        }

    # -- observation ------------------------------------------------------
    @property
    def width(self) -> int:
        """How many steps may be in flight at once."""
        return len(self._states) if self._config.parallel else 1

    def snapshot(self) -> list[dict[str, object]]:
        """Every agent and what it is doing, newest state included."""
        return [state.as_dict() for state in self._states.values()]

    # -- execution --------------------------------------------------------
    async def execute(self, task: Task, plan: Plan, *, handle: StepHandler) -> None:
        """Run ``plan`` to a standstill, reporting each step to ``handle``.

        Returns when nothing is left that can run — either the plan is done,
        or the remaining steps are blocked behind failures, or a handler
        aborted. Never raises for a failing step; only cancellation
        propagates.
        """
        in_flight: dict[asyncio.Task[ToolResult], tuple[PlanStep, AgentProfile]] = {}
        try:
            while True:
                self._skip_blocked(plan)
                await self._launch(task, plan, in_flight)
                if not in_flight:
                    return  # nothing running, nothing runnable
                done, _ = await asyncio.wait(
                    in_flight.keys(), return_when=asyncio.FIRST_COMPLETED
                )
                for finished in done:
                    step, profile = in_flight.pop(finished)
                    await self._release(profile)
                    result = finished.result()
                    step.result = result
                    if await handle(step, result) is StepOutcome.ABORT:
                        return
        finally:
            await self._cancel_all(in_flight)
            self._retire_unstarted(plan)

    async def _launch(
        self,
        task: Task,
        plan: Plan,
        in_flight: dict[asyncio.Task[ToolResult], tuple[PlanStep, AgentProfile]],
    ) -> None:
        """Start as many ready steps as there are free agents and slots."""
        running_ids = {step.id for step, _ in in_flight.values()}
        for step in plan.ready_steps():
            if len(in_flight) >= self.width:
                return
            if step.id in running_ids:
                continue
            profile = self._claim()
            if profile is None:
                return
            step.status = StepStatus.RUNNING
            step.attempts += 1
            step.agent_id = profile.id
            await self._occupy(profile, task, step)
            runner = asyncio.create_task(
                self._tools.execute(
                    step.tool or "",
                    step.arguments,
                    task_id=task.id,
                    step_id=step.id,
                    agent_id=profile.id,
                ),
                name=f"{profile.id}:{step.id}",
            )
            in_flight[runner] = (step, profile)
            running_ids.add(step.id)

    @staticmethod
    def _skip_blocked(plan: Plan) -> None:
        """Retire steps whose dependencies failed, so the plan can settle."""
        for step in plan.blocked_steps():
            step.status = StepStatus.SKIPPED
            step.error = step.error or "Skipped: a step it depended on did not finish."

    @staticmethod
    def _retire_unstarted(plan: Plan) -> None:
        """Mark anything still waiting once the plan has stopped.

        An aborted plan leaves steps sitting at ``PENDING`` that will now
        never run. Left alone they read as "still to do" in the summary and
        on the dashboard, which is the opposite of the truth.
        """
        for step in plan.steps:
            if step.status is StepStatus.PENDING:
                step.status = StepStatus.SKIPPED
                step.error = step.error or "Skipped: the task stopped before this ran."

    @staticmethod
    async def _cancel_all(
        in_flight: dict[asyncio.Task[ToolResult], tuple[PlanStep, AgentProfile]],
    ) -> None:
        """Stop anything still running, and wait for it to actually stop."""
        if not in_flight:
            return
        for runner in in_flight:
            runner.cancel()
        await asyncio.gather(*in_flight, return_exceptions=True)
        for step, _ in in_flight.values():
            if step.status is StepStatus.RUNNING:
                step.status = StepStatus.SKIPPED
        in_flight.clear()

    # -- agent bookkeeping ------------------------------------------------
    def _claim(self) -> AgentProfile | None:
        """Take the first idle agent, or ``None`` when all are busy."""
        for state in self._states.values():
            if state.status == "idle":
                return state.profile
        return None

    async def _occupy(self, profile: AgentProfile, task: Task, step: PlanStep) -> None:
        state = self._states[profile.id]
        state.status = "working"
        state.task_id = task.id
        state.step_id = step.id
        state.tool = step.tool
        state.description = step.description
        state.arguments = dict(redact_arguments(step.arguments))
        state.since = datetime.now(tz=UTC)
        _log.info(
            "agent assigned",
            extra={"agent": profile.id, "tool": step.tool, "task_id": task.id},
        )
        await self._bus.emit(
            EventType.AGENT_ASSIGNED,
            f"{profile.name} → {step.description}",
            task_id=task.id,
            step_id=step.id,
            agent=state.as_dict(),
        )

    async def _release(self, profile: AgentProfile) -> None:
        state = self._states[profile.id]
        task_id = state.task_id
        state.status = "idle"
        state.tool = None
        state.step_id = None
        state.description = None
        state.arguments = {}
        state.task_id = None
        state.since = datetime.now(tz=UTC)
        await self._bus.emit(
            EventType.AGENT_IDLE,
            f"{profile.name} is free.",
            task_id=task_id,
            agent=state.as_dict(),
        )
