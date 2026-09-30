"""Planner: break a request into steps before anything executes.

The :class:`~jarvis.planner.planner.Planner` asks the Brain to turn a
natural-language request into a JSON plan — an ordered list of steps, each
bound to a registered tool with concrete arguments. It validates the JSON
shape and every tool name, tags each step with the risk level the security
policy assigns, and retries once with the parse error included if the model
produces malformed output.

When a step fails during execution, :meth:`Planner.revise` gives the model
the failure context and asks for replacement steps — this is how "try an
alternative approach" works. The Planner never executes anything itself.
"""

from jarvis.planner.planner import Planner

__all__ = ["Planner"]
