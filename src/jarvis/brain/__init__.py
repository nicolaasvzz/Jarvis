"""Brain: the connection to the language model.

The Brain owns exactly one job — send conversation state to the LLM and
return a structured :class:`~jarvis.brain.base.BrainResponse` (text, tool
calls, stop reason). It contains no tool implementations and no other I/O.

The interface is the :class:`~jarvis.brain.base.Brain` protocol. Two
implementations ship with Jarvis:
:class:`~jarvis.brain.gemini_brain.GeminiBrain`, which talks to Google's
Gemini API (the default — a free API key is enough), and
:class:`~jarvis.brain.anthropic_brain.AnthropicBrain`, which talks to the
Anthropic API. :func:`~jarvis.brain.factory.build_brain` picks between
them from configuration; tests and offline development use a scripted
stand-in. Because the Planner and Orchestrator only ever see the protocol,
the model provider is swappable without touching either.
"""

from jarvis.brain.anthropic_brain import AnthropicBrain
from jarvis.brain.base import Brain, BrainMessage, BrainResponse
from jarvis.brain.factory import build_brain
from jarvis.brain.gemini_brain import GeminiBrain

__all__ = [
    "AnthropicBrain",
    "Brain",
    "BrainMessage",
    "BrainResponse",
    "GeminiBrain",
    "build_brain",
]
