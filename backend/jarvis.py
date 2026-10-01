"""JARVIS — a Gemini-powered assistant behind a live dashboard, in one file.

    python jarvis.py

Gemini (a fast Flash-Lite model by default) answers questions itself and
uses a handful of tools: live weather, news, web search, reading pages, and
running terminal commands on this PC. Long-running commands get their own
terminal window and keep going while Jarvis replies.

Who Jarvis is and how it behaves lives in persona.md, not here. Settings
live in .env (see .env.example). The dashboard is the ../frontend folder;
the HTTP routes below are the ones its API.md describes.

Nothing reaches the terminal without your say-so: every command waits for
Allow/Deny on the dashboard unless AUTO_APPROVE=true.
"""

import asyncio
import base64
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
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
WINDOWS = platform.system() == "Windows"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
WISPR_URL = "https://platform-api.wisprflow.ai/api/v1/dash/api"
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
    voice_provider: str = "edge"
    listen_provider: str = "off"
    wispr_api_key: str = ""
    wispr_language: str = "en"
    openai_api_key: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "onyx"
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
            voice_provider=get("JARVIS_VOICE_PROVIDER", "edge").lower(),
            listen_provider=get("JARVIS_LISTEN_PROVIDER", "off").lower(),
            wispr_api_key=get("WISPR_API_KEY"),
            wispr_language=get("WISPR_LANGUAGE", "en").lower(),
            openai_api_key=get("OPENAI_API_KEY"),
            openai_tts_model=get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            openai_tts_voice=get("OPENAI_TTS_VOICE", "onyx").lower(),
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


@dataclass
class Agent:
    """A character in the dashboard's office: Jarvis, or a terminal window."""

    id: str
    name: str
    hue: int
    index: int
    status: str = "idle"
    room: str = "lobby"
    task_id: str | None = None
    tool: str | None = None
    description: str | None = None
    since: str = field(default_factory=now)

    def move(self, room: str, status: str, task: Task | None = None,
             tool: str | None = None, description: str | None = None) -> None:
        self.room, self.status, self.tool, self.description = room, status, tool, description
        self.task_id = task.id if task else None
        self.since = now()

    def out(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "hue": self.hue, "sprite": self.index,
                "index": self.index, "status": self.status, "room": self.room,
                "task_id": self.task_id, "step_id": None, "tool": self.tool,
                "description": self.description, "arguments": {}, "since": self.since}


@dataclass
class Job:
    """A long-running command in its own terminal window."""

    command: str
    folder: Path
    task: Task
    process: "subprocess.Popen[bytes]"
    agent: Agent | None
    id: str = field(default_factory=lambda: short_id("j"))
    status: str = "running"
    exit_code: int | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None

    @property
    def output(self) -> Path:
        return self.folder / "output.txt"

    @property
    def done_file(self) -> Path:
        return self.folder / ".done"


#: The office's rooms; ids match frontend/js/components/office-world.js.
ROOMS = [
    {"id": "lobby", "name": "Lobby", "subtitle": "standing by", "hue": 210, "desks": 2},
    {"id": "situation", "name": "Situation Room", "subtitle": "thinking", "hue": 280, "desks": 4},
    {"id": "web", "name": "Web Wing", "subtitle": "weather, news, web", "hue": 150, "desks": 4},
    {"id": "workshop", "name": "Workshop", "subtitle": "terminal commands", "hue": 30, "desks": 4},
    {"id": "library", "name": "Library", "subtitle": "long-running windows", "hue": 90,
     "desks": 2},
    {"id": "archives", "name": "Archives", "subtitle": "reading files", "hue": 190, "desks": 4},
    {"id": "observatory", "name": "Observatory", "subtitle": "checking on jobs", "hue": 320,
     "desks": 2},
]


class Hub:
    """The event stream: every change is one frame, pushed to every open page."""

    def __init__(self, voice_enabled: bool) -> None:
        self.history: deque[dict[str, Any]] = deque(maxlen=300)
        self.touched: deque[dict[str, Any]] = deque(maxlen=200)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.voice_enabled = voice_enabled
        self._seq = 0

    def emit(self, type: str, message: str, *, task_id: str | None = None,
             tool: str | None = None, agent: Agent | None = None, speak: bool = False,
             files: dict[str, Any] | None = None,
             data: dict[str, Any] | None = None) -> dict[str, Any]:
        self._seq += 1
        payload = dict(data or {})
        if agent is not None:
            payload["agent"] = agent.out()
        frame: dict[str, Any] = {
            "seq": self._seq, "type": type, "message": message, "task_id": task_id,
            "created_at": now(), "data": payload, "tool": tool,
            "agent_id": agent.id if agent else None, "room": agent.room if agent else None,
            "speak": speak and self.voice_enabled,
        }
        if files:
            frame["files"] = files
            for path in files.get("paths", []):
                self.touched.append({"path": path, "action": files["action"],
                                     "at": frame["created_at"], "agent_id": frame["agent_id"]})
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
    room: str
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
    """The conversation with Gemini, the tools it may use, and the windows it opens."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self.hub = Hub(voice_enabled=self.can_speak)
        self.tasks: dict[str, Task] = {}
        self.approvals: dict[str, Approval] = {}
        self.jobs: dict[str, Job] = {}
        self.chat: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.me = Agent("jarvis", "Jarvis", 190, 0)
        self.windows = [Agent("term-1", "Terminal 1", 30, 1),
                        Agent("term-2", "Terminal 2", 330, 2),
                        Agent("term-3", "Terminal 3", 140, 3)]
        self.tools = {tool.name: tool for tool in self._tools()}
        settings.workspace.mkdir(parents=True, exist_ok=True)

    @property
    def voice_provider(self) -> str:
        """Which engine speaks: "openai" if chosen and keyed, else "edge" if
        installed, else "" (silent). JARVIS_VOICE=off silences everything."""
        if self.settings.voice.lower() in {"", "off", "none", "false"}:
            return ""
        if self.settings.voice_provider == "openai" and self.settings.openai_api_key:
            return "openai"
        return "edge" if importlib.util.find_spec("edge_tts") is not None else ""

    @property
    def can_speak(self) -> bool:
        return bool(self.voice_provider)

    @property
    def listen_provider(self) -> str:
        """Who turns the microphone into text: "wispr" if chosen and keyed, else
        "" (typing only)."""
        if self.settings.listen_provider == "wispr" and self.settings.wispr_api_key:
            return "wispr"
        return ""

    @property
    def can_listen(self) -> bool:
        return bool(self.listen_provider)

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
            self.me.move("situation", "working", task, None, "Thinking")
            self.hub.emit("task.started", "Thinking…", task_id=task.id, agent=self.me)
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
            finally:
                self.me.move("lobby", "idle")
                self.hub.emit("agent.idle", "Jarvis is free.", agent=self.me)

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
                 self.get_weather, "web", lambda a: f"Weather for {a.get('location', '?')}"),
            Tool("get_news",
                 "Current news headlines, optionally about a topic. Live, from Google News.",
                 params(topic="string: what the news should be about; empty for top stories"),
                 self.get_news, "web", lambda a: f"News: {a.get('topic') or 'top stories'}"),
            Tool("web_search",
                 "Search the web. Returns titles, links and snippets; read_webpage reads one.",
                 params(["query"], query="string: what to search for"),
                 self.web_search, "web", lambda a: f"Searching: {a.get('query', '')}"),
            Tool("read_webpage",
                 "Read the text of a web page (http/https).",
                 params(["url"], url="string: the page address"),
                 self.read_webpage, "web", lambda a: f"Reading {a.get('url', '')}"),
            Tool("run_command",
                 "Run a terminal command on the user's PC. Quick commands return their "
                 "output. Set new_window=true for anything long-running (installs, builds, "
                 "servers): it opens its own terminal window and keeps going while you reply.",
                 params(["command"],
                        command="string: the full command line",
                        new_window="boolean: run in its own window instead of waiting",
                        timeout_seconds="integer: for quick commands; default 60"),
                 self.run_command, "workshop",
                 lambda a: f"Terminal: {a.get('command', '')}", risky=True),
            Tool("check_jobs",
                 "How the long-running commands are doing, with the end of their output.",
                 params(),
                 self.check_jobs, "observatory", lambda a: "Checking on terminal windows"),
            Tool("read_workspace_file",
                 "Read a text file from the workspace folder, e.g. a command's saved output.",
                 params(["path"], path="string: path relative to the workspace"),
                 self.read_workspace_file, "archives", lambda a: f"Reading {a.get('path', '')}"),
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
        self.me.move(tool.room, "working", task, name, step.description)
        self.hub.emit("step.started", step.description, task_id=task.id, tool=name,
                      agent=self.me)
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
                      task_id=task.id, tool=name, agent=self.me)
        self.me.move("situation", "working", task, None, "Thinking")
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

    async def check_jobs(self, task: Task) -> dict[str, Any]:
        jobs = []
        for job in sorted(self.jobs.values(), key=lambda j: j.started, reverse=True)[:10]:
            tail = ""
            with contextlib.suppress(OSError):
                tail = job.output.read_text(encoding="utf-8", errors="replace")[-1500:]
            minutes = ((job.finished or time.time()) - job.started) / 60
            jobs.append({"id": job.id, "command": job.command, "status": job.status,
                         "exit_code": job.exit_code, "minutes": round(minutes, 1),
                         "output_file": self.relative(job.output), "output_tail": tail})
        return {"jobs": jobs} if jobs else {"jobs": [], "note": "No long-running commands yet."}

    async def run_command(self, task: Task, command: str, new_window: bool = False,
                          timeout_seconds: int = 60) -> dict[str, Any]:
        command = command.strip()
        if not command:
            return {"error": "The command was empty."}
        how = "in a new terminal window" if new_window else "and wait for the output"
        arguments = {"command": command, "new_window": new_window}
        if not await self._approve(task, "run_command", arguments, f"Run {how}: {command}"):
            return {"denied": True,
                    "note": "The user did not allow this command. Don't retry unless asked."}
        if new_window:
            job = self._launch(task, command)
            return {"started": True, "job_id": job.id,
                    "output_file": self.relative(job.output),
                    "note": "Running in its own terminal window; check_jobs shows progress."}
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
                    "note": f"Stopped after {timeout}s. Use new_window=true for long commands."}
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

    # -- long-running commands --------------------------------------------
    def _launch(self, parent: Task, command: str) -> Job:
        """Start `command` in its own terminal window, teeing output to a file.

        The window stays open afterwards so the user can read it; completion
        is signalled by a .done file holding the exit code.
        """
        name = f"{datetime.now():%Y%m%d-%H%M%S}-{slug(command)}"
        folder = self.settings.workspace / "jobs" / name
        folder.mkdir(parents=True, exist_ok=True)
        output, done, workspace = folder / "output.txt", folder / ".done", self.settings.workspace
        if WINDOWS:
            script = folder / "job.ps1"
            script.write_text("\n".join([
                f"$Host.UI.RawUI.WindowTitle = {ps_quote('JARVIS - ' + command[:60])}",
                "$utf8 = [Text.UTF8Encoding]::new($false)",
                "[Console]::OutputEncoding = $utf8; $OutputEncoding = $utf8",
                f"Set-Location -LiteralPath {ps_quote(workspace)}",
                f"Write-Host {ps_quote('JARVIS> ' + command)} -ForegroundColor Cyan",
                f"$log = [IO.StreamWriter]::new({ps_quote(output)}, $false, $utf8)",
                "$log.AutoFlush = $true",
                "$code = 0",
                "try {",
                "  & {",
                command,
                "  } 2>&1 | ForEach-Object {",
                '    $line = "$_"; Write-Host $line; $log.WriteLine($line)',
                "  }",
                "  if ($LASTEXITCODE) { $code = $LASTEXITCODE }",
                "} catch {",
                '  Write-Host $_ -ForegroundColor Red; $log.WriteLine("ERROR: $_"); $code = 1',
                "} finally { $log.Close() }",
                f"Set-Content -LiteralPath {ps_quote(done)} -Value ([int]$code)",
                "Write-Host ''",
                "Write-Host 'JARVIS: finished. You can close this window.' -ForegroundColor Cyan",
            ]) + "\n", encoding="utf-8-sig")
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)],
                cwd=workspace, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        else:
            script = folder / "job.sh"
            script.write_text(
                f"cd {shlex.quote(str(workspace))}\n"
                f"( {command} ) 2>&1 | tee {shlex.quote(str(output))}\n"
                f"echo ${{PIPESTATUS[0]}} > {shlex.quote(str(done))}\n", encoding="utf-8")
            process = subprocess.Popen(["bash", str(script)], cwd=workspace,
                                       start_new_session=True, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)

        job_task = Task(request=f"Terminal: {command}", status="running")
        job_task.steps.append(Step(description=command[:80], tool="terminal"))
        self.tasks[job_task.id] = job_task
        window = next((w for w in self.windows if w.status == "idle"), None)
        job = Job(command=command, folder=folder, task=job_task, process=process, agent=window)
        self.jobs[job.id] = job
        if window:
            window.move("library", "working", job_task, "terminal", command[:80])
        self.hub.emit("task.started", f"Opened a terminal window: {command}",
                      task_id=job_task.id, agent=window,
                      data={"job_id": job.id, "parent_task": parent.id})
        if window:
            self.hub.emit("agent.assigned", f"{window.name} → {command[:60]}",
                          task_id=job_task.id, agent=window)
        asyncio.get_running_loop().create_task(self._watch(job))
        return job

    async def _watch(self, job: Job) -> None:
        while True:
            await asyncio.sleep(2)
            if job.done_file.exists():
                with contextlib.suppress(ValueError, OSError):
                    job.exit_code = int(job.done_file.read_text(encoding="utf-8-sig") or 0)
                job.status = "failed" if job.exit_code else "completed"
                break
            if job.process.poll() is not None:
                await asyncio.sleep(1)  # the script may still be writing .done
                if not job.done_file.exists():
                    job.status = "cancelled"
                    break
        job.finished = time.time()
        task, where = job.task, self.relative(job.output)
        task.steps[0].status = "completed" if job.status == "completed" else "failed"
        if job.status == "completed":
            task.result = f"Finished — output saved to {where}"
            task.set("completed")
            self.hub.emit("task.completed", f"Finished: {job.command[:80]} — output in {where}.",
                          task_id=task.id, speak=True, files={"action": "write", "paths": [where]})
        else:
            task.error = ("The window was closed before it finished." if job.status == "cancelled"
                          else f"It stopped with exit code {job.exit_code} — see {where}.")
            task.set("failed")
            self.hub.emit("task.failed", f"{job.command[:80]}: {task.error}", task_id=task.id,
                          speak=True)
        if job.agent:
            job.agent.move("lobby", "idle")
            self.hub.emit("agent.idle", f"{job.agent.name} is free.", agent=job.agent)

    def relative(self, path: Path) -> str:
        with contextlib.suppress(ValueError):
            return path.resolve().relative_to(self.settings.workspace.resolve()).as_posix()
        return str(path)

    # -- dashboard data ---------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "workspace": str(self.settings.workspace),
            "rooms": ROOMS,
            "agents": [a.out() for a in (self.me, *self.windows)],
            "tasks": [t.out() for t in self.recent_tasks()],
            "approvals": [a.out() for a in self.approvals.values()],
            "events": list(self.hub.history),
            "touched": list(self.hub.touched),
            "voice": self.voice_info(),
            "settings": {"particles": 900, "accent": "#22d3ee", "stats_interval": 2.0},
        }

    def recent_tasks(self, limit: int = 20) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]

    def voice_info(self) -> dict[str, Any]:
        if not (self.can_speak or self.can_listen):
            return {"enabled": False}
        openai = self.voice_provider == "openai"
        return {"enabled": True, "can_speak": self.can_speak, "can_listen": self.can_listen,
                "voice": self.settings.openai_tts_voice if openai else self.settings.voice,
                "provider": self.voice_provider, "listen_provider": self.listen_provider,
                "wake_word": "jarvis",
                "wake_word_required": False, "listen_enabled": self.can_listen,
                "speak_events": ["task.completed", "task.failed", "approval.required"]}

    async def speak(self, text: str) -> bytes:
        clean = re.sub(r"https?://\S+", "", text)  # links and markdown read badly aloud
        clean = re.sub(r"[*_#`>|]+", "", clean)[:1500]
        if self.voice_provider == "openai":
            return await self._speak_openai(clean)
        import edge_tts

        communicate = edge_tts.Communicate(clean, self.settings.voice, rate="+8%", pitch="-2Hz")
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    async def _speak_openai(self, text: str) -> bytes:
        body: dict[str, Any] = {"model": self.settings.openai_tts_model,
                                "voice": self.settings.openai_tts_voice,
                                "input": text, "response_format": "mp3"}
        if self.settings.openai_tts_model.startswith("gpt-"):  # tts-1 ignores instructions
            body["instructions"] = ("Speak as a calm, dry-witted British butler: "
                                    "warm, brief and precise, never theatrical.")
        response = await self.http.post(
            OPENAI_SPEECH_URL, json=body, timeout=60,
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}"})
        if response.status_code == 401:
            raise JarvisError("OpenAI rejected the key — check OPENAI_API_KEY in .env.")
        if response.status_code >= 400:
            raise JarvisError(f"OpenAI speech error {response.status_code}: "
                              f"{response.text[:200]}")
        return response.content

    async def transcribe(self, wav: bytes) -> str:
        """Speech to text through Wispr Flow. `wav` is 16 kHz mono WAV."""
        if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
            raise JarvisError("Wispr Flow needs a 16 kHz WAV recording.")
        response = await self.http.post(
            WISPR_URL, timeout=60,
            json={"audio": base64.b64encode(wav).decode(),
                  "language": [self.settings.wispr_language]},
            headers={"Authorization": f"Bearer {self.settings.wispr_api_key}"})
        if response.status_code in {401, 403}:
            raise JarvisError("Wispr Flow rejected the key — check WISPR_API_KEY in .env.")
        if response.status_code >= 400:
            raise JarvisError(f"Wispr Flow error {response.status_code}: {response.text[:200]}")
        return str(response.json().get("text", "")).strip()

    def tree(self, depth: int = 3, limit: int = 260) -> dict[str, Any]:
        root = self.settings.workspace
        nodes: list[dict[str, Any]] = [{"id": ".", "name": root.name, "path": ".",
                                        "type": "dir", "size": None, "parent": None, "depth": 0}]
        links: list[dict[str, str]] = []
        queue: deque[tuple[Path, str, int]] = deque([(root, ".", 0)])
        truncated = False
        while queue:
            folder, rel, level = queue.popleft()
            if level >= depth:
                continue
            try:
                entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if len(nodes) >= limit:
                    truncated = True
                    queue.clear()
                    break
                path = entry.name if rel == "." else f"{rel}/{entry.name}"
                is_dir = entry.is_dir()
                size = None
                if not is_dir:
                    with contextlib.suppress(OSError):
                        size = entry.stat().st_size
                nodes.append({"id": path, "name": entry.name, "path": path,
                              "type": "dir" if is_dir else "file", "size": size,
                              "parent": rel, "depth": level + 1})
                links.append({"source": rel, "target": path})
                if is_dir:
                    queue.append((entry, path, level + 1))
        return {"root": str(root), "nodes": nodes, "links": links, "truncated": truncated,
                "depth": depth, "limit": limit}


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

    @app.get("/dash/api/tree", dependencies=guard)
    async def tree(depth: int = 3, limit: int = 260) -> dict[str, Any]:
        return jarvis.tree(min(depth, 8), min(limit, 2000))

    @app.get("/dash/api/stats", dependencies=guard)
    async def stats() -> dict[str, Any]:
        return machine_stats()

    def heard(said: str, submit: bool) -> dict[str, Any]:
        text = re.sub(r"^\s*(hey\s+)?jarvis[\s,:.!-]*", "", said, flags=re.I).strip()
        text = text or said
        task = jarvis.submit(text) if submit else None
        return {"text": said, "addressed": True, "command": text,
                "submitted": task is not None, "task_id": task.id if task else None,
                "confidence": None, "details": {}}

    @app.post("/dash/api/command", dependencies=guard)
    async def command(body: CommandIn) -> dict[str, Any]:
        return heard(body.text, body.submit)

    @app.post("/dash/api/listen", dependencies=guard)
    async def listen(audio: UploadFile, submit: Annotated[bool, Form()] = True) -> dict[str, Any]:
        if not jarvis.can_listen:
            raise HTTPException(
                status_code=503,
                detail="Listening is off — set JARVIS_LISTEN_PROVIDER=wispr and WISPR_API_KEY.")
        try:
            said = await jarvis.transcribe(await audio.read())
        except JarvisError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not said:
            return {"text": "", "addressed": False, "command": "", "submitted": False,
                    "task_id": None, "confidence": None, "details": {}}
        return heard(said, submit)

    @app.post("/dash/api/speak", dependencies=guard)
    async def speak(body: SpeakIn) -> Response:
        if not jarvis.can_speak:
            raise HTTPException(
                status_code=503,
                detail="Speech is off — set OPENAI_API_KEY, or pip install edge-tts.")
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
        frames = [f for f in jarvis.hub.history if not f["type"].startswith("agent.")]
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
        voice = (f"{jarvis.voice_info()['provider']} / {jarvis.voice_info()['voice']}"
                 if jarvis.can_speak else "off (set OPENAI_API_KEY or pip install edge-tts)")
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
        await uvicorn.Server(config).serve()


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
