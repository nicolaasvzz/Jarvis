"""Application wiring and entry points.

:mod:`jarvis.app.runtime` builds the whole object graph (dependency
injection by hand — explicit and boring on purpose), and
:mod:`jarvis.app.cli` exposes the ``jarvis`` command with ``serve``,
``run``, ``tools``, and ``token`` subcommands.
"""

from jarvis.app.runtime import JarvisRuntime, build_runtime

__all__ = ["JarvisRuntime", "build_runtime"]
