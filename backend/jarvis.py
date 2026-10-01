"""JARVIS — a Gemini-powered assistant behind a live dashboard, in one file.

    python jarvis.py

Gemini (a fast Flash-Lite model by default) answers questions itself and
uses a handful of tools: live weather, news, web search, reading pages, and
terminal commands on this PC. Quick commands run and return their output;
anything longer runs in a live terminal on the dashboard's Terminal tab, which
you and Jarvis share — you both see it, type in it, and come back to it later.

Who Jarvis is and how it behaves lives in persona.md, not here. Settings
live in .env (see .env.example). The dashboard is the ../frontend folder;
the HTTP routes below are the ones its API.md describes.

Nothing reaches a terminal from Jarvis without your say-so: every command,
and every keystroke it types into a dashboard terminal, waits for Allow/Deny
unless AUTO_APPROVE=true. What you type yourself goes straight through.
"""

import asyncio
import contextlib
import hmac
import html
import importlib.util
import ipaddress
import json
import mimetypes
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
WINDOWS = platform.system() == "Windows"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
KEY_URL = "https://aistudio.google.com/apikey"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


class JarvisError(Exception):
    """A failure with a message fit to show the user as-is."""


def now() -> str:
    return datetime.now(UTC).isoformat()


# =============================================================== settings ===


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines; blank values and # comments are ignored."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        if value:
            values[key.strip()] = value
    return values


@dataclass
class Settings:
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    thinking: str = ""
    api_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    workspace: Path = HERE / "workspace"
    auto_approve: bool = False
    home_location: str = ""
    voice: str = "en-GB-RyanNeural"
    news_country: str = "US"
    news_language: str = "en"
    cors_origins: list[str] = field(default_factory=list)
    open_browser: bool = True
    frontend: Path = HERE.parent / "frontend"
    persona_file: Path = HERE / "persona.md"

    @classmethod
    def load(cls, env_file: Path) -> "Settings":
        """Settings from .env, with real environment variables winning."""
        env = {**read_env_file(env_file), **{k: v for k, v in os.environ.items() if v}}

        def get(name: str, default: str = "") -> str:
            return env.get(name, default).strip()

        def flag(name: str, default: bool) -> bool:
            value = get(name).lower()
            return default if not value else value in {"1", "true", "yes", "on"}

        def path(name: str, default: Path) -> Path:
            value = Path(get(name, str(default)))
            return value if value.is_absolute() else HERE / value

        return cls(
            gemini_api_key=get("GEMINI_API_KEY") or get("GOOGLE_API_KEY"),
            gemini_model=get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            thinking=get("GEMINI_THINKING").lower(),
            api_token=get("JARVIS_API_TOKEN"),
            host=get("JARVIS_HOST", "127.0.0.1"),
            port=int(get("JARVIS_PORT", "8765")),
            workspace=path("JARVIS_WORKSPACE", HERE / "workspace"),
            auto_approve=flag("AUTO_APPROVE", False),
            home_location=get("HOME_LOCATION"),
            voice=get("JARVIS_VOICE", "en-GB-RyanNeural"),
            news_country=get("NEWS_COUNTRY", "US").upper(),
            news_language=get("NEWS_LANGUAGE", "en").lower(),
            cors_origins=[o.strip() for o in get("JARVIS_CORS_ORIGINS").split(",") if o.strip()],
            open_browser=flag("JARVIS_OPEN_BROWSER", True),
            frontend=path("JARVIS_FRONTEND", HERE.parent / "frontend"),
        )


def save_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, replacing an existing (even blank) line."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ================================================================= state ===


def short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class Step:
    description: str
    tool: str
    id: str = field(default_factory=lambda: short_id("s"))
    status: str = "running"
    risk: str = "safe"
    error: str | None = None

    def out(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description, "tool": self.tool,
                "status": self.status, "risk": self.risk, "error": self.error}


@dataclass
class Task:
    request: str
    id: str = field(default_factory=lambda: short_id("t"))
    status: str = "pending"
    steps: list[Step] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def set(self, status: str) -> None:
        self.status = status
        self.updated_at = now()

    def out(self) -> dict[str, Any]:
        return {"id": self.id, "request": self.request, "status": self.status, "goal": None,
                "steps": [s.out() for s in self.steps], "result": self.result,
                "error": self.error, "created_at": self.created_at,
                "updated_at": self.updated_at}


@dataclass
class Approval:
    task_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    future: "asyncio.Future[str]"
    id: str = field(default_factory=lambda: short_id("a"))
    created_at: str = field(default_factory=now)

    def out(self) -> dict[str, Any]:
        return {"id": self.id, "task_id": self.task_id, "tool": self.tool,
                "arguments": self.arguments, "reason": self.reason,
                "created_at": self.created_at}


class Pty(Protocol):
    """A shell in a pseudo-terminal: pywinpty's PtyProcess (ConPTY) on Windows,
    ptyprocess's PtyProcessUnicode elsewhere. Tests pass a fake."""

    exitstatus: int | None

    def read(self, size: int) -> str: ...
    def write(self, data: str) -> int: ...
    def isalive(self) -> bool: ...
    def terminate(self, force: bool = False) -> bool: ...


def spawn_shell(cwd: Path, cols: int, rows: int) -> Pty:
    """A real interactive shell — colours, prompts and REPLs all work."""
    try:
        if WINDOWS:
            from winpty import PtyProcess
            argv = ["powershell.exe", "-NoLogo", "-ExecutionPolicy", "Bypass"]
        else:
            from ptyprocess import PtyProcessUnicode as PtyProcess
            argv = [os.environ.get("SHELL") or "bash", "-l"]
    except ImportError as exc:
        raise JarvisError("Terminals need pywinpty (or ptyprocess) and pyte — "
                          "run: pip install -r requirements.txt") from exc
    process: Pty = PtyProcess.spawn(argv, cwd=str(cwd), dimensions=(rows, cols))
    return process


#: A shell waiting for input: "PS C:\path>" or "user@host:~$".
PROMPT = re.compile(r"^(PS [^>]*>|\S*[$#%])\s*$")
PROMPT_PREFIX = re.compile(r"^(PS [^>]*>|\S*[$#%])\s*")
#: Escape sequences and control characters, e.g. a focus report, in typed keys.
CONTROL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z~]|[\x00-\x1f\x7f]")

#: Questions a shell asks its terminal: "what are you?" and "where's the
#: cursor?". The backend answers them, not the browser — ConPTY can hold back
#: the first prompt until "what are you?" is answered, and a terminal must
#: work with no browser watching. They're kept from browsers so the answer
#: isn't sent twice.
QUERIES = re.compile(r"\x1b\[0?c|\x1b\[6n")
DA_REPLY = "\x1b[?1;2c"  # a VT100 with advanced video, as xterm.js says

#: Keys Jarvis may press by name in terminal_write.
KEYS = {"enter": "\r", "ctrl+c": "\x03", "ctrl+d": "\x04", "ctrl+z": "\x1a", "tab": "\t",
        "escape": "\x1b", "up": "\x1b[A", "down": "\x1b[B", "y": "y\r", "n": "n\r"}


def paint(row: Any, width: int) -> str:
    """One row of a pyte screen as text with SGR colour codes."""
    from pyte import graphics

    def colour(value: str, named: dict[str, int], base: int) -> str:
        if value == "default":
            return ""
        if value in named:
            return str(named[value])
        if re.fullmatch(r"[0-9a-fA-F]{6}", value):
            r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
            return f"{base};2;{r};{g};{b}"
        return ""

    fg = {name: code for code, name in {**graphics.FG_ANSI, **graphics.FG_AIXTERM}.items()}
    bg = {name: code for code, name in {**graphics.BG_ANSI, **graphics.BG_AIXTERM}.items()}
    cells = [row[x] for x in range(width)]
    while cells and cells[-1].data == " " and cells[-1].bg == "default" \
            and not cells[-1].reverse:
        cells.pop()
    out, style = [], None
    for cell in cells:
        codes = [colour(cell.fg, fg, 38), colour(cell.bg, bg, 48),
                 "1" if cell.bold else "", "3" if cell.italics else "",
                 "4" if cell.underscore else "", "7" if cell.reverse else ""]
        now_style = ";".join(c for c in codes if c)
        if now_style != style:
            out.append(f"\x1b[0;{now_style}m" if now_style else "\x1b[0m")
            style = now_style
        out.append(cell.data)  # "" after a wide character
    return "".join(out) + ("\x1b[0m" if style else "")


class Terminal:
    """One live shell on the dashboard's Terminal tab, shared by you and Jarvis.

    Output is drawn on a virtual screen (pyte) as it arrives. That screen is
    what Jarvis reads — plain text, as a person would see it — and what a
    browser opening the tab later is painted from (`redraw`).
    `log` remembers what was typed and by whom, so Jarvis keeps the thread of
    each terminal.
    """

    def __init__(self, id: str, title: str, purpose: str, opened_by: str, process: Pty,
                 cols: int, rows: int, on_exit: Callable[["Terminal"], None]) -> None:
        import pyte

        self.id, self.title, self.purpose, self.opened_by = id, title, purpose, opened_by
        self.process, self.cols, self.rows, self.on_exit = process, cols, rows, on_exit
        self.screen = pyte.HistoryScreen(cols, rows, history=3000)
        self.stream = pyte.Stream(self.screen)
        self.listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self.log: deque[dict[str, str]] = deque(maxlen=40)
        self.status = "running"
        self.exit_code: int | None = None
        self.created_at = now()
        self.last_output = self.last_input = time.monotonic()
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._pump, daemon=True, name=f"pty-{id}").start()

    # -- output, from the reader thread ------------------------------------
    def _pump(self) -> None:
        while True:
            try:
                data = self.process.read(65536)
            except (EOFError, OSError):
                break
            if data:
                try:
                    self._loop.call_soon_threadsafe(self._output, data)
                except RuntimeError:  # the server is shutting down
                    return
            elif not self.process.isalive():
                break
            else:
                time.sleep(0.02)
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._exited)

    def _output(self, data: str) -> None:
        self.last_output = time.monotonic()
        queries = QUERIES.findall(data)
        data = QUERIES.sub("", data)
        with contextlib.suppress(Exception):  # an odd escape code must not stop the pump
            self.stream.feed(data)
        for query in queries:
            cursor = self.screen.cursor
            reply = DA_REPLY if query.endswith("c") else f"\x1b[{cursor.y + 1};{cursor.x + 1}R"
            with contextlib.suppress(Exception):
                self.process.write(reply)
        if data:
            self._send({"type": "output", "data": data})

    def _exited(self) -> None:
        if self.status != "running":
            return
        self.status = "exited"
        self.exit_code = getattr(self.process, "exitstatus", None)
        self._send({"type": "exit", "code": self.exit_code})
        self.on_exit(self)

    def _send(self, message: dict[str, Any]) -> None:
        for queue in list(self.listeners):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(message)

    @contextlib.contextmanager
    def subscribe(self) -> Iterator["asyncio.Queue[dict[str, Any]]"]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self.listeners.add(queue)
        try:
            yield queue
        finally:
            self.listeners.discard(queue)

    # -- what's on screen --------------------------------------------------
    def lines(self, count: int = 60) -> list[str]:
        """The last `count` lines of scrollback and screen, as plain text."""
        width = self.screen.columns
        past = ["".join(line[x].data for x in range(width)).rstrip()
                for line in self.screen.history.top]
        text = past + [row.rstrip() for row in self.screen.display]
        while text and not text[-1]:
            text.pop()
        return text[-count:]

    def redraw(self) -> str:
        """Scrollback and screen as escape codes, colours included, that paint
        a fresh terminal of this size to look exactly like this one."""
        rows = [*self.screen.history.top, *(self.screen.buffer[y] for y in range(self.rows))]
        painted = "\r\n".join(paint(row, self.screen.columns) for row in rows)
        cursor = self.screen.cursor
        return f"{painted}\x1b[0m\x1b[{cursor.y + 1};{cursor.x + 1}H"

    def _unwrap(self, rows: list[str]) -> str:
        """The last row joined to the full rows it wrapped on from — a prompt
        for a deep folder wraps across several."""
        line = rows[-1].rstrip() if rows else ""
        for row in reversed(rows[-8:-1]):
            if len(row.rstrip()) < self.screen.columns:
                break
            line = row + line
        return line

    @property
    def at_prompt(self) -> bool:
        rows = list(self.screen.display)
        while rows and not rows[-1].strip():
            rows.pop()
        return self.status == "running" and bool(PROMPT.match(self._unwrap(rows)))

    # -- input -------------------------------------------------------------
    def send(self, data: str, shown: str) -> None:
        """Jarvis typing: `shown` is what goes in the log."""
        self._write(data)
        self.note("jarvis", shown)

    async def keys(self, data: str) -> None:
        """Your keystrokes, from the dashboard. On Enter, the command is noted
        as the screen shows it after the prompt — right even after tab
        completion or arrow-key history. The screen trails the keys by a few
        milliseconds, so first wait (briefly) for the echo to catch up."""
        while "\r" in data:
            before, _, data = data.partition("\r")
            if before:
                self._write(before)
            await self._echoed()
            line = self._unwrap(self.screen.display[:self.screen.cursor.y + 1]).strip()
            typed = PROMPT_PREFIX.sub("", line, count=1).strip()
            if not typed:  # a shell still starting up may not have echoed it
                typed = CONTROL.sub("", before).strip()
            if typed and not PROMPT.match(typed):
                self.note("you", typed)
            self._write("\r")
        if data:
            self._write(data)

    def _write(self, data: str) -> None:
        if self.status != "running":
            raise JarvisError(f"{self.title} has exited — open a new terminal.")
        self.last_input = time.monotonic()
        self.process.write(data)

    async def _echoed(self, limit: float = 1.0) -> None:
        start = time.monotonic()
        while time.monotonic() - start < limit:
            if self.last_output > self.last_input and time.monotonic() - self.last_output > 0.04:
                return
            await asyncio.sleep(0.02)

    def note(self, by: str, text: str) -> None:
        self.log.append({"at": now(), "by": by, "text": text[:300]})

    def close(self) -> None:
        self.status = "closed"
        self._send({"type": "closed"})
        with contextlib.suppress(Exception):
            self.process.terminate(force=True)

    async def settle(self, limit: float) -> None:
        """Wait for a command's output to pause: back at the prompt and quiet,
        quiet for a few seconds (it may want input), or `limit` seconds."""
        start = time.monotonic()
        await asyncio.sleep(0.3)
        while time.monotonic() - start < limit and self.status == "running":
            quiet = time.monotonic() - self.last_output
            if (quiet >= 0.5 and self.at_prompt) or quiet >= 3:
                return
            await asyncio.sleep(0.1)

    async def ready(self, limit: float = 10) -> None:
        """Wait for a new shell's first prompt."""
        start = time.monotonic()
        while time.monotonic() - start < limit and self.status == "running":
            if self.at_prompt:
                return
            await asyncio.sleep(0.1)

    # -- descriptions ------------------------------------------------------
    @property
    def state(self) -> str:
        if self.status != "running":
            return "exited" if self.exit_code is None else f"exited with code {self.exit_code}"
        return "at its prompt" if self.at_prompt else "busy or waiting for input"

    def summary(self) -> dict[str, Any]:
        """For Jarvis: what this terminal is, what it's for, and what was typed."""
        return {"terminal_id": self.id, "title": self.title, "purpose": self.purpose,
                "opened_by": self.opened_by, "state": self.state,
                "recent_input": [f"{e['by']}: {e['text']}" for e in list(self.log)[-6:]]}

    def out(self) -> dict[str, Any]:
        """For the dashboard."""
        return {"id": self.id, "title": self.title, "purpose": self.purpose,
                "opened_by": self.opened_by, "status": self.status, "state": self.state,
                "at_prompt": self.at_prompt, "exit_code": self.exit_code,
                "created_at": self.created_at, "cols": self.cols, "rows": self.rows,
                "log": list(self.log)[-10:]}


class Hub:
    """The event stream: every change is one frame, pushed to every open page."""

    def __init__(self, voice_enabled: bool) -> None:
        self.history: deque[dict[str, Any]] = deque(maxlen=300)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.voice_enabled = voice_enabled
        self._seq = 0

    def emit(self, type: str, message: str, *, task_id: str | None = None,
             tool: str | None = None, speak: bool = False,
             data: dict[str, Any] | None = None) -> dict[str, Any]:
        self._seq += 1
        frame: dict[str, Any] = {
            "seq": self._seq, "type": type, "message": message, "task_id": task_id,
            "created_at": now(), "data": dict(data or {}), "tool": tool,
            "speak": speak and self.voice_enabled,
        }
        self.history.append(frame)
        for queue in list(self.subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(frame)
        line = message.replace("\n", " ")[:110]
        # The console log is a courtesy. A console that can't encode a
        # character (or has gone away) must never break the work itself.
        with contextlib.suppress(Exception):
            print(f"  {datetime.now():%H:%M:%S}  {type:<18} {line}", flush=True)
        return frame

    @contextlib.contextmanager
    def subscribe(self) -> Iterator["asyncio.Queue[dict[str, Any]]"]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self.subscribers.add(queue)
        try:
            yield queue
        finally:
            self.subscribers.discard(queue)


# ======================================================== tool plumbing ===

WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog",
    48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[dict[str, Any]]]
    describe: Callable[[dict[str, Any]], str]
    risky: bool = False

    def declaration(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "parametersJsonSchema": self.parameters}


def params(required: list[str] | None = None, **props: str) -> dict[str, Any]:
    """A JSON schema from name="type: description" pairs."""
    properties = {}
    for name, spec in props.items():
        kind, _, description = spec.partition(":")
        properties[name] = {"type": kind.strip(), "description": description.strip()}
    return {"type": "object", "properties": properties, "required": required or []}


def ps_quote(value: str | Path) -> str:
    """A PowerShell single-quoted literal: nothing inside is interpreted."""
    return "'" + str(value).replace("'", "''") + "'"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "job"


def trim(text: str, limit: int = 6000) -> str:
    """Keep the start and the end of long output — errors tend to be at the end."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n…[{len(text) - limit} characters cut]…\n{text[-half:]}"


def html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", page)
    page = html.unescape(re.sub(r"<[^>]+>", " ", page))
    page = re.sub(r"[ \t\r\f\v]+", " ", page)
    return re.sub(r"\n\s*\n+", "\n\n", page).strip()


def is_public_host(host: str) -> bool:
    """False for anything on this machine or the local network."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


# ================================================================ jarvis ===


class Jarvis:
    """The conversation with Gemini, the tools it may use, and the terminals it shares."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self.hub = Hub(voice_enabled=self.can_speak)
        self.tasks: dict[str, Task] = {}
        self.approvals: dict[str, Approval] = {}
        self.terminals: dict[str, Terminal] = {}
        self.spawn_shell: Callable[[Path, int, int], Pty] = spawn_shell
        self._terminal_count = 0
        self.view_size = (120, 30)
        self.chat: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.tools = {tool.name: tool for tool in self._tools()}
        settings.workspace.mkdir(parents=True, exist_ok=True)

    @property
    def can_speak(self) -> bool:
        if self.settings.voice.lower() in {"", "off", "none", "false"}:
            return False
        return importlib.util.find_spec("edge_tts") is not None

    # -- requests -------------------------------------------------------
    def submit(self, request: str) -> Task:
        task = Task(request=request.strip())
        self.tasks[task.id] = task
        self.hub.emit("task.created", task.request, task_id=task.id)
        asyncio.get_running_loop().create_task(self._run(task))
        return task

    async def _run(self, task: Task) -> None:
        async with self.lock:  # one conversation: requests take turns
            task.set("running")
            self.hub.emit("task.started", "Thinking…", task_id=task.id)
            try:
                answer = await self._converse(task)
            except JarvisError as exc:
                task.error = str(exc)
                task.set("failed")
                self.hub.emit("task.failed", task.error, task_id=task.id, speak=True)
            except Exception as exc:  # noqa: BLE001 - one bad request must not stop the server
                task.error = f"Unexpected error: {type(exc).__name__}: {exc}"
                task.set("failed")
                self.hub.emit("task.failed", task.error, task_id=task.id)
            else:
                task.result = answer
                task.set("completed")
                self.hub.emit("task.completed", answer, task_id=task.id, speak=True,
                              data={"request": task.request})

    async def _converse(self, task: Task) -> str:
        """Gemini's tool loop: run the tools it asks for until it answers in words."""
        user_turn = {"role": "user", "parts": [{"text": task.request}]}
        contents = [*self.chat[-24:], user_turn]
        declarations = [t.declaration() for t in self.tools.values()]
        for _ in range(10):
            data = await self._gemini({
                "systemInstruction": {"parts": [{"text": self.persona()}]},
                "contents": contents,
                "tools": [{"functionDeclarations": declarations}],
                "generationConfig": self._generation_config(),
            })
            candidate = (data.get("candidates") or [{}])[0]
            parts = [p for p in (candidate.get("content") or {}).get("parts") or []
                     if isinstance(p, dict)]
            calls = [p["functionCall"] for p in parts if isinstance(p.get("functionCall"), dict)]
            if not calls:
                answer = "".join(p["text"] for p in parts
                                 if isinstance(p.get("text"), str) and not p.get("thought"))
                answer = answer.strip() or self._no_answer(data, candidate)
                self.chat += [user_turn, {"role": "model", "parts": [{"text": answer}]}]
                return answer
            # The model turn goes back verbatim: Gemini 3 needs its
            # thoughtSignature parts returned exactly as they came.
            contents.append({"role": "model", "parts": parts})
            replies = []
            for call in calls:
                result = await self._call_tool(task, call)
                reply: dict[str, Any] = {"name": call.get("name", ""),
                                         "response": {"result": result}}
                if call.get("id"):
                    reply["id"] = call["id"]
                replies.append({"functionResponse": reply})
            contents.append({"role": "user", "parts": replies})
        raise JarvisError("I went round in circles on that one — try asking more specifically.")

    @staticmethod
    def _no_answer(data: dict[str, Any], candidate: dict[str, Any]) -> str:
        feedback = data.get("promptFeedback") or {}
        reason = candidate.get("finishReason") or feedback.get("blockReason")
        if reason in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}:
            return "I'm afraid I can't help with that one."
        if reason == "MAX_TOKENS":
            raise JarvisError("Gemini ran out of room before answering — ask something narrower.")
        raise JarvisError(f"Gemini gave an empty answer ({reason or 'no reason given'}).")

    def _generation_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"maxOutputTokens": 8192}
        if self.settings.thinking in {"minimal", "low", "medium", "high"}:
            config["thinkingConfig"] = {"thinkingLevel": self.settings.thinking.upper()}
        return config

    def persona(self) -> str:
        """persona.md with its {{placeholders}} filled in. Re-read every time,
        so edits apply without a restart."""
        try:
            text = self.settings.persona_file.read_text(encoding="utf-8")
        except OSError:
            text = "You are JARVIS, a concise, capable personal assistant."
        s = self.settings
        values = {
            "date": datetime.now().strftime("%A %d %B %Y, %H:%M"),
            "os": f"{platform.system()} {platform.release()}",
            "shell": "Windows PowerShell" if WINDOWS else "bash",
            "workspace": str(s.workspace),
            "home": s.home_location or "not set — ask where, if it matters",
            "approval": ("Commands run straight away (AUTO_APPROVE is on)." if s.auto_approve
                         else "Each command waits for the user to press Allow on the dashboard."),
            "terminals": self.terminals_summary(),
        }
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        return text

    # -- gemini -----------------------------------------------------------
    async def _gemini(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{GEMINI_API}/models/{self.settings.gemini_model}:generateContent"
        headers = {"x-goog-api-key": self.settings.gemini_api_key}
        for attempt in range(4):
            try:
                response = await self.http.post(url, json=body, headers=headers, timeout=120)
            except httpx.TimeoutException as exc:
                raise JarvisError("Gemini didn't answer within two minutes.") from exc
            except httpx.RequestError as exc:
                raise JarvisError("Can't reach Gemini — check the internet connection.") from exc
            if response.status_code in {429, 500, 503} and attempt < 3:
                wait = self._retry_after(response, attempt)
                if wait is not None:
                    self.hub.emit("step.retrying", f"Gemini is busy — retrying in {wait:.0f}s.")
                    await asyncio.sleep(wait)
                    continue
            if response.status_code >= 400:
                raise JarvisError(self._gemini_error(response))
            result: dict[str, Any] = response.json()
            return result
        raise JarvisError("Gemini stayed busy — try again in a minute.")

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float | None:
        """Wait as long as Google asks — but never for a per-day quota."""
        with contextlib.suppress(ValueError, KeyError, AttributeError, TypeError):
            for detail in response.json()["error"].get("details", []):
                match = re.match(r"^(\d+(?:\.\d+)?)s$", str(detail.get("retryDelay", "")))
                if match:
                    seconds = float(match.group(1))
                    return seconds if seconds <= 60 else None
        return float(2 ** (attempt + 1))

    def _gemini_error(self, response: httpx.Response) -> str:
        try:
            detail = str(response.json()["error"]["message"])
        except (ValueError, KeyError, TypeError):
            detail = response.text[:300]
        status = response.status_code
        if status in {401, 403} or "api key" in detail.lower():
            return f"Gemini rejected the API key — check GEMINI_API_KEY in .env ({KEY_URL})."
        if status == 404:
            return f"Gemini has no model {self.settings.gemini_model!r} — check GEMINI_MODEL."
        if status == 429:
            return "Gemini's free-tier limit is used up for now — wait a minute and try again."
        if "thinking" in detail.lower():
            return f"This model doesn't take GEMINI_THINKING={self.settings.thinking!r}: {detail}"
        return f"Gemini error {status}: {detail}"

    async def check_gemini(self) -> str:
        """One model lookup at startup — proves the key without using quota."""
        try:
            response = await self.http.get(
                f"{GEMINI_API}/models/{self.settings.gemini_model}",
                headers={"x-goog-api-key": self.settings.gemini_api_key}, timeout=15)
        except httpx.RequestError:
            return "not reachable (offline?)"
        if response.status_code >= 400:
            return self._gemini_error(response)
        name = response.json().get("displayName", self.settings.gemini_model)
        return f"connected — {name}"

    # -- tools ------------------------------------------------------------
    def _tools(self) -> list[Tool]:
        return [
            Tool("get_weather",
                 "Current weather and a 3-day forecast for a place. Live data.",
                 params(["location"], location="string: city or place, e.g. 'Cape Town'"),
                 self.get_weather, lambda a: f"Weather for {a.get('location', '?')}"),
            Tool("get_news",
                 "Current news headlines, optionally about a topic. Live, from Google News.",
                 params(topic="string: what the news should be about; empty for top stories"),
                 self.get_news, lambda a: f"News: {a.get('topic') or 'top stories'}"),
            Tool("web_search",
                 "Search the web. Returns titles, links and snippets; read_webpage reads one.",
                 params(["query"], query="string: what to search for"),
                 self.web_search, lambda a: f"Searching: {a.get('query', '')}"),
            Tool("read_webpage",
                 "Read the text of a web page (http/https).",
                 params(["url"], url="string: the page address"),
                 self.read_webpage, lambda a: f"Reading {a.get('url', '')}"),
            Tool("run_command",
                 "Run a quick terminal command on the user's PC, out of sight, and get its "
                 "output (versions, git status, listing things, opening an app). For anything "
                 "long-running or interactive use terminal_open instead.",
                 params(["command"],
                        command="string: the full command line",
                        timeout_seconds="integer: default 60, at most 600"),
                 self.run_command, lambda a: f"Terminal: {a.get('command', '')}", risky=True),
            Tool("terminal_open",
                 "Open a new live terminal on the dashboard's Terminal tab, optionally running "
                 "a command in it. Use it for installs, builds, servers, anything long or "
                 "interactive. The user can watch and type in it too. Returns its terminal_id.",
                 params(["title"],
                        title="string: a short name, e.g. 'npm install' or 'dev server'",
                        purpose="string: one line on what this terminal is for",
                        command="string: a command to run in it straight away; optional"),
                 self.terminal_open, lambda a: f"Opening terminal: {a.get('title', '')}",
                 risky=True),
            Tool("terminal_write",
                 "Type into an open terminal — a command, an answer to a prompt, or a key "
                 "like ctrl+c — then return what its screen shows.",
                 params(["terminal_id"],
                        terminal_id="string: which terminal, e.g. 'term-2'",
                        text="string: what to type",
                        key="string: press a key instead: " + ", ".join(KEYS),
                        press_enter="boolean: press Enter after the text; default true",
                        wait_seconds="integer: how long to wait for output; default 10, max 60"),
                 self.terminal_write,
                 lambda a: f"Typing into {a.get('terminal_id', '?')}: "
                           f"{a.get('key') or a.get('text', '')}", risky=True),
            Tool("terminal_read",
                 "Read what an open terminal's screen and recent scrollback show right now.",
                 params(["terminal_id"],
                        terminal_id="string: which terminal, e.g. 'term-2'",
                        lines="integer: how many lines from the bottom; default 60, max 300"),
                 self.terminal_read, lambda a: f"Reading {a.get('terminal_id', '?')}"),
            Tool("terminal_list",
                 "List the open terminals: id, title, purpose, state, and what was typed.",
                 params(),
                 self.terminal_list, lambda a: "Checking the terminals"),
            Tool("read_workspace_file",
                 "Read a text file from the workspace folder.",
                 params(["path"], path="string: path relative to the workspace"),
                 self.read_workspace_file, lambda a: f"Reading {a.get('path', '')}"),
        ]

    async def _call_tool(self, task: Task, call: dict[str, Any]) -> dict[str, Any]:
        name = str(call.get("name", ""))
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"There is no tool called {name!r}."}
        raw = call.get("args")
        args = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items()
                if k in tool.parameters["properties"]}
        step = Step(description=tool.describe(args), tool=name,
                    risk="confirm" if tool.risky and not self.settings.auto_approve else "safe")
        task.steps.append(step)
        self.hub.emit("step.started", step.description, task_id=task.id, tool=name)
        try:
            result = await tool.handler(task, **args)
        except JarvisError as exc:
            result = {"error": str(exc)}
        except TypeError as exc:
            result = {"error": f"Bad arguments for {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - tools report failure, never crash the loop
            result = {"error": f"{type(exc).__name__}: {exc}"}
        failed = "error" in result or bool(result.get("denied"))
        step.status = "failed" if failed else "completed"
        step.error = str(result.get("error") or "denied") if failed else None
        self.hub.emit("step.failed" if failed else "step.completed", step.description,
                      task_id=task.id, tool=name)
        return result

    async def _get(self, url: str, **query: Any) -> httpx.Response:
        host = urlparse(url).netloc
        try:
            response = await self.http.get(url, params=query or None, timeout=20,
                                           follow_redirects=True,
                                           headers={"User-Agent": BROWSER_UA})
        except httpx.RequestError as exc:
            raise JarvisError(f"Couldn't reach {host}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise JarvisError(f"{host} answered {response.status_code}.")
        return response

    async def get_weather(self, task: Task, location: str) -> dict[str, Any]:
        geo = (await self._get("https://geocoding-api.open-meteo.com/v1/search",
                               name=location, count=1, language="en")).json()
        if not geo.get("results"):
            return {"error": f"Couldn't find a place called {location!r}."}
        place = geo["results"][0]
        forecast = (await self._get(
            "https://api.open-meteo.com/v1/forecast",
            latitude=place["latitude"], longitude=place["longitude"], timezone="auto",
            forecast_days=3,
            current="temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,weather_code,wind_speed_10m",
            daily="weather_code,temperature_2m_max,temperature_2m_min,"
                  "precipitation_probability_max,sunrise,sunset",
        )).json()
        current = forecast.get("current", {})
        daily = forecast.get("daily", {})
        columns = zip(daily.get("time", []), daily.get("weather_code", []),
                      daily.get("temperature_2m_max", []), daily.get("temperature_2m_min", []),
                      daily.get("precipitation_probability_max", []),
                      daily.get("sunrise", []), daily.get("sunset", []), strict=False)
        days = [{"date": day, "conditions": WMO.get(code, f"code {code}"), "high": high,
                 "low": low, "rain_chance_percent": rain, "sunrise": rise, "sunset": sets}
                for day, code, high, low, rain, rise, sets in columns]
        where = [place.get("name"), place.get("admin1"), place.get("country")]
        return {"place": ", ".join(str(p) for p in where if p),
                "now": {**current, "conditions": WMO.get(current.get("weather_code"), "?")},
                "units": forecast.get("current_units", {}), "next_days": days}

    async def get_news(self, task: Task, topic: str = "") -> dict[str, Any]:
        s = self.settings
        edition = {"hl": f"{s.news_language}-{s.news_country}", "gl": s.news_country,
                   "ceid": f"{s.news_country}:{s.news_language}"}
        if topic.strip():
            url = "https://news.google.com/rss/search?" + urlencode({"q": topic, **edition})
        else:
            url = "https://news.google.com/rss?" + urlencode(edition)
        root = ET.fromstring((await self._get(url)).text)
        headlines = [{"title": item.findtext("title", ""), "source": item.findtext("source", ""),
                      "published": item.findtext("pubDate", ""), "link": item.findtext("link", "")}
                     for item in list(root.iter("item"))[:8]]
        return {"topic": topic or "top stories", "headlines": headlines}

    async def web_search(self, task: Task, query: str) -> dict[str, Any]:
        try:
            response = await self.http.post("https://html.duckduckgo.com/html/",
                                            data={"q": query}, timeout=20,
                                            headers={"User-Agent": BROWSER_UA})
        except httpx.RequestError as exc:
            raise JarvisError("The web search didn't respond.") from exc
        pattern = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
                             r'.*?class="result__snippet"[^>]*>(.*?)</a>', re.S)
        results = []
        for href, title, snippet in pattern.findall(response.text)[:8]:
            if "uddg=" in href:
                href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            results.append({"title": html_to_text(title), "url": html.unescape(href),
                            "snippet": html_to_text(snippet)})
        if not results:
            return {"error": "No results — the search may be busy; try get_news instead."}
        return {"query": query, "results": results}

    async def read_webpage(self, task: Task, url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {"error": "Only http(s) addresses can be read."}
        if not is_public_host(parsed.hostname):
            return {"error": "Addresses on this computer or the local network are off limits."}
        response = await self._get(url)
        text = response.text
        if "html" in response.headers.get("content-type", ""):
            text = html_to_text(text)
        return {"url": str(response.url), "text": trim(text, 8000)}

    async def read_workspace_file(self, task: Task, path: str) -> dict[str, Any]:
        root = self.settings.workspace.resolve()
        target = (root / path).resolve()
        if root != target and root not in target.parents:
            return {"error": "That path is outside the workspace."}
        if not target.is_file():
            return {"error": f"No file at {path!r} in the workspace."}
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "text": trim(text, 12000)}

    async def run_command(self, task: Task, command: str,
                          timeout_seconds: int = 60) -> dict[str, Any]:
        command = command.strip()
        if not command:
            return {"error": "The command was empty."}
        if not await self._approve(task, "run_command", {"command": command},
                                   f"Run and wait for the output: {command}"):
            return {"denied": True,
                    "note": "The user did not allow this command. Don't retry unless asked."}
        timeout = max(5, min(int(timeout_seconds or 60), 600))
        return await asyncio.to_thread(self._run_captured, command, timeout)

    def _run_captured(self, command: str, timeout: int) -> dict[str, Any]:
        if WINDOWS:
            utf8 = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
            args = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-Command", utf8 + command]
        else:
            args = ["bash", "-lc", command]
        try:
            proc = subprocess.run(args, cwd=self.settings.workspace, capture_output=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or b"").decode("utf-8", "replace")
            return {"timed_out": True, "output": trim(partial, 3000),
                    "note": f"Stopped after {timeout}s. Use terminal_open for long commands."}
        except FileNotFoundError as exc:
            return {"error": f"Couldn't start the shell: {exc}"}
        output = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        return {"exit_code": proc.returncode, "output": trim(output.strip() or "(no output)")}

    # -- approvals ----------------------------------------------------------
    async def _approve(self, task: Task, tool: str, arguments: dict[str, Any],
                       reason: str) -> bool:
        if self.settings.auto_approve:
            return True
        approval = Approval(task_id=task.id, tool=tool, arguments=arguments, reason=reason,
                            future=asyncio.get_running_loop().create_future())
        self.approvals[approval.id] = approval
        self.hub.emit("approval.required", f"Jarvis wants to: {reason}", task_id=task.id,
                      tool=tool, speak=True,
                      data={"approval_id": approval.id, "arguments": arguments})
        try:
            decision = await asyncio.wait_for(approval.future, timeout=600)
        except TimeoutError:
            decision = "deny"
        finally:
            self.approvals.pop(approval.id, None)
        verdict = "Allowed" if decision == "allow" else "Denied"
        self.hub.emit("approval.resolved", f"{verdict}: {reason}", task_id=task.id, tool=tool)
        return decision == "allow"

    def decide(self, approval_id: str, decision: str) -> None:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        if not approval.future.done():
            approval.future.set_result(decision)

    # -- terminals ----------------------------------------------------------
    MAX_TERMINALS = 8

    def open_terminal(self, title: str, purpose: str = "", opened_by: str = "you",
                      cols: int | None = None, rows: int | None = None) -> Terminal:
        """A new shell. Its size is fixed for life — the page scales its font to
        fit — because shrinking a terminal cuts its lines (pyte doesn't reflow).
        Without a size it takes the size of the last one the page opened."""
        if cols and rows:
            self.view_size = (cols, rows)
        cols, rows = self.view_size
        if len(self.terminals) >= self.MAX_TERMINALS:
            raise JarvisError(f"{self.MAX_TERMINALS} terminals are open already — "
                              "close one on the Terminal tab first.")
        self._terminal_count += 1
        terminal_id = f"term-{self._terminal_count}"
        title = " ".join(title.split())[:60] or f"Terminal {self._terminal_count}"
        process = self.spawn_shell(self.settings.workspace, cols, rows)
        terminal = Terminal(terminal_id, title, " ".join(purpose.split())[:300], opened_by,
                            process, cols, rows, self._terminal_exited)
        self.terminals[terminal_id] = terminal
        who = "Jarvis opened" if opened_by == "jarvis" else "Opened"
        self.hub.emit("terminal.opened", f"{who} a terminal: {title}",
                      data={"terminal": terminal.out()})
        return terminal

    def _terminal_exited(self, terminal: Terminal) -> None:
        self.hub.emit("terminal.exited", f"{terminal.title}: the shell {terminal.state}.",
                      data={"terminal": terminal.out()})

    def close_terminal(self, terminal_id: str) -> None:
        terminal = self.terminals.pop(terminal_id)
        terminal.close()
        self.hub.emit("terminal.closed", f"Closed the terminal: {terminal.title}",
                      data={"terminal_id": terminal_id})

    def close_all_terminals(self) -> None:
        for terminal in self.terminals.values():
            terminal.close()
        self.terminals.clear()

    def terminals_summary(self) -> str:
        """The terminals as a few lines of the system prompt, so Jarvis always
        knows which exist, what each is for, and what was last done in it."""
        if not self.terminals:
            return "None open right now."
        lines = []
        for t in self.terminals.values():
            line = f'- `{t.id}` "{t.title}" — opened by {t.opened_by}, {t.state}.'
            if t.purpose:
                line += f" For: {t.purpose}."
            recent = "; ".join(f"{e['by']}: {e['text']}" for e in list(t.log)[-3:])
            if recent:
                line += f" Last typed — {recent}"
            lines.append(line)
        return "\n".join(lines)

    def _terminal(self, terminal_id: str) -> Terminal:
        terminal = self.terminals.get(str(terminal_id).strip())
        if terminal is None:
            known = ", ".join(self.terminals) or "none are open"
            raise JarvisError(f"There's no terminal {terminal_id!r} ({known}).")
        return terminal

    @staticmethod
    def _screen(terminal: Terminal, lines: int = 40) -> dict[str, Any]:
        return {**terminal.summary(), "screen": "\n".join(terminal.lines(lines))}

    async def terminal_open(self, task: Task, title: str, purpose: str = "",
                            command: str = "") -> dict[str, Any]:
        command = command.strip()
        if command and not await self._approve(
                task, "terminal_open", {"title": title, "command": command},
                f'Open a terminal "{title}" and run: {command}'):
            return {"denied": True,
                    "note": "The user did not allow this command. Don't retry unless asked."}
        terminal = self.open_terminal(title, purpose, opened_by="jarvis")
        if not command:
            return {**terminal.summary(), "note": "Open on the Terminal tab, at its prompt."}
        await terminal.ready()
        terminal.send(command + "\r", shown=command)
        await terminal.settle(limit=8)
        result = self._screen(terminal)
        if not terminal.at_prompt:
            result["note"] = ("Still going on the Terminal tab. terminal_read checks on it "
                              "later; don't wait for it.")
        return result

    async def terminal_write(self, task: Task, terminal_id: str, text: str = "", key: str = "",
                             press_enter: bool = True, wait_seconds: int = 10) -> dict[str, Any]:
        terminal = self._terminal(terminal_id)
        if key:
            data = KEYS.get(key.lower().replace(" ", ""))
            if data is None:
                return {"error": f"Unknown key {key!r} — use one of: {', '.join(KEYS)}."}
            shown = f"[{key.lower()}]"
        else:
            if not text:
                return {"error": "Nothing to type — give text or a key."}
            data = text.replace("\r\n", "\r").replace("\n", "\r") + ("\r" if press_enter else "")
            shown = text
        if not await self._approve(
                task, "terminal_write",
                {"terminal_id": terminal.id, "title": terminal.title, "text": shown},
                f'Type into "{terminal.title}" ({terminal.id}): {shown}'):
            return {"denied": True,
                    "note": "The user did not allow this. Don't retry unless asked."}
        terminal.send(data, shown=shown)
        self.hub.emit("terminal.input", f"Jarvis typed into {terminal.title}: {shown}",
                      task_id=task.id, data={"terminal_id": terminal.id})
        await terminal.settle(limit=max(1, min(int(wait_seconds or 10), 60)))
        return self._screen(terminal)

    async def terminal_read(self, task: Task, terminal_id: str,
                            lines: int = 60) -> dict[str, Any]:
        return self._screen(self._terminal(terminal_id), max(5, min(int(lines or 60), 300)))

    async def terminal_list(self, task: Task) -> dict[str, Any]:
        if not self.terminals:
            return {"terminals": [], "note": "No terminals are open."}
        return {"terminals": [t.summary() for t in self.terminals.values()]}

    # -- dashboard data ---------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "workspace": str(self.settings.workspace),
            "tasks": [t.out() for t in self.recent_tasks()],
            "approvals": [a.out() for a in self.approvals.values()],
            "terminals": [t.out() for t in self.terminals.values()],
            "events": list(self.hub.history),
            "voice": self.voice_info(),
            "settings": {"particles": 900, "accent": "#22d3ee", "stats_interval": 2.0},
        }

    def recent_tasks(self, limit: int = 20) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]

    def voice_info(self) -> dict[str, Any]:
        if not self.can_speak:
            return {"enabled": False}
        return {"enabled": True, "can_speak": True, "can_listen": False,
                "voice": self.settings.voice, "provider": "edge", "wake_word": "jarvis",
                "wake_word_required": False, "listen_enabled": False,
                "speak_events": ["task.completed", "task.failed", "approval.required"]}

    async def speak(self, text: str) -> bytes:
        import edge_tts

        clean = re.sub(r"https?://\S+", "", text)  # links and markdown read badly aloud
        clean = re.sub(r"[*_#`>|]+", "", clean)[:1500]
        communicate = edge_tts.Communicate(clean, self.settings.voice, rate="+8%", pitch="-2Hz")
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)


def machine_stats() -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {"available": False}
    stats: dict[str, Any] = {"available": True}
    with contextlib.suppress(Exception):
        stats["cpu"] = psutil.cpu_percent(interval=None)
    with contextlib.suppress(Exception):
        mem = psutil.virtual_memory()
        stats["memory"] = {"percent": mem.percent, "used": mem.used, "total": mem.total}
    with contextlib.suppress(Exception):
        disk = psutil.disk_usage(Path.home().anchor or "/")
        stats["disk"] = {"percent": disk.percent, "used": disk.used, "total": disk.total}
    with contextlib.suppress(Exception):
        bat = psutil.sensors_battery()
        stats["battery"] = (None if bat is None else
                            {"percent": round(bat.percent), "plugged": bat.power_plugged})
    with contextlib.suppress(Exception):
        net = psutil.net_io_counters()
        stats["network"] = {"sent": net.bytes_sent, "received": net.bytes_recv}
    return stats


# =================================================================== api ===


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    submit: bool = True


class TaskIn(BaseModel):
    request: str = Field(min_length=1, max_length=8000)


class DecisionIn(BaseModel):
    decision: Literal["allow", "deny"]


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class TerminalIn(BaseModel):
    title: str = Field(default="", max_length=200)
    purpose: str = Field(default="", max_length=1000)
    cols: int | None = Field(default=None, ge=20, le=400)
    rows: int | None = Field(default=None, ge=5, le=200)


class TerminalInputIn(BaseModel):
    data: str = Field(min_length=1, max_length=65536)


def create_app(jarvis: Jarvis) -> FastAPI:
    """The HTTP API the dashboard uses — frontend/API.md describes each route."""
    settings = jarvis.settings
    has_frontend = (settings.frontend / "index.html").is_file()
    app = FastAPI(title="JARVIS", version="2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if "*" in settings.cors_origins else settings.cors_origins,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        token = (header[7:].strip() if header.lower().startswith("bearer ")
                 else request.query_params.get("token"))
        if not token or not hmac.compare_digest(token.encode(), settings.api_token.encode()):
            raise HTTPException(status_code=401, detail="Missing or wrong API token.")

    guard = [Depends(auth)]
    # Every route is async on purpose: plain `def` routes run on a worker
    # thread, where there is no event loop to start a task on, and where
    # resolving an approval's future would not be thread-safe.

    def event_stream() -> StreamingResponse:
        async def generate() -> AsyncIterator[str]:
            with jarvis.hub.subscribe() as queue:
                while True:
                    try:
                        frame = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(frame)}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # -- the dashboard pages' API ------------------------------------------
    @app.get("/dash/api/snapshot", dependencies=guard)
    async def snapshot() -> dict[str, Any]:
        return jarvis.snapshot()

    @app.get("/dash/api/stream", dependencies=guard)
    async def stream() -> StreamingResponse:
        return event_stream()

    # -- the Terminal tab: live shells shared with Jarvis ---------------------
    def terminal(terminal_id: str) -> Terminal:
        found = jarvis.terminals.get(terminal_id)
        if found is None:
            raise HTTPException(status_code=404, detail="That terminal is closed.")
        return found

    @app.get("/dash/api/terminals", dependencies=guard)
    async def list_terminals() -> list[dict[str, Any]]:
        return [t.out() for t in jarvis.terminals.values()]

    @app.post("/dash/api/terminals", dependencies=guard, status_code=201)
    async def open_terminal(body: TerminalIn) -> dict[str, Any]:
        try:
            return jarvis.open_terminal(body.title, body.purpose, "you", body.cols,
                                        body.rows).out()
        except JarvisError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/dash/api/terminals/{terminal_id}/stream", dependencies=guard)
    async def terminal_stream(terminal_id: str) -> StreamingResponse:
        live = terminal(terminal_id)

        async def generate() -> AsyncIterator[str]:
            with live.subscribe() as queue:
                first = {"type": "replay", "data": live.redraw(), "terminal": live.out()}
                yield f"data: {json.dumps(first)}\n\n"
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(message)}\n\n"
                    if message["type"] == "closed":
                        return

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/dash/api/terminals/{terminal_id}/input", dependencies=guard)
    async def terminal_input(terminal_id: str, body: TerminalInputIn) -> dict[str, bool]:
        try:
            await terminal(terminal_id).keys(body.data)
        except JarvisError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/dash/api/terminals/{terminal_id}/close", dependencies=guard)
    async def terminal_close(terminal_id: str) -> dict[str, bool]:
        terminal(terminal_id)
        jarvis.close_terminal(terminal_id)
        return {"ok": True}

    @app.get("/dash/api/stats", dependencies=guard)
    async def stats() -> dict[str, Any]:
        return machine_stats()

    @app.post("/dash/api/command", dependencies=guard)
    async def command(body: CommandIn) -> dict[str, Any]:
        text = re.sub(r"^\s*(hey\s+)?jarvis[\s,:.!-]*", "", body.text, flags=re.I).strip()
        text = text or body.text
        task = jarvis.submit(text) if body.submit else None
        return {"text": body.text, "addressed": True, "command": text,
                "submitted": task is not None, "task_id": task.id if task else None,
                "confidence": None, "details": {}}

    @app.post("/dash/api/listen", dependencies=guard)
    async def listen() -> None:
        raise HTTPException(status_code=503, detail="Listening isn't built in — type instead.")

    @app.post("/dash/api/speak", dependencies=guard)
    async def speak(body: SpeakIn) -> Response:
        if not jarvis.can_speak:
            raise HTTPException(status_code=503, detail="Speech is off — pip install edge-tts.")
        try:
            audio = await jarvis.speak(body.text)
        except Exception as exc:  # noqa: BLE001 - speech is a nicety; say why and move on
            raise HTTPException(status_code=503, detail=f"Speech failed: {exc}") from exc
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    # -- plain REST, for the HUD page and anything else ----------------------
    @app.get("/system", dependencies=guard)
    async def system() -> dict[str, Any]:
        active = sum(t.status in {"pending", "running"} for t in jarvis.tasks.values())
        return {"brain": {"provider": "gemini", "model": settings.gemini_model,
                          "connected": bool(settings.gemini_api_key)},
                "tools": sorted(jarvis.tools), "active_tasks": active,
                "total_tasks": len(jarvis.tasks), "workspace": str(settings.workspace)}

    @app.get("/tasks", dependencies=guard)
    async def list_tasks() -> list[dict[str, Any]]:
        return [t.out() for t in jarvis.recent_tasks(100)]

    @app.post("/tasks", dependencies=guard, status_code=201)
    async def create_task(body: TaskIn) -> dict[str, Any]:
        return jarvis.submit(body.request).out()

    @app.get("/tasks/{task_id}", dependencies=guard)
    async def get_task(task_id: str) -> dict[str, Any]:
        task = jarvis.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="No such task.")
        return task.out()

    @app.get("/approvals", dependencies=guard)
    async def list_approvals() -> list[dict[str, Any]]:
        return [a.out() for a in jarvis.approvals.values()]

    @app.post("/approvals/{approval_id}", dependencies=guard)
    async def decide(approval_id: str, body: DecisionIn) -> dict[str, str]:
        try:
            jarvis.decide(approval_id, body.decision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="That's no longer pending.") from exc
        return {"detail": f"Approval {body.decision}ed."}

    @app.get("/notifications", dependencies=guard)
    async def notifications(limit: int = 50) -> list[dict[str, Any]]:
        kinds = {"task.failed": "error", "error": "error", "approval.required": "approval"}
        frames = list(jarvis.hub.history)
        return [{"type": kinds.get(f["type"], "info"), "message": f["message"],
                 "task_id": f["task_id"], "created_at": f["created_at"]}
                for f in reversed(frames[-limit:])]

    @app.get("/events", dependencies=guard)
    async def events() -> StreamingResponse:
        return event_stream()

    # -- the frontend folder, served at /dash/ -------------------------------
    @app.get("/dash/config.js", include_in_schema=False)
    async def frontend_config() -> Response:
        own = settings.frontend / "config.js"
        base = own.read_text(encoding="utf-8") if own.is_file() else ""
        override = ("\n// Added by jarvis.py: pages served from here talk back to it.\n"
                    'window.HUD_CONFIG = Object.assign(window.HUD_CONFIG || {}, '
                    '{ server: "" });\n')
        return Response(base + override, media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})

    if has_frontend:
        # Windows can map .js to text/plain; browsers then refuse the modules.
        mimetypes.add_type("text/javascript", ".js")
        mimetypes.add_type("text/css", ".css")
        app.mount("/dash", StaticFiles(directory=settings.frontend, html=True), name="frontend")

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/dash/" if has_frontend else "/docs")

    return app


# ================================================================== main ===


async def serve(settings: Settings) -> None:
    import uvicorn

    async with httpx.AsyncClient() as http:
        jarvis = Jarvis(settings, http)
        approvals = ("automatic (AUTO_APPROVE=true)" if settings.auto_approve
                     else "asked on the dashboard")
        print(f"  Gemini     {settings.gemini_model}: {await jarvis.check_gemini()}")
        print(f"  Workspace  {settings.workspace}")
        print(f"  Commands   {approvals}")
        voice = settings.voice if jarvis.can_speak else "off (pip install edge-tts)"
        print(f"  Voice      {voice}")
        host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
        url = f"http://{host}:{settings.port}/dash/?token={settings.api_token}"
        has_frontend = (settings.frontend / "index.html").is_file()
        print(f"\n  Dashboard  {url if has_frontend else '(no frontend folder — API only)'}")
        print(f"  API docs   http://{host}:{settings.port}/docs\n  Ctrl-C to stop.\n", flush=True)
        if settings.open_browser and has_frontend:
            asyncio.get_running_loop().call_later(1.5, lambda: webbrowser.open(url))
        config = uvicorn.Config(create_app(jarvis), host=settings.host, port=settings.port,
                                log_level="warning")
        try:
            await uvicorn.Server(config).serve()
        finally:
            jarvis.close_all_terminals()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    env_file = HERE / ".env"
    if not env_file.exists() and (HERE / ".env.example").exists():
        shutil.copyfile(HERE / ".env.example", env_file)
        print(f"Created {env_file} from .env.example.")
    settings = Settings.load(env_file)
    print("\n  J.A.R.V.I.S.\n")
    if not settings.gemini_api_key:
        print(f"  No Gemini key yet. Get a free one at {KEY_URL}\n"
              f"  and paste it into {env_file} as GEMINI_API_KEY=...  then run this again.\n")
        return 1
    if not settings.api_token:
        settings.api_token = secrets.token_urlsafe(32)
        save_env_value(env_file, "JARVIS_API_TOKEN", settings.api_token)
        print(f"  Made a dashboard token and saved it to {env_file}.")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
