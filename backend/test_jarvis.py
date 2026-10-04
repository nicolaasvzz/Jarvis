"""Tests for jarvis.py, against a fake Gemini — no key, no network, no quota.

    pip install pytest pytest-asyncio
    python -m pytest test_jarvis.py
"""

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import httpx
import psutil
import pytest

import jarvis as J


def reply(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)},
                            "finishReason": "STOP"}]}


class FakeWeb:
    """Answers Gemini with scripted replies, and anything else from `pages`."""

    def __init__(self, *gemini: Any, pages: dict[str, Any] | None = None,
                 brains: dict[str, list[Any]] | None = None) -> None:
        self.gemini = list(gemini)
        self.pages = pages or {}
        self.gemini_bodies: list[dict[str, Any]] = []
        # Other brains, by host: scripted replies, and the requests they got.
        self.brains = brains or {}
        self.brain_requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host in self.brains:
            self.brain_requests.append(request)
            answer = self.brains[request.url.host].pop(0)
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(200, json=answer)
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
    # Every path in tmp_path: a test must never touch the real backend/data.
    overrides.setdefault("data_dir", tmp_path / "data")
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
    assert jarvis.hub.history[-1]["type"] == "task.completed"


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
        assert snapshot["terminals"] == [] and "agents" not in snapshot
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
    frame = hub.emit("terminal.opened", "Opened a terminal \u2192 build")
    assert frame["message"] == "Opened a terminal \u2192 build"
    assert list(hub.history) == [frame]


def mark(code: int) -> str:
    """The prompt's "a command finished" mark, as the real shells print it."""
    return f"\x1b]633;D;{code}\x07"


class FakeShell:
    """A pretend PowerShell in a pseudo-terminal: echoes keys and answers each
    line with "ran: <line>", so tests never start a real shell. Like ConPTY,
    it first asks what terminal it's talking to, and waits for the answer
    before showing its prompt."""

    PROMPT = "PS C:\\ws> "
    SLOW = 1.2  # seconds a "slow…" command takes

    def __init__(self, cwd: Path, cols: int, rows: int) -> None:
        self.cwd = cwd
        self.output: queue.Queue[str | None] = queue.Queue()
        self.output.put("\x1b[c")
        self.answered = False
        self.line = ""
        self.written: list[str] = []
        self.size = (rows, cols)
        self.alive = True
        self.exitstatus: int | None = None

    def read(self, size: int) -> str:
        try:
            chunk = self.output.get(timeout=0.05)
        except queue.Empty:
            return ""
        if chunk is None:
            raise EOFError
        return chunk

    lag = 0.0  # seconds before the echo appears, as with a real shell

    def write(self, data: str) -> int:
        if data == J.DA_REPLY and not self.answered:
            self.answered = True
            self.output.put(mark(0) + self.PROMPT)
            return len(data)
        self.written.append(data)
        if self.lag:
            threading.Timer(self.lag, self._answer, [data]).start()
        else:
            self._answer(data)
        return len(data)

    def _answer(self, data: str) -> None:
        for ch in data:
            if ch == "\r":
                line, self.line = self.line, ""
                if line == "exit":
                    self.exitstatus, self.alive = 0, False
                    self.output.put("\r\n")
                    self.output.put(None)
                elif line.startswith("slow"):  # takes a while, like a build
                    self.output.put(f"\r\nworking on {line}...")
                    threading.Timer(self.SLOW, self.output.put,
                                    [f"\r\ndone\r\n{mark(0)}{self.PROMPT}"]).start()
                else:  # "bad…" fails, anything else works
                    code = 1 if line.startswith("bad") else 0
                    self.output.put(f"\r\nran: {line}\r\n{mark(code)}{self.PROMPT}")
            elif ch == "\x03":
                self.output.put(f"^C\r\n{mark(1)}{self.PROMPT}")
            else:
                self.line += ch
                self.output.put(ch)

    def isalive(self) -> bool:
        return self.alive

    def terminate(self, force: bool = False) -> bool:
        self.alive = False
        self.output.put(None)
        return True


def with_fake_shells(jarvis: J.Jarvis) -> list[FakeShell]:
    shells: list[FakeShell] = []

    def spawn(cwd: Path, cols: int, rows: int) -> FakeShell:
        shells.append(FakeShell(cwd, cols, rows))
        return shells[-1]

    jarvis.spawn_shell = spawn  # type: ignore[assignment]
    return shells


def tool_result(web: FakeWeb, index: int) -> dict[str, Any]:
    reply_part = web.gemini_bodies[index]["contents"][-1]["parts"][0]["functionResponse"]
    result: dict[str, Any] = reply_part["response"]["result"]
    return result


@pytest.mark.asyncio
async def test_jarvis_opens_a_terminal_and_keeps_working_in_it(tmp_path: Path) -> None:
    opening = {"functionCall": {"name": "terminal_open", "args": {
        "title": "build", "purpose": "build the app", "command": "npm run build"}}}
    carrying_on = {"functionCall": {"name": "terminal_write", "args": {
        "terminal_id": "term-1", "text": "npm test", "wait_seconds": 5}}}
    web = FakeWeb(reply(opening), reply({"text": "Building."}),
                  reply(carrying_on), reply({"text": "Testing."}))
    jarvis = make(tmp_path, web, auto_approve=True)
    shells = with_fake_shells(jarvis)
    (tmp_path / "persona.md").write_text("Terminals:\n{{terminals}}", encoding="utf-8")
    await finished(jarvis, jarvis.submit("build the app"))
    opened = tool_result(web, 1)
    assert opened["terminal_id"] == "term-1" and opened["state"] == "at its prompt"
    assert "ran: npm run build" in opened["screen"]

    await finished(jarvis, jarvis.submit("now run the tests in the build terminal"))
    # The next turn's system prompt carries each terminal's purpose and history.
    persona = web.gemini_bodies[2]["systemInstruction"]["parts"][0]["text"]
    assert '`term-1` "build"' in persona and "build the app" in persona
    assert "jarvis: npm run build" in persona
    written = tool_result(web, 3)
    assert "ran: npm test" in written["screen"]
    assert shells[0].written == ["npm run build\r", "npm test\r"]
    assert [e["text"] for e in jarvis.terminals["term-1"].log] == ["npm run build", "npm test"]
    assert "terminal.input" in [f["type"] for f in jarvis.hub.history]
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_typing_into_a_terminal_waits_for_approval(tmp_path: Path) -> None:
    write = {"functionCall": {"name": "terminal_write", "args": {
        "terminal_id": "term-1", "key": "ctrl+c"}}}
    web = FakeWeb(reply(write), reply({"text": "Very well, I'll leave it."}))
    jarvis = make(tmp_path, web)
    shells = with_fake_shells(jarvis)
    jarvis.open_terminal("server", "the dev server")
    task = jarvis.submit("stop the server")
    for _ in range(100):
        if jarvis.approvals:
            break
        await asyncio.sleep(0.05)
    (approval,) = jarvis.approvals.values()
    assert approval.arguments == {"terminal_id": "term-1", "title": "server", "text": "[ctrl+c]"}
    jarvis.decide(approval.id, "deny")
    await finished(jarvis, task)
    assert tool_result(web, 1)["denied"] is True
    assert shells[0].written == []
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_the_terminal_tab_routes(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb())
    shells = with_fake_shells(jarvis)
    auth = {"Authorization": "Bearer secret-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        assert (await client.get("/dash/api/terminals")).status_code == 401
        made = (await client.post("/dash/api/terminals", headers=auth,
                                  json={"title": "Main", "cols": 100, "rows": 20})).json()
        assert made["id"] == "term-1" and made["opened_by"] == "you"
        base = f"/dash/api/terminals/{made['id']}"
        for keys in ("dir", "\r", "git status\r"):  # typed, then pasted
            sent = await client.post(f"{base}/input", headers=auth, json={"data": keys})
            assert sent.status_code == 200
            await asyncio.sleep(0.2)
        assert [e["text"] for e in jarvis.terminals["term-1"].log] == ["dir", "git status"]

        async def close_soon() -> None:
            await asyncio.sleep(0.3)
            await client.post(f"{base}/close", headers=auth)

        closer = asyncio.create_task(close_soon())
        stream = await client.get(f"{base}/stream?token=secret-token")
        await closer
        messages = [json.loads(line[6:]) for line in stream.text.splitlines()
                    if line.startswith("data: ")]
        assert messages[0]["type"] == "replay" and "ran: git status" in messages[0]["data"]
        assert messages[-1]["type"] == "closed"
        assert (await client.get(f"{base}/stream", headers=auth)).status_code == 404
        assert (await client.get("/dash/api/terminals", headers=auth)).json() == []
    # A terminal keeps the size it opened at; Jarvis's take the page's last size.
    jarvis.open_terminal("for jarvis", opened_by="jarvis")
    assert shells[0].size == shells[1].size == (20, 100)
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_a_shell_that_exits_is_reported(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb())
    with_fake_shells(jarvis)
    terminal = jarvis.open_terminal("short-lived")
    await terminal.ready()
    await terminal.keys("exit\r")
    for _ in range(100):
        if terminal.status == "exited":
            break
        await asyncio.sleep(0.02)
    assert terminal.status == "exited" and terminal.exit_code == 0
    assert jarvis.hub.history[-1]["type"] == "terminal.exited"
    with pytest.raises(J.JarvisError):
        await terminal.keys("dir\r")
    missing = await jarvis._call_tool(J.Task(request="x"), {
        "name": "terminal_read", "args": {"terminal_id": "term-9"}})
    assert "term-1" in missing["error"]


@pytest.mark.asyncio
async def test_a_long_prompt_that_wraps_is_still_a_prompt(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb())
    shells = with_fake_shells(jarvis)
    FakeShell.PROMPT = r"PS C:\Users\someone\a-deeply-nested-workspace> "
    try:
        terminal = jarvis.open_terminal("narrow", cols=20, rows=6)
        await terminal.ready(limit=2)
        assert terminal.at_prompt
        await terminal.keys("dir\r")
        assert [e["text"] for e in terminal.log] == ["dir"]
        assert shells[0].written == ["dir", "\r"]
    finally:
        FakeShell.PROMPT = r"PS C:\ws> "
        jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_enter_waits_for_the_echo_before_noting_the_command(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb())
    shells = with_fake_shells(jarvis)
    terminal = jarvis.open_terminal("slow echo")
    await terminal.ready()
    shells[0].lag = 0.15  # keys arrive faster than the screen shows them
    await terminal.keys("Get-Date")
    await terminal.keys("\r")
    assert [e["text"] for e in terminal.log] == ["Get-Date"]
    jarvis.close_all_terminals()


def test_only_read_only_commands_count_as_safe() -> None:
    for command in ("git status", "git log --oneline -5", "dir", "python --version",
                    "Get-Date", "where git"):
        assert J.is_safe(command), command
    for command in ("git branch -D old", "git status; Remove-Item x", "dir > out.txt",
                    "git diff --output=a", "npm install", "echo $(whoami)", "git log | more"):
        assert not J.is_safe(command), command


@pytest.mark.asyncio
async def test_a_safe_command_runs_without_asking(tmp_path: Path) -> None:
    call = {"functionCall": {"name": "run_command", "args": {"command": "hostname"}}}
    web = FakeWeb(reply(call), reply({"text": "That's this PC's name."}))
    jarvis = make(tmp_path, web)  # AUTO_APPROVE is off
    task = await finished(jarvis, jarvis.submit("what's this computer called?"))
    assert task.status == "completed" and not jarvis.approvals
    assert tool_result(web, 1)["exit_code"] == 0
    assert "approval.auto" in [f["type"] for f in jarvis.hub.history]


@pytest.mark.asyncio
async def test_jarvis_types_freely_in_a_trusted_terminal(tmp_path: Path) -> None:
    write = {"functionCall": {"name": "terminal_write", "args": {
        "terminal_id": "term-1", "text": "npm test", "wait_seconds": 2}}}
    web = FakeWeb(reply(write), reply({"text": "Tests are running."}))
    jarvis = make(tmp_path, web)
    shells = with_fake_shells(jarvis)
    await jarvis.open_terminal("app").ready()
    jarvis.trust_terminal("term-1", True)
    await finished(jarvis, jarvis.submit("run the tests"))
    assert shells[0].written == ["npm test\r"] and not jarvis.approvals
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_a_failed_command_is_flagged_and_can_be_explained(tmp_path: Path) -> None:
    web = FakeWeb(reply({"text": "The script name is misspelt."}))
    jarvis = make(tmp_path, web)
    with_fake_shells(jarvis)
    terminal = jarvis.open_terminal("app")
    await terminal.ready()
    await terminal.keys("bad-script\r")
    for _ in range(100):
        if terminal.last_result:
            break
        await asyncio.sleep(0.02)
    assert terminal.last_result and terminal.last_result["ok"] is False
    assert jarvis.hub.history[-1]["type"] == "terminal.failed"
    task = await finished(jarvis, jarvis.explain_terminal("term-1"))
    assert task.request == 'Why did "bad-script" fail?'
    sent = web.gemini_bodies[0]["contents"][-1]["parts"][0]["text"]
    assert "ran: bad-script" in sent  # the screen goes with the question
    assert task.result == "The script name is misspelt."
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_a_long_command_jarvis_wasnt_watching_gets_a_notice(tmp_path: Path) -> None:
    web = FakeWeb(reply({"text": "The build finished cleanly."}))
    jarvis = make(tmp_path, web, notify_after=1)
    with_fake_shells(jarvis)
    terminal = jarvis.open_terminal("build")
    await terminal.ready()
    await terminal.keys("slow build\r")
    for _ in range(200):
        if jarvis.tasks:
            break
        await asyncio.sleep(0.02)
    (task,) = jarvis.tasks.values()
    assert task.request == 'build: "slow build" worked'
    await finished(jarvis, task)
    assert "automatic notice" in web.gemini_bodies[0]["contents"][-1]["parts"][0]["text"]
    assert task.result == "The build finished cleanly."
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_startup_terminals_open_and_run_their_commands(tmp_path: Path) -> None:
    listing = tmp_path / "terminals.json"
    listing.write_text(json.dumps([
        {"title": "dev server", "purpose": "the web app", "command": "npm run dev"},
        {"title": "scratch"}]), encoding="utf-8")
    jarvis = make(tmp_path, FakeWeb(), startup_terminals=listing)
    shells = with_fake_shells(jarvis)
    await jarvis.open_startup_terminals()
    server, scratch = jarvis.terminals.values()
    assert (server.title, server.purpose, server.opened_by) == ("dev server", "the web app",
                                                                 "startup")
    assert shells[0].written == ["npm run dev\r"] and shells[1].written == []
    assert scratch.title == "scratch"
    jarvis.close_all_terminals()


def test_the_phone_link_needs_the_network_opened_up() -> None:
    local = J.phone_link(J.Settings(api_token="tok"))
    assert "mothership.html?token=tok" in local and "Connections → Phone" in local
    anywhere = J.phone_link(J.Settings(api_token="tok"),
                            {"url": "https://pc.tail-1.ts.net"})
    assert anywhere.startswith("https://pc.tail-1.ts.net/dash/mothership.html?token=tok")


SERVING_JARVIS = {"TCP": {"443": {"HTTPS": True}},
                  "Web": {"pc.tail-1.ts.net:443": {
                      "Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}}


def test_the_https_address_tailscale_passes_to_jarvis() -> None:
    assert J.served_url(SERVING_JARVIS, 8765) == "https://pc.tail-1.ts.net"
    assert J.served_url(SERVING_JARVIS, 9000) == ""  # serving something else
    other_port = {"Web": {"pc.tail-1.ts.net:8443": {
        "Handlers": {"/": {"Proxy": "http://localhost:8765/"}}}}}
    assert J.served_url(other_port, 8765) == "https://pc.tail-1.ts.net:8443"
    # Under a sub-path Jarvis's own /dash/api/... addresses wouldn't line up.
    sub_path = {"Web": {"pc.tail-1.ts.net:443": {
        "Handlers": {"/jarvis": {"Proxy": "http://127.0.0.1:8765"}}}}}
    for nothing in (sub_path, {}, None, {"Web": "odd"}):
        assert J.served_url(nothing, 8765) == ""


def fake_tailscale(monkeypatch: pytest.MonkeyPatch, state: str = "Running",
                   serve: Any = None) -> list[tuple[str, ...]]:
    asked: list[tuple[str, ...]] = []

    def run(*args: str) -> Any:
        asked.append(args)
        if args == ("status",):
            return {"BackendState": state, "CertDomains": ["pc.tail-1.ts.net"],
                    "Self": {"DNSName": "pc.tail-1.ts.net.",
                             "TailscaleIPs": ["100.64.0.7", "fd7a:115c::7"]},
                    "Peer": {"k1": {"HostName": "Pixel 9", "OS": "android", "Online": True},
                             "k2": {"HostName": "desktop", "OS": "windows", "Online": True}}}
        return serve if serve is not None else {}

    monkeypatch.setattr(J, "tailscale_exe", lambda: "C:/Program Files/Tailscale/tailscale.exe")
    monkeypatch.setattr(J, "run_tailscale", run)
    monkeypatch.setattr(J, "lan_address", lambda: "192.168.1.20")
    return asked


def test_phone_access_without_tailscale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(J, "tailscale_exe", lambda: None)
    monkeypatch.setattr(J, "lan_address", lambda: "192.168.1.20")
    alone = J.phone_access(J.Settings(api_token="tok"))
    assert alone["tailscale"]["installed"] is False and alone["links"] == []
    assert alone["tailscale"]["download"].startswith("https://tailscale.com")
    wifi = J.phone_access(J.Settings(api_token="tok", host="0.0.0.0"))
    assert wifi["links"] == [{"via": "wifi", "anywhere": False, "secure": False,
                              "url": "http://192.168.1.20:8765/dash/mothership.html"}]


def test_phone_access_through_tailscale(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tailscale(monkeypatch, serve=SERVING_JARVIS)
    access = J.phone_access(J.Settings(api_token="tok"))
    ts = access["tailscale"]
    assert (ts["signed_in"], ts["name"], ts["address"], ts["https"]) == (
        True, "pc.tail-1.ts.net", "100.64.0.7", True)
    assert ts["phones"] == [{"name": "Pixel 9", "os": "android", "online": True}]
    assert [link["url"] for link in access["links"]] == [
        "https://pc.tail-1.ts.net/dash/mothership.html"]
    assert "tok" not in json.dumps(access)  # the page adds its own token

    asked = fake_tailscale(monkeypatch, state="NeedsLogin")
    signed_out = J.phone_access(J.Settings(api_token="tok", host="0.0.0.0"))
    assert signed_out["tailscale"]["signed_in"] is False
    assert asked == [("status",)]  # no point asking what it serves
    assert [link["via"] for link in signed_out["links"]] == ["wifi"]


@pytest.mark.asyncio
async def test_the_phone_card_turns_tailscale_serve_on_in_a_terminal(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tailscale(monkeypatch, serve=SERVING_JARVIS)
    monkeypatch.setattr(J.shutil, "which", lambda name: None)  # not on PATH
    jarvis = make(tmp_path, FakeWeb())
    shells = with_fake_shells(jarvis)
    auth = {"Authorization": "Bearer secret-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        assert (await client.get("/dash/api/phone")).status_code == 401
        view = (await client.get("/dash/api/phone", headers=auth)).json()
        assert view["links"][0]["url"] == "https://pc.tail-1.ts.net/dash/mothership.html"
        opened = (await client.post("/dash/api/phone/tailscale", headers=auth,
                                    json={"share": True})).json()
        assert opened["title"] == "Phone access"
        off = await client.post("/dash/api/phone/tailscale", headers=auth,
                                json={"share": False})
        assert off.status_code == 200
        exe = "& 'C:/Program Files/Tailscale/tailscale.exe'" if J.WINDOWS else (
            "'C:/Program Files/Tailscale/tailscale.exe'")
        assert shells[0].written == [f"{exe} serve --bg 8765\r"]  # you pressed it: no asking
        assert shells[1].written == [f"{exe} serve --https=443 off\r"]
        monkeypatch.setattr(J, "tailscale_exe", lambda: None)
        missing = await client.post("/dash/api/phone/tailscale", headers=auth, json={})
        assert missing.status_code == 409 and "tailscale.com" in missing.json()["detail"]
    assert jarvis.approvals == {}
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_the_app_manifest_carries_the_token_only_to_whoever_sent_it(
        tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb())
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "manifest.webmanifest").write_text(
        json.dumps({"name": "JARVIS", "start_url": "mothership.html"}), encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        plain = await client.get("/dash/manifest.webmanifest")
        assert plain.headers["content-type"].startswith("application/manifest+json")
        assert plain.json() == {"name": "JARVIS", "start_url": "mothership.html"}
        wrong = await client.get("/dash/manifest.webmanifest?token=guess")
        assert wrong.json()["start_url"] == "mothership.html"
        right = await client.get("/dash/manifest.webmanifest?token=secret-token")
        assert right.json()["start_url"] == "mothership.html?token=secret-token"


def openai_web(seen: list[httpx.Request]) -> FakeWeb:
    web = FakeWeb()
    base = web.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            seen.append(request)
            if request.headers["authorization"] != "Bearer sk-good":
                return httpx.Response(401, json={"error": {"message": "bad key"}})
            return httpx.Response(200, content=b"ID3fake-mp3")
        return base(request)

    web.handler = handler  # type: ignore[method-assign]
    return web


@pytest.mark.asyncio
async def test_openai_voice_speaks_through_the_speech_endpoint(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    jarvis = make(tmp_path, openai_web(seen), voice_provider="openai", openai_api_key="sk-good")
    jarvis.settings.voice = "en-GB-RyanNeural"  # not "off"
    assert jarvis.can_speak
    info = jarvis.voice_info()
    assert (info["provider"], info["voice"]) == ("openai", "onyx")
    audio = await jarvis.speak("Good evening, **sir**. See https://example.com")
    assert audio == b"ID3fake-mp3"
    body = json.loads(seen[0].content)
    assert body["model"] == "gpt-4o-mini-tts" and body["voice"] == "onyx"
    assert body["input"] == "Good evening, sir. See "  # markdown and links stripped
    assert "British" in body["instructions"]
    jarvis.settings.voice_style = "Whisper like a pirate."
    await jarvis.speak("hi")
    assert json.loads(seen[1].content)["instructions"] == "Whisper like a pirate."


@pytest.mark.asyncio
async def test_openai_voice_reports_a_bad_key_and_off_stays_silent(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    jarvis = make(tmp_path, openai_web(seen), voice_provider="openai", openai_api_key="sk-bad",
                  listen_provider="off")
    jarvis.settings.voice = "en-GB-RyanNeural"
    with pytest.raises(J.JarvisError, match="OPENAI_API_KEY"):
        await jarvis.speak("hello")
    jarvis.settings.voice = "off"
    assert not jarvis.can_speak and jarvis.voice_info() == {"enabled": False}


def test_openai_provider_without_a_key_falls_back_to_edge(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb(), voice_provider="openai")
    jarvis.settings.voice = "en-GB-RyanNeural"
    assert jarvis.voice_provider in {"edge", ""}  # never "openai" without a key


def wispr_web(seen: list[httpx.Request]) -> FakeWeb:
    web = FakeWeb(reply({"text": "On it."}))
    base = web.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "platform-api.wisprflow.ai":
            seen.append(request)
            if request.headers["authorization"] != "Bearer wk-good":
                return httpx.Response(401, json={})
            return httpx.Response(200, json={"text": "Hey Jarvis, what time is it?"})
        return base(request)

    web.handler = handler  # type: ignore[method-assign]
    return web


WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24


@pytest.mark.asyncio
async def test_wispr_listening_turns_a_recording_into_a_task(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    seen: list[httpx.Request] = []
    jarvis = make(tmp_path, wispr_web(seen), listen_provider="wispr", wispr_api_key="wk-good")
    info = jarvis.voice_info()
    assert info["enabled"] and info["can_listen"] and not info["can_speak"]
    assert info["listen_provider"] == "wispr"
    client = TestClient(J.create_app(jarvis))
    auth = {"Authorization": "Bearer secret-token"}
    reply_ = client.post("/dash/api/listen", headers=auth, data={"submit": "true"},
                         files={"audio": ("u.wav", WAV, "audio/wav")})
    assert reply_.status_code == 200
    heard = reply_.json()
    assert heard["text"] == "Hey Jarvis, what time is it?"
    assert heard["command"] == "what time is it?"  # wake word stripped
    body = json.loads(seen[0].content)
    assert body["language"] == ["en"] and body["audio"]
    bad = client.post("/dash/api/listen", headers=auth,
                      files={"audio": ("u.webm", b"not a wav", "audio/webm")})
    assert bad.status_code == 503 and "WAV" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_listening_can_be_off_and_wispr_reports_a_bad_key(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    seen: list[httpx.Request] = []
    off = make(tmp_path, wispr_web(seen), listen_provider="off")
    assert not off.can_listen and off.voice_info() == {"enabled": False}
    r = TestClient(J.create_app(off)).post(
        "/dash/api/listen", headers={"Authorization": "Bearer secret-token"},
        files={"audio": ("u.wav", WAV, "audio/wav")})
    assert r.status_code == 503 and "JARVIS_LISTEN_PROVIDER" in r.json()["detail"]
    assert J.Settings().listen_provider == "browser"  # works out of the box
    keyless = make(tmp_path, wispr_web(seen), listen_provider="wispr")
    assert not keyless.can_listen  # chosen but no key: stays off
    wrong = make(tmp_path, wispr_web(seen), listen_provider="wispr", wispr_api_key="nope")
    with pytest.raises(J.JarvisError, match="WISPR_API_KEY"):
        await wrong.transcribe(WAV)


@pytest.mark.asyncio
async def test_browser_listening_needs_no_key_and_takes_no_uploads(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    jarvis = make(tmp_path, FakeWeb(reply({"text": "Hello."})), listen_language="en-GB")
    info = jarvis.voice_info()
    assert info["enabled"] and info["can_listen"] and not info["can_speak"]
    assert (info["listen_provider"], info["listen_language"]) == ("browser", "en-GB")
    client = TestClient(J.create_app(jarvis))
    auth = {"Authorization": "Bearer secret-token"}
    upload = client.post("/dash/api/listen", headers=auth,
                         files={"audio": ("u.wav", WAV, "audio/wav")})
    assert upload.status_code == 503 and "inside the page" in upload.json()["detail"]
    heard = client.post("/dash/api/command", headers=auth,
                        json={"text": "Jarvis, hello", "submit": False}).json()
    assert heard["command"] == "hello"  # the page posts what it heard here


class FakeWhisper:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, path: str, **kwargs: Any) -> tuple[list[Any], None]:
        self.calls.append({"bytes": Path(path).read_bytes(), **kwargs})
        return [type("S", (), {"text": " Jarvis, open the pod bay doors."})()], None


@pytest.mark.asyncio
async def test_whisper_listening_transcribes_on_this_machine(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setattr(J, "have_module", lambda name: True)
    jarvis = make(tmp_path, FakeWeb(reply({"text": "No."})), listen_provider="whisper")
    fake = FakeWhisper()
    jarvis._whisper = fake
    info = jarvis.voice_info()
    assert info["can_listen"] and info["listen_provider"] == "whisper"
    client = TestClient(J.create_app(jarvis))
    r = client.post("/dash/api/listen", headers={"Authorization": "Bearer secret-token"},
                    data={"submit": "false"},
                    files={"audio": ("u.webm", b"webm-bytes", "audio/webm")})
    assert r.status_code == 200
    assert r.json()["command"] == "open the pod bay doors."  # wake word stripped
    assert fake.calls[0]["bytes"] == b"webm-bytes"  # no WAV conversion needed
    assert fake.calls[0]["language"] == "en" and "Jarvis" in fake.calls[0]["initial_prompt"]


def test_whisper_without_the_package_stays_off(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(J, "have_module", lambda name: False)
    jarvis = make(tmp_path, FakeWeb(), listen_provider="whisper")
    assert not jarvis.can_listen


# -- the Mothership ---------------------------------------------------------------


def empty_mothership(tmp_path: Path) -> None:
    """Start a test from no controls or projects, not the examples."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "mothership.json").write_text('{"controls": [], "projects": []}', encoding="utf-8")


async def until(check: Any, timeout: float = 5) -> None:
    for _ in range(int(timeout * 50)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting")


@pytest.mark.asyncio
async def test_a_fresh_install_starts_from_the_example_mothership(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb())
    names = [c["name"] for c in jarvis.mothership.controls]
    assert "Make it rain" in names and jarvis.mothership.projects
    assert "`Make it rain`" in jarvis.mothership_summary()


@pytest.mark.asyncio
async def test_controls_projects_and_ideas_over_the_api(tmp_path: Path) -> None:
    empty_mothership(tmp_path)
    folder = tmp_path / "bot"
    folder.mkdir()
    (folder / "live_state.json").write_text('{"cash": 100.5, "halted": false}', encoding="utf-8")
    jarvis = make(tmp_path, FakeWeb())
    shells = with_fake_shells(jarvis)
    auth = {"Authorization": "Bearer secret-token"}
    ms = "/dash/api/mothership"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        assert (await client.get(ms)).status_code == 401
        project = (await client.post(f"{ms}/projects", headers=auth, json={
            "name": "TradeBot", "folder": str(folder), "status_file": "live_state.json"})).json()
        relative = await client.post(f"{ms}/projects", headers=auth,
                                     json={"name": "x", "folder": "not/absolute"})
        assert relative.status_code == 422
        status = (await client.get(f"{ms}/projects/{project['id']}/status", headers=auth)).json()
        assert status["data"] == {"cash": 100.5, "halted": False}

        control = (await client.post(f"{ms}/controls", headers=auth, json={
            "name": "One cycle", "kind": "command", "action": "run-once",
            "project": project["id"]})).json()
        bad_link = await client.post(f"{ms}/controls", headers=auth, json={
            "name": "Sneaky", "kind": "link", "action": "javascript:alert(1)"})
        assert bad_link.status_code == 422

        # Pressing the button yourself runs it straight away, in the project's folder.
        ran = (await client.post(f"{ms}/controls/{control['id']}/run", headers=auth)).json()
        terminal = jarvis.terminals[ran["terminal"]]
        assert shells[0].cwd == folder and shells[0].written == ["run-once\r"]
        assert (terminal.control, terminal.project) == (control["id"], project["id"])
        await until(lambda: terminal.last_result is not None)
        again = await client.post(f"{ms}/controls/{control['id']}/run", headers=auth)
        assert again.status_code == 200 and shells[0].written[-1] == "run-once\r"  # reused
        stopped = await client.post(f"{ms}/controls/{control['id']}/stop", headers=auth)
        assert stopped.status_code == 200 and shells[0].written[-1] == "\x03"

        idea = (await client.post(f"{ms}/projects/{project['id']}/ideas", headers=auth,
                                  json={"text": "Trailing stops"})).json()
        await client.post(f"{ms}/projects/{project['id']}/ideas/{idea['id']}", headers=auth,
                          json={"done": True})
        assert jarvis.mothership.project(project["id"])["ideas"][0]["done"] is True
        await client.post(f"{ms}/projects/{project['id']}/ideas/{idea['id']}/delete",
                          headers=auth)
        assert jarvis.mothership.project(project["id"])["ideas"] == []

        everything = (await client.get(ms, headers=auth)).json()
        assert [c["name"] for c in everything["controls"]] == ["One cycle"]
        await client.post(f"{ms}/projects/{project['id']}/delete", headers=auth)
        assert jarvis.mothership.controls[0]["project"] == ""  # kept, unfiled
        missing = await client.post(f"{ms}/controls/c-nope/run", headers=auth)
        assert missing.status_code == 404
    saved = json.loads((tmp_path / "data" / "mothership.json").read_text(encoding="utf-8"))
    assert saved["controls"][0]["name"] == "One cycle"
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_jarvis_runs_a_control_by_voice_asking_first_unless_trusted(
        tmp_path: Path) -> None:
    empty_mothership(tmp_path)
    press = {"functionCall": {"name": "run_control", "args": {"control": "rain"}}}
    web = FakeWeb(reply(press), reply({"text": "I'll leave the sky alone."}),
                  reply(press), reply({"text": "It's raining."}))
    jarvis = make(tmp_path, web)
    shells = with_fake_shells(jarvis)
    jarvis.save_control({"name": "Make it rain", "group": "BeamNG.drive", "kind": "command",
                         "action": "weather rain", "trusted": False})
    task = jarvis.submit("make it rain")
    await until(lambda: bool(jarvis.approvals))
    (approval,) = jarvis.approvals.values()
    assert approval.arguments == {"control": "Make it rain", "command": "weather rain"}
    jarvis.decide(approval.id, "deny")
    await finished(jarvis, task)
    assert shells == []  # nothing ran

    jarvis.mothership.controls[0]["trusted"] = True
    await finished(jarvis, jarvis.submit("make it rain"))
    assert not jarvis.approvals and shells[0].written == ["weather rain\r"]
    result = tool_result(web, 3)
    assert result["control"] == "Make it rain" and "ran: weather rain" in result["screen"]
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_an_unbuilt_control_says_so_and_ideas_can_be_noted(tmp_path: Path) -> None:
    empty_mothership(tmp_path)
    web = FakeWeb(
        reply({"functionCall": {"name": "run_control", "args": {"control": "night"}}}),
        reply({"text": "That one isn't built yet."}),
        reply({"functionCall": {"name": "add_idea", "args": {
            "project": "velocity", "idea": "A pit-wall menu"}}}),
        reply({"text": "Noted."}))
    (tmp_path / "persona.md").write_text("Mothership:\n{{mothership}}", encoding="utf-8")
    jarvis = make(tmp_path, web)
    jarvis.save_control({"name": "Night time", "kind": "idea", "description": "Set it to night"})
    jarvis.save_project({"name": "VelocityRacing"})
    await finished(jarvis, jarvis.submit("make it night"))
    assert "Build with Claude" in tool_result(web, 1)["error"]
    await finished(jarvis, jarvis.submit("note an idea for velocity"))
    (idea,) = jarvis.mothership.projects[0]["ideas"]
    assert (idea["text"], idea["by"]) == ("A pit-wall menu", "jarvis")
    persona = web.gemini_bodies[0]["systemInstruction"]["parts"][0]["text"]
    assert "Night time" in persona and "VelocityRacing" in persona


@pytest.mark.asyncio
async def test_build_with_claude_opens_claude_code_with_a_brief(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty_mothership(tmp_path)
    monkeypatch.setattr(J.shutil, "which", lambda name: f"C:/bin/{name}.exe")
    jarvis = make(tmp_path, FakeWeb())
    shells = with_fake_shells(jarvis)
    control = jarvis.save_control({"name": "Make it rain", "group": "BeamNG.drive",
                                   "kind": "idea", "description": "Rain on the current map"})
    terminal = await jarvis.build_control(control["id"])
    assert terminal.title == "Claude: Make it rain"
    assert shells[0].cwd == tmp_path / "data" / "builds" / "make-it-rain"
    (typed,) = shells[0].written
    assert typed.startswith('claude "Read the brief in ') and typed.endswith('"\r')
    (brief,) = (tmp_path / "data" / "briefs").iterdir()
    text = brief.read_text(encoding="utf-8")
    assert "Rain on the current map" in text and control["id"] in text
    assert str(jarvis.mothership.path) in text  # Claude knows where to switch it on
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_a_hand_edit_to_the_mothership_file_shows_up(tmp_path: Path) -> None:
    empty_mothership(tmp_path)
    jarvis = make(tmp_path, FakeWeb())
    assert jarvis.mothership.controls == []
    path = tmp_path / "data" / "mothership.json"
    path.write_text(json.dumps({"controls": [{"id": "c-1", "name": "Built by Claude",
                                              "kind": "command", "action": "rain.ps1"}],
                                "projects": []}), encoding="utf-8")
    later = path.stat().st_mtime + 5
    os.utime(path, (later, later))
    assert jarvis.mothership.control("built")["action"] == "rain.ps1"


@pytest.mark.asyncio
async def test_requests_survive_a_restart(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb(reply({"text": "Noted, sir."})))
    task = await finished(jarvis, jarvis.submit("remember this"))
    again = make(tmp_path, FakeWeb())
    restored = again.tasks[task.id]
    assert (restored.request, restored.result, restored.kind) == (
        "remember this", "Noted, sir.", "asked")


# -- connections: other brains, keys in .env, applied live ----------------------------


def openai_reply(content: str | None = None, calls: list[dict[str, Any]] | None = None,
                 ) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


def claude_reply(*content: dict[str, Any], stop: str = "end_turn") -> dict[str, Any]:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": list(content), "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.mark.asyncio
async def test_switching_brains_writes_env_and_applies_live(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=gem-secret-1234\nJARVIS_API_TOKEN=secret-token\n",
                   encoding="utf-8")
    call = {"id": "call_1", "type": "function",
            "function": {"name": "terminal_list", "arguments": "{}"}}
    web = FakeWeb(brains={"api.groq.com": [openai_reply(calls=[call]),
                                           openai_reply("Groq here, sir.")]})
    jarvis = make(tmp_path, web, env_file=env)
    jarvis.reload_settings()  # as at startup: from the .env above
    auth = {"Authorization": "Bearer secret-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        view = (await client.get("/dash/api/connections", headers=auth)).json()
        gemini = next(b for b in view["brain"]["brains"] if b["id"] == "gemini")
        assert gemini["key_hint"] == "…1234" and "gem-secret" not in json.dumps(view)
        changed = await client.post("/dash/api/connections", headers=auth, json={"values": {
            "JARVIS_BRAIN": "groq", "GROQ_API_KEY": "gsk-new-key-9999",
            "GROQ_MODEL": "llama-3.3-70b-versatile"}})
        assert changed.status_code == 200 and changed.json()["brain"]["active"] == "groq"
        refused = await client.post("/dash/api/connections", headers=auth,
                                    json={"values": {"JARVIS_DATA": "C:/elsewhere"}})
        assert refused.status_code == 422
        bad = await client.post("/dash/api/connections", headers=auth,
                                json={"values": {"JARVIS_BRAIN": "skynet"}})
        assert bad.status_code == 422
    saved = J.read_env_file(env)
    assert saved["JARVIS_BRAIN"] == "groq" and saved["GROQ_API_KEY"] == "gsk-new-key-9999"
    assert jarvis.settings.brain == "groq"  # live: no restart
    task = await finished(jarvis, jarvis.submit("which terminals are open?"))
    assert task.result == "Groq here, sir."
    first, second = web.brain_requests
    assert first.headers["authorization"] == "Bearer gsk-new-key-9999"
    sent = json.loads(second.content)
    assert sent["model"] == "llama-3.3-70b-versatile"
    assert sent["messages"][0]["role"] == "system"
    last = sent["messages"][-1]
    assert last["role"] == "tool" and last["tool_call_id"] == "call_1"
    assert "connections.updated" in [f["type"] for f in jarvis.hub.history]
    assert all("gsk-new" not in json.dumps(f) for f in jarvis.hub.history)


@pytest.mark.asyncio
async def test_a_used_up_brain_says_to_switch(tmp_path: Path) -> None:
    busy = httpx.Response(429, headers={"retry-after": "3600"},
                          json={"error": {"message": "Rate limit reached"}})
    web = FakeWeb(brains={"api.groq.com": [busy]})
    jarvis = make(tmp_path, web, brain="groq", brain_keys={"groq": "gsk"})
    task = await finished(jarvis, jarvis.submit("hello"))
    assert task.status == "failed" and "Connections" in (task.error or "")


@pytest.mark.asyncio
async def test_claude_as_the_brain_runs_tools_through_the_sdk(tmp_path: Path) -> None:
    pytest.importorskip("anthropic")
    web = FakeWeb(brains={"api.anthropic.com": [
        claude_reply({"type": "tool_use", "id": "toolu_1", "name": "terminal_list", "input": {}},
                     stop="tool_use"),
        claude_reply({"type": "text", "text": "No terminals are open, sir."})]})
    jarvis = make(tmp_path, web, brain="anthropic", brain_keys={"anthropic": "sk-ant-test"})
    task = await finished(jarvis, jarvis.submit("any terminals?"))
    assert task.result == "No terminals are open, sir."
    first, second = web.brain_requests
    assert first.headers["x-api-key"] == "sk-ant-test"
    assert "server-side-fallback-2026-07-01" in first.headers.get("anthropic-beta", "")
    body = json.loads(first.content)
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["tools"][0]["input_schema"]["type"] == "object"
    follow = json.loads(second.content)["messages"]
    assert follow[-2]["role"] == "assistant" and follow[-2]["content"][0]["type"] == "tool_use"
    (result,) = follow[-1]["content"]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"


@pytest.mark.asyncio
async def test_no_key_for_the_chosen_brain_is_explained(tmp_path: Path) -> None:
    jarvis = make(tmp_path, FakeWeb(), brain="deepseek")
    task = await finished(jarvis, jarvis.submit("hello"))
    assert "DeepSeek key" in (task.error or "") and "Connections" in (task.error or "")


@pytest.mark.asyncio
async def test_testing_a_key_lists_models_without_spending_tokens(tmp_path: Path) -> None:
    models = {"data": [{"id": "llama-3.3-70b-versatile"}]}
    web = FakeWeb(brains={"api.groq.com": [models, models]})
    jarvis = make(tmp_path, web, brain_keys={"groq": "gsk"})
    ok, detail = await jarvis.check_brain("groq")
    assert ok and "connected" in detail
    jarvis.settings.brain_models["groq"] = "no-such-model"
    ok, detail = await jarvis.check_brain("groq")
    assert not ok and "no-such-model" in detail
    assert all(r.method == "GET" for r in web.brain_requests)


@pytest.mark.asyncio
async def test_gemini_key_slots_and_token_rotation(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=first-key-aaaa\nGEMINI_API_KEY_2=second-key-bbbb\n"
                   "JARVIS_API_TOKEN=secret-token\n", encoding="utf-8")
    jarvis = make(tmp_path, FakeWeb(), env_file=env)
    jarvis.reload_settings()
    assert jarvis.settings.gemini_api_key == "first-key-aaaa"
    jarvis.change_connections({"GEMINI_KEY": "2"})
    assert jarvis.settings.gemini_api_key == "second-key-bbbb"  # the other account, live
    auth = {"Authorization": "Bearer secret-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        token = (await client.post("/dash/api/connections/token", headers=auth)).json()["token"]
        assert (await client.get("/dash/api/connections", headers=auth)).status_code == 401
        fresh = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/dash/api/connections", headers=fresh)).status_code == 200
        # Host and port need a restart; tests have no server to restart.
        view = (await client.post("/dash/api/connections", headers=fresh,
                                  json={"values": {"JARVIS_PORT": "9999"}})).json()
        assert view["restart_needed"] == {"port": 9999} and jarvis.settings.port == 8765
        assert (await client.post("/dash/api/restart", headers=fresh)).status_code == 409
    assert J.read_env_file(env)["JARVIS_API_TOKEN"] == token


@pytest.mark.asyncio
async def test_restart_stops_a_running_control_and_runs_it_again(tmp_path: Path) -> None:
    empty_mothership(tmp_path)
    jarvis = make(tmp_path, FakeWeb())
    shells = with_fake_shells(jarvis)
    control = jarvis.save_control({"name": "Paper trading", "kind": "command",
                                   "action": "slow bot"})
    auth = {"Authorization": "Bearer secret-token"}
    route = f"/dash/api/mothership/controls/{control['id']}/restart"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=J.create_app(jarvis)),
                                 base_url="http://jarvis") as client:
        # Not running yet: restart just starts it.
        first = (await client.post(route, headers=auth)).json()
        terminal = jarvis.terminals[first["terminal"]]
        await until(lambda: terminal.running is not None)
        # The page hears that it started, so it can show Stop and Restart.
        started = [e for e in jarvis.hub.history if e["type"] == "terminal.updated"]
        assert started[-1]["data"]["terminal"]["running"]["command"] == "slow bot"
        again = (await client.post(route, headers=auth)).json()
        assert again["terminal"] == terminal.id  # same terminal, run again
        assert shells[0].written == ["slow bot\r", "\x03", "slow bot\r"]
        assert terminal.running is not None and terminal.running["command"] == "slow bot"
    jarvis.close_all_terminals()


@pytest.mark.asyncio
async def test_jarvis_restarts_or_stops_a_control_by_voice_with_approval(
        tmp_path: Path) -> None:
    empty_mothership(tmp_path)
    restart = {"functionCall": {"name": "run_control",
                                "args": {"control": "paper", "action": "restart"}}}
    stop = {"functionCall": {"name": "run_control",
                             "args": {"control": "paper", "action": "stop"}}}
    web = FakeWeb(reply(restart), reply({"text": "Restarted."}),
                  reply(stop), reply({"text": "It wasn't running."}))
    jarvis = make(tmp_path, web)
    shells = with_fake_shells(jarvis)
    control = jarvis.save_control({"name": "Paper trading", "kind": "command",
                                   "action": "slow bot"})
    await jarvis.run_control(control, by="you")
    terminal = next(iter(jarvis.terminals.values()))
    await until(lambda: terminal.running is not None)

    task = jarvis.submit("restart the bot")
    await until(lambda: bool(jarvis.approvals))
    (approval,) = jarvis.approvals.values()
    assert approval.arguments == {"control": "Paper trading", "action": "restart"}
    jarvis.decide(approval.id, "allow")
    await finished(jarvis, task)
    assert shells[0].written == ["slow bot\r", "\x03", "slow bot\r"]
    assert tool_result(web, 1)["restarted"] is True

    await until(lambda: terminal.running is None)  # the slow command finishes
    await finished(jarvis, jarvis.submit("stop the bot"))
    assert not jarvis.approvals  # nothing to stop: it says so, no approval asked
    assert "isn't running" in tool_result(web, 3)["error"]
    jarvis.close_all_terminals()


def test_end_children_stops_a_command_but_keeps_the_shell() -> None:
    """Ctrl+C typed into ConPTY doesn't reach every program, so Stop ends the
    processes the shell started. Here: a "shell" running a long "command"."""
    sleeper = "import time; time.sleep(60)"
    shell = subprocess.Popen([sys.executable, "-c",
                              f"import subprocess, sys, time; "
                              f"subprocess.Popen([sys.executable, '-c', {sleeper!r}]); "
                              f"time.sleep(60)"])
    try:
        parent = psutil.Process(shell.pid)
        for _ in range(100):
            if parent.children():
                break
            time.sleep(0.05)
        (command,) = parent.children()

        class Shell:
            pid = shell.pid

        assert J.end_children(cast(J.Pty, Shell())) == 1
        assert not command.is_running() or command.status() == psutil.STATUS_ZOMBIE
        assert shell.poll() is None  # the shell itself is still there
        assert J.end_children(cast(J.Pty, FakeShell(Path("."), 80, 24))) == 0  # no pid
    finally:
        shell.kill()
        shell.wait()
