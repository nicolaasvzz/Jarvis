"""Tests for the Planner."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from jarvis.config.schema import SecurityConfig
from jarvis.core.errors import PlanningError
from jarvis.core.models import PlanStep, RiskLevel, StepStatus, Task, ToolResult
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
    # Same invalid answer twice: one corrective retry, then it fails.
    brain = ScriptedBrain([bad, bad])
    planner = _planner(brain)
    with pytest.raises(PlanningError, match="not_a_tool"):
        await planner.plan(Task(request="r"))
    assert len(brain.calls) == 2


async def test_unknown_tool_is_retried_with_the_reason() -> None:
    bad = json.dumps(
        {
            "goal": "g",
            "steps": [{"description": "x", "tool": "not_a_tool", "arguments": {}}],
        }
    )
    brain = ScriptedBrain([bad, _plan_json()])
    plan, _ = await _planner(brain).plan(Task(request="r"))
    assert [s.tool for s in plan.steps] == ["write_file", "delete_path"]
    # The retry told the model exactly what was wrong.
    assert "not_a_tool" in brain.calls[1]["messages"][-1].content


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


async def test_revise_shows_the_model_what_completed_steps_returned() -> None:
    """The replacement needs the real values, not a plausible guess.

    A step described as "open the first search result" carries a URL chosen
    before the search ran. Unless the revision can see what the search
    actually returned, it invents another URL and fails identically.
    """
    replacement = json.dumps({"steps": []})
    brain = ScriptedBrain([replacement])
    planner = _planner(brain)
    task = Task(request="research and write it up")
    plan, _ = await _planner(ScriptedBrain([_plan_json()])).plan(task)

    done = plan.steps[0]
    done.status = StepStatus.COMPLETED
    done.description = "Search the web"
    done.result = ToolResult.success(
        "browser_search",
        [{"title": "Real Article", "url": "https://example.com/real-article"}],
        datetime.now(tz=UTC),
    )

    await planner.revise(task, plan.steps[-1], "HTTP 404", [], [done])

    sent = brain.calls[0]["messages"][0].content
    assert "completed_steps" in sent
    assert "https://example.com/real-article" in sent


async def test_revise_swallows_planning_errors_and_returns_empty() -> None:
    planner = _planner(ScriptedBrain(["garbage", "garbage"]))
    task = Task(request="r")
    step_plan, _ = await _planner(ScriptedBrain([_plan_json()])).plan(task)
    steps = await planner.revise(task, step_plan.steps[0], "err", [])
    assert steps == []


async def test_plain_arguments_run_without_a_resolution_call() -> None:
    """No placeholder, no extra model round-trip."""
    brain = ScriptedBrain([])
    planner = _planner(brain)
    task = Task(request="r")
    step = PlanStep(description="write", tool="write_file", arguments={"path": "a.txt"})
    assert await planner.resolve_arguments(task, step, []) == {"path": "a.txt"}
    assert brain.calls == []


async def test_placeholder_arguments_are_rewritten_from_real_output() -> None:
    """The whole point: content is composed after the sources are read.

    Written at plan time, a guide's citations are invented - the plan is
    authored before the research runs. Resolving here grounds them in the
    URLs that were actually fetched.
    """
    resolved = json.dumps(
        {
            "arguments": {
                "path": "guide.txt",
                "content": "Guide. Source: https://real.example/a",
            }
        }
    )
    brain = ScriptedBrain([resolved])
    planner = _planner(brain)
    task = Task(request="research and write a guide")

    done = PlanStep(description="research", tool="browser_research")
    done.result = ToolResult.success(
        "browser_research",
        [{"url": "https://real.example/a", "title": "Real", "text": "body"}],
        datetime.now(tz=UTC),
    )
    step = PlanStep(
        description="write the guide citing the sources read",
        tool="write_file",
        arguments={"path": "guide.txt", "content": "{{from_previous}}"},
    )

    out = await planner.resolve_arguments(task, step, [done])
    assert out["content"] == "Guide. Source: https://real.example/a"
    assert "https://real.example/a" in brain.calls[0]["messages"][0].content


async def test_resolution_falls_back_to_the_original_arguments() -> None:
    """A failed resolution must not strand the step with no arguments."""
    planner = _planner(ScriptedBrain(["garbage", "garbage"]))
    task = Task(request="r")
    step = PlanStep(
        description="write", tool="write_file",
        arguments={"path": "a.txt", "content": "{{from_previous}}"},
    )
    assert await planner.resolve_arguments(task, step, []) == step.arguments


async def test_context_stays_bounded_as_steps_accumulate() -> None:
    """A long task must not get slower with every step.

    Passing every result in full grows the prompt without bound; on a model
    doing a couple of tokens a second that ends in a timeout, not an answer.
    """
    from jarvis.planner.planner import _recent_context

    steps = []
    for i in range(12):
        step = PlanStep(description=f"slice {i}", tool="read_file_slice")
        step.result = ToolResult.success(
            "read_file_slice", "y" * 5000, datetime.now(tz=UTC)
        )
        steps.append(step)

    entries = _recent_context(steps, budget=8000)
    assert len(entries) == 12                      # every step still mentioned
    body = json.dumps(entries)
    assert len(body) < 20_000                      # but the prompt stays small
    # The newest results survive in full; the oldest are the ones dropped.
    assert str(entries[-1]["result"]).startswith("y" * 4000)
    assert "omitted" in str(entries[0]["result"])


async def test_unresolved_placeholder_is_rejected_not_passed_on() -> None:
    """A placeholder that survives resolution must not reach the tool.

    Passed through, "{{from_previous}}" arrives at read_file_slice as a
    literal path and fails with a confusing "Not a file" - the real problem
    being that nothing was resolved at all.
    """
    still_unresolved = json.dumps(
        {"arguments": {"path": "{{from_previous}}/page2.html", "start": 0}}
    )
    planner = _planner(ScriptedBrain([still_unresolved]))
    task = Task(request="r")
    step = PlanStep(
        description="read the archived page",
        tool="read_file_slice",
        arguments={"path": "{{from_previous}}", "start": 0},
    )
    with pytest.raises(PlanningError, match="placeholder was left unfilled"):
        await planner.resolve_arguments(task, step, [])
