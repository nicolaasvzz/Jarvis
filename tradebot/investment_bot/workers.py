"""Pool workers that don't outlive the bot.

A run that is killed rather than stopped — its terminal closed, its process
ended from outside — can't shut its process pool down, and on Windows the
workers then live on: idle, but holding memory and the lab's memory-mapped
indicator files (which Windows then won't let the next lab replace). Every
pool's initializer calls ``exit_with_parent()``, so a worker ends itself
within a few seconds of the bot that started it.
"""
from __future__ import annotations

import os
import threading
import time


def exit_with_parent(every: float = 5.0) -> None:
    from .jarvis_status import _alive

    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(every)
            if not _alive(parent):
                os._exit(0)

    threading.Thread(target=watch, name="exit-with-parent", daemon=True).start()
