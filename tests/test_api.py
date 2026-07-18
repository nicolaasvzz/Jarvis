"""Tests for the API server, driven through a real (scripted-brain) runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from jarvis.agent import Orchestrator
from jarvis.api import create_app
from jarvis.config.schema import AgentConfig, SecurityConfig
from jarvis.core.events import EventBus
from jarvis.files import FileManager, build_file_tools
from jarvis.memory import MemoryStore
from jarvis.notifications import InMemoryChannel, NotificationService
from jarvis.planner import Planner
from jarvis.security import PermissionPolicy, TokenAuthenticator
from jarvis.tools import ToolManager, ToolRegistry
from tests.helpers import ScriptedBrain

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(tmp_path: Path, brain: ScriptedBrain):
    fm = FileManager(tmp_path / "sandbox")
    registry = ToolRegistry()
    registry.register_all(build_file_tools(fm))
    policy = PermissionPolicy(SecurityConfig(require_confirmation=["delete_files"]))
    bus = EventBus()
    tools = ToolManager(registry, policy, bus)
    planner = Planner(brain, registry, policy)
    memory = MemoryStore(tmp_path / "memory.db")
    orchestrator = Orchestrator(
        brain=brain,
        planner=planner,
        tools=tools,
        memory=memory,
        bus=bus,
        config=AgentConfig(),
    )
    live = InMemoryChannel()
    notifications = NotificationService(bus, [live])
    app = create_app(
        orchestrator=orchestrator,
        policy=policy,
        notifications=notifications,
        live_channel=live,
        files=fm,
        authenticator=TokenAuthenticator(SecretStr(TOKEN)),
        log_file=None,
    )
    return app, orchestrator, policy, fm


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://jarvis"
    )


async def test_health_is_open_but_everything_else_needs_auth(tmp_path: Path) -> None:
    app, *_ = make_app(tmp_path, ScriptedBrain([]))
    async with client_for(app) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/tasks")).status_code == 401
        bad = await client.get("/tasks", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        ok = await client.get("/tasks", headers=AUTH)
        assert ok.status_code == 200


async def test_query_param_token_also_works(tmp_path: Path) -> None:
    app, *_ = make_app(tmp_path, ScriptedBrain([]))
    async with client_for(app) as client:
        assert (await client.get(f"/tasks?token={TOKEN}")).status_code == 200


async def test_submit_task_runs_and_reports_progress(tmp_path: Path) -> None:
    plan = json.dumps(
        {
            "goal": "Write a note",
            "steps": [
                {
                    "description": "Write it",
                    "tool": "write_file",
                    "arguments": {"path": "note.txt", "content": "hello"},
                }
            ],
        }
    )
    app, orchestrator, _, fm = make_app(
        tmp_path, ScriptedBrain([plan, "Wrote note.txt for you."])
    )
    async with client_for(app) as client:
        created = await client.post(
            "/tasks", json={"request": "write a note"}, headers=AUTH
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        await orchestrator.wait(task_id)

        detail = await client.get(f"/tasks/{task_id}", headers=AUTH)
        body = detail.json()
        assert body["status"] == "completed"
        assert body["result"] == "Wrote note.txt for you."
        assert body["steps"][0]["status"] == "completed"
        assert fm.read_text("note.txt") == "hello"

        listing = await client.get("/tasks", headers=AUTH)
        assert len(listing.json()) == 1

        notifications = await client.get("/notifications", headers=AUTH)
        messages = [n["message"] for n in notifications.json()]
        assert any("Wrote note.txt" in m for m in messages)


async def test_approval_flow_over_http(tmp_path: Path) -> None:
    plan = json.dumps(
        {
            "goal": "Delete a file",
            "steps": [
                {
                    "description": "Write victim",
                    "tool": "write_file",
                    "arguments": {"path": "victim.txt", "content": "x"},
                },
                {
                    "description": "Delete victim",
                    "tool": "delete_path",
                    "arguments": {"path": "victim.txt"},
                },
            ],
        }
    )
    app, orchestrator, policy, _ = make_app(
        tmp_path, ScriptedBrain([plan, "Deleted after approval."])
    )
    async with client_for(app) as client:
        created = await client.post(
            "/tasks", json={"request": "delete victim.txt"}, headers=AUTH
        )
        task_id = created.json()["id"]

        # Wait for the approval to appear via the API.
        approval_id = None
        for _ in range(300):
            await asyncio.sleep(0.01)
            listing = (await client.get("/approvals", headers=AUTH)).json()
            if listing:
                approval_id = listing[0]["id"]
                break
        assert approval_id, "approval request never appeared"

        decided = await client.post(
            f"/approvals/{approval_id}", json={"decision": "allow"}, headers=AUTH
        )
        assert decided.status_code == 200

        await orchestrator.wait(task_id)
        final = (await client.get(f"/tasks/{task_id}", headers=AUTH)).json()
        assert final["status"] == "completed"

        # Deciding again conflicts; unknown id is 404.
        again = await client.post(
            f"/approvals/{approval_id}", json={"decision": "deny"}, headers=AUTH
        )
        assert again.status_code == 409
        missing = await client.post(
            "/approvals/appr-nope", json={"decision": "allow"}, headers=AUTH
        )
        assert missing.status_code == 404


async def test_cancel_endpoint(tmp_path: Path) -> None:
    # Planner reply that never comes lets the task sit in PLANNING.
    class HangingBrain:
        async def complete(self, **_: object):
            await asyncio.sleep(60)

    app, orchestrator, _, _ = make_app(tmp_path, ScriptedBrain([]))
    orchestrator._brain = HangingBrain()  # type: ignore[assignment]
    orchestrator._planner._brain = HangingBrain()  # type: ignore[attr-defined]

    async with client_for(app) as client:
        created = await client.post(
            "/tasks", json={"request": "never finishes"}, headers=AUTH
        )
        task_id = created.json()["id"]
        await asyncio.sleep(0.05)
        cancelled = await client.post(f"/tasks/{task_id}/cancel", headers=AUTH)
        assert cancelled.status_code == 200
        final = (await client.get(f"/tasks/{task_id}", headers=AUTH)).json()
        assert final["status"] == "cancelled"

        missing = await client.post("/tasks/task-nope/cancel", headers=AUTH)
        assert missing.status_code == 404


async def test_file_upload_lands_in_sandbox(tmp_path: Path) -> None:
    app, _, _, fm = make_app(tmp_path, ScriptedBrain([]))
    async with client_for(app) as client:
        response = await client.post(
            "/files/upload",
            files={"file": ("photo.jpg", b"jpegdata", "image/jpeg")},
            headers=AUTH,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["path"] == "uploads/photo.jpg"
        assert body["size"] == 8
        assert (fm.root / "uploads" / "photo.jpg").read_bytes() == b"jpegdata"


async def test_upload_filename_is_sanitised(tmp_path: Path) -> None:
    app, _, _, fm = make_app(tmp_path, ScriptedBrain([]))
    async with client_for(app) as client:
        response = await client.post(
            "/files/upload",
            files={"file": ("../../evil.sh", b"#!/bin/sh", "text/plain")},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.json()["path"] == "uploads/evil.sh"  # basename only
        assert not (tmp_path / "evil.sh").exists()


async def test_unknown_task_is_404(tmp_path: Path) -> None:
    app, *_ = make_app(tmp_path, ScriptedBrain([]))
    async with client_for(app) as client:
        assert (await client.get("/tasks/task-nope", headers=AUTH)).status_code == 404
