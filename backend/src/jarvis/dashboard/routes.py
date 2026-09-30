"""HTTP surface for the dashboard.

Mounted under ``/dash`` on the same server, and therefore in the same
process, as the rest of the API. That is the whole point: live agent state
lives in the Orchestrator's memory, so a dashboard in a second process could
only ever show what had already been written to disk.

Authentication matches the rest of the API — every data route needs the
token. Only the static shell is public, because a page with no data in it
gives nothing away, and something has to load before a token can be entered.

The pages themselves are the separate ``frontend/`` folder, mounted by
:func:`jarvis.api.server.create_app` when it is present. This router only
adds what the backend has to answer itself: the frontend's ``config.js``
(pointing it back at this same server) and a plain explanation when the
frontend is not installed here.

Note: like :mod:`jarvis.api.server`, this module deliberately does NOT use
``from __future__ import annotations``; FastAPI must evaluate the dependency
annotations that reference closure locals.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from jarvis.agent.orchestrator import Orchestrator
from jarvis.config.schema import DashboardConfig
from jarvis.core.errors import VoiceError
from jarvis.dashboard.hub import DashboardHub
from jarvis.files.operations import FileManager
from jarvis.logging import get_logger
from jarvis.voice.service import VoiceService

_log = get_logger(__name__)

#: Served in place of the frontend's own config.js: pages that came from
#: this server talk back to this server, whatever the file on disk says.
_SAME_ORIGIN_CONFIG = 'window.JARVIS_CONFIG = { server: "" };\n'

_NO_FRONTEND = """<!doctype html>
<meta charset="utf-8"><title>Jarvis API</title>
<body style="font-family:system-ui;background:#05080c;color:#cbd5e1;padding:40px">
<h1 style="color:#22d3ee;letter-spacing:.3em">JARVIS</h1>
<p>This backend is running, but the web frontend is not installed next to it.</p>
<p>Either put the <code>frontend</code> folder beside this <code>backend</code>
folder (or set <code>dashboard.web_root</code> to it) and restart, or open the
frontend on its own and connect it to this server.</p>
<p>The API itself is here: <a style="color:#22d3ee" href="/docs">/docs</a></p>
</body>
"""

#: Heartbeat interval for the event stream. Without it a proxy or a sleeping
#: laptop can hold a dead connection open indefinitely; a comment line costs
#: nothing and lets EventSource notice and reconnect.
_KEEPALIVE_SECONDS = 15.0


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class CommandRequest(BaseModel):
    """Something the user said or typed for Jarvis to act on."""

    text: str = Field(min_length=1, max_length=8000)
    #: When true the wake word is stripped and the rest run as a task.
    submit: bool = True


class HeardResponse(BaseModel):
    text: str
    addressed: bool
    command: str
    submitted: bool = False
    task_id: str | None = None
    confidence: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


def build_router(
    *,
    hub: DashboardHub,
    orchestrator: Orchestrator,
    files: FileManager,
    config: DashboardConfig,
    voice: VoiceService | None,
    require_auth: Any,
    web_root: Path | None = None,
) -> APIRouter:
    """Build the dashboard router around already-wired components.

    ``web_root`` is the frontend folder the server mounts at ``/dash``, or
    ``None`` when this backend runs without one.
    """
    router = APIRouter(prefix="/dash", tags=["dashboard"])
    auth = Depends(require_auth)

    # -- the page shell (public, no data) ---------------------------------
    @router.get("/config.js", include_in_schema=False)
    def frontend_config() -> Response:
        return Response(
            content=_SAME_ORIGIN_CONFIG,
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    # The pages used to live at extension-less paths; keep old links working.
    @router.get("/files", include_in_schema=False)
    def files_page() -> RedirectResponse:
        return RedirectResponse(url="/dash/files.html")

    @router.get("/office", include_in_schema=False)
    def office_page() -> RedirectResponse:
        return RedirectResponse(url="/dash/office.html")

    if web_root is None:

        @router.get("/", include_in_schema=False)
        def no_frontend() -> HTMLResponse:
            return HTMLResponse(_NO_FRONTEND)

    # -- live state -------------------------------------------------------
    @router.get("/api/snapshot", dependencies=[auth])
    def snapshot() -> dict[str, Any]:
        """Everything a page needs to render itself from cold."""
        return hub.snapshot()

    @router.get("/api/stream", dependencies=[auth])
    async def stream() -> StreamingResponse:
        """Enriched events as server-sent events."""

        async def generate() -> AsyncIterator[str]:
            with hub.stream() as subscription:
                while True:
                    try:
                        frame = await asyncio.wait_for(
                            subscription.__anext__(), timeout=_KEEPALIVE_SECONDS
                        )
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(frame, default=str)}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Stops nginx and friends buffering an infinite response.
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/api/tree", dependencies=[auth])
    def file_tree(depth: int | None = None, limit: int | None = None) -> dict[str, Any]:
        """The workspace as nodes and links for the file constellation."""
        return _build_tree(
            files,
            depth=depth or config.file_tree_depth,
            limit=limit or config.file_tree_max_nodes,
        )

    @router.get("/api/stats", dependencies=[auth])
    def stats() -> dict[str, Any]:
        """Machine vitals for the status panel."""
        return _system_stats()

    # -- acting -----------------------------------------------------------
    @router.post("/api/command", response_model=HeardResponse, dependencies=[auth])
    async def command(body: CommandRequest) -> HeardResponse:
        """Run typed text as a command, applying the same wake-word rules."""
        transcript = (
            voice.interpret(body.text)
            if voice is not None
            else _plain_transcript(body.text)
        )
        return await _maybe_submit(orchestrator, transcript, submit=body.submit)

    @router.post("/api/listen", response_model=HeardResponse, dependencies=[auth])
    async def listen(
        audio: Annotated[UploadFile, File()],
        submit: Annotated[bool, Form()] = True,
    ) -> HeardResponse:
        """Transcribe a recording from the browser, locally."""
        if voice is None or not voice.can_listen:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Listening is not available. Enable voice.listen_enabled and "
                    'install it with: pip install "jarvis-assistant[listen]"'
                ),
            )
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="The recording was empty.")
        suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
        try:
            transcript = await voice.listen(data, suffix=suffix)
        except VoiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return await _maybe_submit(orchestrator, transcript, submit=submit)

    @router.post("/api/speak", dependencies=[auth])
    async def speak(body: SpeakRequest) -> Response:
        """Render text in Jarvis's voice and return the audio."""
        if voice is None or not voice.can_speak:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Speech is not available. Enable voice.enabled and install "
                    'it with: pip install "jarvis-assistant[voice]"'
                ),
            )
        try:
            spoken = await voice.speak(body.text)
        except VoiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(
            content=spoken.audio,
            media_type=spoken.mime,
            headers={"Cache-Control": "no-store", "X-Jarvis-Voice": spoken.voice},
        )

    return router


def _plain_transcript(text: str) -> Any:
    """Stand-in transcript for when the voice module is switched off."""
    from jarvis.voice.base import Transcript

    return Transcript(text=text, addressed=True, command=text.strip())


async def _maybe_submit(
    orchestrator: Orchestrator, transcript: Any, *, submit: bool
) -> HeardResponse:
    """Turn a transcript into a task, when it was actually addressed to us."""
    should_run = submit and transcript.addressed and not transcript.is_empty
    task_id: str | None = None
    if should_run:
        task = await orchestrator.submit(transcript.command)
        task_id = task.id
    return HeardResponse(
        text=transcript.text,
        addressed=transcript.addressed,
        command=transcript.command,
        submitted=should_run,
        task_id=task_id,
        confidence=transcript.confidence,
        details=dict(transcript.details),
    )


# -- the file constellation ------------------------------------------------


def _build_tree(files: FileManager, *, depth: int, limit: int) -> dict[str, Any]:
    """Walk the workspace breadth-first into nodes and parent links.

    Breadth-first so that when the node budget runs out, what survives is the
    shallow shape of the workspace — the part worth looking at — rather than
    every file inside whichever folder happened to sort first.
    """
    nodes: list[dict[str, Any]] = [
        {
            "id": ".",
            "name": files.root.name or "workspace",
            "path": ".",
            "type": "dir",
            "size": None,
            "parent": None,
            "depth": 0,
        }
    ]
    links: list[dict[str, str]] = []
    truncated = False
    queue: list[tuple[str, int]] = [(".", 0)]

    while queue:
        path, level = queue.pop(0)
        if level >= depth:
            continue
        try:
            entries = files.list_dir(path)
        except Exception as exc:  # noqa: BLE001 - unreadable folders are normal
            _log.info("skipping folder", extra={"path": path, "error": str(exc)})
            continue
        for entry in entries:
            if len(nodes) >= limit:
                truncated = True
                queue.clear()
                break
            child = str(entry["path"])
            nodes.append(
                {
                    "id": child,
                    "name": entry["name"],
                    "path": child,
                    "type": entry["type"],
                    "size": entry["size"],
                    "parent": path,
                    "depth": level + 1,
                }
            )
            links.append({"source": path, "target": child})
            if entry["type"] == "dir":
                queue.append((child, level + 1))

    return {
        "root": str(files.root),
        "nodes": nodes,
        "links": links,
        "truncated": truncated,
        "depth": depth,
        "limit": limit,
    }


# -- machine vitals --------------------------------------------------------

#: psutil measures CPU as the change since the previous call, so the very
#: first one has nothing to compare against and always answers 0.0. Rather
#: than open the dashboard on a flat line, the first sample blocks briefly
#: for a real measurement; every one after that is free.
_cpu_primed = False


def _system_stats() -> dict[str, Any]:
    """CPU, memory, disk and battery, when psutil is installed.

    Absence is reported rather than faked: a status panel showing invented
    numbers would be worse than one showing none.
    """
    global _cpu_primed
    try:
        import psutil
    except ImportError:
        return {"available": False}

    stats: dict[str, Any] = {"available": True}
    # Each reading is independent; one unsupported sensor must not blank the
    # whole panel.
    try:
        stats["cpu"] = psutil.cpu_percent(interval=None if _cpu_primed else 0.08)
        _cpu_primed = True
    except Exception:  # noqa: BLE001
        stats["cpu"] = None
    try:
        memory = psutil.virtual_memory()
        stats["memory"] = {
            "percent": memory.percent,
            "used": memory.used,
            "total": memory.total,
        }
    except Exception:  # noqa: BLE001
        stats["memory"] = None
    try:
        disk = psutil.disk_usage(str(Path.home().anchor or Path.home()))
        stats["disk"] = {
            "percent": disk.percent,
            "used": disk.used,
            "total": disk.total,
        }
    except Exception:  # noqa: BLE001
        stats["disk"] = None
    try:
        battery = psutil.sensors_battery()
        stats["battery"] = (
            None
            if battery is None
            else {"percent": round(battery.percent), "plugged": battery.power_plugged}
        )
    except Exception:  # noqa: BLE001
        stats["battery"] = None
    try:
        counters = psutil.net_io_counters()
        stats["network"] = {"sent": counters.bytes_sent, "received": counters.bytes_recv}
    except Exception:  # noqa: BLE001
        stats["network"] = None
    return stats
