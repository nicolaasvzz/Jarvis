"""The ``jarvis`` command-line interface.

Subcommands:

* ``jarvis serve`` — start the API server, the dashboard, and (if configured)
  the phone bridge, all in one process.
* ``jarvis dash`` — the same, and open the dashboard in your browser.
* ``jarvis run "<request>"`` — run one task from the terminal, streaming
  progress and prompting for approvals interactively.
* ``jarvis phone`` — the Telegram bridge on its own.
* ``jarvis brain`` — show the configured model provider and check it works.
* ``jarvis tools`` — list the tools available on this machine.
* ``jarvis token`` — generate a strong token for ``JARVIS_API_TOKEN``.

``serve`` runs everything on one event loop on purpose. Live agent state
exists only in the Orchestrator's memory, so a dashboard or bridge in a
second process would each build their own runtime and show a different,
emptier Jarvis than the one actually doing the work.

argparse is used deliberately: zero extra dependencies for the entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import secrets as pysecrets
import sys
import webbrowser
from typing import TYPE_CHECKING

from jarvis import __version__

if TYPE_CHECKING:
    from jarvis.app.runtime import JarvisRuntime
    from jarvis.core.models import ApprovalRequest
    from jarvis.security import PermissionPolicy


def main(argv: list[str] | None = None) -> int:
    # A model is free to answer with any Unicode it likes (smart quotes,
    # non-breaking hyphens, ...); Windows' console defaults to cp1252, which
    # cannot encode most of that and would crash the CLI on a perfectly
    # successful response.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="jarvis", description="Jarvis — personal AI desktop assistant"
    )
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    parser.add_argument(
        "--config", default=None, help="Path to a config YAML file", metavar="PATH"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("serve", "Start the API server, dashboard and phone bridge"),
        ("dash", "Start everything and open the dashboard in your browser"),
    ):
        parser_for = sub.add_parser(name, help=help_text)
        parser_for.add_argument("--host", default=None, help="Override api.host")
        parser_for.add_argument("--port", type=int, default=None, help="Override api.port")
        parser_for.add_argument(
            "--no-dash",
            action="store_true",
            help="Run the API only, without the web dashboard",
        )
        parser_for.add_argument(
            "--open",
            dest="open_browser",
            action="store_true",
            help="Open the dashboard in your browser once it is up",
        )

    run = sub.add_parser("run", help="Run a single request from the terminal")
    run.add_argument("request", help="What you want Jarvis to do")
    run.add_argument(
        "--yes",
        action="store_true",
        help="Approve dangerous actions automatically (use with care)",
    )

    sub.add_parser(
        "phone",
        help="Run the Telegram bridge — control Jarvis from your phone "
        "(outbound only, needs no wifi/LAN)",
    )
    sub.add_parser(
        "brain", help="Show the configured model provider and check it responds"
    )
    sub.add_parser("tools", help="List the tools available on this machine")
    sub.add_parser("token", help="Generate a token for JARVIS_API_TOKEN")

    args = parser.parse_args(argv)

    if args.command == "token":
        print(pysecrets.token_urlsafe(48))
        return 0
    if args.command in {"serve", "dash"}:
        if args.command == "dash":
            args.open_browser = True
        return asyncio.run(_serve(args))
    if args.command == "run":
        return asyncio.run(_run_once(args))
    if args.command == "phone":
        return asyncio.run(_run_phone(args))
    if args.command == "brain":
        return asyncio.run(_check_brain(args))
    if args.command == "tools":
        return _list_tools(args)
    return 2  # pragma: no cover - argparse enforces the choices


def _build(args: argparse.Namespace) -> JarvisRuntime | None:
    """Build the runtime, or explain in plain language why it cannot start.

    A missing secret is the single most likely first-run failure, and a
    stack trace is a poor way to say "you have not set your API key yet".
    """
    from jarvis.app.runtime import build_runtime

    try:
        return build_runtime(args.config)
    except RuntimeError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return None
    except FileNotFoundError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return None


async def _run_phone(args: argparse.Namespace) -> int:
    runtime = _build(args)
    if runtime is None:
        return 1
    bridge = runtime.telegram
    if bridge is None:
        print(
            "The Telegram bridge is not configured. Set telegram.enabled: true in\n"
            "your config and the TELEGRAM_BOT_TOKEN secret, then try again.\n"
            'Install the extra with: pip install "jarvis-assistant[phone]"',
            file=sys.stderr,
        )
        await runtime.close()
        return 1
    print("Jarvis Telegram bridge running. Message your bot; press Ctrl-C to stop.")
    try:
        await bridge.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runtime.close()
    return 0


async def _serve(args: argparse.Namespace) -> int:
    """Run the API, the dashboard and the phone bridge on one event loop."""
    try:
        import uvicorn
    except ImportError:
        print(
            'The API server needs the api extra: pip install "jarvis-assistant[api]"',
            file=sys.stderr,
        )
        return 1

    from jarvis.api import create_app
    from jarvis.security import TokenAuthenticator

    runtime = _build(args)
    if runtime is None:
        return 1
    try:
        token = runtime.secrets.require("jarvis_api_token")
    except RuntimeError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        print("Generate one with: jarvis token", file=sys.stderr)
        await runtime.close()
        return 1

    hub = None if args.no_dash else runtime.dashboard
    app = create_app(
        orchestrator=runtime.orchestrator,
        policy=runtime.policy,
        notifications=runtime.notifications,
        live_channel=runtime.live_channel,
        files=runtime.files,
        authenticator=TokenAuthenticator(token),
        registry=runtime.registry,
        log_file=runtime.log_file,
        hub=hub,
        dashboard=runtime.config.dashboard,
        voice=runtime.voice,
    )

    host = args.host or runtime.config.api.host
    port = args.port or runtime.config.api.port
    _print_banner(runtime, host, port, hub is not None, token.get_secret_value())

    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning")
    )
    # One stop signal shared by everything: whichever component exits first
    # (usually the server, on Ctrl-C) brings the others down with it.
    stop = asyncio.Event()

    async def run_server() -> None:
        try:
            await server.serve()
        finally:
            stop.set()

    jobs = [run_server()]
    if runtime.telegram is not None:
        jobs.append(runtime.telegram.run(stop))

    if args.open_browser or (hub and runtime.config.dashboard.open_browser):
        _open_dashboard(host, port, token.get_secret_value())

    try:
        await asyncio.gather(*jobs)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop.set()
        await runtime.close()
    return 0


def _print_banner(
    runtime: JarvisRuntime, host: str, port: int, dashboard: bool, token: str
) -> None:
    config = runtime.config
    print(f"Jarvis API listening on http://{host}:{port}")
    print(f"  workspace  {runtime.files.root}")
    print(
        f"  agents     {config.agent.pool_size} "
        f"({'parallel' if config.agent.parallel else 'sequential'})"
    )
    if dashboard:
        print(f"  dashboard  http://{host}:{port}/dash/?token={token}")
    if runtime.voice is not None:
        speaks = runtime.voice.voice_name if runtime.voice.can_speak else "off"
        hears = "on" if runtime.voice.can_listen else "off"
        print(f"  voice      speaks: {speaks} · listens: {hears}")
    if runtime.telegram is not None:
        print("  telegram   bridge running in this process")
    print("Press Ctrl-C to stop.")


def _open_dashboard(host: str, port: int, token: str) -> None:
    # 0.0.0.0 means "every interface" to a server but nothing to a browser.
    reachable = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{reachable}:{port}/dash/?token={token}"
    # A headless box has no browser to open; that is not worth failing over.
    with contextlib.suppress(Exception):
        webbrowser.open(url)


async def resolve_approval_interactively(
    policy: PermissionPolicy,
    approval_id: str,
    request: ApprovalRequest,
    *,
    auto_yes: bool,
) -> None:
    """Resolve one pending approval: auto-allow, ask the terminal, or deny.

    ``policy.resolve()`` must be called whatever happens here — the task
    waiting on this approval blocks on an event that only that call sets,
    so an uncaught exception would hang it forever rather than fail it.
    """
    from jarvis.core.models import ApprovalDecision

    if auto_yes:
        print("  --yes given: approving automatically")
        policy.resolve(approval_id, ApprovalDecision.ALLOW)
        return
    try:
        answer = await asyncio.to_thread(
            input, f"  Approve {request.tool} {request.arguments}? [y/N] "
        )
    except EOFError:
        # No terminal to ask (stdin closed or redirected): deny, not hang.
        print("  no input available to answer this - denying by default")
        answer = "n"
    decision = (
        ApprovalDecision.ALLOW
        if answer.strip().lower() in {"y", "yes"}
        else ApprovalDecision.DENY
    )
    policy.resolve(approval_id, decision)


async def _run_once(args: argparse.Namespace) -> int:
    from jarvis.core.events import Event, EventType
    from jarvis.core.models import TaskStatus

    runtime = _build(args)
    if runtime is None:
        return 1

    async def on_event(event: Event) -> None:
        print(f"  [{event.type.value}] {event.message}")
        if event.type is EventType.APPROVAL_REQUIRED:
            approval_id = str(event.data.get("approval_id"))
            request = runtime.policy.get(approval_id)
            if request is not None:
                await resolve_approval_interactively(
                    runtime.policy, approval_id, request, auto_yes=args.yes
                )

    runtime.bus.subscribe(on_event)
    try:
        task = await runtime.orchestrator.submit(args.request)
        task = await runtime.orchestrator.wait(task.id)
    finally:
        await runtime.close()

    print()
    if task.status is TaskStatus.COMPLETED:
        print(task.result or "Done.")
        return 0
    print(f"Task {task.status.value}: {task.error or ''}", file=sys.stderr)
    return 1


async def _check_brain(args: argparse.Namespace) -> int:
    """Report which provider is configured and prove it is usable.

    Deliberately does not build the whole runtime: this must stay useful
    when the reason nothing works is the model connection itself.
    """
    import logging

    from jarvis.brain import GeminiBrain, build_brain
    from jarvis.config import load_config, load_secrets
    from jarvis.core.errors import BrainError

    # This command prints its own diagnosis; the library's log line for a
    # failed call would only say the same thing twice, less clearly.
    logging.getLogger("jarvis").setLevel(logging.CRITICAL)

    config = load_config(args.config)
    print(f"provider: {config.llm.provider}")
    print(f"model:    {config.llm.model}")

    try:
        brain = build_brain(config.llm, load_secrets())
    except RuntimeError as exc:  # a required secret is missing
        print(f"Cannot use this provider: {exc}", file=sys.stderr)
        return 1

    if not isinstance(brain, GeminiBrain):
        print("endpoint: Anthropic API (hosted)")
        return 0

    print(f"endpoint: {config.llm.base_url}")
    try:
        # A model lookup, not a generation: proves the key and the model
        # name without spending any of the free tier's request budget.
        info = await brain.describe_model()
    except BrainError as exc:
        print(f"NOT usable: {exc}", file=sys.stderr)
        return 1
    finally:
        await brain.aclose()

    name = info.get("displayName") or config.llm.model
    limit = info.get("inputTokenLimit")
    detail = f" ({limit:,} token context)" if isinstance(limit, int) else ""
    print(f"connected: yes — {name}{detail}")
    print("Jarvis is ready.")
    return 0


def _list_tools(args: argparse.Namespace) -> int:
    from jarvis.app.runtime import build_runtime
    from jarvis.brain.base import BrainResponse

    class _Offline:
        async def complete(self, **_: object) -> BrainResponse:
            return BrainResponse(text="offline")

    runtime = build_runtime(args.config, brain=_Offline())
    for tool in sorted(runtime.registry, key=lambda t: t.name):
        marker = f"  [confirm: {tool.risk_category}]" if tool.risk_category else ""
        print(f"{tool.name:24s} {tool.description.splitlines()[0]}{marker}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
