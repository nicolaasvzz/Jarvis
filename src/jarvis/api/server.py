"""FastAPI application factory for the Jarvis API server.

The factory takes fully constructed components (orchestrator, policy,
notifications, file manager, authenticator) so it stays free of wiring
concerns and is trivially testable with fakes.

Authentication: every route except ``/health`` requires the shared token,
either as ``Authorization: Bearer <token>`` or — for the EventSource-based
live stream, which cannot set headers — as a ``?token=`` query parameter.

Note: this module deliberately does NOT use ``from __future__ import
annotations`` — FastAPI needs to evaluate dependency annotations that
reference closure locals (``bearer``), which postponed evaluation breaks.
"""

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from jarvis.agent.orchestrator import Orchestrator
from jarvis.api.schemas import (
    ApprovalOut,
    DecideApprovalRequest,
    MessageOut,
    NotificationOut,
    SubmitTaskRequest,
    TaskOut,
    UploadOut,
)
from jarvis.config.schema import DashboardConfig
from jarvis.core.models import ApprovalDecision
from jarvis.dashboard.hub import DashboardHub
from jarvis.files.operations import FileManager
from jarvis.logging import get_logger
from jarvis.notifications import InMemoryChannel, NotificationService
from jarvis.security.auth import AuthError, TokenAuthenticator
from jarvis.security.permissions import PermissionPolicy
from jarvis.voice.service import VoiceService

_log = get_logger(__name__)

_UPLOADS_DIR = "uploads"


def create_app(
    *,
    orchestrator: Orchestrator,
    policy: PermissionPolicy,
    notifications: NotificationService,
    live_channel: InMemoryChannel,
    files: FileManager,
    authenticator: TokenAuthenticator,
    log_file: Path | None = None,
    hub: DashboardHub | None = None,
    dashboard: DashboardConfig | None = None,
    voice: VoiceService | None = None,
) -> FastAPI:
    """Build the FastAPI app around already-wired components.

    The dashboard is mounted only when a ``hub`` is supplied, so the API can
    still be run headless — on a server, or by tests — without dragging in
    the web assets.
    """
    app = FastAPI(title="Jarvis", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)

    def require_auth(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer)
        ],
        token: Annotated[str | None, Query()] = None,
    ) -> None:
        presented = credentials.credentials if credentials else token
        try:
            authenticator.verify(presented)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    auth = Depends(require_auth)

    # -- health ---------------------------------------------------------
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # -- tasks ----------------------------------------------------------
    @app.post("/tasks", response_model=TaskOut, dependencies=[auth], status_code=201)
    async def submit_task(body: SubmitTaskRequest) -> TaskOut:
        task = await orchestrator.submit(body.request)
        return TaskOut.from_task(task)

    @app.get("/tasks", response_model=list[TaskOut], dependencies=[auth])
    def list_tasks() -> list[TaskOut]:
        return [TaskOut.from_task(t) for t in orchestrator.all_tasks()]

    @app.get("/tasks/{task_id}", response_model=TaskOut, dependencies=[auth])
    def get_task(task_id: str) -> TaskOut:
        task = orchestrator.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return TaskOut.from_task(task)

    @app.post(
        "/tasks/{task_id}/cancel", response_model=MessageOut, dependencies=[auth]
    )
    async def cancel_task(task_id: str) -> MessageOut:
        if orchestrator.get(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        cancelled = await orchestrator.cancel(task_id)
        return MessageOut(
            detail="Task cancelled." if cancelled else "Task already finished."
        )

    # -- approvals --------------------------------------------------------
    @app.get("/approvals", response_model=list[ApprovalOut], dependencies=[auth])
    def pending_approvals() -> list[ApprovalOut]:
        return [ApprovalOut.from_request(r) for r in policy.pending()]

    @app.post(
        "/approvals/{approval_id}", response_model=MessageOut, dependencies=[auth]
    )
    def decide_approval(approval_id: str, body: DecideApprovalRequest) -> MessageOut:
        try:
            policy.resolve(approval_id, ApprovalDecision(body.decision))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return MessageOut(detail=f"Approval {body.decision}ed.")

    # -- notifications ----------------------------------------------------
    @app.get(
        "/notifications", response_model=list[NotificationOut], dependencies=[auth]
    )
    def recent_notifications(limit: int = 50) -> list[NotificationOut]:
        return [
            NotificationOut(
                type=n.type,
                message=n.message,
                task_id=n.task_id,
                created_at=n.created_at,
            )
            for n in notifications.history(limit)
        ]

    @app.get("/events", dependencies=[auth])
    async def event_stream() -> StreamingResponse:
        """Live notification feed as server-sent events."""

        async def generate() -> AsyncIterator[str]:
            with live_channel.stream() as subscription:
                async for notification in subscription:
                    payload = json.dumps(
                        {
                            "type": notification.type,
                            "message": notification.message,
                            "task_id": notification.task_id,
                            "created_at": notification.created_at.isoformat(),
                        }
                    )
                    yield f"data: {payload}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # -- files ------------------------------------------------------------
    @app.post("/files/upload", response_model=UploadOut, dependencies=[auth])
    async def upload_file(file: UploadFile) -> UploadOut:
        name = os.path.basename(file.filename or "upload.bin")
        if not name or name in {".", ".."}:
            raise HTTPException(status_code=400, detail="Invalid filename.")
        data = await file.read()
        target = f"{_UPLOADS_DIR}/{name}"
        files.write_bytes(target, data)
        _log.info("file uploaded", extra={"path": target, "size": len(data)})
        return UploadOut(path=target, size=len(data))

    # -- logs ---------------------------------------------------------------
    @app.get("/logs", dependencies=[auth])
    def tail_logs(limit: int = 100) -> list[dict[str, object]]:
        if log_file is None or not log_file.exists():
            return []
        lines = log_file.read_text(encoding="utf-8").splitlines()[-limit:]
        entries: list[dict[str, object]] = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    # -- dashboard ----------------------------------------------------------
    if hub is not None:
        from jarvis.dashboard.routes import WEB_ROOT, build_router

        app.include_router(
            build_router(
                hub=hub,
                orchestrator=orchestrator,
                files=files,
                config=dashboard or DashboardConfig(),
                voice=voice,
                require_auth=require_auth,
            )
        )
        if WEB_ROOT.is_dir():
            app.mount(
                "/dash/static",
                StaticFiles(directory=WEB_ROOT),
                name="dashboard-static",
            )

        @app.get("/", include_in_schema=False)
        def home() -> RedirectResponse:
            """Send a bare visit to the dashboard rather than a bare 404."""
            return RedirectResponse(url="/dash/")

    return app
