"""Where the web frontend's files are, if they are here at all.

The UI lives in its own top-level ``frontend/`` folder, beside ``backend/``,
so it can be handed to someone on its own. The backend serves it at
``/dash/`` when it can find it, and runs as a plain API when it cannot —
someone who was given only the backend loses nothing but the web pages,
and can still point a separately-hosted frontend at this server.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.logging import get_logger

_log = get_logger(__name__)

#: ``frontend/`` beside ``backend/`` in a checkout of the repository:
#: this file is backend/src/jarvis/dashboard/frontend.py.
CHECKOUT_FRONTEND = Path(__file__).resolve().parents[4] / "frontend"


def find_frontend(configured: Path | None = None) -> Path | None:
    """The folder holding the frontend's ``index.html``, or ``None``.

    ``configured`` (``dashboard.web_root``) wins when set. Otherwise the
    ``frontend/`` folder of the checkout this backend was installed from is
    used, if it exists.
    """
    if configured is not None:
        if (configured / "index.html").is_file():
            return configured
        _log.warning(
            "dashboard.web_root has no index.html; serving the API only",
            extra={"web_root": str(configured)},
        )
        return None
    if (CHECKOUT_FRONTEND / "index.html").is_file():
        return CHECKOUT_FRONTEND
    return None
