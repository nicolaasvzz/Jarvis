"""Make tool arguments safe to publish on the event bus.

The dashboard needs to know *what* Jarvis touched — which file it opened,
which URL it visited, which window it focused. That detail lives in the tool
arguments, but those same arguments can carry the entire contents of a file
being written, or a password being typed into a form.

So arguments are summarised rather than forwarded: identifying fields (paths,
URLs, selectors) survive intact because they are what the UI draws, while
anything that looks like payload or credential is reduced to a description of
its size. The result is small, boring, and safe to render in a browser.

This is a one-way summary for display. It is never parsed back into
arguments, and the real arguments still go to the tool untouched.
"""

from __future__ import annotations

from typing import Any

#: Fields replaced by a size summary rather than shown. These carry payloads
#: (file bodies, typed text) or credentials — never useful to a viewer, and
#: actively harmful to splash on a screen.
_OPAQUE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "body",
        "content",
        "credential",
        "data",
        "html",
        "image",
        "password",
        "payload",
        "secret",
        "text",
        "token",
        "value",
    }
)

#: Substrings that mark a key as sensitive even with a prefix or suffix,
#: e.g. ``anthropic_api_key`` or ``auth_token``.
_SENSITIVE_MARKERS = ("password", "secret", "token", "api_key", "credential")

_MAX_STRING = 160
_MAX_ITEMS = 8
_MAX_KEYS = 12
_MAX_DEPTH = 3


def redact_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Summarise tool arguments for display on the event stream.

    Returns a new dict; the input is never mutated.
    """
    if not arguments:
        return {}
    return {
        key: _redact_value(key, value, depth=0)
        for key, value in list(arguments.items())[:_MAX_KEYS]
    }


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in _OPAQUE_KEYS:
        return True
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def _redact_value(key: str, value: Any, *, depth: int) -> Any:
    if _is_sensitive(key):
        return _describe(value)
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if depth >= _MAX_DEPTH:
        return _describe(value)
    if isinstance(value, list):
        head = [_redact_value(key, item, depth=depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            head.append(f"…+{len(value) - _MAX_ITEMS} more")
        return head
    if isinstance(value, dict):
        return {
            str(k): _redact_value(str(k), v, depth=depth + 1)
            for k, v in list(value.items())[:_MAX_KEYS]
        }
    return _clip(str(value))


def _clip(text: str) -> str:
    if len(text) <= _MAX_STRING:
        return text
    return text[: _MAX_STRING - 1] + "…"


def _describe(value: Any) -> str:
    """Describe a value's shape without revealing it."""
    if value is None:
        return "<none>"
    if isinstance(value, str):
        return f"<{len(value)} chars>"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, list | tuple | set):
        return f"<{len(value)} items>"
    if isinstance(value, dict):
        return f"<{len(value)} fields>"
    return f"<{type(value).__name__}>"
