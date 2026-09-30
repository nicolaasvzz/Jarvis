"""Exception hierarchy for Jarvis.

All Jarvis-raised errors derive from :class:`JarvisError`, so callers can
catch the whole family with one ``except`` and the agent loop can tell a
recoverable tool failure apart from a fatal one.
"""

from __future__ import annotations


class JarvisError(Exception):
    """Base class for every error Jarvis raises deliberately."""


class ToolError(JarvisError):
    """A tool failed while executing.

    ``recoverable`` tells the executor whether retrying or trying an
    alternative is worthwhile (default) or whether it should give up on this
    step and surface the failure.
    """

    def __init__(self, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class ToolNotFound(JarvisError):
    """A tool was requested by a name that is not in the registry."""


class ConfirmationRequired(JarvisError):
    """Raised when a dangerous action needs explicit user approval first.

    Carries the pending :class:`~jarvis.core.models.ApprovalRequest` so the
    caller can surface it to the user and resume once a decision arrives.
    """

    def __init__(self, request: object) -> None:
        super().__init__("User confirmation required before this action.")
        self.request = request


class ApprovalDenied(JarvisError):
    """The user declined a confirmation request."""


class PlanningError(JarvisError):
    """The planner could not produce a valid plan for the request."""


class BrainError(JarvisError):
    """The language model backend could not complete a request.

    Raised with a message that is already safe to show the user directly —
    translated from whatever the underlying provider actually returned (an
    HTTP status, a network failure, ...) into plain language.
    """


class VoiceError(JarvisError):
    """Speech could not be synthesised or transcribed.

    Like :class:`BrainError`, the message is already user-facing: a missing
    engine, an unavailable voice or an unreadable recording should read as an
    instruction, not a stack trace. Speech is a convenience, so callers are
    expected to catch this and carry on silently in text.
    """
