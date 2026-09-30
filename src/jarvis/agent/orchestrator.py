"""The plan → execute → observe loop.

Error-handling philosophy (mirrors the project rules):

* a failing step is retried up to ``agent.max_step_attempts`` times;
* if retries are exhausted, the Planner is asked to *revise* the remaining
  plan (try an alternative approach), up to ``agent.max_plan_revisions``
  times per task;
* only when no revision is possible does the task fail — and even then it
  fails with a recorded error and a notification, never a crash.

Execution itself is delegated to the :class:`~jarvis.agent.pool.AgentPool`,
which runs every currently-unblocked step at once across several agents.
This module keeps the judgement — retry, revise, give up — because that is
the part that needs the Planner and the task's history. A plan with no
parallel branches puts one step in flight at a time, so the behaviour of a
sequential plan is unchanged.

Every state change is published on the event bus (so the phone and the
dashboard see live progress) and the finished task is persisted to Memory.
"""

from __future__ import annotations

import asyncio
import contextlib

from jarvis.agent.pool import AgentPool, StepOutcome
from jarvis.brain.base import Brain, BrainMessage
from jarvis.config.schema import AgentConfig
from jarvis.core.errors import BrainError, PlanningError
from jarvis.core.events import EventBus, EventType
from jarvis.core.models import (
    Plan,
    PlanStep,
    StepStatus,
    Task,
    TaskStatus,
    ToolResult,
)
from jarvis.logging import get_logger, log_context
from jarvis.memory.store import MemoryStore
from jarvis.planner.planner import Planner
from jarvis.tools.manager import ToolManager

_log = get_logger(__name__)

_SUMMARY_SYSTEM = """\
You are Jarvis, a personal desktop assistant. Summarise the outcome of the
task you just performed for the user in 2-5 sentences: what was done, any
problems hit, and the final state. Be concrete and plain-spoken.
"""


class Orchestrator:
    """Owns tasks end-to-end and coordinates all other modules."""

    def __init__(
        self,
        *,
        brain: Brain,
        planner: Planner,
        tools: ToolManager,
        memory: MemoryStore,
        bus: EventBus,
        config: AgentConfig,
    ) -> None:
        self._brain = brain
        self._planner = planner
        self._tools = tools
        self._memory = memory
        self._bus = bus
        self._config = config
        self._pool = AgentPool(tools=tools, bus=bus, config=config)
        self._tasks: dict[str, Task] = {}
        self._running: dict[str, asyncio.Task[None]] = {}

    @property
    def pool(self) -> AgentPool:
        """The agents doing the work — read by the dashboard."""
        return self._pool

    # -- public API -------------------------------------------------------
    async def submit(self, request: str) -> Task:
        """Accept a request, start working on it in the background."""
        task = Task(request=request)
        self._tasks[task.id] = task
        self._memory.add_message("user", request)
        await self._bus.emit(
            EventType.TASK_CREATED, f"Task received: {request}", task_id=task.id
        )
        runner = asyncio.create_task(self._run(task))
        self._running[task.id] = runner
        runner.add_done_callback(lambda _: self._running.pop(task.id, None))
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task; returns True if there was one to cancel."""
        runner = self._running.get(task_id)
        task = self._tasks.get(task_id)
        if runner is None or task is None or task.is_terminal:
            return False
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        task.touch(TaskStatus.CANCELLED)
        self._memory.save_task(task)
        await self._bus.emit(
            EventType.TASK_CANCELLED, "Task cancelled.", task_id=task.id
        )
        return True

    async def wait(self, task_id: str) -> Task:
        """Block until the task finishes (used by tests and the CLI)."""
        runner = self._running.get(task_id)
        if runner is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await runner
        return self._tasks[task_id]

    # -- the loop -----------------------------------------------------------
    async def _run(self, task: Task) -> None:
        with log_context(task_id=task.id):
            try:
                await self._plan_and_execute(task)
            except asyncio.CancelledError:
                raise
            except PlanningError as exc:
                await self._fail(task, f"Planning failed: {exc}")
            except BrainError as exc:
                await self._fail(task, str(exc))
            except Exception as exc:  # noqa: BLE001 — the loop must survive anything
                _log.exception("unexpected orchestrator error")
                await self._fail(task, f"Unexpected error: {type(exc).__name__}: {exc}")

    async def _plan_and_execute(self, task: Task) -> None:
        task.touch(TaskStatus.PLANNING)
        await self._bus.emit(
            EventType.TASK_PLANNING, "Working out a plan…", task_id=task.id
        )

        context = self._history_context()
        plan, direct_response = await self._planner.plan(task, context)
        task.plan = plan

        # Pure question — answer directly, no execution needed.
        if not plan.steps:
            task.result = direct_response or "There is nothing to do for this request."
            task.touch(TaskStatus.COMPLETED)
            self._finish(task)
            await self._bus.emit(
                EventType.TASK_COMPLETED, task.result, task_id=task.id
            )
            return

        task.touch(TaskStatus.RUNNING)
        width = min(self._pool.width, len(plan.steps))
        await self._bus.emit(
            EventType.TASK_STARTED,
            f"Starting: {plan.goal} ({len(plan.steps)} steps)",
            task_id=task.id,
            steps=len(plan.steps),
            agents=width,
        )

        failure = await self._execute_plan(task, plan)
        if failure is not None:
            await self._fail(task, failure)
            return

        task.result = await self._summarise(task)
        task.touch(TaskStatus.COMPLETED)
        self._finish(task)
        await self._bus.emit(EventType.TASK_COMPLETED, task.result, task_id=task.id)

    async def _execute_plan(self, task: Task, plan: Plan) -> str | None:
        """Run the plan through the pool. Returns an error, or ``None``.

        The handler below is the whole failure policy: retry the step, ask
        the Planner for a different approach, or give up. The pool calls it
        once per finished step and obeys whatever it returns.
        """
        revisions_left = self._config.max_plan_revisions
        failure: str | None = None

        async def handle(step: PlanStep, result: ToolResult) -> StepOutcome:
            nonlocal revisions_left, failure

            if result.ok:
                step.status = StepStatus.COMPLETED
                await self._bus.emit(
                    EventType.TASK_PROGRESS,
                    f"Done: {step.description}",
                    task_id=task.id,
                    step_id=step.id,
                    agent_id=step.agent_id,
                )
                return StepOutcome.CONTINUE

            step.error = result.error
            if step.attempts < self._config.max_step_attempts:
                step.status = StepStatus.PENDING  # retry the same step
                await self._bus.emit(
                    EventType.STEP_RETRYING,
                    f"Retrying: {step.description} ({result.error})",
                    task_id=task.id,
                    step_id=step.id,
                    agent_id=step.agent_id,
                )
                return StepOutcome.RETRY

            step.status = StepStatus.FAILED
            if revisions_left > 0:
                revisions_left -= 1
                # Only work that has not started is up for replacement;
                # steps already running belong to independent branches and
                # are left to finish.
                remaining = [s for s in plan.steps if s.status == StepStatus.PENDING]
                # The finished steps carry the values the replacement needs -
                # the URLs a search returned, the path a file landed at. Left
                # out, the model invents them and fails the same way again.
                completed = [
                    s for s in plan.steps if s.status == StepStatus.COMPLETED
                ]
                replacement = await self._planner.revise(
                    task,
                    step,
                    result.error or "unknown error",
                    remaining,
                    completed,
                )
                if replacement:
                    for pending in remaining:
                        pending.status = StepStatus.SKIPPED
                    plan.steps.extend(replacement)
                    await self._bus.emit(
                        EventType.TASK_PROGRESS,
                        f"Trying another approach ({len(replacement)} new steps).",
                        task_id=task.id,
                        steps=len(replacement),
                    )
                    return StepOutcome.CONTINUE

            failure = (
                f"Step failed after {step.attempts} attempts: "
                f"{step.description} — {result.error}"
            )
            return StepOutcome.ABORT

        async def prepare(step: PlanStep) -> None:
            # Arguments marked "{{from_previous}}" were unknowable when the
            # plan was written; fill them in now that the steps this one
            # depends on have actually produced something.
            step.arguments = await self._planner.resolve_arguments(
                task,
                step,
                [s for s in plan.steps if s.status == StepStatus.COMPLETED],
            )

        await self._pool.execute(task, plan, handle=handle, prepare=prepare)
        return failure

    # -- helpers ------------------------------------------------------------
    def _history_context(self) -> str:
        limit = self._config.history_messages
        if limit <= 0:
            return ""
        messages = self._memory.recent_messages(limit)
        if not messages:
            return ""
        lines = [f"{m.role}: {m.content}" for m in messages]
        return "Recent conversation for context:\n" + "\n".join(lines)

    async def _summarise(self, task: Task) -> str:
        assert task.plan is not None
        outcome_lines = [f"Request: {task.request}", f"Goal: {task.plan.goal}"]
        for step in task.plan.steps:
            state = step.status.value
            detail = ""
            if step.result is not None and step.result.ok:
                detail = f" → {_short(step.result.output)}"
            elif step.error:
                detail = f" → error: {step.error}"
            outcome_lines.append(f"- [{state}] {step.description}{detail}")
        transcript = "\n".join(outcome_lines)

        try:
            response = await self._brain.complete(
                system=_SUMMARY_SYSTEM,
                messages=[BrainMessage(role="user", content=transcript)],
            )
            if response.text.strip():
                return response.text.strip()
        except Exception as exc:  # noqa: BLE001 — summary must never sink a task
            _log.warning("summary generation failed", extra={"error": str(exc)})
        completed = sum(
            1 for s in task.plan.steps if s.status == StepStatus.COMPLETED
        )
        return f"Completed {completed} of {len(task.plan.steps)} steps for: {task.request}"

    async def _fail(self, task: Task, error: str) -> None:
        task.error = error
        task.touch(TaskStatus.FAILED)
        self._finish(task)
        await self._bus.emit(EventType.TASK_FAILED, error, task_id=task.id)

    def _finish(self, task: Task) -> None:
        self._memory.save_task(task)
        if task.result:
            self._memory.add_message("assistant", task.result)


def _short(value: object, limit: int = 200) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
