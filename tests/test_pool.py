"""Tests for the agent pool and the plan dependency graph.

The behaviour that matters here is not "does it go faster" but "does it stay
correct": work the planner marked dependent must never overlap, and work it
marked independent should overlap. Both are asserted by watching how many
steps are actually in flight at once.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jarvis.agent import Orchestrator
from jarvis.agent.roster import MAX_AGENTS, build_roster
from jarvis.config.schema import AgentConfig, SecurityConfig
from jarvis.core.events import EventBus, EventType
from jarvis.core.models import Plan, PlanStep, StepStatus, TaskStatus
from jarvis.files import FileManager, build_file_tools
from jarvis.memory import MemoryStore
from jarvis.planner import Planner
from jarvis.security import PermissionPolicy
from jarvis.tools import ToolManager, ToolRegistry, tool
from tests.helpers import ScriptedBrain


class ConcurrencyProbe:
    """A tool that records the highest number of simultaneous calls."""

    def __init__(self, delay: float = 0.04) -> None:
        self.delay = delay
        self.live = 0
        self.peak = 0
        self.calls = 0

    def build(self):
        probe = self

        @tool(name="probe", description="Records how many calls overlap.")
        async def probe_tool(label: str = "") -> str:
            probe.calls += 1
            probe.live += 1
            probe.peak = max(probe.peak, probe.live)
            try:
                await asyncio.sleep(probe.delay)
                return f"done {label}"
            finally:
                probe.live -= 1

        return probe_tool


def system(tmp_path: Path, brain, *, pool_size: int = 4, parallel: bool = True):
    fm = FileManager(tmp_path / "sandbox")
    registry = ToolRegistry()
    registry.register_all(build_file_tools(fm))
    probe = ConcurrencyProbe()
    registry.register(probe.build())
    policy = PermissionPolicy(SecurityConfig(require_confirmation=[]))
    bus = EventBus()
    orchestrator = Orchestrator(
        brain=brain,
        planner=Planner(brain, registry, policy),
        tools=ToolManager(registry, policy, bus),
        memory=MemoryStore(tmp_path / "memory.db"),
        bus=bus,
        config=AgentConfig(pool_size=pool_size, parallel=parallel),
    )
    return orchestrator, probe, bus


def plan_json(steps: list[dict[str, object]]) -> str:
    return json.dumps({"goal": "test", "steps": steps})


def probe_step(label: str, **extra: object) -> dict[str, object]:
    return {
        "description": f"probe {label}",
        "tool": "probe",
        "arguments": {"label": label},
        **extra,
    }


class TestRoster:
    def test_identities_are_stable_and_distinct(self) -> None:
        roster = build_roster(6)
        assert [a.id for a in roster] == [f"agent-0{i}" for i in range(1, 7)]
        assert len({a.name for a in roster}) == 6
        assert len({a.hue for a in roster}) == 6

    def test_pool_size_is_capped_at_the_roster(self) -> None:
        assert len(build_roster(999)) == MAX_AGENTS
        assert len(build_roster(0)) == 1


class TestPlanGraph:
    def test_ready_steps_respect_dependencies(self) -> None:
        first = PlanStep(description="a")
        second = PlanStep(description="b", depends_on=[first.id])
        plan = Plan(task_id="t", goal="g", steps=[first, second])

        assert [s.id for s in plan.ready_steps()] == [first.id]
        first.status = StepStatus.COMPLETED
        assert [s.id for s in plan.ready_steps()] == [second.id]

    def test_independent_steps_are_all_ready(self) -> None:
        steps = [PlanStep(description=str(i)) for i in range(3)]
        plan = Plan(task_id="t", goal="g", steps=steps)
        assert len(plan.ready_steps()) == 3

    def test_dependents_of_a_failure_are_blocked(self) -> None:
        first = PlanStep(description="a", status=StepStatus.FAILED)
        second = PlanStep(description="b", depends_on=[first.id])
        plan = Plan(task_id="t", goal="g", steps=[first, second])
        assert plan.ready_steps() == []
        assert [s.id for s in plan.blocked_steps()] == [second.id]

    def test_unknown_dependencies_do_not_deadlock(self) -> None:
        """A hallucinated step id must not freeze the plan."""
        step = PlanStep(description="a", depends_on=["step_does_not_exist"])
        plan = Plan(task_id="t", goal="g", steps=[step])
        assert [s.id for s in plan.ready_steps()] == [step.id]


class TestPlannerDependencies:
    async def test_steps_are_chained_when_ordering_is_unstated(
        self, tmp_path: Path
    ) -> None:
        """Sequencing is the safe default: silence must not mean parallel."""
        brain = ScriptedBrain(
            [plan_json([probe_step("a"), probe_step("b"), probe_step("c")]), "done"]
        )
        orchestrator, probe, _ = system(tmp_path, brain)
        task = await orchestrator.wait((await orchestrator.submit("go")).id)

        assert task.status is TaskStatus.COMPLETED
        assert probe.calls == 3
        assert probe.peak == 1  # never overlapped

    async def test_independent_steps_run_together(self, tmp_path: Path) -> None:
        brain = ScriptedBrain(
            [
                plan_json(
                    [
                        probe_step("a", depends_on=[]),
                        probe_step("b", depends_on=[]),
                        probe_step("c", depends_on=[]),
                    ]
                ),
                "done",
            ]
        )
        orchestrator, probe, _ = system(tmp_path, brain)
        task = await orchestrator.wait((await orchestrator.submit("go")).id)

        assert task.status is TaskStatus.COMPLETED
        assert probe.peak == 3

    async def test_a_fan_out_still_waits_for_its_join(self, tmp_path: Path) -> None:
        """Steps 2 and 3 overlap; step 4 must not start until both finish."""
        brain = ScriptedBrain(
            [
                plan_json(
                    [
                        probe_step("root", depends_on=[]),
                        probe_step("left", depends_on=[1]),
                        probe_step("right", depends_on=[1]),
                        probe_step("join", depends_on=[2, 3]),
                    ]
                ),
                "done",
            ]
        )
        orchestrator, probe, _ = system(tmp_path, brain)
        task = await orchestrator.wait((await orchestrator.submit("go")).id)

        assert task.status is TaskStatus.COMPLETED
        assert probe.peak == 2  # only the two middle steps ever overlapped
        steps = task.plan.steps
        assert steps[3].depends_on == [steps[1].id, steps[2].id]

    async def test_forward_references_are_dropped(self, tmp_path: Path) -> None:
        """Only earlier steps may be depended on, so cycles cannot exist."""
        brain = ScriptedBrain(
            [
                plan_json(
                    [
                        probe_step("a", depends_on=[2]),  # forward: dropped
                        probe_step("b", depends_on=[1]),
                    ]
                ),
                "done",
            ]
        )
        orchestrator, probe, _ = system(tmp_path, brain)
        task = await orchestrator.wait((await orchestrator.submit("go")).id)

        assert task.status is TaskStatus.COMPLETED
        assert task.plan.steps[0].depends_on == []
        assert probe.calls == 2

    async def test_parallel_can_be_switched_off(self, tmp_path: Path) -> None:
        brain = ScriptedBrain(
            [
                plan_json(
                    [probe_step("a", depends_on=[]), probe_step("b", depends_on=[])]
                ),
                "done",
            ]
        )
        orchestrator, probe, _ = system(tmp_path, brain, parallel=False)
        await orchestrator.wait((await orchestrator.submit("go")).id)
        assert probe.peak == 1

    async def test_pool_size_caps_concurrency(self, tmp_path: Path) -> None:
        brain = ScriptedBrain(
            [plan_json([probe_step(str(i), depends_on=[]) for i in range(6)]), "done"]
        )
        orchestrator, probe, _ = system(tmp_path, brain, pool_size=2)
        await orchestrator.wait((await orchestrator.submit("go")).id)
        assert probe.calls == 6
        assert probe.peak == 2


class TestPoolFailures:
    async def test_a_failed_branch_skips_only_its_dependents(
        self, tmp_path: Path
    ) -> None:
        @tool()
        def boom() -> str:
            """Always fails."""
            raise RuntimeError("nope")

        brain = ScriptedBrain(
            [
                plan_json(
                    [
                        {"description": "fail", "tool": "boom", "arguments": {},
                         "depends_on": []},
                        {"description": "after", "tool": "probe",
                         "arguments": {"label": "after"}, "depends_on": [1]},
                        probe_step("independent", depends_on=[]),
                    ]
                ),
                json.dumps({"steps": []}),  # revision has no ideas
            ]
        )
        fm = FileManager(tmp_path / "sandbox")
        registry = ToolRegistry()
        registry.register_all(build_file_tools(fm))
        probe = ConcurrencyProbe()
        registry.register(probe.build())
        registry.register(boom)
        policy = PermissionPolicy(SecurityConfig(require_confirmation=[]))
        bus = EventBus()
        orchestrator = Orchestrator(
            brain=brain,
            planner=Planner(brain, registry, policy),
            tools=ToolManager(registry, policy, bus),
            memory=MemoryStore(tmp_path / "memory.db"),
            bus=bus,
            config=AgentConfig(max_step_attempts=1, max_plan_revisions=1, pool_size=3),
        )
        task = await orchestrator.wait((await orchestrator.submit("go")).id)

        assert task.status is TaskStatus.FAILED
        steps = task.plan.steps
        assert steps[0].status is StepStatus.FAILED
        assert steps[1].status is StepStatus.SKIPPED  # depended on the failure
        # The independent branch was allowed to run rather than being
        # collateral damage from an unrelated failure.
        assert probe.calls == 1

    async def test_cancel_stops_agents_mid_flight(self, tmp_path: Path) -> None:
        started = asyncio.Event()

        @tool()
        async def slow() -> str:
            """Hangs until cancelled."""
            started.set()
            await asyncio.sleep(60)
            return "never"

        fm = FileManager(tmp_path / "sandbox")
        registry = ToolRegistry()
        registry.register_all(build_file_tools(fm))
        registry.register(slow)
        policy = PermissionPolicy(SecurityConfig(require_confirmation=[]))
        bus = EventBus()
        brain = ScriptedBrain(
            [plan_json([{"description": "slow", "tool": "slow", "arguments": {}}])]
        )
        orchestrator = Orchestrator(
            brain=brain,
            planner=Planner(brain, registry, policy),
            tools=ToolManager(registry, policy, bus),
            memory=MemoryStore(tmp_path / "memory.db"),
            bus=bus,
            config=AgentConfig(pool_size=3),
        )
        task = await orchestrator.submit("go")
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await orchestrator.cancel(task.id) is True
        assert orchestrator.get(task.id).status is TaskStatus.CANCELLED


class TestAgentEvents:
    async def test_agents_are_announced_as_they_pick_work_up(
        self, tmp_path: Path
    ) -> None:
        brain = ScriptedBrain([plan_json([probe_step("a")]), "done"])
        orchestrator, _, bus = system(tmp_path, brain)
        seen: list[tuple[str, object]] = []

        async def record(event) -> None:
            if event.type.value.startswith("agent."):
                seen.append((event.type.value, event.data.get("agent")))

        bus.subscribe(record)
        await orchestrator.wait((await orchestrator.submit("go")).id)

        kinds = [kind for kind, _ in seen]
        assert EventType.AGENT_ASSIGNED.value in kinds
        assert EventType.AGENT_IDLE.value in kinds
        assigned = next(agent for kind, agent in seen if kind == "agent.assigned")
        assert assigned["id"].startswith("agent-")
        assert assigned["status"] == "working"
        assert assigned["tool"] == "probe"

    async def test_steps_carry_the_agent_and_redacted_arguments(
        self, tmp_path: Path
    ) -> None:
        brain = ScriptedBrain(
            [
                plan_json(
                    [
                        {
                            "description": "write",
                            "tool": "write_file",
                            "arguments": {"path": "a.txt", "content": "s3cret payload"},
                        }
                    ]
                ),
                "done",
            ]
        )
        orchestrator, _, bus = system(tmp_path, brain)
        started: list[dict[str, object]] = []

        async def record(event) -> None:
            if event.type is EventType.STEP_STARTED:
                started.append(event.data)

        bus.subscribe(record)
        await orchestrator.wait((await orchestrator.submit("go")).id)

        assert started
        data = started[0]
        assert data["tool"] == "write_file"
        assert str(data["agent_id"]).startswith("agent-")
        assert data["arguments"]["path"] == "a.txt"
        # The file's contents must never ride along on the event stream.
        assert data["arguments"]["content"] == "<14 chars>"
