"""LLM-backed planning: request → validated, risk-tagged Plan."""

from __future__ import annotations

import json
from typing import Any

from jarvis.brain.base import Brain, BrainMessage
from jarvis.core.errors import PlanningError
from jarvis.core.models import Plan, PlanStep, Task
from jarvis.logging import get_logger
from jarvis.security.permissions import PermissionPolicy
from jarvis.tools.registry import ToolRegistry

_log = get_logger(__name__)

_PLANNING_SYSTEM = """\
You are the planning module of Jarvis, a personal desktop assistant.

Given the user's request and the available tools, produce a plan as JSON —
nothing else, no prose, no code fences.

Schema:
{
  "goal": "<one sentence restating the objective>",
  "response": "<only for pure questions that need NO tools: the direct answer>",
  "steps": [
    {
      "description": "<what this step accomplishes, user-readable>",
      "tool": "<name of one available tool>",
      "arguments": { <arguments matching that tool's input schema> }
    }
  ]
}

Rules:
- Break the request into the smallest reasonable number of concrete steps.
- Every step MUST use one of the available tools, with valid arguments.
- If the request is a question you can answer without tools, return an empty
  steps list and put the answer in "response".
- If the request cannot be done with the available tools, return an empty
  steps list and explain what is missing in "response".
- Steps run strictly in order; a step may rely on files or state produced by
  earlier steps.
- You are writing the plan BEFORE anything runs, so you cannot know what a
  search will return or what a page will say. When an argument depends on an
  earlier step's output, do not invent a plausible value - put the string
  "{{from_previous}}" there and describe what belongs in it in the step's
  description. It is filled in later from the real output. Inventing a URL,
  or writing a document's text before reading the sources it cites, produces
  confident nonsense.

Available tools:
"""

_REVISION_SYSTEM = """\
You are the planning module of Jarvis, a personal desktop assistant.
A step of the current plan failed. Propose replacement steps for the failed
step and the remaining unfinished work, taking the error into account.
"completed_steps" holds what the finished steps actually returned. Take
concrete values - URLs, file paths, ids - from there rather than inventing
plausible-looking ones: a made-up URL fails exactly the way the last one did.
Answer with JSON only, using this schema:
{
  "steps": [ {"description": ..., "tool": ..., "arguments": {...}} ]
}
Return an empty steps list if there is no sensible way to recover.

Available tools:
"""


def _tools_block(registry: ToolRegistry) -> str:
    return json.dumps(registry.schemas(), indent=2)


_RESOLUTION_SYSTEM = """You are the planning module of Jarvis, a personal desktop assistant.
One step of the plan is about to run, and its arguments contain the
placeholder "{{from_previous}}" because their real value was not knowable
when the plan was written. "completed_steps" holds what the earlier steps
actually returned. Produce the arguments for this step for real.

Answer with JSON only:
{
  "arguments": { <the complete argument object for this step> }
}

Rules:
- Every argument the tool needs must be present, placeholder or not.
- Use only values that appear in completed_steps. Never invent a URL, and
  never cite a page that is not there - if you are writing a document, its
  sources are exactly the ones actually read.
- If a piece of content has to be composed (a guide, a summary, a report),
  write it out in full here, grounded in what the earlier steps returned.
"""


def _needs_resolution(arguments: dict[str, Any]) -> bool:
    """True when any argument still carries a plan-time placeholder."""
    return "{{" in json.dumps(arguments, default=str)


def _recent_context(
    completed: list[PlanStep], budget: int = 8000
) -> list[dict[str, Any]]:
    """The most recent step results, newest first, within a total budget.

    Passing every result in full is what makes a long task get slower with
    each step: the prompt grows without bound, and on a model running at a
    couple of tokens a second that ends in a timeout rather than an answer.
    The newest results are the ones a step actually needs, so spend the
    budget there and summarise the rest to a single line.
    """
    entries: list[dict[str, Any]] = []
    spent = 0
    for step in reversed(completed):
        text = _short_output(step.result.output if step.result else None, 4000)
        size = len(text) if isinstance(text, str) else 0
        if entries and spent + size > budget:
            entries.append(
                {
                    "description": step.description,
                    "tool": step.tool,
                    "result": "(earlier step, omitted to keep the prompt small)",
                }
            )
            continue
        spent += size
        entries.append(
            {"description": step.description, "tool": step.tool, "result": text}
        )
    entries.reverse()
    return entries


def _short_output(output: Any, limit: int = 2000) -> Any:
    """Trim one tool result to something that fits beside the rest of the prompt."""
    if output is None:
        return None
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + " ...(truncated)"


def _extract_json(text: str) -> dict[str, Any]:
    """Parse the model's reply as a JSON object, tolerating code fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline:].strip().removesuffix("```").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in the model's reply.")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Plan JSON must be an object.")
    return parsed


class Planner:
    """Produces and revises plans via the Brain, validating every step."""

    def __init__(
        self,
        brain: Brain,
        registry: ToolRegistry,
        policy: PermissionPolicy,
    ) -> None:
        self._brain = brain
        self._registry = registry
        self._policy = policy

    async def plan(self, task: Task, context: str = "") -> tuple[Plan, str | None]:
        """Create a plan for ``task``.

        Returns ``(plan, direct_response)``. When the model decides no tools
        are needed, the plan has no steps and ``direct_response`` carries the
        answer to give the user.
        """
        system = _PLANNING_SYSTEM + _tools_block(self._registry)
        user_content = task.request if not context else f"{context}\n\nRequest: {task.request}"
        messages = [BrainMessage(role="user", content=user_content)]

        parsed = await self._complete_json(system, messages)
        steps = self._validate_steps(parsed.get("steps", []))
        plan = Plan(
            task_id=task.id,
            goal=str(parsed.get("goal", task.request)),
            steps=steps,
        )
        direct_response = parsed.get("response")
        _log.info(
            "plan created",
            extra={"task_id": task.id, "n_steps": len(steps)},
        )
        return plan, str(direct_response) if direct_response else None

    async def resolve_arguments(
        self,
        task: Task,
        step: PlanStep,
        completed: list[PlanStep],
    ) -> dict[str, Any]:
        """Fill a step's "{{from_previous}}" arguments from real output.

        Plan arguments are chosen before anything runs, so a value that
        depends on an earlier step can only be guessed. This asks for it
        again once the earlier steps have actually produced something.
        """
        if not _needs_resolution(step.arguments):
            return step.arguments

        system = _RESOLUTION_SYSTEM + _tools_block(self._registry)
        summary = {
            "request": task.request,
            "step": {
                "description": step.description,
                "tool": step.tool,
                "arguments": step.arguments,
            },
            "completed_steps": _recent_context(completed),
        }
        messages = [BrainMessage(role="user", content=json.dumps(summary, indent=2))]
        try:
            parsed = await self._complete_json(system, messages)
        except PlanningError as exc:
            _log.warning("argument resolution failed", extra={"error": str(exc)})
            return step.arguments
        resolved = parsed.get("arguments")
        if not isinstance(resolved, dict):
            return step.arguments
        if _needs_resolution(resolved):
            # The reply carried the placeholder straight through, so nothing
            # was actually resolved. Passing it on sends "{{from_previous}}"
            # to the tool as a literal path; say so instead.
            _log.warning(
                "resolution returned an unresolved placeholder",
                extra={"tool": step.tool, "arguments": resolved},
            )
            raise PlanningError(
                f"Could not work out the arguments for {step.tool!r} from the "
                "earlier results; the placeholder was left unfilled."
            )
        _log.info(
            "arguments resolved from earlier results",
            extra={"tool": step.tool, "keys": sorted(resolved)},
        )
        return resolved

    async def revise(
        self,
        task: Task,
        failed_step: PlanStep,
        error: str,
        remaining: list[PlanStep],
        completed: list[PlanStep] | None = None,
    ) -> list[PlanStep]:
        """Ask for replacement steps after a failure; may return []."""
        system = _REVISION_SYSTEM + _tools_block(self._registry)
        summary = {
            "request": task.request,
            # What the finished steps actually returned. Without it the model
            # re-guesses values it could simply have read here, so a step like
            # "open the first search result" gets an invented URL and fails
            # the same way the original did.
            "completed_steps": _recent_context(completed or []),
            "failed_step": {
                "description": failed_step.description,
                "tool": failed_step.tool,
                "arguments": failed_step.arguments,
                "error": error,
            },
            "remaining_steps": [
                {"description": s.description, "tool": s.tool, "arguments": s.arguments}
                for s in remaining
            ],
        }
        messages = [BrainMessage(role="user", content=json.dumps(summary, indent=2))]
        try:
            parsed = await self._complete_json(system, messages)
            return self._validate_steps(parsed.get("steps", []))
        except PlanningError as exc:
            _log.warning("plan revision failed", extra={"error": str(exc)})
            return []

    async def _complete_json(
        self, system: str, messages: list[BrainMessage]
    ) -> dict[str, Any]:
        """Get a JSON object from the Brain, retrying once on bad output."""
        response = await self._brain.complete(
            system=system, messages=messages, json_mode=True
        )
        if response.refused:
            raise PlanningError("The model declined to plan this request.")
        try:
            return _extract_json(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            _log.warning("plan JSON invalid, retrying", extra={"error": str(exc)})
            retry_messages = [
                *messages,
                BrainMessage(role="assistant", content=response.text),
                BrainMessage(
                    role="user",
                    content=(
                        f"That was not valid JSON ({exc}). "
                        "Reply again with ONLY the JSON object."
                    ),
                ),
            ]
            retry = await self._brain.complete(
                system=system, messages=retry_messages, json_mode=True
            )
            try:
                return _extract_json(retry.text)
            except (ValueError, json.JSONDecodeError) as retry_exc:
                raise PlanningError(
                    f"Could not produce a valid plan: {retry_exc}"
                ) from retry_exc

    def _validate_steps(self, raw_steps: Any) -> list[PlanStep]:
        if not isinstance(raw_steps, list):
            raise PlanningError("Plan 'steps' must be a list.")
        steps: list[PlanStep] = []
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                raise PlanningError(f"Step {index} is not an object.")
            tool_name = raw.get("tool")
            if not tool_name or not self._registry.has(str(tool_name)):
                raise PlanningError(
                    f"Step {index} uses unknown tool {tool_name!r}. "
                    f"Available: {', '.join(self._registry.names())}"
                )
            tool = self._registry.get(str(tool_name))
            arguments = raw.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise PlanningError(f"Step {index} arguments must be an object.")
            steps.append(
                PlanStep(
                    description=str(raw.get("description", tool_name)),
                    tool=str(tool_name),
                    arguments=arguments,
                    risk=self._policy.risk_for(tool.risk_category),
                )
            )
        return steps
