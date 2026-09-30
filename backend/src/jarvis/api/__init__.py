"""API Server: the secure HTTP interface the phone client talks to.

Built with FastAPI. Every route (except ``/health``) requires the shared
bearer token, verified in constant time by the security module. The phone
can: submit tasks, watch progress (polling or a live server-sent-events
stream), approve or deny dangerous actions, cancel tasks, upload files into
the sandbox, read notifications, and tail the structured logs.

:func:`create_app` is a pure factory over already-constructed components,
so the API layer stays independent of how the application is wired.
"""

from jarvis.api.server import create_app

__all__ = ["create_app"]
