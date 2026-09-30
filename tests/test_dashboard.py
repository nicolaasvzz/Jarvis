"""Tests for the dashboard: event enrichment, the HTTP surface, and mapping."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from jarvis.agent import Orchestrator
from jarvis.api import create_app
from jarvis.config.schema import (
    AgentConfig,
    DashboardConfig,
    SecurityConfig,
    VoiceConfig,
)
from jarvis.core.events import EventBus, EventType
from jarvis.dashboard import DashboardHub
from jarvis.dashboard.mapping import file_action, paths_touched, room_for_tool
from jarvis.files import FileManager, build_file_tools
from jarvis.memory import MemoryStore
from jarvis.notifications import InMemoryChannel, NotificationService
from jarvis.planner import Planner
from jarvis.security import PermissionPolicy, TokenAuthenticator
from jarvis.tools import ToolManager, ToolRegistry
from jarvis.voice import VoiceService
from tests.helpers import ScriptedBrain

TOKEN = "dash-token-abc"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def build(tmp_path: Path, brain: ScriptedBrain | None = None, *, voice=None):
    brain = brain or ScriptedBrain([])
    fm = FileManager(tmp_path / "sandbox")
    registry = ToolRegistry()
    registry.register_all(build_file_tools(fm))
    policy = PermissionPolicy(SecurityConfig(require_confirmation=["delete_files"]))
    bus = EventBus()
    tools = ToolManager(registry, policy, bus)
    memory = MemoryStore(tmp_path / "memory.db")
    orchestrator = Orchestrator(
        brain=brain,
        planner=Planner(brain, registry, policy),
        tools=tools,
        memory=memory,
        bus=bus,
        config=AgentConfig(pool_size=3),
    )
    hub = DashboardHub(
        config=DashboardConfig(),
        orchestrator=orchestrator,
        policy=policy,
        files=fm,
        voice=voice,
    )
    hub.attach(bus)
    live = InMemoryChannel()
    app = create_app(
        orchestrator=orchestrator,
        policy=policy,
        notifications=NotificationService(bus, [live]),
        live_channel=live,
        files=fm,
        authenticator=TokenAuthenticator(SecretStr(TOKEN)),
        registry=registry,
        hub=hub,
        dashboard=DashboardConfig(),
        voice=voice,
    )
    return app, hub, bus, fm, orchestrator


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://jarvis"
    )


class TestMapping:
    def test_tools_map_to_the_room_the_work_belongs_in(self) -> None:
        assert room_for_tool("read_file") == "archives"
        assert room_for_tool("browser_click") == "web"
        assert room_for_tool("remember_fact") == "library"
        assert room_for_tool("capture_screen") == "observatory"
        assert room_for_tool("focus_window") == "workshop"

    def test_unknown_tools_still_get_a_room(self) -> None:
        """A tool added later must not vanish from the office."""
        assert room_for_tool("some_future_tool") == "workshop"
        assert room_for_tool(None) == "lobby"

    def test_file_actions_are_classified_for_colour(self) -> None:
        assert file_action("read_file") == "read"
        assert file_action("write_file") == "write"
        assert file_action("delete_path") == "delete"
        assert file_action("browser_click") is None

    def test_paths_are_normalised_to_forward_slashes(self) -> None:
        found = paths_touched({"path": "notes\\todo.txt"})
        assert found == ["notes/todo.txt"]

    def test_redaction_summaries_are_not_mistaken_for_paths(self) -> None:
        """Redacted content reads as '<412 chars>' — never a file."""
        assert paths_touched({"path": "a.txt", "content": "<412 chars>"}) == ["a.txt"]

    def test_glob_patterns_are_not_treated_as_files(self) -> None:
        """search_files(pattern='*.md') searches; it does not touch '*.md'."""
        assert paths_touched({"pattern": "*.md", "path": "reports"}) == ["reports"]


class TestHub:
    async def test_events_are_enriched_with_room_and_files(self, tmp_path: Path) -> None:
        _, hub, bus, *_ = build(tmp_path)
        await bus.emit(
            EventType.STEP_STARTED,
            "Running read_file",
            task_id="task-1",
            tool="read_file",
            agent_id="agent-01",
            arguments={"path": "notes/todo.txt"},
        )
        frame = hub.history()[-1]
        assert frame["room"] == "archives"
        assert frame["tool"] == "read_file"
        assert frame["files"] == {"action": "read", "paths": ["notes/todo.txt"]}

    async def test_a_file_is_counted_once_per_operation(self, tmp_path: Path) -> None:
        """STEP_STARTED and STEP_COMPLETED carry the same arguments, so only
        one of them may attribute file activity — otherwise every read is
        drawn and listed twice."""
        _, hub, bus, *_ = build(tmp_path)
        for event in (EventType.STEP_STARTED, EventType.STEP_COMPLETED):
            await bus.emit(
                event,
                "read_file",
                task_id="t1",
                tool="read_file",
                agent_id="agent-01",
                arguments={"path": "notes/todo.txt"},
            )
        with_files = [f for f in hub.history() if f.get("files")]
        assert len(with_files) == 1
        assert with_files[0]["type"] == "step.started"

    async def test_agents_return_to_the_lobby_when_idle(self, tmp_path: Path) -> None:
        _, hub, bus, *_ = build(tmp_path)
        await bus.emit(
            EventType.STEP_STARTED, "x", tool="browser_open", agent_id="agent-01"
        )
        assert hub.history()[-1]["room"] == "web"
        await bus.emit(EventType.AGENT_IDLE, "free", agent={"id": "agent-01"})
        assert hub.history()[-1]["room"] == "lobby"

    async def test_history_is_bounded(self, tmp_path: Path) -> None:
        """A long-running Jarvis must not accumulate frames forever."""
        _, hub, bus, *_ = build(tmp_path)
        hub._history.clear()
        for index in range(40):
            await bus.emit(EventType.TASK_PROGRESS, f"step {index}")
        assert len(hub.history()) <= DashboardConfig().history_limit

    async def test_subscribers_receive_frames(self, tmp_path: Path) -> None:
        _, hub, bus, *_ = build(tmp_path)
        with hub.stream() as subscription:
            await bus.emit(EventType.TASK_CREATED, "hello", task_id="t1")
            frame = await subscription.__anext__()
        assert frame["message"] == "hello"
        assert frame["type"] == "task.created"

    async def test_speak_flag_follows_the_voice_config(self, tmp_path: Path) -> None:
        voice = VoiceService(
            VoiceConfig(speak_events=["task.completed"]),
            synthesizer=_FakeSynth(),
            transcriber=None,
        )
        _, hub, bus, *_ = build(tmp_path, voice=voice)
        await bus.emit(EventType.TASK_COMPLETED, "done")
        assert hub.history()[-1]["speak"] is True
        await bus.emit(EventType.STEP_STARTED, "running", tool="read_file")
        assert hub.history()[-1]["speak"] is False


class TestRoutes:
    async def test_pages_are_public_but_data_needs_the_token(
        self, tmp_path: Path
    ) -> None:
        app, *_ = build(tmp_path)
        async with client_for(app) as client:
            assert (await client.get("/dash/")).status_code == 200
            assert (await client.get("/dash/files")).status_code == 200
            assert (await client.get("/dash/office")).status_code == 200
            assert (await client.get("/dash/api/snapshot")).status_code == 401

    async def test_snapshot_describes_the_whole_system(self, tmp_path: Path) -> None:
        app, *_ = build(tmp_path)
        async with client_for(app) as client:
            body = (await client.get("/dash/api/snapshot", headers=AUTH)).json()
        assert len(body["agents"]) == 3
        assert {room["id"] for room in body["rooms"]} >= {"lobby", "archives", "web"}
        assert body["agents"][0]["room"] == "lobby"
        assert "settings" in body and "voice" in body

    async def test_tree_reports_the_workspace(self, tmp_path: Path) -> None:
        app, _, _, fm, _ = build(tmp_path)
        fm.write_text("notes/todo.txt", "buy milk")
        fm.write_text("photo.png", "x")
        async with client_for(app) as client:
            tree = (await client.get("/dash/api/tree", headers=AUTH)).json()
        paths = {node["path"] for node in tree["nodes"]}
        assert {".", "notes", "notes/todo.txt", "photo.png"} <= paths
        assert any(link["source"] == "notes" for link in tree["links"])

    async def test_tree_respects_the_node_budget(self, tmp_path: Path) -> None:
        app, _, _, fm, _ = build(tmp_path)
        for index in range(30):
            fm.write_text(f"bulk/file{index}.txt", "x")
        async with client_for(app) as client:
            tree = (await client.get("/dash/api/tree?limit=12", headers=AUTH)).json()
        assert len(tree["nodes"]) <= 12
        assert tree["truncated"] is True

    async def test_stats_reports_honestly_when_psutil_is_absent(
        self, tmp_path: Path
    ) -> None:
        app, *_ = build(tmp_path)
        async with client_for(app) as client:
            stats = (await client.get("/dash/api/stats", headers=AUTH)).json()
        assert "available" in stats

    async def test_typed_command_runs_as_a_task(self, tmp_path: Path) -> None:
        brain = ScriptedBrain(
            [json.dumps({"goal": "answer", "response": "Right away.", "steps": []})]
        )
        voice = VoiceService(
            VoiceConfig(wake_word_required=True), synthesizer=None, transcriber=None
        )
        app, _, _, _, orchestrator = build(tmp_path, brain, voice=voice)
        async with client_for(app) as client:
            body = (
                await client.post(
                    "/dash/api/command",
                    headers=AUTH,
                    json={"text": "Jarvis, say hello", "submit": True},
                )
            ).json()
        assert body["addressed"] is True
        assert body["command"] == "say hello"
        assert body["submitted"] is True
        await orchestrator.wait(body["task_id"])

    async def test_command_without_the_wake_word_is_not_run(
        self, tmp_path: Path
    ) -> None:
        voice = VoiceService(
            VoiceConfig(wake_word_required=True), synthesizer=None, transcriber=None
        )
        app, *_ = build(tmp_path, voice=voice)
        async with client_for(app) as client:
            body = (
                await client.post(
                    "/dash/api/command",
                    headers=AUTH,
                    json={"text": "the weather is nice", "submit": True},
                )
            ).json()
        assert body["addressed"] is False
        assert body["submitted"] is False

    async def test_speech_reports_clearly_when_unavailable(
        self, tmp_path: Path
    ) -> None:
        app, *_ = build(tmp_path, voice=None)
        async with client_for(app) as client:
            response = await client.post(
                "/dash/api/speak", headers=AUTH, json={"text": "hello"}
            )
        assert response.status_code == 503
        assert "voice" in response.json()["detail"].lower()

    async def test_static_assets_are_served(self, tmp_path: Path) -> None:
        app, *_ = build(tmp_path)
        async with client_for(app) as client:
            css = await client.get("/dash/static/css/hud.css")
            js = await client.get("/dash/static/js/hud.js")
        assert css.status_code == 200
        assert js.status_code == 200

    async def test_root_redirects_to_the_dashboard(self, tmp_path: Path) -> None:
        app, *_ = build(tmp_path)
        async with client_for(app) as client:
            response = await client.get("/")
        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/dash/"


class _FakeSynth:
    """Stands in for a real TTS engine so tests need no network or audio."""

    @property
    def voice(self) -> str:
        return "en-GB-RyanNeural"

    async def synthesize(self, text: str):
        from jarvis.voice.base import SpokenAudio

        return SpokenAudio(audio=b"AUDIO", mime="audio/mpeg", voice=self.voice, text=text)
