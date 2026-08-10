"""The ``jarvis`` command-line interface.

Subcommands:

* ``jarvis serve`` — start the API server the phone connects to.
* ``jarvis run "<request>"`` — run one task from the terminal, streaming
  progress and prompting for approvals interactively.
* ``jarvis brain`` — show the configured model provider and check it works.
* ``jarvis tools`` — list the tools available on this machine.
* ``jarvis token`` — generate a strong token for ``JARVIS_API_TOKEN``.

argparse is used deliberately: zero extra dependencies for the entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets as pysecrets
import sys

from jarvis import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis", description="Jarvis — personal AI desktop assistant"
    )
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    parser.add_argument(
        "--config", default=None, help="Path to a config YAML file", metavar="PATH"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the API server for the phone client")
    serve.add_argument("--host", default=None, help="Override api.host")
    serve.add_argument("--port", type=int, default=None, help="Override api.port")

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
    if args.command == "serve":
        return _serve(args)
    if args.command == "run":
        return asyncio.run(_run_once(args))
    if args.command == "phone":
        return asyncio.run(_run_phone(args))
    if args.command == "brain":
        return asyncio.run(_check_brain(args))
    if args.command == "tools":
        return _list_tools(args)
    return 2  # pragma: no cover - argparse enforces the choices


async def _run_phone(args: argparse.Namespace) -> int:
    from jarvis.app.runtime import build_runtime

    runtime = build_runtime(args.config)
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


def _serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            'The API server needs the api extra: pip install "jarvis-assistant[api]"',
            file=sys.stderr,
        )
        return 1

    from jarvis.api import create_app
    from jarvis.app.runtime import build_runtime
    from jarvis.security import TokenAuthenticator

    runtime = build_runtime(args.config)
    try:
        token = runtime.secrets.require("jarvis_api_token")
    except RuntimeError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        print("Generate one with: jarvis token", file=sys.stderr)
        return 1

    app = create_app(
        orchestrator=runtime.orchestrator,
        policy=runtime.policy,
        notifications=runtime.notifications,
        live_channel=runtime.live_channel,
        files=runtime.files,
        authenticator=TokenAuthenticator(token),
        log_file=runtime.log_file,
    )
    host = args.host or runtime.config.api.host
    port = args.port or runtime.config.api.port
    print(f"Jarvis API listening on http://{host}:{port} — workspace: {runtime.files.root}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


async def _run_once(args: argparse.Namespace) -> int:
    from jarvis.app.runtime import build_runtime
    from jarvis.core.events import Event, EventType
    from jarvis.core.models import ApprovalDecision, TaskStatus

    runtime = build_runtime(args.config)

    async def on_event(event: Event) -> None:
        print(f"  [{event.type.value}] {event.message}")
        if event.type is EventType.APPROVAL_REQUIRED:
            approval_id = str(event.data.get("approval_id"))
            request = runtime.policy.get(approval_id)
            if request is None:
                return
            if args.yes:
                print("  --yes given: approving automatically")
                runtime.policy.resolve(approval_id, ApprovalDecision.ALLOW)
                return
            answer = await asyncio.to_thread(
                input, f"  Approve {request.tool} {request.arguments}? [y/N] "
            )
            decision = (
                ApprovalDecision.ALLOW
                if answer.strip().lower() in {"y", "yes"}
                else ApprovalDecision.DENY
            )
            runtime.policy.resolve(approval_id, decision)

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

    from jarvis.brain import OllamaBrain, build_brain
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

    if not isinstance(brain, OllamaBrain):
        print("endpoint: Anthropic API (hosted)")
        return 0

    print(f"endpoint: {config.llm.base_url}")
    try:
        models = await brain.list_models()
    except BrainError as exc:
        print(f"NOT reachable: {exc}", file=sys.stderr)
        return 1
    finally:
        await brain.aclose()

    print(f"connected: yes — {len(models)} model(s) downloaded")
    # "qwen3" in config means "qwen3:latest" to Ollama.
    wanted = {config.llm.model}
    if ":" not in config.llm.model:
        wanted.add(f"{config.llm.model}:latest")
    if wanted & set(models):
        print(f"model {config.llm.model!r} is available — Jarvis is ready.")
        return 0
    print(
        f"model {config.llm.model!r} is NOT downloaded "
        f"(have: {', '.join(models) or 'none'}).\n"
        f"Download it with: ollama pull {config.llm.model}",
        file=sys.stderr,
    )
    return 1


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
