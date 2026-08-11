"""Brain: the connection to the language model.

The Brain owns exactly one job — send conversation state to the LLM and
return a structured :class:`~jarvis.brain.base.BrainResponse` (text, tool
calls, stop reason). It contains no tool implementations and no other I/O.

The interface is the :class:`~jarvis.brain.base.Brain` protocol. Two real
implementations exist: :class:`~jarvis.brain.anthropic_brain.AnthropicBrain`
talks to the Anthropic API, and :class:`~jarvis.brain.ollama_brain.OllamaBrain`
talks to a local ``ollama serve`` instance for fully offline operation.
Tests use a scripted stand-in. Because the Planner and Orchestrator only see
the protocol, the model provider is swappable without touching either.
"""

from jarvis.brain.anthropic_brain import AnthropicBrain
from jarvis.brain.base import Brain, BrainMessage, BrainResponse
from jarvis.brain.ollama_brain import OllamaBrain

__all__ = ["AnthropicBrain", "Brain", "BrainMessage", "BrainResponse", "OllamaBrain"]
