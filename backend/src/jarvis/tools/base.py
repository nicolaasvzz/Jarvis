"""The Tool abstraction and a decorator for defining tools quickly.

A tool is a named, described, schema-carrying, risk-tagged callable. Tools
may be async or sync; the manager handles both. The :func:`tool` decorator
turns a plain function into a :class:`Tool`, deriving the argument schema
from type hints so simple tools need almost no boilerplate.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


@dataclass
class ToolContext:
    """Ambient services a tool may use while running.

    Passed by the manager to every tool that opts in (by declaring a
    ``context`` parameter). Keeps tools free of global state and easy to
    test with fakes.
    """

    task_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class Tool:
    """One executable capability with metadata for the LLM and the policy."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        func: Callable[..., Any],
        parameters: dict[str, Any] | None = None,
        risk_category: str | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters or _schema_from_signature(func)
        # A category the permission policy recognises (e.g. "delete_files").
        # None means the action is inherently safe and never needs approval.
        self.risk_category = risk_category
        self._wants_context = "context" in inspect.signature(func).parameters

    async def __call__(self, arguments: dict[str, Any], context: ToolContext) -> Any:
        kwargs = dict(arguments)
        if self._wants_context:
            kwargs["context"] = context
        result = self.func(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def schema(self) -> dict[str, Any]:
        """The tool definition in the shape the Anthropic API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


def _schema_from_signature(func: Callable[..., Any]) -> dict[str, Any]:
    """Build a JSON-schema object from a function's typed parameters."""
    hints = get_type_hints(func)
    signature = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in signature.parameters.items():
        if param_name == "context":
            continue
        annotation = hints.get(param_name, str)
        json_type = _PY_TO_JSON.get(annotation, "string")
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    risk_category: str | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """Decorator that turns a function into a :class:`Tool`.

    The name defaults to the function name and the description to its
    docstring, so most tools need only ``@tool()`` plus a good docstring.
    """

    def decorator(func: Callable[..., Any]) -> Tool:
        return Tool(
            name=name or func.__name__,
            description=description or (inspect.getdoc(func) or func.__name__),
            func=func,
            risk_category=risk_category,
        )

    return decorator


AsyncToolFunc = Callable[..., Awaitable[Any]]
