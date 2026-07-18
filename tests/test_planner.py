"""Tests for the Planner."""

from __future__ import annotations

import json

import pytest

from jarvis.config.schema import SecurityConfig
from jarvis.core.errors import PlanningError
from jarvis.core.models import RiskLevel, Task
from jarvis.planner import Planner
from jarvis.security import PermissionPolicy
from jarvis.tools import ToolRegistry, tool
from tests.helpers import ScriptedBrain


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    @tool()
    def write_file(path: str, content: str) -> str:
        """Write a file."""
        return path

    @tool(risk_category="delete_files")
    def delete_path(path: str) -> str:
        """Delete a path."""
        return path

    registry.register_all([write_file, delete_path])
    return registry


def _planner(brain: ScriptedBrain) -> Planner:
    policy = PermissionPolicy(SecurityConfig(require_confirmation=["delete_files"]))
    return Planner(brain, _registry(), policy)


def _plan_json() -> str:
    return json.dumps(
        {
            "goal": "Write then delete a note",
            "steps": [
                {
                    "description": "Write the note",
                    "tool": "write_file",
                    "arguments": {"path": "note.txt", "content": "hi"},
                },
                {
                    "description": "Delete the note",
                    "tool": "delete_path",
                    "arguments": {"path": "note.txt"},
                },
            ],
        }
    )


async def test_plan_parses_validates_and_tags_risk() -> None:
    planner = _planner(ScriptedBrain([_plan_json()]))
    plan, direct = await planner.plan(Task(request="write then delete note"))
    assert direct is None
    assert plan.goal == "Write then delete a note"
    assert [s.tool for s in plan.steps] == ["write_file", "delete_path"]
    assert plan.steps[0].risk is RiskLevel.SAFE
    assert plan.steps[1].risk is RiskLevel.CONFIRM


async def test_plan_tolerates_code_fences() -> None:
    fenced = f"```json\n{_plan_json()}\n```"
    planner = _planner(ScriptedBrain([fenced]))
    plan, _ = await planner.plan(Task(request="r"))
    assert len(plan.steps) == 2


async def test_direct_response_for_pure_questions() -> None:
    reply = json.dumps({"goal": "answer", "response": "It is Tuesday.", "steps": []})
    planner = _planner(ScriptedBrain([reply]))
    plan, direct = await planner.plan(Task(request="what day is it?"))
    assert plan.steps == []
    assert direct == "It is Tuesday."


async def test_invalid_json_is_retried_once_then_fails() -> None:
    brain = ScriptedBrain(["not json at all", "still not json"])
    planner = _planner(brain)
    with pytest.raises(PlanningError):
        await planner.plan(Task(request="r"))
    assert len(brain.calls) == 2  # original + one retry


async def test_retry_recovers_from_bad_first_reply() -> None:
    brain = ScriptedBrain(["oops", _plan_json()])
    planner = _planner(brain)
    plan, _ = await planner.plan(Task(request="r"))
    assert len(plan.steps) == 2


async def test_unknown_tool_is_rejected() -> None:
    bad = json.dumps(
        {
            "goal": "g",
            "steps": [{"description": "x", "tool": "not_a_tool", "arguments": {}}],
        }
    )
    # Same invalid answer twice: the validation error is not a JSON error,
    # so it fails immediately without a retry.
    planner = _planner(ScriptedBrain([bad, bad]))
    with pytest.raises(PlanningError, match="not_a_tool"):
        await planner.plan(Task(request="r"))


async def test_revise_returns_replacement_steps() -> None:
    replacement = json.dumps(
        {
            "steps": [
                {
                    "description": "Write elsewhere",
                    "tool": "write_file",
                    "arguments": {"path": "other.txt", "content": "hi"},
                }
            ]
        }
    )
    planner = _planner(ScriptedBrain([replacement]))
    task = Task(request="r")
    plan, _ = await _planner(ScriptedBrain([_plan_json()])).plan(task)
    steps = await planner.revise(task, plan.steps[0], "disk full", plan.steps[1:])
    assert len(steps) == 1
    assert steps[0].tool == "write_file"


async def test_revise_swallows_planning_errors_and_returns_empty() -> None:
    planner = _planner(ScriptedBrain(["garbage", "garbage"]))
    task = Task(request="r")
    step_plan, _ = await _planner(ScriptedBrain([_plan_json()])).plan(task)
    steps = await planner.revise(task, step_plan.steps[0], "err", [])
    assert steps == []
