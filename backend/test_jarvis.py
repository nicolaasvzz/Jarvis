"""Tests for jarvis.py, against a fake Gemini — no key, no network, no quota.

    pip install pytest pytest-asyncio
    python -m pytest test_jarvis.py
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import jarvis as J


def reply(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)},
                            "finishReason": "STOP"}]}


class FakeWeb:
    """Answers Gemini with scripted replies, and anything else from `pages`."""

    def __init__(self, *gemini: Any, pages: dict[str, Any] | None = None) -> None:
        self.gemini = list(gemini)
        self.pages = pages or {}
        self.gemini_bodies: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "generativelanguage.googleapis.com":
            if request.method == "GET":
                return httpx.Response(200, json={"displayName": "Fake Flash-Lite"})
            self.gemini_bodies.append(json.loads(request.content))
            answer = self.gemini.pop(0)
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(200, json=answer)
        for fragment, body in self.pages.items():
            if fragment in str(request.url):
                return httpx.Response(200, json=body)
        return httpx.Response(404)


def make(tmp_path: Path, web: FakeWeb, **overrides: Any) -> J.Jarvis:
    settings = J.Settings(gemini_api_key="k", api_token="secret-token",
                          workspace=tmp_path / "ws", frontend=tmp_path / "frontend",
                          persona_file=tmp_path / "persona.md", voice="off", **overrides)
    http = httpx.AsyncClient(transport=httpx.MockTransport(web.handler))
    return J.Jarvis(settings, http)


async def finished(jarvis: J.Jarvis, task: J.Task, timeout: float = 20) -> J.Task:
    for _ in range(int(timeout * 20)):
        if task.status in {"completed", "failed"}:
            return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"task still {task.status}")


@pytest.mark.asyncio
async def test_a_plain_question_is_answered(tmp_path: Path) -> None:
    web = FakeWeb(reply({"text": "Good evening, sir."}))
    jarvis = make(tmp_path, web)
    task = await finished(jarvis, jarvis.submit("hello"))
    assert task.status == "completed"
    assert task.result == "Good evening, sir."
    body = web.gemini_bodies[0]
    assert body["contents"][-1] == {"role": "user", "parts": [{"text": "hello"}]}
    assert "thinkingConfig" not in body["generationConfig"]  # model default: fastest
    assert [f["type"] for f in jarvis.hub.history][-2:] == ["task.completed", "agent.idle"]


@pytest.mark.asyncio
async def test_the_tool_loop_returns_signatures_verbatim(tmp_path: Path) -> None:
    call = {"functionCall": {"id": "c1", "name": "get_weather", "args": {"location": "Paris"}},
            "thoughtSignature": "c2ln"}
    web = FakeWeb(
        reply(call),
        reply({"text": "Mild and clear in Paris."}),
        pages={
            "geocoding-api": {"results": [{"name": "Paris", "country": "France",
                                           "latitude": 48.8, "longitude": 2.3}]},
            "api.open-meteo.com": {"current": {"temperature_2m": 18, "weather_code": 0},
                                   "daily": {"time": ["2026-09-30"], "weather_code": [1],
                                             "temperature_2m_max": [20],
                                             "temperature_2m_min": [11],
                                             "precipitation_probability_max": [5],
                                             "sunrise": ["06:40"], "sunset": ["18:20"]}},
        },
    )
    jarvis = make(tmp_path, web)
    task = await finished(jarvis, jarvis.submit("weather in paris?"))
    assert task.result == "Mild and clear in Paris."
    second = web.gemini_bodies[1]["contents"]
    assert second[-2] == {"role": "model", "parts": [call]}  # thoughtSignature kept
    response = second[-1]["parts"][0]["functionResponse"]
    assert response["id"] == "c1"
    assert response["response"]["result"]["now"]["conditions"] == "clear sky"
    assert [s.status for s in task.steps] == ["completed"]


@pytest.mark.asyncio
async def test_commands_wait_for_approval_and_a_denial_is_reported(tmp_path: Path) -> None:
    call = {"functionCall": {"name": "run_command", "args": {"command": "echo hi"}}}
    web = FakeWeb(reply(call), reply({"text": "Understood, I won't."}))
    jarvis = make(tmp_path, web)
    task = jarvis.submit("say hi in the terminal")
    for _ in range(100):
        if jarvis.approvals:
            break
        await asyncio.sleep(0.05)
    (approval,) = jarvis.approvals.values()
    assert approval.arguments["command"] == "echo hi"
    jarvis.decide(approval.id, "deny")
    await finished(jarvis, task)
    result = web.gemini_bodies[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert result["response"]["result"]["denied"] is True
    assert task.steps[0].status == "failed"


@pytest.mark.asyncio
async def test_an_allowed_command_really_runs(tmp_path: Path) -> None:
    call = {"functionCall": {"name": "run_command", "args": {"command": "echo jarvis-was-here"}}}
    web = FakeWeb(reply(call), reply({"text": "Done."}))
    jarvis = make(tmp_path, web, auto_approve=True)
    await finished(jarvis, jarvis.submit("echo something"))
    result = web.gemini_bodies[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert result["result"]["exit_code"] == 0
    assert "jarvis-was-here" in result["result"]["output"]


@pytest.mark.asyncio
async def test_rate_limits_are_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(J.asyncio, "sleep", no_wait)
    busy = httpx.Response(429, json={"error": {"message": "slow down", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}]}})
    web = FakeWeb(busy, reply({"text": "Sorted."}))
    jarvis = make(tmp_path, web)
    assert await jarvis._gemini({}) == reply({"text": "Sorted."})


@pytest.mark.asyncio
async def test_a_bad_key_is_explained(tmp_path: Path) -> None:
    web = FakeWeb(httpx.Response(400, json={"error": {"message": "API key not valid."}}))
    jarvis = make(tmp_path, web)
    task = await finished(jarvis, jarvis.submit("hi"))
    assert task.status == "failed"
    assert "GEMINI_API_KEY" in (task.error or "")


@pytest.mark.asyncio
async def test_files_outside_the_workspace_and_local_pages_are_off_limits(
    tmp_path: Path,
) -> None:
    jarvis = make(tmp_path, FakeWeb())
    task = J.Task(request="x")
    escaped = await jarvis.read_workspace_file(task, "../persona.md")
    assert "outside" in escaped["error"]
    local = await jarvis.read_webpage(task, "http://127.0.0.1:8765/tasks")
    assert "off limits" in local["error"]


@pytest.mark.asyncio
async def test_the_api_needs_the_token_and_serves_the_dashboard(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<title>dash</title>", encoding="utf-8")
    (frontend / "config.js").write_text('window.HUD_CONFIG = { appName: "EDITH" };',
                                        encoding="utf-8")
    jarvis = make(tmp_path, FakeWeb(reply({"text": "At once."})))
    app = J.create_app(jarvis)
    auth = {"Authorization": "Bearer secret-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://jarvis") as client:
        assert (await client.get("/dash/api/snapshot")).status_code == 401
        snapshot = (await client.get("/dash/api/snapshot", headers=auth)).json()
        assert [a["name"] for a in snapshot["agents"]][0] == "Jarvis"
        assert (await client.get("/dash/")).status_code == 200
        config = (await client.get("/dash/config.js")).text
        assert 'appName: "EDITH"' in config and config.rstrip().endswith('{ server: "" });')
        heard = (await client.post("/dash/api/command", headers=auth,
                                   json={"text": "Jarvis, status report"})).json()
        assert heard["command"] == "status report" and heard["submitted"]
        task = await finished(jarvis, jarvis.tasks[heard["task_id"]])
        assert task.result == "At once."
        missing = await client.post("/approvals/a-nope", headers=auth,
                                    json={"decision": "allow"})
        assert missing.status_code == 404


def test_settings_come_from_env_with_real_env_winning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nGEMINI_API_KEY='abc'\nGEMINI_MODEL=\nAUTO_APPROVE=true\n"
                   "JARVIS_PORT=9000\n", encoding="utf-8")
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "AUTO_APPROVE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JARVIS_PORT", "9100")
    settings = J.Settings.load(env)
    assert settings.gemini_api_key == "abc"
    assert settings.gemini_model == "gemini-3.5-flash-lite"  # blank means default
    assert settings.auto_approve is True
    assert settings.port == 9100
    J.save_env_value(env, "JARVIS_API_TOKEN", "tok")
    assert J.read_env_file(env)["JARVIS_API_TOKEN"] == "tok"


@pytest.mark.asyncio
async def test_the_allow_button_releases_a_waiting_command(tmp_path: Path) -> None:
    call = {"functionCall": {"name": "run_command", "args": {"command": "echo allowed"}}}
    jarvis = make(tmp_path, FakeWeb(reply(call), reply({"text": "Ran it."})))
    auth = {"Authorization": "Bearer secret-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        heard = (await client.post("/dash/api/command", headers=auth,
                                   json={"text": "run echo"})).json()
        pending: list[dict[str, Any]] = []
        for _ in range(100):
            pending = (await client.get("/approvals", headers=auth)).json()
            if pending:
                break
            await asyncio.sleep(0.05)
        assert pending[0]["arguments"]["command"] == "echo allowed"
        decided = await client.post(f"/approvals/{pending[0]['id']}", headers=auth,
                                    json={"decision": "allow"})
        assert decided.status_code == 200
        task = await finished(jarvis, jarvis.tasks[heard["task_id"]])
    assert task.result == "Ran it."
    assert task.steps[0].status == "completed"


def test_a_console_that_cannot_print_never_breaks_an_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_print(*_: Any, **__: Any) -> None:
        raise UnicodeEncodeError("cp1252", "\u2192", 0, 1, "can't encode")

    monkeypatch.setattr("builtins.print", broken_print)
    hub = J.Hub(voice_enabled=False)
    frame = hub.emit("agent.assigned", "Terminal 1 \u2192 build")
    assert frame["message"] == "Terminal 1 \u2192 build"
    assert list(hub.history) == [frame]


class FakeProcess:
    """Stands in for a terminal window, so tests never open one."""

    def poll(self) -> int | None:
        return None


@pytest.mark.asyncio
async def test_a_window_job_is_marked_done_by_its_done_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_sleep = asyncio.sleep

    async def quick(_: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(J.asyncio, "sleep", quick)
    jarvis = make(tmp_path, FakeWeb())
    folder = tmp_path / "ws" / "jobs" / "demo"
    folder.mkdir(parents=True)
    task = J.Task(request="Terminal: build", status="running")
    task.steps.append(J.Step(description="build", tool="terminal"))
    window = jarvis.windows[0]
    window.move("library", "working", task, "terminal", "build")
    job = J.Job(command="build", folder=folder, task=task, process=FakeProcess(),  # type: ignore[arg-type]
                agent=window)
    (folder / "output.txt").write_text("all good\n", encoding="utf-8")
    (folder / ".done").write_text("0\n", encoding="utf-8")
    await jarvis._watch(job)
    assert job.status == "completed" and task.status == "completed"
    assert window.status == "idle"
    assert jarvis.hub.history[-2]["files"] == {"action": "write", "paths": ["jobs/demo/output.txt"]}
