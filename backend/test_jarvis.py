"""Tests for jarvis.py, against a fake Gemini — no key, no network, no quota.

    pip install pytest pytest-asyncio
    python -m pytest test_jarvis.py
"""

import asyncio
import json
import queue
import threading
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


class FakeShell:
    """A pretend PowerShell in a pseudo-terminal: echoes keys and answers each
    line with "ran: <line>", so tests never start a real shell. Like ConPTY,
    it first asks what terminal it's talking to, and waits for the answer
    before showing its prompt."""

    PROMPT = "PS C:\\ws> "

    def __init__(self, cwd: Path, cols: int, rows: int) -> None:
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
            self.output.put(self.PROMPT)
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
                else:
                    self.output.put(f"\r\nran: {line}\r\n{self.PROMPT}")
            elif ch == "\x03":
                self.output.put(f"^C\r\n{self.PROMPT}")
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
