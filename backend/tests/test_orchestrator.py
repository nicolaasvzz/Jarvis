"""End-to-end tests of the agent loop with a scripted brain and real tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jarvis.agent import Orchestrator
from jarvis.brain.base import BrainMessage, BrainResponse
from jarvis.config.schema import AgentConfig, SecurityConfig
from jarvis.core.errors import BrainError, ToolError
from jarvis.core.events import EventBus, EventType
from jarvis.core.models import ApprovalDecision, StepStatus, TaskStatus
from jarvis.files import FileManager, build_file_tools
from jarvis.memory import MemoryStore
from jarvis.planner import Planner
from jarvis.security import PermissionPolicy
from jarvis.tools import ToolManager, ToolRegistry, tool
from tests.helpers import ScriptedBrain


class _ExplodingBrain:
    """A Brain whose first call raises a BrainError, as AnthropicBrain does
    on an API failure (bad key, no credit, rate limit, ...)."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def complete(
        self,
        *,
        system: str,
        messages: list[BrainMessage],
        tools: list[dict[str, object]] | None = None,
        json_mode: bool = False,
    ) -> BrainResponse:
        raise BrainError(self._message)


def _system(
    tmp_path: Path,
    brain: ScriptedBrain,
    *,
    require_confirmation: list[str] | None = None,
    extra_tools: list[object] | None = None,
) -> tuple[Orchestrator, PermissionPolicy, EventBus, FileManager]:
    fm = FileManager(tmp_path / "sandbox")
    registry = ToolRegistry()
    registry.register_all(build_file_tools(fm))
    for extra in extra_tools or []:
        registry.register(extra)  # type: ignore[arg-type]
    policy = PermissionPolicy(
        SecurityConfig(require_confirmation=require_confirmation or [])
    )
    bus = EventBus()
    tools = ToolManager(registry, policy, bus)
    planner = Planner(brain, registry, policy)
    memory = MemoryStore(tmp_path / "memory.db")
    orchestrator = Orchestrator(
        brain=brain,
        planner=planner,
        tools=tools,
        memory=memory,
        bus=bus,
        config=AgentConfig(max_step_attempts=2, max_plan_revisions=1),
    )
    return orchestrator, policy, bus, fm


def _plan(steps: list[dict[str, object]], goal: str = "goal") -> str:
    return json.dumps({"goal": goal, "steps": steps})


async def test_happy_path_executes_all_steps_and_summarises(tmp_path: Path) -> None:
    brain = ScriptedBrain(
        [
            _plan(
                [
                    {
                        "description": "Create the notes folder",
                        "tool": "make_directory",
                        "arguments": {"path": "notes"},
                    },
                    {
                        "description": "Write the note",
                        "tool": "write_file",
                        "arguments": {"path": "notes/todo.txt", "content": "buy milk"},
                    },
                ],
                goal="Save a note",
            ),
            "I created notes/todo.txt with your reminder.",  # summary
        ]
    )
    orchestrator, _, bus, fm = _system(tmp_path, brain)
    events: list[str] = []

    async def record(event: object) -> None:
        events.append(event.type.value)  # type: ignore[attr-defined]

    bus.subscribe(record)

    task = await orchestrator.submit("save a note to buy milk")
    task = await orchestrator.wait(task.id)

    assert task.status is TaskStatus.COMPLETED
    assert task.result == "I created notes/todo.txt with your reminder."
    assert fm.read_text("notes/todo.txt") == "buy milk"
    assert all(s.status is StepStatus.COMPLETED for s in task.plan.steps)
    assert EventType.TASK_COMPLETED.value in events


async def test_pure_question_answers_without_executing(tmp_path: Path) -> None:
    brain = ScriptedBrain(
        [json.dumps({"goal": "answer", "response": "You asked me that yesterday.", "steps": []})]
    )
    orchestrator, _, _, _ = _system(tmp_path, brain)
    task = await orchestrator.submit("did I ask this before?")
    task = await orchestrator.wait(task.id)
    assert task.status is TaskStatus.COMPLETED
    assert task.result == "You asked me that yesterday."


async def test_step_failure_retries_then_revises_then_succeeds(tmp_path: Path) -> None:
    attempts = {"n": 0}

    @tool()
    def flaky() -> str:
        """A tool that always fails."""
        attempts["n"] += 1
        raise RuntimeError("always broken")

    brain = ScriptedBrain(
        [
            _plan([{"description": "Try flaky", "tool": "flaky", "arguments": {}}]),
            # Revision: replace with a working step.
            json.dumps(
                {
                    "steps": [
                        {
                            "description": "Write a recovery file",
                            "tool": "write_file",
                            "arguments": {"path": "ok.txt", "content": "recovered"},
                        }
                    ]
                }
            ),
            "Recovered by writing ok.txt.",  # summary
        ]
    )
    orchestrator, _, _, fm = _system(tmp_path, brain, extra_tools=[flaky])
    task = await orchestrator.submit("do the flaky thing")
    task = await orchestrator.wait(task.id)

    assert attempts["n"] == 2  # retried once before revising
    assert task.status is TaskStatus.COMPLETED
    assert fm.read_text("ok.txt") == "recovered"


async def test_unrecoverable_failure_marks_task_failed(tmp_path: Path) -> None:
    @tool()
    def flaky() -> str:
        """A tool that always fails."""
        raise RuntimeError("always broken")

    brain = ScriptedBrain(
        [
            _plan([{"description": "Try flaky", "tool": "flaky", "arguments": {}}]),
            json.dumps({"steps": []}),  # revision has no ideas
        ]
    )
    orchestrator, _, _, _ = _system(tmp_path, brain, extra_tools=[flaky])
    task = await orchestrator.submit("do the flaky thing")
    task = await orchestrator.wait(task.id)
    assert task.status is TaskStatus.FAILED
    assert "always broken" in (task.error or "")


async def test_dangerous_step_waits_for_approval(tmp_path: Path) -> None:
    brain = ScriptedBrain(
        [
            _plan(
                [
                    {
                        "description": "Write victim",
                        "tool": "write_file",
                        "arguments": {"path": "victim.txt", "content": "x"},
                    },
                    {
                        "description": "Delete victim",
                        "tool": "delete_path",
                        "arguments": {"path": "victim.txt"},
                    },
                ]
            ),
            "Deleted victim.txt after your approval.",
        ]
    )
    orchestrator, policy, _, fm = _system(
        tmp_path, brain, require_confirmation=["delete_files"]
    )
    task = await orchestrator.submit("clean up victim.txt")

    # Wait for the approval request to surface, then approve it.
    for _ in range(200):
        await asyncio.sleep(0.01)
        if policy.pending():
            break
    assert task.status is not TaskStatus.COMPLETED
    policy.resolve(policy.pending()[0].id, ApprovalDecision.ALLOW)

    task = await orchestrator.wait(task.id)
    assert task.status is TaskStatus.COMPLETED
    with pytest.raises(ToolError):
        fm.read_text("victim.txt")  # actually deleted


async def test_cancel_stops_a_running_task(tmp_path: Path) -> None:
    started = asyncio.Event()

    @tool()
    async def slow() -> str:
        """A tool that hangs until cancelled."""
        started.set()
        await asyncio.sleep(60)
        return "never"

    brain = ScriptedBrain(
        [_plan([{"description": "Run slow", "tool": "slow", "arguments": {}}])]
    )
    orchestrator, _, _, _ = _system(tmp_path, brain, extra_tools=[slow])
    task = await orchestrator.submit("run the slow thing")
    await asyncio.wait_for(started.wait(), timeout=5)
    assert await orchestrator.cancel(task.id) is True
    assert orchestrator.get(task.id).status is TaskStatus.CANCELLED


async def test_finished_tasks_are_persisted_to_memory(tmp_path: Path) -> None:
    brain = ScriptedBrain(
        [json.dumps({"goal": "answer", "response": "Hi!", "steps": []})]
    )
    orchestrator, _, _, _ = _system(tmp_path, brain)
    task = await orchestrator.submit("say hi")
    await orchestrator.wait(task.id)

    store = MemoryStore(tmp_path / "memory.db")
    history = store.task_history()
    assert history and history[0]["id"] == task.id
    roles = [m.role for m in store.recent_messages()]
    assert roles == ["user", "assistant"]


async def test_brain_error_fails_the_task_with_its_own_message(tmp_path: Path) -> None:
    """A BrainError (e.g. Anthropic billing/auth/rate-limit failure) should
    fail the task with its own friendly message, not a generic dump."""
    brain = _ExplodingBrain(
        "Your Anthropic account has insufficient credit. Add credits at "
        "https://console.anthropic.com/settings/billing."
    )
    orchestrator, _, _, _ = _system(tmp_path, brain)  # type: ignore[arg-type]
    task = await orchestrator.submit("do anything")
    task = await orchestrator.wait(task.id)
    assert task.status is TaskStatus.FAILED
    assert task.error == (
        "Your Anthropic account has insufficient credit. Add credits at "
        "https://console.anthropic.com/settings/billing."
    )
    assert "Unexpected error" not in (task.error or "")
