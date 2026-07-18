"""Builds the full Jarvis object graph from configuration.

This is the only place that knows how the modules fit together. Everything
is constructed here and handed to consumers as arguments — no globals, no
import-time side effects — so tests can assemble alternative graphs and any
module can be swapped by changing one line.

Platform-specific tool sets (browser, desktop, vision) are *optional*:
if their dependencies aren't installed the runtime logs what is missing
and continues with the tools that work everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis.agent import Orchestrator
from jarvis.brain import AnthropicBrain
from jarvis.brain.base import Brain
from jarvis.config import AppConfig, Secrets, load_config, load_secrets
from jarvis.core.events import EventBus
from jarvis.files import FileManager, build_file_tools
from jarvis.logging import get_logger, setup_logging
from jarvis.memory import MemoryStore
from jarvis.memory.tools import build_memory_tools
from jarvis.notifications import InMemoryChannel, LogChannel, NotificationService
from jarvis.planner import Planner
from jarvis.security import PermissionPolicy
from jarvis.tools import ToolManager, ToolRegistry

_log = get_logger(__name__)


@dataclass
class JarvisRuntime:
    """Everything a front-end (API server or CLI) needs, fully wired."""

    config: AppConfig
    secrets: Secrets
    bus: EventBus
    policy: PermissionPolicy
    registry: ToolRegistry
    tool_manager: ToolManager
    files: FileManager
    memory: MemoryStore
    notifications: NotificationService
    live_channel: InMemoryChannel
    brain: Brain
    planner: Planner
    orchestrator: Orchestrator
    log_file: Path

    async def close(self) -> None:
        """Release external resources (browser, database, subscriptions)."""
        browser = getattr(self, "_browser_controller", None)
        if browser is not None:
            await browser.close()
        self.notifications.close()
        self.memory.close()


def build_runtime(
    config_file: str | Path | None = None,
    *,
    brain: Brain | None = None,
) -> JarvisRuntime:
    """Load config, wire every module, and return the ready runtime.

    ``brain`` may be injected (tests, offline mode); by default the
    Anthropic brain is built and requires ``ANTHROPIC_API_KEY``.
    """
    config = load_config(config_file)
    log_file = setup_logging(config.logging)
    secrets = load_secrets()

    bus = EventBus()
    policy = PermissionPolicy(config.security)

    files_root = config.files.root or (config.paths.data_dir / "workspace")
    files = FileManager(Path(files_root))
    memory = MemoryStore(config.paths.data_dir / "memory.db")

    registry = ToolRegistry()
    registry.register_all(build_file_tools(files))
    registry.register_all(build_memory_tools(memory))
    browser_controller = _register_optional_tools(registry, config, files)

    tool_manager = ToolManager(registry, policy, bus)

    if brain is None:
        api_key = secrets.require("anthropic_api_key")
        brain = AnthropicBrain(config.llm, api_key)

    planner = Planner(brain, registry, policy)
    orchestrator = Orchestrator(
        brain=brain,
        planner=planner,
        tools=tool_manager,
        memory=memory,
        bus=bus,
        config=config.agent,
    )

    live_channel = InMemoryChannel()
    notifications = NotificationService(bus, [LogChannel(), live_channel])

    runtime = JarvisRuntime(
        config=config,
        secrets=secrets,
        bus=bus,
        policy=policy,
        registry=registry,
        tool_manager=tool_manager,
        files=files,
        memory=memory,
        notifications=notifications,
        live_channel=live_channel,
        brain=brain,
        planner=planner,
        orchestrator=orchestrator,
        log_file=log_file,
    )
    # Kept for close(); not part of the public dataclass fields.
    runtime._browser_controller = browser_controller  # type: ignore[attr-defined]
    _log.info(
        "runtime ready",
        extra={"tools": registry.names(), "workspace": str(files.root)},
    )
    return runtime


def _register_optional_tools(
    registry: ToolRegistry, config: AppConfig, files: FileManager
) -> object | None:
    """Register browser/desktop/vision tools when their deps are available.

    Tool *construction* is import-free by design (heavy imports happen on
    first use), so registration itself never fails — but we probe imports
    here to log an honest capability report at startup.
    """
    browser_controller = None
    try:
        import playwright  # noqa: F401

        from jarvis.browser import BrowserController, build_browser_tools

        browser_controller = BrowserController(config.browser, files)
        registry.register_all(build_browser_tools(browser_controller))
    except ImportError:
        _log.info("browser tools disabled (playwright not installed)")

    try:
        import pyautogui  # noqa: F401

        from jarvis.desktop import (
            DesktopController,
            WindowsBackend,
            build_desktop_tools,
        )

        registry.register_all(
            build_desktop_tools(DesktopController(WindowsBackend()))
        )
    except Exception:  # noqa: BLE001 - pyautogui import can fail without a display
        _log.info("desktop tools disabled (pyautogui unavailable here)")

    try:
        import mss  # noqa: F401

        from jarvis.vision import MssGrabber, VisionService, build_vision_tools

        registry.register_all(
            build_vision_tools(VisionService(MssGrabber(), files))
        )
    except ImportError:
        _log.info("vision tools disabled (mss not installed)")

    return browser_controller
