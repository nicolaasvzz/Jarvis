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
import base64
import contextlib
import hmac
import html
import importlib.util
import io
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
import threading
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import psutil
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
REPO = HERE.parent  # the whole repo: backend/, frontend/, tradebot/
WINDOWS = platform.system() == "Windows"
VOICE_STYLE = ("A deep, refined British voice in a Received Pronunciation accent, calm and "
               "measured, dry and understated, like a butler AI. Warm, brief and precise; "
               "never theatrical.")
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
WISPR_URL = "https://platform-api.wisprflow.ai/api/v1/dash/api"
def have_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
KEY_URL = "https://aistudio.google.com/apikey"

#: The brains Jarvis can think with. Switch on Mothership → Connections (or
#: JARVIS_BRAIN in .env). "openai"-kind brains all speak the OpenAI chat
#: format at their own address; Claude speaks Anthropic's (via its SDK).
BRAINS: dict[str, dict[str, Any]] = {
    "gemini": {"name": "Gemini", "maker": "Google", "kind": "gemini",
               "key_env": "GEMINI_API_KEY", "model_env": "GEMINI_MODEL",
               "model": "gemini-3.5-flash-lite",
               "models": ["gemini-3.5-flash-lite", "gemini-3.8-flash"],
               "key_url": KEY_URL, "note": "Free tier — the default."},
    "anthropic": {"name": "Claude", "maker": "Anthropic", "kind": "anthropic",
                  "key_env": "ANTHROPIC_API_KEY", "model_env": "ANTHROPIC_MODEL",
                  "model": "claude-opus-5-5",
                  "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"],
                  "key_url": "https://console.anthropic.com/settings/keys",
                  "note": "Paid per use. Opus 5.5 is the most capable; Sonnet 5.5 is cheaper "
                          "and Haiku 4.5 cheapest."},
    "openai": {"name": "OpenAI", "maker": "OpenAI", "kind": "openai",
               "base": "https://api.openai.com/v1",
               "key_env": "OPENAI_API_KEY", "model_env": "OPENAI_MODEL",
               "model": "gpt-4o-mini", "models": ["gpt-4o-mini", "gpt-4o"],
               "key_url": "https://platform.openai.com/api-keys",
               "note": "Paid per use. The same key powers the OpenAI voice."},
    "groq": {"name": "Groq", "maker": "Groq", "kind": "openai",
             "base": "https://api.groq.com/openai/v1",
             "key_env": "GROQ_API_KEY", "model_env": "GROQ_MODEL",
             "model": "llama-3.3-70b-versatile",
             "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"],
             "key_url": "https://console.groq.com/keys",
             "note": "Free tier, very fast — a good backup when Gemini runs out."},
    "openrouter": {"name": "OpenRouter", "maker": "OpenRouter", "kind": "openai",
                   "base": "https://openrouter.ai/api/v1",
                   "key_env": "OPENROUTER_API_KEY", "model_env": "OPENROUTER_MODEL",
                   "model": "openrouter/auto", "models": ["openrouter/auto"],
                   "key_url": "https://openrouter.ai/keys",
                   "note": "One key, hundreds of models — some free (ids ending in :free)."},
    "deepseek": {"name": "DeepSeek", "maker": "DeepSeek", "kind": "openai",
                 "base": "https://api.deepseek.com/v1",
                 "key_env": "DEEPSEEK_API_KEY", "model_env": "DEEPSEEK_MODEL",
                 "model": "deepseek-chat", "models": ["deepseek-chat"],
                 "key_url": "https://platform.deepseek.com/api_keys",
                 "note": "Paid, but very cheap."},
}

#: Claude models that take Anthropic's server-side refusal fallback: a
#: declined request is re-run on a fallback model instead of just stopping.
CLAUDE_FALLBACK = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
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
    brain: str = "gemini"
    gemini_api_key: str = ""  # the key in use: the chosen one of gemini_keys
    gemini_keys: tuple[str, str, str] = ("", "", "")
    gemini_slot: int = 1
    gemini_model: str = "gemini-3.5-flash-lite"
    brain_keys: dict[str, str] = field(default_factory=dict)  # the other brains
    brain_models: dict[str, str] = field(default_factory=dict)
    thinking: str = ""
    api_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    workspace: Path = HERE / "workspace"
    auto_approve: bool = False
    allow_safe: bool = True
    notify_after: int = 20
    startup_terminals: Path = HERE / "terminals.json"
    data_dir: Path = HERE / "data"
    home_location: str = ""
    voice: str = "en-GB-RyanNeural"
    voice_provider: str = "edge"
    listen_provider: str = "browser"
    listen_language: str = "en-GB"
    whisper_model: str = "small.en"
    wispr_api_key: str = ""
    wispr_language: str = "en"
    openai_api_key: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "onyx"
    voice_style: str = VOICE_STYLE
    news_country: str = "US"
    news_language: str = "en"
    cors_origins: list[str] = field(default_factory=list)
    open_browser: bool = True
    frontend: Path = HERE.parent / "frontend"
    persona_file: Path = HERE / "persona.md"
    env_file: Path = HERE / ".env"

    def key_for(self, brain: str) -> str:
        if brain == "gemini":
            return self.gemini_api_key
        if brain == "openai":
            return self.openai_api_key  # one OpenAI key, for the brain and the voice
        return self.brain_keys.get(brain, "")

    def model_for(self, brain: str) -> str:
        if brain == "gemini":
            return self.gemini_model
        return self.brain_models.get(brain) or str(BRAINS.get(brain, {}).get("model", ""))

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

        keys = (get("GEMINI_API_KEY") or get("GOOGLE_API_KEY"), get("GEMINI_API_KEY_2"),
                get("GEMINI_API_KEY_3"))
        slot = int(get("GEMINI_KEY", "1")) if get("GEMINI_KEY", "1").isdigit() else 1
        slot = slot if 1 <= slot <= 3 else 1
        others = [b for b, spec in BRAINS.items() if spec["kind"] != "gemini"]
        brain = get("JARVIS_BRAIN", "gemini").lower()
        return cls(
            brain=brain if brain in BRAINS else "gemini",
            gemini_api_key=keys[slot - 1],
            gemini_keys=keys,
            gemini_slot=slot,
            brain_keys={b: get(BRAINS[b]["key_env"]) for b in others if b != "openai"},
            brain_models={b: get(BRAINS[b]["model_env"], BRAINS[b]["model"]) for b in others},
            env_file=env_file,
            gemini_model=get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            thinking=get("GEMINI_THINKING").lower(),
            api_token=get("JARVIS_API_TOKEN"),
            host=get("JARVIS_HOST", "127.0.0.1"),
            port=int(get("JARVIS_PORT", "8765")),
            workspace=path("JARVIS_WORKSPACE", HERE / "workspace"),
            auto_approve=flag("AUTO_APPROVE", False),
            allow_safe=flag("ALLOW_SAFE_COMMANDS", True),
            notify_after=int(get("NOTIFY_AFTER_SECONDS", "20") or 0),
            startup_terminals=path("STARTUP_TERMINALS", HERE / "terminals.json"),
            data_dir=path("JARVIS_DATA", HERE / "data"),
            home_location=get("HOME_LOCATION"),
            voice=get("JARVIS_VOICE", "en-GB-RyanNeural"),
            voice_provider=get("JARVIS_VOICE_PROVIDER", "edge").lower(),
            listen_provider=get("JARVIS_LISTEN_PROVIDER", "browser").lower(),
            listen_language=get("JARVIS_LISTEN_LANGUAGE", "en-GB"),
            whisper_model=get("WHISPER_MODEL", "small.en"),
            wispr_api_key=get("WISPR_API_KEY"),
            wispr_language=get("WISPR_LANGUAGE", "en").lower(),
            voice_style=get("JARVIS_VOICE_STYLE", VOICE_STYLE),
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
    #: What Gemini is sent, when it isn't simply `request` — a notice or an
    #: "explain this" carries the terminal's screen, too long to show as asked.
    prompt: str | None = None
    #: Who started it: "asked" (you), "notice" (a long command finished),
    #: "explain" (the Explain button) or "control" (a Mothership control).
    kind: str = "asked"
    #: The page that asked (a random id per open page), so only it speaks the
    #: reply — a question from the phone is answered on the phone.
    client: str | None = None
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
        return {"id": self.id, "request": self.request, "kind": self.kind,
                "status": self.status, "goal": None,
                "steps": [s.out() for s in self.steps], "result": self.result,
                "error": self.error, "created_at": self.created_at,
                "updated_at": self.updated_at}

    @classmethod
    def load(cls, saved: dict[str, Any]) -> "Task":
        """A finished task back from data/history.json."""
        steps = [Step(description=str(s.get("description", "")), tool=str(s.get("tool", "")),
                      id=str(s.get("id") or short_id("s")), status=str(s.get("status", "")),
                      risk=str(s.get("risk", "safe")), error=s.get("error"))
                 for s in saved.get("steps") or [] if isinstance(s, dict)]
        return cls(request=str(saved.get("request", "")), kind=str(saved.get("kind", "asked")),
                   id=str(saved.get("id") or short_id("t")),
                   status=str(saved.get("status", "completed")), steps=steps,
                   result=saved.get("result"), error=saved.get("error"),
                   created_at=str(saved.get("created_at") or now()),
                   updated_at=str(saved.get("updated_at") or now()))


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
    """A real interactive shell — colours, prompts and REPLs all work — whose
    prompt marks each finished command (see MARK). Your own prompt is kept."""
    env = dict(os.environ)
    try:
        if WINDOWS:
            from winpty import PtyProcess
            script = base64.b64encode(PS_PROMPT_MARK.encode("utf-16-le")).decode()
            argv = ["powershell.exe", "-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", script]
        else:
            from ptyprocess import PtyProcessUnicode as PtyProcess
            argv = [os.environ.get("SHELL") or "bash", "-l"]
            env["PROMPT_COMMAND"] = BASH_PROMPT_MARK
    except ImportError as exc:
        raise JarvisError("Terminals need pywinpty (or ptyprocess) and pyte — "
                          "run: pip install -r requirements.txt") from exc
    process: Pty = PtyProcess.spawn(argv, cwd=str(cwd), env=env, dimensions=(rows, cols))
    return process


def end_children(process: Pty, wait: float = 3) -> int:
    """End everything a shell started — the command it is running — but not
    the shell. Ctrl+C typed into ConPTY doesn't reach every program (a Python
    script, Start-Sleep), so it alone can't stop one. Returns how many ended."""
    pid = getattr(process, "pid", None)
    if not pid:
        return 0
    try:
        children = psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return 0
    for child in children:
        with contextlib.suppress(psutil.Error):
            child.terminate()
    _, alive = psutil.wait_procs(children, timeout=wait)
    for child in alive:
        with contextlib.suppress(psutil.Error):
            child.kill()
    return len(children)


def decode_16k(audio: bytes) -> Any:
    """A recording (the browser's webm/opus, Safari's mp4, wav, mp3…) as 16 kHz
    mono float32 samples, the way Whisper wants them. Decoded here rather than
    by faster-whisper, whose decoder passes PyAV an option (`metadata_errors`)
    that PyAV 15 removed — every recording failed with it."""
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks: list[Any] = []
    with av.open(io.BytesIO(audio), mode="r") as container:
        for frame in container.decode(audio=0):
            frame.pts = None  # browser recordings' timestamps can trip the resampler
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


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

#: The prompt's invisible "a command just finished" mark, with its exit
#: status (0 = it worked). The shells are started with a prompt that prints
#: it — the same OSC 633 sequence VS Code's shell integration uses.
MARK = re.compile(r"\x1b\]633;D;(\d+)\x07")
PS_PROMPT_MARK = r"""
$global:__jarvisPrompt = $function:prompt
function global:prompt {
  $code = if ($global:?) { 0 } else { 1 }
  "$([char]27)]633;D;$code$([char]7)" + (& $global:__jarvisPrompt)
}
"""
BASH_PROMPT_MARK = r'printf "\033]633;D;%s\007" $?'

#: Read-only commands that run without asking (ALLOW_SAFE_COMMANDS=false to
#: ask anyway). Matched against the whole command, and anything that could
#: chain, redirect or substitute another command is never "safe".
SAFE_COMMANDS = [re.compile(p, re.I) for p in (
    r"git (status|diff|log|show)( [\w./~^@:+-]+)*",
    r"git (branch|remote|tag|stash list)( (-a|-v|-vv|--list))*",
    r"(dir|ls|gci|Get-ChildItem)( [\w./\\:*~-]+)*",
    r"(pwd|cd|whoami|hostname|Get-Location|Get-Date|ipconfig|systeminfo|nvidia-smi)",
    r"(where|where\.exe|which|Get-Command|gcm) [\w.-]+",
    r"[\w.-]+ (--version|-v|-V|version)",
)]
NEVER_SAFE = re.compile(r"[;&|<>`\n\r]|\$\(|--output|-o\b", re.I)


def is_safe(command: str) -> bool:
    """True for a read-only command that may run without asking."""
    command = command.strip()
    return bool(command) and not NEVER_SAFE.search(command) and any(
        pattern.fullmatch(command) for pattern in SAFE_COMMANDS)


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

    A command starts when Enter is pressed on a line that begins with the
    prompt, and finishes when the prompt's mark (MARK) comes back, carrying
    its exit status; `on_finish` then hears how it went.
    """

    def __init__(self, id: str, title: str, purpose: str, opened_by: str, process: Pty,
                 cols: int, rows: int, on_exit: Callable[["Terminal"], None],
                 on_finish: Callable[["Terminal", dict[str, Any]], None] | None = None,
                 ) -> None:
        import pyte

        self.id, self.title, self.purpose, self.opened_by = id, title, purpose, opened_by
        self.process, self.cols, self.rows, self.on_exit = process, cols, rows, on_exit
        self.on_finish = on_finish
        self.running: dict[str, Any] | None = None  # the command in progress
        self.last_result: dict[str, Any] | None = None  # how the last one went
        self.trusted = False  # Jarvis may type here without asking
        self.watchers = 0  # Jarvis tools waiting on this terminal right now
        self.control: str | None = None  # the Mothership control running here
        self.project: str | None = None  # the Mothership project it belongs to
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
        marks = MARK.findall(data)
        data = MARK.sub("", QUERIES.sub("", data))
        with contextlib.suppress(Exception):  # an odd escape code must not stop the pump
            self.stream.feed(data)
        for code in marks:
            self._finished(int(code))
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

    # -- commands, start to finish -----------------------------------------
    def _line(self) -> str:
        """The line the cursor is on, unwrapped."""
        return self._unwrap(self.screen.display[:self.screen.cursor.y + 1]).strip()

    def _start(self, command: str, by: str) -> None:
        self.running = {"command": command, "by": by, "started": time.monotonic(),
                        "started_at": now()}

    def _finished(self, code: int) -> None:
        if self.running is None:
            return  # a prompt with no command before it: start-up, or a bare Enter
        started = self.running.pop("started")
        result = {**self.running, "ok": code == 0, "seconds": round(time.monotonic() - started),
                  "finished_at": now(), "watched": self.watchers > 0}
        self.running, self.last_result = None, result
        if self.on_finish:
            self.on_finish(self, result)

    # -- input -------------------------------------------------------------
    def send(self, data: str, shown: str, by: str = "jarvis") -> None:
        """Jarvis (or a start-up command) typing: `shown` goes in the log."""
        if data.endswith("\r") and PROMPT_PREFIX.match(self._line()):
            self._start(shown, by)
        self._write(data)
        self.note(by, shown)

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
            line = self._line()
            typed = PROMPT_PREFIX.sub("", line, count=1).strip()
            if not typed:  # a shell still starting up may not have echoed it
                typed = CONTROL.sub("", before).strip()
            if typed and not PROMPT.match(typed):
                self.note("you", typed)
                if PROMPT_PREFIX.match(line):  # a new command, not an answer
                    self._start(typed, "you")
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
        self.watchers += 1  # what finishes now, Jarvis sees — no separate notice
        try:
            await asyncio.sleep(0.3)
            while time.monotonic() - start < limit and self.status == "running":
                quiet = time.monotonic() - self.last_output
                if (quiet >= 0.5 and self.at_prompt) or quiet >= 3:
                    return
                await asyncio.sleep(0.1)
        finally:
            self.watchers -= 1

    async def idle(self, limit: float) -> bool:
        """Wait for the running command to finish and the prompt to return."""
        start = time.monotonic()
        while time.monotonic() - start < limit and self.status == "running":
            if self.running is None and self.at_prompt:
                return True
            await asyncio.sleep(0.1)
        return self.running is None and self.at_prompt

    async def interrupt(self, by: str) -> None:
        """Stop the running command: Ctrl+C, and if that isn't enough within a
        few seconds, end the processes it started. The shell stays open."""
        self.send("\x03", shown="[ctrl+c]", by=by)
        if self.running is None or await self.idle(limit=3):
            return
        if await asyncio.to_thread(end_children, self.process):
            self.note(by, "[ended the command's processes]")

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
        if self.at_prompt:
            return "at its prompt"
        if self.running:
            return f"running {self.running['command'][:60]}"
        return "busy or waiting for input"

    def result_text(self) -> str | None:
        r = self.last_result
        if r is None:
            return None
        how = "worked" if r["ok"] else "failed"
        return f"{r['command'][:80]} — {how} after {duration(r['seconds'])}"

    def summary(self) -> dict[str, Any]:
        """For Jarvis: what this terminal is, what it's for, and what was typed."""
        info: dict[str, Any] = {"terminal_id": self.id, "title": self.title,
                                "purpose": self.purpose,
                "opened_by": self.opened_by, "state": self.state,
                "recent_input": [f"{e['by']}: {e['text']}" for e in list(self.log)[-6:]]}
        if self.last_result:
            info["last_command"] = self.result_text()
        if self.trusted:
            info["trusted"] = "the user lets you type here without asking"
        return info

    def out(self) -> dict[str, Any]:
        """For the dashboard."""
        last = self.last_result
        return {"id": self.id, "title": self.title, "purpose": self.purpose,
                "opened_by": self.opened_by, "status": self.status, "state": self.state,
                "at_prompt": self.at_prompt, "exit_code": self.exit_code,
                "created_at": self.created_at, "cols": self.cols, "rows": self.rows,
                "trusted": self.trusted, "running": self.running and {
                    k: v for k, v in self.running.items() if k != "started"},
                "last_result": last, "control": self.control, "project": self.project,
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


class Mothership:
    """Your controls and projects, kept in data/mothership.json.

    A *control* is a button: ask Jarvis something ("ask"), run a command in a
    terminal ("command"), open a page ("link"), or a not-yet-built "idea".
    A *project* is a folder with a purpose: its controls, ideas, terminals
    and, optionally, a status file the dashboard shows live.

    The file is re-read whenever it changes on disk, so an edit by hand — or
    by Claude, finishing a control it built with you — shows up without a
    restart.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self.data: dict[str, list[dict[str, Any]]] = {"controls": [], "projects": []}

    def load(self) -> dict[str, list[dict[str, Any]]]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return self.data
        if mtime != self._mtime:
            with contextlib.suppress(ValueError, OSError):
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict):
                    self.data = {key: [x for x in raw.get(key) or [] if isinstance(x, dict)]
                                 for key in ("controls", "projects")}
            self._mtime = mtime
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        spare = self.path.with_suffix(".tmp")
        spare.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        spare.replace(self.path)
        self._mtime = self.path.stat().st_mtime

    @property
    def controls(self) -> list[dict[str, Any]]:
        return self.load()["controls"]

    @property
    def projects(self) -> list[dict[str, Any]]:
        return self.load()["projects"]

    @staticmethod
    def _find(items: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        """By id, then exact name, then a name containing `key`."""
        key = str(key).strip()
        wanted = key.casefold()
        for match in (lambda x: x.get("id") == key,
                      lambda x: str(x.get("name", "")).casefold() == wanted,
                      lambda x: wanted and wanted in str(x.get("name", "")).casefold()):
            found = [x for x in items if match(x)]
            if found:
                return found[0]
        return None

    def control(self, key: str) -> dict[str, Any] | None:
        return self._find(self.controls, key)

    def project(self, key: str) -> dict[str, Any] | None:
        return self._find(self.projects, key)

    def folder(self, project_id: str | None) -> Path | None:
        """A project's folder, if it names one that exists. A relative folder
        is inside this repo (the TradeBot's is "tradebot"), so a fresh
        download finds it wherever the repo was put."""
        project = self.project(project_id) if project_id else None
        text = str(project.get("folder") or "") if project else ""
        if not text:
            return None
        folder = Path(text) if Path(text).is_absolute() else REPO / text
        return folder if folder.is_dir() else None


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
    """A PowerShell single-quoted literal: nothing inside is interpreted. PowerShell
    also ends such a string at a curly single quote, so those are doubled too."""
    return "'" + re.sub("['‘’‚‛]", lambda m: m.group() * 2,
                        str(value)) + "'"


def shell_quote(value: str) -> str:
    """A literal for this PC's terminals: PowerShell on Windows, else bash."""
    return ps_quote(value) if WINDOWS else shlex.quote(value)


REPORT_TYPES = {".html": "html", ".htm": "html", ".md": "text", ".txt": "text",
                ".log": "text", ".json": "text", ".csv": "text"}
REPORT_NAME = re.compile(r"[\w][\w .()+,=-]{0,199}")  # a plain file name: no folders
REPORTS_LISTED = 300
_REPORT_STAMP = re.compile(r"(\d{4}-\d\d-\d\d)[_ T](\d\d)(\d\d)(\d\d)?[_-]?([A-Za-z][\w-]*)?")
_report_labels: dict[tuple[str, float], dict[str, Any]] = {}


def report_label(path: Path, mtime: float, size: int) -> dict[str, Any]:
    """How a report is listed: its title and summary from the page's own head
    (<title>, <meta name="description">, <meta name="tone">), else from its file
    name, which may start with when it was made: 2026-10-05_213015_backtest.html."""
    key = (str(path), mtime)
    if key in _report_labels:
        return _report_labels[key]
    title = summary = tone = ""
    kind = ""
    made = datetime.fromtimestamp(mtime, UTC).isoformat()
    stamp = _REPORT_STAMP.match(path.stem)
    if stamp:
        day, hour, minute, second, kind = stamp.groups()
        with contextlib.suppress(ValueError):
            made = datetime.fromisoformat(
                f"{day}T{hour}:{minute}:{second or '00'}").astimezone(UTC).isoformat()
        kind = (kind or "").split("-")[0]
    if REPORT_TYPES.get(path.suffix.lower()) == "html":
        with contextlib.suppress(OSError):
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                head = handle.read(12_000)

            def meta(name: str) -> str:
                found = re.search(rf'<meta\s+name=["\']{name}["\']\s+content=(["\'])(.*?)\1',
                                  head, re.I | re.S)
                return html.unescape(found.group(2)).strip() if found else ""

            found = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
            title = " ".join(html.unescape(found.group(1)).split()) if found else ""
            summary, tone = meta("description"), meta("tone").lower()
    if not title:
        words = (kind or path.stem).replace("_", " ").replace("-", " ").strip()
        title = words[:1].upper() + words[1:]
    label = {"name": path.name, "title": title[:120], "summary": summary[:300],
             "tone": tone if tone in {"good", "bad"} else "", "kind": kind.lower(),
             "created_at": made, "size": size,
             "type": REPORT_TYPES.get(path.suffix.lower(), "text")}
    if len(_report_labels) > 2000:
        _report_labels.clear()
    _report_labels[key] = label
    return label


def fill_inputs(control: dict[str, Any],
                given: dict[str, Any] | None) -> tuple[str, dict[str, str]]:
    """A command control's command line with its inputs filled in.

    A control may ask for inputs before it runs — `"inputs"`: a list of
    `{"name", "label", "kind": "choice"|"text", "options", "default"}` — and its
    command line names them as `{name}`. Each value goes in as a quoted literal:
    a choice must be one of its options, and text loses control characters, so
    nothing typed can run as a command. → (command, the values used).
    """
    action = str(control.get("action") or "").strip()
    given = given or {}
    values: dict[str, str] = {}
    for spec in control.get("inputs") or []:
        name = str(spec.get("name") or "")
        if not name:
            continue
        value = given.get(name)
        if value is None or not str(value).strip():
            value = spec.get("default") or ""
        value = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).split())[:300]
        if spec.get("kind") == "choice":
            allowed = [str(o.get("value") if isinstance(o, dict) else o)
                       for o in spec.get("options") or []]
            if value not in allowed:
                raise JarvisError(f"“{spec.get('label') or name}” must be one of: "
                                  f"{', '.join(allowed)}.")
        values[name] = value
        action = action.replace("{" + name + "}", shell_quote(value))
    return action, values


def input_summary(spec: dict[str, Any]) -> str:
    """One control input for the system prompt: `goal (What to learn: text)`."""
    what = spec.get("label") or spec.get("kind") or "text"
    if spec.get("kind") == "choice":
        what += ": one of " + ", ".join(str(o.get("value") if isinstance(o, dict) else o)
                                        for o in spec.get("options") or [])
    else:
        what += ": text"
    if spec.get("default"):
        what += f", default {spec['default']}"
    return f"{spec.get('name')} ({what})"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "job"


def error_detail(response: httpx.Response) -> str:
    """The message in an API's error reply: {"error": {"message": …}} or close."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(error or body)[:300]


def mask(secret: str) -> str:
    """Enough of a key to recognise it — never enough to use it."""
    return f"…{secret[-4:]}" if len(secret) > 8 else ("set" if secret else "")


def duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


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
        self._whisper: Any = None
        self.tools = {tool.name: tool for tool in self._tools()}
        settings.workspace.mkdir(parents=True, exist_ok=True)
        self.mothership = Mothership(settings.data_dir / "mothership.json")
        example = HERE / "mothership.example.json"
        if not self.mothership.path.exists() and example.is_file():
            with contextlib.suppress(OSError):  # a fresh install starts from the examples
                settings.data_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(example, self.mothership.path)
        self.history_file = settings.data_dir / "history.json"
        self._load_history()

    # -- history: finished requests survive a restart -----------------------
    HISTORY_KEPT = 500

    def _load_history(self) -> None:
        with contextlib.suppress(ValueError, OSError):
            saved = json.loads(self.history_file.read_text(encoding="utf-8"))
            for entry in saved if isinstance(saved, list) else []:
                if isinstance(entry, dict):
                    task = Task.load(entry)
                    self.tasks[task.id] = task

    def _save_history(self) -> None:
        finished = [t.out() for t in self.recent_tasks(self.HISTORY_KEPT)
                    if t.status in {"completed", "failed"}]
        with contextlib.suppress(OSError):  # history is a nicety; never fail a request on it
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            spare = self.history_file.with_suffix(".tmp")
            spare.write_text(json.dumps(finished, ensure_ascii=False), encoding="utf-8")
            spare.replace(self.history_file)

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
        """Who turns the microphone into text: "browser" (the page's own speech
        recognition, no key), "whisper" (on this computer, no key, any browser),
        "wispr" if chosen and keyed, else "" (typing only)."""
        if self.settings.listen_provider == "browser":
            return "browser"
        if self.settings.listen_provider == "wispr" and self.settings.wispr_api_key:
            return "wispr"
        if self.settings.listen_provider == "whisper" and have_module("faster_whisper"):
            return "whisper"
        return ""

    @property
    def can_listen(self) -> bool:
        return bool(self.listen_provider)

    # -- requests -------------------------------------------------------
    def submit(self, request: str, prompt: str | None = None, kind: str = "asked",
               client: str | None = None) -> Task:
        task = Task(request=request.strip(), prompt=prompt, kind=kind, client=client)
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
                self.hub.emit("task.failed", task.error, task_id=task.id, speak=True,
                              data={"client": task.client})
            except Exception as exc:  # noqa: BLE001 - one bad request must not stop the server
                task.error = f"Unexpected error: {type(exc).__name__}: {exc}"
                task.set("failed")
                self.hub.emit("task.failed", task.error, task_id=task.id)
            else:
                task.result = answer
                task.set("completed")
                self.hub.emit("task.completed", answer, task_id=task.id, speak=True,
                              data={"request": task.request, "client": task.client})
            finally:
                self._save_history()

    async def _converse(self, task: Task) -> str:
        """Answer with whichever brain is switched on (Mothership → Connections)."""
        brain = self.settings.brain if self.settings.brain in BRAINS else "gemini"
        spec = BRAINS[brain]
        if not self.settings.key_for(brain):
            raise JarvisError(f"There's no {spec['name']} key yet — add one in the Mothership, "
                              "under Connections.")
        if spec["kind"] == "anthropic":
            return await self._converse_claude(task)
        if spec["kind"] == "openai":
            return await self._converse_openai(task, brain)
        return await self._converse_gemini(task)

    def _remember(self, task: Task, answer: str) -> None:
        """Keep the exchange as plain text, so any brain can pick up the thread."""
        self.chat += [{"role": "user", "parts": [{"text": task.prompt or task.request}]},
                      {"role": "model", "parts": [{"text": answer}]}]

    def _history(self) -> list[dict[str, str]]:
        """The recent conversation as user/assistant text turns."""
        return [{"role": "assistant" if turn["role"] == "model" else "user",
                 "content": "".join(p.get("text", "") for p in turn["parts"])}
                for turn in self.chat[-24:]]

    async def _converse_gemini(self, task: Task) -> str:
        """Gemini's tool loop: run the tools it asks for until it answers in words."""
        user_turn = {"role": "user", "parts": [{"text": task.prompt or task.request}]}
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
                self._remember(task, answer)
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

    # -- the OpenAI chat format: OpenAI, Groq, OpenRouter, DeepSeek -----------
    async def _converse_openai(self, task: Task, brain: str) -> str:
        spec = BRAINS[brain]
        model = self.settings.model_for(brain)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.persona()}, *self._history(),
            {"role": "user", "content": task.prompt or task.request}]
        tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                   "parameters": t.parameters}}
                 for t in self.tools.values()]
        for _ in range(10):
            data = await self._brain_post(
                brain, f"{spec['base']}/chat/completions",
                {"model": model, "messages": messages, "tools": tools},
                {"Authorization": f"Bearer {self.settings.key_for(brain)}"})
            message = ((data.get("choices") or [{}])[0]).get("message") or {}
            calls = [c for c in message.get("tool_calls") or [] if isinstance(c, dict)]
            if not calls:
                content = message.get("content")
                answer = (content if isinstance(content, str) else "").strip()
                if not answer:
                    raise JarvisError(f"{spec['name']} gave an empty answer.")
                self._remember(task, answer)
                return answer
            messages.append(message)  # verbatim, tool calls and all
            for call in calls:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    args = {}
                result = await self._call_tool(task, {"name": function.get("name", ""),
                                                      "args": args if isinstance(args, dict)
                                                      else {}})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
        raise JarvisError("I went round in circles on that one — try asking more specifically.")

    async def _brain_post(self, brain: str, url: str, body: dict[str, Any],
                          headers: dict[str, str]) -> dict[str, Any]:
        name = BRAINS[brain]["name"]
        for attempt in range(3):
            try:
                response = await self.http.post(url, json=body, headers=headers, timeout=120)
            except httpx.TimeoutException as exc:
                raise JarvisError(f"{name} didn't answer within two minutes.") from exc
            except httpx.RequestError as exc:
                raise JarvisError(f"Can't reach {name} — check the internet connection.") from exc
            if response.status_code in {429, 500, 502, 503} and attempt < 2:
                wait = response.headers.get("retry-after", "")
                seconds = float(wait) if wait.replace(".", "", 1).isdigit() else 2.0 ** attempt
                if seconds <= 20:
                    self.hub.emit("step.retrying", f"{name} is busy — retrying in {seconds:.0f}s.")
                    await asyncio.sleep(seconds)
                    continue
            if response.status_code >= 400:
                raise JarvisError(self._brain_error(brain, response.status_code,
                                                    error_detail(response)))
            result: dict[str, Any] = response.json()
            return result
        raise JarvisError(f"{name} stayed busy — try again in a minute, or switch brain.")

    def _brain_error(self, brain: str, status: int, detail: str) -> str:
        name = BRAINS[brain]["name"]
        if status in {401, 403}:
            return f"{name} rejected the key — check it in the Mothership, under Connections."
        if status == 404:
            return (f"{name} has no model {self.settings.model_for(brain)!r} — pick another "
                    "under Connections.")
        if status in {402, 429}:
            return (f"{name}'s usage limit is used up for now — wait a little, or switch "
                    "brain in the Mothership, under Connections.")
        return f"{name} error {status}: {detail}"

    # -- Claude, through Anthropic's SDK ----------------------------------------------
    def _claude(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise JarvisError("Claude needs the anthropic package — run: "
                              "pip install -r requirements.txt") from exc
        # Jarvis's own HTTP client: one connection pool, and the tests' fake web.
        return anthropic.AsyncAnthropic(api_key=self.settings.key_for("anthropic"),
                                        http_client=self.http, max_retries=2)

    async def _converse_claude(self, task: Task) -> str:
        import anthropic

        client = self._claude()
        model = self.settings.model_for("anthropic")
        messages: list[dict[str, Any]] = [
            *self._history(), {"role": "user", "content": task.prompt or task.request}]
        tools = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                 for t in self.tools.values()]
        options: dict[str, Any] = {}
        if model in CLAUDE_FALLBACK:  # a declined request goes to a fallback model
            options = {"betas": ["server-side-fallback-2026-07-01"],
                       "extra_body": {"fallbacks": "default"}}
        for _ in range(10):
            try:
                response = await client.beta.messages.create(
                    model=model, max_tokens=16000, system=self.persona(), messages=messages,
                    tools=tools, **options)
            except anthropic.AuthenticationError as exc:
                raise JarvisError(self._brain_error("anthropic", 401, "")) from exc
            except anthropic.NotFoundError as exc:
                raise JarvisError(self._brain_error("anthropic", 404, "")) from exc
            except anthropic.RateLimitError as exc:
                raise JarvisError(self._brain_error("anthropic", 429, "")) from exc
            except anthropic.APIStatusError as exc:
                raise JarvisError(self._brain_error("anthropic", exc.status_code,
                                                    exc.message)) from exc
            except anthropic.APIConnectionError as exc:
                raise JarvisError("Can't reach Claude — check the internet connection.") from exc
            if response.stop_reason == "refusal":
                return "I'm afraid I can't help with that one."
            uses = [b for b in response.content if b.type == "tool_use"]
            if not uses:
                answer = "".join(b.text for b in response.content if b.type == "text").strip()
                if not answer:
                    raise JarvisError(f"Claude gave an empty answer ({response.stop_reason}).")
                self._remember(task, answer)
                return answer
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in uses:
                result = await self._call_tool(task, {"name": block.name, "id": block.id,
                                                      "args": dict(block.input or {})})
                reply: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id,
                                         "content": json.dumps(result, ensure_ascii=False,
                                                               default=str)}
                if "error" in result:
                    reply["is_error"] = True
                results.append(reply)
            messages.append({"role": "user", "content": results})  # all results, one message
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
            "mothership": self.mothership_summary(),
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
            return ("Gemini rejected the API key — check GEMINI_API_KEY (Mothership → "
                    f"Connections, or .env; {KEY_URL}).")
        if status == 404:
            return f"Gemini has no model {self.settings.gemini_model!r} — check GEMINI_MODEL."
        if status == 429:
            return ("Gemini's free-tier limit is used up for now — wait a minute, or switch "
                    "brain in the Mothership, under Connections.")
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

    async def check_brain(self, brain: str) -> tuple[bool, str]:
        """Prove a brain's key and model with a lookup — no tokens, no quota."""
        spec = BRAINS[brain]
        key, model = self.settings.key_for(brain), self.settings.model_for(brain)
        if not key:
            return False, f"No {spec['name']} key yet."
        if spec["kind"] == "gemini":
            detail = await self.check_gemini()
            return detail.startswith("connected"), detail
        if spec["kind"] == "anthropic":
            import anthropic

            try:
                found = await self._claude().models.retrieve(model)
            except anthropic.APIStatusError as exc:
                return False, self._brain_error(brain, exc.status_code, exc.message)
            except anthropic.APIConnectionError:
                return False, "not reachable (offline?)"
            return True, f"connected — {found.display_name}"
        try:
            response = await self.http.get(f"{spec['base']}/models", timeout=15,
                                           headers={"Authorization": f"Bearer {key}"})
        except httpx.RequestError:
            return False, "not reachable (offline?)"
        if response.status_code >= 400:
            return False, self._brain_error(brain, response.status_code, error_detail(response))
        with contextlib.suppress(ValueError, AttributeError, TypeError):
            ids = {str(m.get("id")) for m in response.json().get("data", [])}
            if ids and model not in ids and not model.startswith("openrouter/"):
                return False, (f"The key works, but {spec['name']} has no model {model!r} — "
                               "pick another.")
        return True, f"connected — {model}"

    # -- connections: keys and providers, kept in .env, applied live ---------------
    #: What the dashboard may change in .env — nothing else, ever.
    EDITABLE = {
        "JARVIS_BRAIN", "GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_KEY",
        "GEMINI_MODEL", "GEMINI_THINKING", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
        "OPENAI_API_KEY", "OPENAI_MODEL", "GROQ_API_KEY", "GROQ_MODEL", "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "JARVIS_VOICE",
        "JARVIS_VOICE_PROVIDER", "OPENAI_TTS_MODEL", "OPENAI_TTS_VOICE", "JARVIS_VOICE_STYLE",
        "JARVIS_LISTEN_PROVIDER", "JARVIS_LISTEN_LANGUAGE", "WHISPER_MODEL", "WISPR_API_KEY",
        "WISPR_LANGUAGE", "JARVIS_HOST", "JARVIS_PORT",
    }
    #: Settings that only a restart can change (the server is already bound).
    RESTART_ONLY = ("host", "port", "data_dir", "frontend", "cors_origins", "workspace",
                    "persona_file", "env_file", "startup_terminals")
    restart: Callable[[], None] | None = None  # set by serve(): stop and start again

    def connections_out(self) -> dict[str, Any]:
        s = self.settings
        brains = []
        for brain, spec in BRAINS.items():
            key = s.key_for(brain)
            brains.append({"id": brain, "name": spec["name"], "maker": spec["maker"],
                           "configured": bool(key), "key_hint": mask(key),
                           "model": s.model_for(brain), "models": spec["models"],
                           "key_env": spec["key_env"], "model_env": spec["model_env"],
                           "key_url": spec["key_url"], "note": spec["note"]})
        slots = s.gemini_keys if any(s.gemini_keys) else (s.gemini_api_key, "", "")
        fresh = Settings.load(s.env_file) if s.env_file.is_file() else s
        restart = {name: getattr(fresh, name) for name in ("host", "port")
                   if getattr(fresh, name) != getattr(s, name)}
        return {
            "brain": {"active": s.brain, "brains": brains, "gemini_slot": s.gemini_slot,
                      "gemini_keys": [{"slot": i + 1, "configured": bool(k), "key_hint": mask(k)}
                                      for i, k in enumerate(slots)],
                      "thinking": s.thinking},
            "voice": {"provider": s.voice_provider, "voice": s.voice,
                      "speaking": self.voice_provider,
                      "openai_voice": s.openai_tts_voice, "openai_model": s.openai_tts_model,
                      "style": s.voice_style, "openai_configured": bool(s.openai_api_key),
                      "edge_installed": have_module("edge_tts")},
            "listen": {"provider": s.listen_provider, "listening": self.listen_provider,
                       "language": s.listen_language, "whisper_model": s.whisper_model,
                       "whisper_installed": have_module("faster_whisper"),
                       "wispr_configured": bool(s.wispr_api_key),
                       "wispr_hint": mask(s.wispr_api_key), "wispr_language": s.wispr_language},
            "access": {"token_hint": mask(s.api_token), "host": s.host, "port": s.port,
                       "phone": s.host in {"0.0.0.0", "::"}},
            "restart_needed": restart,
            "can_restart": self.restart is not None,
            "env_file": str(s.env_file),
            "overridden": sorted(k for k in self.EDITABLE if os.environ.get(k)),
        }

    def change_connections(self, values: dict[str, str | None]) -> None:
        """Write the given .env keys (None clears one) and apply them now."""
        unknown = set(values) - self.EDITABLE
        if unknown:
            raise JarvisError(f"Those can't be changed here: {', '.join(sorted(unknown))}.")
        for key, value in values.items():
            text = "" if value is None else str(value).strip()
            if "\n" in text or "\r" in text or len(text) > 2000:
                raise JarvisError(f"{key} doesn't look right.")
            check = {"JARVIS_BRAIN": set(BRAINS), "GEMINI_KEY": {"1", "2", "3"},
                     "JARVIS_HOST": {"127.0.0.1", "0.0.0.0"},
                     "JARVIS_VOICE_PROVIDER": {"edge", "openai"},
                     "JARVIS_LISTEN_PROVIDER": {"browser", "whisper", "wispr", "off"},
                     "GEMINI_THINKING": {"", "minimal", "low", "medium", "high"}}.get(key)
            if check is not None and text not in check:
                raise JarvisError(f"{key} must be one of: {', '.join(sorted(check))}.")
            if key == "JARVIS_PORT" and not (text.isdigit() and 1 <= int(text) <= 65535):
                raise JarvisError("The port must be a number from 1 to 65535.")
        for key, value in values.items():
            save_env_value(self.settings.env_file, key, "" if value is None else str(value).strip())
        self.reload_settings()
        # Names only: a key's value never goes into the event stream or the log.
        self.hub.emit("connections.updated", f"Saved {', '.join(sorted(values))}.",
                      data={"keys": sorted(values)})

    def reload_settings(self) -> None:
        """Re-read .env into the running settings — everything a restart isn't
        needed for, so open terminals and the conversation carry on."""
        fresh = Settings.load(self.settings.env_file)
        for item in fields(Settings):
            if item.name not in self.RESTART_ONLY:
                setattr(self.settings, item.name, getattr(fresh, item.name))
        self.hub.voice_enabled = self.can_speak

    def rotate_token(self) -> str:
        token = secrets.token_urlsafe(32)
        save_env_value(self.settings.env_file, "JARVIS_API_TOKEN", token)
        self.settings.api_token = token
        self.hub.emit("connections.updated", "Made a new dashboard token.",
                      data={"keys": ["JARVIS_API_TOKEN"]})
        return token

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
            Tool("run_control",
                 "Run one of the user's Mothership controls (listed in the system prompt) "
                 "— e.g. change the weather in a sim, start the trading bot. Match the "
                 "user's words to the control's name. `action` stop or restart acts on a "
                 "control that is running (e.g. stop or restart the trading bot). A control "
                 "listed with inputs takes them in `inputs`; ask the user for any they "
                 "didn't give that have no default.",
                 params(["control"], control="string: the control's name or id",
                        action="string: run (default), stop or restart",
                        inputs='string: JSON object of the control\'s inputs, e.g. '
                               '{"duration": "8h", "goal": "learn shorts"}'),
                 self.use_control,
                 lambda a: f"Control: {a.get('control', '?')}"
                           + (f" ({a['action']})" if a.get("action") not in (None, "run")
                              else ""),
                 risky=True),
            Tool("add_idea",
                 "Note an idea on one of the user's Mothership projects.",
                 params(["project", "idea"], project="string: the project's name",
                        idea="string: the idea, in a sentence"),
                 self.note_idea, lambda a: f"Idea for {a.get('project', '?')}"),
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
                                   f"Run and wait for the output: {command}",
                                   free=self._safe(command)):
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
                       reason: str, free: str | None = None) -> bool:
        """Wait for Allow/Deny — unless AUTO_APPROVE is on, or `free` says why
        this one needs no asking (a read-only command, a trusted terminal)."""
        if self.settings.auto_approve:
            return True
        if free:
            self.hub.emit("approval.auto", f"Didn't ask ({free}): {reason}", task_id=task.id,
                          tool=tool)
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

    def _safe(self, command: str) -> str | None:
        """Why `command` needs no asking, or None if it does."""
        return "read-only" if self.settings.allow_safe and is_safe(command) else None

    def decide(self, approval_id: str, decision: str) -> None:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        if not approval.future.done():
            approval.future.set_result(decision)

    # -- terminals ----------------------------------------------------------
    MAX_TERMINALS = 8

    def open_terminal(self, title: str, purpose: str = "", opened_by: str = "you",
                      cols: int | None = None, rows: int | None = None,
                      cwd: Path | None = None, control: str | None = None,
                      project: str | None = None) -> Terminal:
        """A new shell, in `cwd` (default: the workspace). Its size is fixed for
        life — the page scales its font to fit — because shrinking a terminal
        cuts its lines (pyte doesn't reflow). Without a size it takes the size
        of the last one the page opened."""
        if cols and rows:
            self.view_size = (cols, rows)
        cols, rows = self.view_size
        if len(self.terminals) >= self.MAX_TERMINALS:
            raise JarvisError(f"{self.MAX_TERMINALS} terminals are open already — "
                              "close one on the Terminal tab first.")
        self._terminal_count += 1
        terminal_id = f"term-{self._terminal_count}"
        title = " ".join(title.split())[:60] or f"Terminal {self._terminal_count}"
        process = self.spawn_shell(cwd or self.settings.workspace, cols, rows)
        terminal = Terminal(terminal_id, title, " ".join(purpose.split())[:300], opened_by,
                            process, cols, rows, self._terminal_exited,
                            on_finish=self._terminal_finished)
        terminal.control, terminal.project = control, project
        self.terminals[terminal_id] = terminal
        who = {"jarvis": "Jarvis opened", "startup": "Started"}.get(opened_by, "Opened")
        self.hub.emit("terminal.opened", f"{who} a terminal: {title}",
                      data={"terminal": terminal.out()})
        return terminal

    def _terminal_exited(self, terminal: Terminal) -> None:
        self.hub.emit("terminal.exited", f"{terminal.title}: the shell {terminal.state}.",
                      data={"terminal": terminal.out()})

    def _terminal_finished(self, terminal: Terminal, result: dict[str, Any]) -> None:
        """A command came back to the prompt. A failure is flagged (the page
        offers Explain); a long one Jarvis wasn't already watching gets a
        spoken word on how it went."""
        long = 0 < self.settings.notify_after <= result["seconds"]
        if not result["ok"]:
            self.hub.emit("terminal.failed", f"{terminal.title}: {terminal.result_text()}",
                          data={"terminal": terminal.out()})
        elif long:
            self.hub.emit("terminal.finished", f"{terminal.title}: {terminal.result_text()}",
                          data={"terminal": terminal.out()})
        if long and not result["watched"]:
            how = "worked" if result["ok"] else "failed"
            self.submit(
                f'{terminal.title}: "{result["command"][:60]}" {how}',
                prompt=(f"(An automatic notice, not typed by the user.) In terminal "
                        f"{terminal.id} \"{terminal.title}\", `{result['command']}` just "
                        f"finished after {duration(result['seconds'])} and "
                        f"{'worked' if result['ok'] else 'failed'}. The end of its screen:\n"
                        f"```\n{chr(10).join(terminal.lines(40))}\n```\n"
                        "Tell the user how it went in one or two short sentences — mention "
                        "errors or warnings that matter. Don't run anything."), kind="notice")

    def explain_terminal(self, terminal_id: str) -> Task:
        """Ask Jarvis what went wrong in a terminal, with its screen attached."""
        terminal = self._terminal(terminal_id)
        last = terminal.last_result
        if last and not last["ok"]:
            asked = f'Why did "{last["command"][:60]}" fail?'
            about = f"`{last['command']}` failed there."
        else:
            asked = f"What's going on in {terminal.title}?"
            about = "The user wants to know what its screen shows and whether anything is wrong."
        return self.submit(asked, prompt=(
            f"Look at terminal {terminal.id} \"{terminal.title}\". {about} Its screen:\n"
            f"```\n{chr(10).join(terminal.lines(80))}\n```\n"
            "Explain briefly and plainly what went wrong and how to fix it. If a command "
            "would fix it, give it — and offer to run it rather than running it."),
            kind="explain")

    def trust_terminal(self, terminal_id: str, trusted: bool) -> Terminal:
        terminal = self._terminal(terminal_id)
        terminal.trusted = trusted
        self.hub.emit("terminal.updated",
                      f"Jarvis {'may type freely' if trusted else 'must ask to type'} in "
                      f"{terminal.title}.", data={"terminal": terminal.out()})
        return terminal

    async def open_startup_terminals(self) -> None:
        """Open the terminals listed in terminals.json, running their commands.
        You wrote those commands yourself, so they don't wait for approval."""
        path = self.settings.startup_terminals
        if not path.is_file():
            return
        try:
            entries = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            entries = None
        if not isinstance(entries, list):
            self.hub.emit("error", f"{path.name} isn't a JSON list of terminals — skipped.")
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                terminal = self.open_terminal(str(entry.get("title", "")),
                                              str(entry.get("purpose", "")), "startup")
            except JarvisError as exc:
                self.hub.emit("error", str(exc))
                return
            command = str(entry.get("command", "")).strip()
            if command:
                await terminal.ready()
                with contextlib.suppress(JarvisError):
                    terminal.send(command + "\r", shown=command, by="startup")

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
            if t.trusted:
                line += " The user lets you type here without asking."
            if t.last_result:
                line += f" Last command: {t.result_text()}."
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
                f'Open a terminal "{title}" and run: {command}', free=self._safe(command)):
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
        free = "you trust this terminal" if terminal.trusted else (
            self._safe(text) if text and press_enter and not key else None)
        if not await self._approve(
                task, "terminal_write",
                {"terminal_id": terminal.id, "title": terminal.title, "text": shown},
                f'Type into "{terminal.title}" ({terminal.id}): {shown}', free=free):
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

    async def use_control(self, task: Task, control: str, action: str = "run",
                          inputs: str = "") -> dict[str, Any]:
        found = self.mothership.control(control)
        if found is None:
            names = ", ".join(str(c.get("name")) for c in self.mothership.controls) or "none"
            return {"error": f"There's no control like {control!r}. Controls: {names}."}
        verb = str(action or "run").strip().lower()
        if verb in {"stop", "restart"}:
            return await self._stop_or_restart(task, found, verb)
        if verb != "run":
            return {"error": f"Unknown action {action!r} — use run, stop or restart."}
        name, kind = found.get("name"), found.get("kind")
        action = str(found.get("action") or "")
        if kind == "idea" or not action:
            return {"error": f"“{name}” isn't built yet — the user can press Build with "
                             "Claude on it in the Mothership."}
        if kind == "ask":
            return {"do_now": action, "note": "This control is a request — carry it out now."}
        if kind == "link":
            if urlparse(action).scheme not in {"http", "https"}:
                return {"error": "That control's link isn't a web address."}
            await asyncio.to_thread(webbrowser.open, action)
            return {"opened": action}
        try:
            given = json.loads(inputs) if str(inputs or "").strip() else {}
        except ValueError:
            given = None
        if not isinstance(given, dict):
            return {"error": 'inputs must be a JSON object, e.g. {"duration": "8h"}.'}
        action, values = fill_inputs(found, given)
        free = ("the user lets you run this control without asking" if found.get("trusted")
                else self._safe(action))
        if not await self._approve(task, "run_control", {"control": name, "command": action},
                                   f"Run the control “{name}”: {action}", free=free):
            return {"denied": True,
                    "note": "The user did not allow this. Don't retry unless asked."}
        started = await self.run_control(found, by="jarvis", inputs=values)
        terminal = self.terminals[started["terminal"]]
        await terminal.settle(limit=8)
        return {"control": name, **self._screen(terminal)}

    async def _stop_or_restart(self, task: Task, control: dict[str, Any],
                               verb: str) -> dict[str, Any]:
        name = control.get("name")
        terminal = self._control_terminal(control)
        if verb == "stop" and (terminal is None or not terminal.running):
            return {"error": f"“{name}” isn't running."}
        if not control.get("trusted") and not await self._approve(
                task, "run_control", {"control": name, "action": verb},
                f"{verb.capitalize()} the control “{name}”"):
            return {"denied": True,
                    "note": "The user did not allow this. Don't retry unless asked."}
        if verb == "stop":
            terminal = await self.stop_control(str(control["id"]), by="jarvis")
            await terminal.settle(limit=5)
            return {"control": name, "stopped": True, **self._screen(terminal)}
        started = await self.restart_control(str(control["id"]), by="jarvis")
        terminal = self.terminals[started["terminal"]]
        await terminal.settle(limit=8)
        return {"control": name, "restarted": True, **self._screen(terminal)}

    async def note_idea(self, task: Task, project: str, idea: str) -> dict[str, Any]:
        found = self.mothership.project(project)
        if found is None:
            names = ", ".join(str(p.get("name")) for p in self.mothership.projects) or "none"
            return {"error": f"There's no project like {project!r}. Projects: {names}."}
        self.add_project_idea(str(found["id"]), idea, by="jarvis")
        return {"noted": idea, "project": found.get("name")}

    async def terminal_list(self, task: Task) -> dict[str, Any]:
        if not self.terminals:
            return {"terminals": [], "note": "No terminals are open."}
        return {"terminals": [t.summary() for t in self.terminals.values()]}

    # -- the mothership: your controls and projects -------------------------
    def mothership_out(self) -> dict[str, Any]:
        data = self.mothership.load()
        return {"controls": data["controls"], "projects": data["projects"],
                "claude": shutil.which("claude") is not None}

    def _changed(self, message: str) -> None:
        self.mothership.save()
        self.hub.emit("mothership.updated", message)

    @staticmethod
    def _by_id(items: list[dict[str, Any]], item_id: str, what: str) -> dict[str, Any]:
        for item in items:
            if item.get("id") == item_id:
                return item
        raise KeyError(f"No {what} {item_id!r}.")

    def save_control(self, fields: dict[str, Any], control_id: str | None = None,
                     ) -> dict[str, Any]:
        controls = self.mothership.controls
        if control_id:
            control = self._by_id(controls, control_id, "control")
        else:
            control = {"id": short_id("c"), "created_at": now()}
            controls.append(control)
        control.update(fields)
        control["updated_at"] = now()
        self._changed(f"Saved the control “{control.get('name')}”.")
        return control

    def delete_control(self, control_id: str) -> None:
        control = self._by_id(self.mothership.controls, control_id, "control")
        self.mothership.controls.remove(control)
        self._changed(f"Deleted the control “{control.get('name')}”.")

    def save_project(self, fields: dict[str, Any], project_id: str | None = None,
                     ) -> dict[str, Any]:
        projects = self.mothership.projects
        if project_id:
            project = self._by_id(projects, project_id, "project")
        else:
            project = {"id": short_id("p"), "created_at": now(), "ideas": []}
            projects.append(project)
        project.update(fields)
        project["updated_at"] = now()
        self._changed(f"Saved the project “{project.get('name')}”.")
        return project

    def delete_project(self, project_id: str) -> None:
        project = self._by_id(self.mothership.projects, project_id, "project")
        self.mothership.projects.remove(project)
        for control in self.mothership.controls:  # keep its controls, just unfiled
            if control.get("project") == project_id:
                control["project"] = ""
        self._changed(f"Deleted the project “{project.get('name')}”.")

    def add_project_idea(self, project_id: str, text: str, by: str = "you") -> dict[str, Any]:
        project = self._by_id(self.mothership.projects, project_id, "project")
        idea: dict[str, Any] = {"id": short_id("i"), "text": " ".join(text.split())[:500],
                                "done": False, "by": by, "created_at": now()}
        project.setdefault("ideas", []).append(idea)
        self._changed(f"New idea for {project.get('name')}: {idea['text'][:80]}")
        return idea

    def update_idea(self, project_id: str, idea_id: str, text: str | None = None,
                    done: bool | None = None, delete: bool = False) -> None:
        project = self._by_id(self.mothership.projects, project_id, "project")
        ideas = project.setdefault("ideas", [])
        idea = self._by_id(ideas, idea_id, "idea")
        if delete:
            ideas.remove(idea)
        else:
            if text is not None:
                idea["text"] = " ".join(text.split())[:500]
            if done is not None:
                idea["done"] = done
        self._changed(f"Updated the ideas for {project.get('name')}.")

    def project_status(self, project_id: str) -> dict[str, Any]:
        """The project's status file — e.g. a trading bot's live_state.json —
        read fresh each time, and only from inside the project's folder."""
        project = self._by_id(self.mothership.projects, project_id, "project")
        folder = self.mothership.folder(project_id)
        name = str(project.get("status_file") or "").strip()
        if not folder or not name:
            return {"available": False, "note": "No status file set for this project."}
        path = (folder / name).resolve()
        if folder.resolve() not in path.parents:
            return {"available": False, "note": "The status file must be inside the folder."}
        if not path.is_file():
            return {"available": False, "note": f"There's no {name} yet."}
        stat = path.stat()
        if stat.st_size > 4_000_000:
            return {"available": False, "note": f"{name} is too big to show."}
        modified = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            return {"available": True, "file": str(path), "modified": modified,
                    "data": json.loads(text)}
        except ValueError:
            return {"available": True, "file": str(path), "modified": modified,
                    "text": text[-6000:]}

    def _reports_folder(self, project_id: str) -> Path | None:
        """The project's reports folder: inside its folder, and only if it exists."""
        project = self._by_id(self.mothership.projects, project_id, "project")
        folder = self.mothership.folder(project_id)
        name = str(project.get("reports_dir") or "").strip()
        if not folder or not name:
            return None
        path = (folder / name).resolve()
        if path != folder.resolve() and folder.resolve() not in path.parents:
            return None
        return path if path.is_dir() else None

    def project_reports(self, project_id: str) -> dict[str, Any]:
        """Every report in the project's reports folder, newest first, each with
        its label — the page's <title>, and its description and tone meta tags."""
        project = self._by_id(self.mothership.projects, project_id, "project")
        if not str(project.get("reports_dir") or "").strip():
            return {"available": False, "note": "No reports folder set for this project."}
        folder = self._reports_folder(project_id)
        if folder is None:
            return {"available": False,
                    "note": f"There's no {project.get('reports_dir')} folder yet — "
                            "the first run that writes a report makes it."}
        found = []
        for path in folder.iterdir():
            if path.suffix.lower() in REPORT_TYPES and path.is_file():
                stat = path.stat()
                found.append((stat.st_mtime, stat.st_size, path))
        found.sort(key=lambda item: item[0], reverse=True)
        # Newest first by when each was made: the date in its name, else its file time.
        labels = [report_label(path, mtime, size) for mtime, size, path in found[:2000]]
        labels.sort(key=lambda label: str(label["created_at"]), reverse=True)
        return {"available": True, "total": len(found), "reports": labels[:REPORTS_LISTED]}

    def project_report(self, project_id: str, name: str) -> tuple[Path, str]:
        """One report's file, by its plain name; nothing outside the folder."""
        folder = self._reports_folder(project_id)
        if folder is None or not REPORT_NAME.fullmatch(name):
            raise KeyError(f"No report {name!r}.")
        path = folder / name
        if path.suffix.lower() not in REPORT_TYPES or not path.is_file():
            raise KeyError(f"No report {name!r}.")
        if path.stat().st_size > 30_000_000:
            raise JarvisError(f"{name} is too big to show here.")
        return path, REPORT_TYPES[path.suffix.lower()]

    def _control_terminal(self, control: dict[str, Any]) -> Terminal | None:
        return next((t for t in self.terminals.values()
                     if t.control == control.get("id") and t.status == "running"), None)

    async def run_control(self, control: dict[str, Any], by: str,
                          inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        """Do what a control does. Approval, if any, has happened already.
        `inputs` fill a command control's `{name}`s (see `fill_inputs`)."""
        name, kind = control.get("name", "?"), control.get("kind", "idea")
        action = str(control.get("action") or "").strip()
        if kind == "idea" or not action:
            raise JarvisError(f"“{name}” isn't built yet — press Build with Claude.")
        if kind == "link":
            return {"url": action}
        if kind == "ask":
            return {"task_id": self.submit(action, kind="control").id}
        action, values = fill_inputs(control, inputs)
        terminal = self._control_terminal(control)
        if terminal and terminal.running:
            raise JarvisError(f"“{name}” is still running in {terminal.title} — stop it first.")
        if terminal is None:
            project = str(control.get("project") or "") or None
            terminal = self.open_terminal(str(name), str(control.get("description") or ""), by,
                                          cwd=self.mothership.folder(project),
                                          control=control.get("id"), project=project)
            await terminal.ready()
        terminal.send(action + "\r", shown=action, by=by)
        # The page reloaded its terminals when this one opened, before the
        # shell was ready: without this it never learns the control is busy.
        self.hub.emit("terminal.updated", f"{terminal.title}: running {action[:60]}",
                      data={"terminal": terminal.out()})
        control["last_run"] = now()
        if values:
            control["last_inputs"] = values  # the form starts from them; Restart reuses them
        self._changed(f"Ran the control “{name}”.")
        return {"terminal": terminal.id}

    async def stop_control(self, control_id: str, by: str = "you") -> Terminal:
        control = self._by_id(self.mothership.controls, control_id, "control")
        terminal = self._control_terminal(control)
        if terminal is None:
            raise JarvisError(f"“{control.get('name')}” isn't running.")
        await terminal.interrupt(by)
        return terminal

    async def restart_control(self, control_id: str, by: str = "you") -> dict[str, Any]:
        """Stop the control's command (Ctrl+C), wait for its prompt, run it again
        — e.g. a bot picking up new code or settings. Not running? Just run it."""
        control = self._by_id(self.mothership.controls, control_id, "control")
        terminal = self._control_terminal(control)
        if terminal is not None and terminal.running:
            await terminal.interrupt(by)
            if not await terminal.idle(limit=30):
                raise JarvisError(f"“{control.get('name')}” didn't stop within 30 seconds — "
                                  f"look at {terminal.title}.")
        return await self.run_control(control, by=by, inputs=control.get("last_inputs"))

    async def claude_terminal(self, title: str, cwd: Path, brief: str | None = None,
                              project: str | None = None) -> Terminal:
        """Open Claude Code in a terminal, in `cwd`, working from `brief`."""
        if shutil.which("claude") is None:
            raise JarvisError("Claude Code isn't installed on this PC "
                              "(https://claude.com/claude-code).")
        command = "claude"
        if brief:
            briefs = self.settings.data_dir / "briefs"
            briefs.mkdir(parents=True, exist_ok=True)
            path = briefs / f"{datetime.now():%Y%m%d-%H%M%S}-{slug(title)}.md"
            path.write_text(brief, encoding="utf-8")
            command = (f'claude "Read the brief in {ps_quote(path)} and help me build it. '
                       'Start with your plan."')
        cwd.mkdir(parents=True, exist_ok=True)
        terminal = self.open_terminal(f"Claude: {title}", "building with Claude Code", "you",
                                      cwd=cwd, project=project)
        await terminal.ready()
        terminal.send(command + "\r", shown=command, by="you")
        return terminal

    async def tailscale_terminal(self, share: bool) -> Terminal:
        """Turn Jarvis's private https address on (or off) with `tailscale
        serve`, in a terminal you can watch: the first time, it may print a
        link for allowing https in your Tailscale account, then wait for it."""
        if tailscale_exe() is None:
            raise JarvisError(f"Tailscale isn't installed on this PC — {TAILSCALE_DOWNLOAD}")
        port = self.settings.port
        if share:
            command = tailscale_command("serve", "--bg", str(port))
        else:
            url = (await asyncio.to_thread(tailscale_status, port))["url"]
            command = tailscale_command("serve", f"--https={urlparse(url).port or 443}", "off")
        terminal = self.open_terminal("Phone access", "tailscale serve: Jarvis's private https "
                                      "address, for your phone", "you")
        await terminal.ready()
        terminal.send(command + "\r", shown=command, by="you")
        return terminal

    def _plugs_in(self, cwd: Path) -> str:
        return (f"## How it plugs into Jarvis\n\nJarvis is my local assistant (its code is in "
                f"{HERE.parent}). Its Mothership dashboard shows *controls*: buttons I press, "
                "or ask Jarvis for by voice. A command control's command line is typed into "
                f"a PowerShell terminal opened in {cwd}. Controls live in "
                f"{self.mothership.path} — Jarvis notices edits to that file by itself, no "
                "restart needed.\n")

    async def build_control(self, control_id: str) -> Terminal:
        control = self._by_id(self.mothership.controls, control_id, "control")
        name = str(control.get("name"))
        project_id = str(control.get("project") or "") or None
        project = self.mothership.project(project_id) if project_id else None
        cwd = (self.mothership.folder(project_id)
               or self.settings.data_dir / "builds" / slug(name))
        where = [str(control.get("group") or "")]
        if project:
            where.append(f"project {project.get('name')} ({project.get('folder')})")
        current = (f"Right now it is a `{control.get('kind')}` control: "
                   f"`{control.get('action')}`." if control.get("action") else
                   "It isn't wired to anything yet.")
        brief = (f"# Build a Jarvis control: {name}\n\n"
                 f"**What it should do:** {control.get('description') or name}\n\n"
                 f"**Where:** {', '.join(w for w in where if w) or 'on this PC'}\n\n"
                 f"{current}\n\n{self._plugs_in(cwd)}\n"
                 "## What I'd like\n\nWork out how to make this happen on this PC, build it "
                 f"(a script in {cwd} is ideal) and test it with me. When it works, update "
                 f"the entry with \"id\": \"{control_id}\" in {self.mothership.path}: set "
                 "\"kind\" to \"command\" and \"action\" to the command line that runs it. "
                 "If it needs something chosen each time it runs, give it \"inputs\": "
                 "[{\"name\": \"duration\", \"label\": ..., \"kind\": \"choice\", \"options\": "
                 "[{\"label\": \"1 hour\", \"value\": \"1h\"}], \"default\": \"1h\"}, or "
                 "\"kind\": \"text\"], and put {duration} in the command where it goes — it "
                 "arrives quoted, so don't add quotes. Leave the rest of the file as it is.\n")
        return await self.claude_terminal(name, cwd, brief, project_id)

    async def build_idea(self, project_id: str, idea_id: str) -> Terminal:
        project = self._by_id(self.mothership.projects, project_id, "project")
        idea = self._by_id(project.get("ideas") or [], idea_id, "idea")
        cwd = (self.mothership.folder(project_id)
               or self.settings.data_dir / "builds" / slug(str(project.get("name"))))
        brief = (f"# An idea for {project.get('name')}\n\n**The idea:** {idea.get('text')}\n\n"
                 f"**The project:** {project.get('description') or project.get('name')} — "
                 f"folder {cwd}\n\n{self._plugs_in(cwd)}\n"
                 "## What I'd like\n\nHelp me turn this idea into something real. If part of "
                 "it becomes a button I'd press often, add a control for it to the "
                 "\"controls\" list in that file, shaped like the others: "
                 f"{{\"id\": \"c-<something unique>\", \"name\": ..., \"group\": "
                 f"\"{project.get('name')}\", \"project\": \"{project_id}\", \"kind\": "
                 "\"command\", \"action\": \"<command line>\", \"description\": ...}.\n")
        return await self.claude_terminal(str(idea.get("text"))[:40], cwd, brief, project_id)

    def mothership_summary(self) -> str:
        """Controls and projects as lines of the system prompt."""
        lines = []
        for p in self.mothership.projects:
            lines.append(f"- project `{p.get('name')}` — {p.get('description') or ''} "
                         f"(folder: {p.get('folder') or 'none'})".rstrip())
        for c in self.mothership.controls:
            what = c.get("description") or c.get("action") or ""
            ready = "not built yet" if c.get("kind") == "idea" else c.get("kind")
            trusted = ", runs without asking" if c.get("trusted") else ""
            asks = "; ".join(input_summary(i) for i in c.get("inputs") or [])
            lines.append(f"- control `{c.get('name')}` [{c.get('group') or 'general'}; "
                         f"{ready}{trusted}] — {what}" + (f" — inputs: {asks}" if asks else ""))
        return "\n".join(lines) or "Nothing set up yet."

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
        if not (self.can_speak or self.can_listen):
            return {"enabled": False}
        openai = self.voice_provider == "openai"
        return {"enabled": True, "can_speak": self.can_speak, "can_listen": self.can_listen,
                "voice": self.settings.openai_tts_voice if openai else self.settings.voice,
                "provider": self.voice_provider, "listen_provider": self.listen_provider,
                "listen_language": self.settings.listen_language,
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
            body["instructions"] = self.settings.voice_style
        response = await self.http.post(
            OPENAI_SPEECH_URL, json=body, timeout=60,
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}"})
        if response.status_code == 401:
            raise JarvisError("OpenAI rejected the key — check OPENAI_API_KEY in .env.")
        if response.status_code >= 400:
            raise JarvisError(f"OpenAI speech error {response.status_code}: "
                              f"{response.text[:200]}")
        return response.content

    async def transcribe(self, audio: bytes) -> str:
        """Speech to text for an uploaded recording, by whichever provider is on."""
        if self.listen_provider == "whisper":
            return await asyncio.to_thread(self._whisper_text, audio)
        return await self._wispr_text(audio)

    def load_whisper(self) -> None:
        """Load the Whisper model once (a few seconds); startup does it ahead of use."""
        if self._whisper is None:
            from faster_whisper import WhisperModel

            self._whisper = WhisperModel(self.settings.whisper_model, device="cpu",
                                         compute_type="int8")

    def _whisper_text(self, audio: bytes) -> str:
        """Whisper on this machine, on audio decoded here (see decode_16k)."""
        self.load_whisper()
        try:
            samples = decode_16k(audio)
        except Exception as exc:  # noqa: BLE001 - undecodable audio is reported, not raised
            raise JarvisError(f"Could not read that recording: {exc}") from exc
        if not len(samples):
            return ""
        try:
            english = self.settings.whisper_model.endswith(".en")
            segments, _ = self._whisper.transcribe(
                samples, language="en" if english else self.settings.listen_language[:2],
                beam_size=1, vad_filter=True, initial_prompt="Jarvis, hey Jarvis.")
            return " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001 - a failed transcription is reported
            raise JarvisError(f"Could not transcribe that recording: {exc}") from exc

    async def _wispr_text(self, wav: bytes) -> str:
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
    client: str | None = Field(default=None, max_length=64)


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


class TrustIn(BaseModel):
    trusted: bool


class ChoiceIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=100)


class InputIn(BaseModel):
    """Something a control asks for before it runs; its command says `{name}`."""
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,30}$")
    label: str = Field(default="", max_length=80)
    kind: Literal["choice", "text"] = "text"
    options: list[ChoiceIn] = Field(default_factory=list, max_length=40)
    default: str = Field(default="", max_length=300)
    placeholder: str = Field(default="", max_length=200)


class RunIn(BaseModel):
    inputs: dict[str, str] = Field(default_factory=dict)


class ShareIn(BaseModel):
    share: bool = True


class ConnectionsIn(BaseModel):
    #: .env keys to set; null clears one. Only Jarvis.EDITABLE keys are accepted.
    values: dict[str, str | None] = Field(max_length=40)


class BrainTestIn(BaseModel):
    brain: str = Field(min_length=1, max_length=40)


class ControlIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    group: str = Field(default="", max_length=60)
    project: str = Field(default="", max_length=40)
    kind: Literal["ask", "command", "link", "idea"] = "idea"
    action: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=2000)
    trusted: bool = False
    inputs: list[InputIn] = Field(default_factory=list, max_length=10)
    pinned: bool = False  # a button at the top of its project's page, not in the grid


class LinkIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=500)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=2000)
    folder: str = Field(default="", max_length=400)
    status_file: str = Field(default="", max_length=200)
    reports_dir: str = Field(default="", max_length=200)
    controls_title: str = Field(default="", max_length=40)
    hue: int = Field(default=190, ge=0, le=360)
    links: list[LinkIn] = Field(default_factory=list, max_length=20)


class IdeaIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class IdeaChangeIn(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    done: bool | None = None


class FreshFiles(StaticFiles):
    """The dashboard's files, re-checked on every load. Unchanged ones cost a
    304; without this a browser can keep running last version's scripts."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def web_address(url: str) -> bool:
    return urlparse(url.strip()).scheme in {"http", "https"}


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

    @app.post("/dash/api/terminals/{terminal_id}/explain", dependencies=guard)
    async def terminal_explain(terminal_id: str) -> dict[str, Any]:
        terminal(terminal_id)
        return jarvis.explain_terminal(terminal_id).out()

    @app.post("/dash/api/terminals/{terminal_id}/trust", dependencies=guard)
    async def terminal_trust(terminal_id: str, body: TrustIn) -> dict[str, Any]:
        terminal(terminal_id)
        return jarvis.trust_terminal(terminal_id, body.trusted).out()

    # -- the Mothership: controls and projects ---------------------------------
    ms = "/dash/api/mothership"

    @contextlib.contextmanager
    def answers() -> Iterator[None]:
        """Missing things are 404s; things that can't be done now are 409s."""
        try:
            yield
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        except JarvisError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def checked_control(body: ControlIn) -> dict[str, Any]:
        if body.kind == "link" and not web_address(body.action):
            raise HTTPException(status_code=422, detail="A link must start with http(s)://.")
        if body.project and jarvis.mothership.project(body.project) is None:
            raise HTTPException(status_code=422, detail="That project doesn't exist.")
        if any(i.kind == "choice" and not i.options for i in body.inputs):
            raise HTTPException(status_code=422, detail="A choice needs options to pick from.")
        if len({i.name for i in body.inputs}) != len(body.inputs):
            raise HTTPException(status_code=422, detail="Two inputs share a name.")
        return body.model_dump()

    def checked_project(body: ProjectIn) -> dict[str, Any]:
        folder = Path(body.folder)
        if body.folder and not folder.is_absolute() and (
                folder.drive or REPO.resolve() not in (REPO / folder).resolve().parents):
            raise HTTPException(status_code=422, detail="Give the folder's full path, or a "
                                                        "folder inside Jarvis (like tradebot).")
        if any(not web_address(link.url) for link in body.links):
            raise HTTPException(status_code=422, detail="Links must start with http(s)://.")
        return body.model_dump()

    @app.get(ms, dependencies=guard)
    async def mothership() -> dict[str, Any]:
        return jarvis.mothership_out()

    @app.post(f"{ms}/controls", dependencies=guard, status_code=201)
    async def new_control(body: ControlIn) -> dict[str, Any]:
        return jarvis.save_control(checked_control(body))

    @app.post(f"{ms}/controls/{{control_id}}", dependencies=guard)
    async def edit_control(control_id: str, body: ControlIn) -> dict[str, Any]:
        with answers():
            return jarvis.save_control(checked_control(body), control_id)

    @app.post(f"{ms}/controls/{{control_id}}/delete", dependencies=guard)
    async def delete_control(control_id: str) -> dict[str, bool]:
        with answers():
            jarvis.delete_control(control_id)
        return {"ok": True}

    @app.post(f"{ms}/controls/{{control_id}}/run", dependencies=guard)
    async def run_control(control_id: str, body: RunIn | None = None) -> dict[str, Any]:
        # You pressed the button yourself, so nothing waits for approval.
        with answers():
            control = jarvis._by_id(jarvis.mothership.controls, control_id, "control")
            return await jarvis.run_control(control, by="you",
                                            inputs=body.inputs if body else None)

    @app.post(f"{ms}/controls/{{control_id}}/stop", dependencies=guard)
    async def stop_control(control_id: str) -> dict[str, Any]:
        with answers():
            return {"terminal": (await jarvis.stop_control(control_id)).id}

    @app.post(f"{ms}/controls/{{control_id}}/restart", dependencies=guard)
    async def restart_control(control_id: str) -> dict[str, Any]:
        with answers():
            return await jarvis.restart_control(control_id)

    @app.post(f"{ms}/controls/{{control_id}}/build", dependencies=guard)
    async def build_control(control_id: str) -> dict[str, Any]:
        with answers():
            return (await jarvis.build_control(control_id)).out()

    @app.post(f"{ms}/projects", dependencies=guard, status_code=201)
    async def new_project(body: ProjectIn) -> dict[str, Any]:
        return jarvis.save_project(checked_project(body))

    @app.post(f"{ms}/projects/{{project_id}}", dependencies=guard)
    async def edit_project(project_id: str, body: ProjectIn) -> dict[str, Any]:
        with answers():
            return jarvis.save_project(checked_project(body), project_id)

    @app.post(f"{ms}/projects/{{project_id}}/delete", dependencies=guard)
    async def delete_project(project_id: str) -> dict[str, bool]:
        with answers():
            jarvis.delete_project(project_id)
        return {"ok": True}

    @app.get(f"{ms}/projects/{{project_id}}/status", dependencies=guard)
    async def project_status(project_id: str) -> dict[str, Any]:
        with answers():
            return await asyncio.to_thread(jarvis.project_status, project_id)

    @app.get(f"{ms}/projects/{{project_id}}/reports", dependencies=guard)
    async def project_reports(project_id: str) -> dict[str, Any]:
        with answers():
            return await asyncio.to_thread(jarvis.project_reports, project_id)

    @app.get(f"{ms}/projects/{{project_id}}/reports/{{name}}", dependencies=guard)
    async def project_report(project_id: str, name: str) -> Response:
        with answers():
            path, kind = jarvis.project_report(project_id, name)
            content = await asyncio.to_thread(path.read_bytes)
        # Shown inside a sandboxed frame on the page. Opened on its own, the CSP
        # sandbox keeps a report's scripts away from this origin (and the token).
        return Response(content, media_type="text/html" if kind == "html" else "text/plain",
                        headers={"Content-Security-Policy": "sandbox",
                                 "X-Content-Type-Options": "nosniff",
                                 "Cache-Control": "no-store"})

    @app.post(f"{ms}/projects/{{project_id}}/terminal", dependencies=guard)
    async def project_terminal(project_id: str) -> dict[str, Any]:
        with answers():
            project = jarvis._by_id(jarvis.mothership.projects, project_id, "project")
            return jarvis.open_terminal(str(project.get("name")), "", "you",
                                        cwd=jarvis.mothership.folder(project_id),
                                        project=project_id).out()

    @app.post(f"{ms}/projects/{{project_id}}/claude", dependencies=guard)
    async def project_claude(project_id: str) -> dict[str, Any]:
        with answers():
            project = jarvis._by_id(jarvis.mothership.projects, project_id, "project")
            cwd = (jarvis.mothership.folder(project_id)
                   or settings.data_dir / "builds" / slug(str(project.get("name"))))
            return (await jarvis.claude_terminal(str(project.get("name")), cwd,
                                                 project=project_id)).out()

    @app.post(f"{ms}/projects/{{project_id}}/ideas", dependencies=guard, status_code=201)
    async def new_idea(project_id: str, body: IdeaIn) -> dict[str, Any]:
        with answers():
            return jarvis.add_project_idea(project_id, body.text)

    @app.post(f"{ms}/projects/{{project_id}}/ideas/{{idea_id}}", dependencies=guard)
    async def change_idea(project_id: str, idea_id: str, body: IdeaChangeIn) -> dict[str, bool]:
        with answers():
            jarvis.update_idea(project_id, idea_id, body.text, body.done)
        return {"ok": True}

    @app.post(f"{ms}/projects/{{project_id}}/ideas/{{idea_id}}/delete", dependencies=guard)
    async def delete_idea(project_id: str, idea_id: str) -> dict[str, bool]:
        with answers():
            jarvis.update_idea(project_id, idea_id, delete=True)
        return {"ok": True}

    @app.post(f"{ms}/projects/{{project_id}}/ideas/{{idea_id}}/build", dependencies=guard)
    async def build_idea(project_id: str, idea_id: str) -> dict[str, Any]:
        with answers():
            return (await jarvis.build_idea(project_id, idea_id)).out()

    # -- connections: brains, voice, access — written to .env, applied live --------
    @app.get("/dash/api/connections", dependencies=guard)
    async def connections() -> dict[str, Any]:
        return jarvis.connections_out()

    @app.post("/dash/api/connections", dependencies=guard)
    async def change_connections(body: ConnectionsIn) -> dict[str, Any]:
        try:
            jarvis.change_connections(body.values)
        except JarvisError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return jarvis.connections_out()

    @app.post("/dash/api/connections/test", dependencies=guard)
    async def test_connection(body: BrainTestIn) -> dict[str, Any]:
        if body.brain not in BRAINS:
            raise HTTPException(status_code=404, detail="No such brain.")
        ok, detail = await jarvis.check_brain(body.brain)
        return {"ok": ok, "detail": detail}

    @app.post("/dash/api/connections/token", dependencies=guard)
    async def rotate_token() -> dict[str, str]:
        # The only route that returns a whole secret: the page that asked
        # needs the new token to stay connected.
        return {"token": jarvis.rotate_token()}

    @app.post("/dash/api/restart", dependencies=guard)
    async def restart() -> dict[str, str]:
        if jarvis.restart is None:
            raise HTTPException(status_code=409, detail="Restart Jarvis by hand this time.")
        asyncio.get_running_loop().call_later(0.5, jarvis.restart)
        return {"detail": "Restarting — back in a few seconds."}

    # -- your phone: how it reaches Jarvis ------------------------------------------
    @app.get("/dash/api/phone", dependencies=guard)
    async def phone() -> dict[str, Any]:
        return await asyncio.to_thread(phone_access, settings)

    @app.post("/dash/api/phone/tailscale", dependencies=guard)
    async def phone_tailscale(body: ShareIn) -> dict[str, Any]:
        # You pressed it, so nothing waits for approval — like a control.
        try:
            return (await jarvis.tailscale_terminal(body.share)).out()
        except JarvisError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/dash/api/stats", dependencies=guard)
    async def stats() -> dict[str, Any]:
        return machine_stats()

    def heard(said: str, submit: bool, client: str | None = None) -> dict[str, Any]:
        text = re.sub(r"^\s*(hey\s+)?jarvis[\s,:.!-]*", "", said, flags=re.I).strip()
        text = text or said
        task = jarvis.submit(text, client=(client or "")[:64] or None) if submit else None
        return {"text": said, "addressed": True, "command": text,
                "submitted": task is not None, "task_id": task.id if task else None,
                "confidence": None, "details": {}}

    @app.post("/dash/api/command", dependencies=guard)
    async def command(body: CommandIn) -> dict[str, Any]:
        return heard(body.text, body.submit, body.client)

    @app.post("/dash/api/listen", dependencies=guard)
    async def listen(audio: UploadFile, submit: Annotated[bool, Form()] = True,
                     client: Annotated[str | None, Form()] = None) -> dict[str, Any]:
        if jarvis.listen_provider not in {"wispr", "whisper"}:
            raise HTTPException(
                status_code=503,
                detail="Uploads are for the whisper and wispr options — set "
                       "JARVIS_LISTEN_PROVIDER (the browser option listens inside the page).")
        try:
            said = await jarvis.transcribe(await audio.read())
        except JarvisError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not said:
            return {"text": "", "addressed": False, "command": "", "submitted": False,
                    "task_id": None, "confidence": None, "details": {}}
        return heard(said, submit, client)

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

    # -- plain REST, for scripts, curl and anything else ----------------------
    @app.get("/system", dependencies=guard)
    async def system() -> dict[str, Any]:
        active = sum(t.status in {"pending", "running"} for t in jarvis.tasks.values())
        brain = settings.brain
        return {"brain": {"provider": brain, "name": BRAINS[brain]["name"],
                          "model": settings.model_for(brain),
                          "connected": bool(settings.key_for(brain))},
                "tools": sorted(jarvis.tools), "active_tasks": active,
                "total_tasks": len(jarvis.tasks), "workspace": str(settings.workspace)}

    @app.get("/tasks", dependencies=guard)
    async def list_tasks(limit: int = 100) -> list[dict[str, Any]]:
        return [t.out() for t in jarvis.recent_tasks(max(1, min(limit, 1000)))]

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

    @app.get("/dash/manifest.webmanifest", include_in_schema=False)
    async def app_manifest(token: str = "") -> Response:
        # What a phone installs: the static file, plus — when the page sends
        # the right token — that token in the start address. An iPhone keeps
        # a home-screen app's storage apart from Safari's, so without it the
        # app would open logged out; only iPhones and iPads ask for this.
        own = settings.frontend / "manifest.webmanifest"
        manifest = json.loads(own.read_text(encoding="utf-8")) if own.is_file() else {}
        if token and hmac.compare_digest(token.encode(), settings.api_token.encode()):
            start = str(manifest.get("start_url") or "./")
            manifest["start_url"] = f"{start}?{urlencode({'token': token})}"
        return Response(json.dumps(manifest), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})

    if has_frontend:
        # Windows can map .js to text/plain; browsers then refuse the modules.
        mimetypes.add_type("text/javascript", ".js")
        mimetypes.add_type("text/css", ".css")
        app.mount("/dash", FreshFiles(directory=settings.frontend, html=True), name="frontend")

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/dash/" if has_frontend else "/docs")

    return app


# ================================================================== main ===


async def serve(settings: Settings) -> bool:
    """Run until Ctrl-C (False) or the dashboard's Restart button (True)."""
    import uvicorn

    async with httpx.AsyncClient() as http:
        jarvis = Jarvis(settings, http)
        approvals = ("automatic (AUTO_APPROVE=true)" if settings.auto_approve
                     else "asked on the dashboard"
                     + (", read-only ones run straight away" if settings.allow_safe else ""))
        brain = BRAINS[settings.brain]["name"]
        _, status = await jarvis.check_brain(settings.brain)
        print(f"  Brain      {brain} {settings.model_for(settings.brain)}: {status}")
        print(f"  Workspace  {settings.workspace}")
        print(f"  Commands   {approvals}")
        voice = (f"{jarvis.voice_info()['provider']} / {jarvis.voice_info()['voice']}"
                 if jarvis.can_speak else "off (set OPENAI_API_KEY or pip install edge-tts)")
        print(f"  Voice      {voice}")
        if jarvis.listen_provider == "whisper":
            print(f"  Listening  loading Whisper {settings.whisper_model} ...", flush=True)
            await asyncio.to_thread(jarvis.load_whisper)
        print(f"  Listening  {jarvis.listen_provider or 'off (type instead)'}")
        host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
        url = f"http://{host}:{settings.port}/dash/?token={settings.api_token}"
        has_frontend = (settings.frontend / "index.html").is_file()
        print(f"\n  Dashboard  {url if has_frontend else '(no frontend folder — API only)'}")
        if has_frontend:
            tailscale = await asyncio.to_thread(tailscale_status, settings.port)
            print(f"  Phone      {phone_link(settings, tailscale)}")
        print(f"  API docs   http://{host}:{settings.port}/docs\n  Ctrl-C to stop.\n", flush=True)
        if settings.open_browser and has_frontend:
            asyncio.get_running_loop().call_later(1.5, lambda: webbrowser.open(url))
        config = uvicorn.Config(create_app(jarvis), host=settings.host, port=settings.port,
                                log_level="warning")
        server = uvicorn.Server(config)
        restarting = False

        def restart() -> None:
            nonlocal restarting
            restarting = True
            server.should_exit = True

        jarvis.restart = restart
        startup = asyncio.get_running_loop().create_task(jarvis.open_startup_terminals())
        try:
            await server.serve()
        finally:
            startup.cancel()
            jarvis.close_all_terminals()
        return restarting


def lan_address() -> str | None:
    """This PC's address on the local network, as a phone would reach it."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))  # no packet is sent; this only picks a route
        address: str = probe.getsockname()[0]
        return address
    return None


# -- your phone, from anywhere -------------------------------------------------
# Jarvis listens on this PC only. To reach it from a phone anywhere, the PC and
# the phone join the same Tailscale network (free, and private: nothing is
# opened to the internet), and `tailscale serve` gives Jarvis an https address
# on it. https is what lets a phone install the dashboard as an app and use
# its microphone.

PHONE_PAGE = "/dash/mothership.html"  # where the phone app opens
TAILSCALE_DOWNLOAD = "https://tailscale.com/download"


def tailscale_exe() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    for path in (Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Tailscale",
                      "tailscale.exe"),
                 Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")):
        if path.is_file():
            return str(path)
    return None


def run_tailscale(*args: str) -> Any:
    """What `tailscale <args> --json` says, or None if it can't be asked."""
    exe = tailscale_exe()
    if exe is None:
        return None
    try:
        done = subprocess.run([exe, *args, "--json"], capture_output=True, timeout=5,
                              stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    with contextlib.suppress(ValueError):
        return json.loads(done.stdout.decode("utf-8", "replace") or "null")
    return None


def served_url(config: Any, port: int) -> str:
    """The https address `tailscale serve` passes through to Jarvis on `port`."""
    webs = config.get("Web") if isinstance(config, dict) else None
    for host_port, web in (webs if isinstance(webs, dict) else {}).items():
        handler = ((web or {}).get("Handlers") or {}).get("/") or {}
        proxy = str(handler.get("Proxy") or "")
        target = urlparse(proxy if "://" in proxy else f"http://{proxy}")
        with contextlib.suppress(ValueError):
            if target.hostname in {"127.0.0.1", "localhost", "::1"} and target.port == port:
                host, _, https_port = str(host_port).rpartition(":")
                return f"https://{host}" + ("" if https_port == "443" else f":{https_port}")
    return ""


def tailscale_status(port: int) -> dict[str, Any]:
    """Is this PC on Tailscale, and is its https address passed to Jarvis?"""
    out: dict[str, Any] = {"installed": tailscale_exe() is not None, "state": "",
                           "signed_in": False, "name": "", "address": "", "https": False,
                           "url": "", "phones": [], "download": TAILSCALE_DOWNLOAD}
    if not out["installed"]:
        return out
    status = run_tailscale("status")
    if not isinstance(status, dict):
        out["state"] = "NotRunning"  # installed, but its service isn't answering
        return out
    me = status.get("Self") or {}
    out["state"] = str(status.get("BackendState") or "")
    out["signed_in"] = out["state"] == "Running"
    out["name"] = str(me.get("DNSName") or "").rstrip(".")
    out["address"] = next((str(ip) for ip in me.get("TailscaleIPs") or [] if "." in str(ip)), "")
    out["https"] = bool(status.get("CertDomains"))
    peers = status.get("Peer")
    if not isinstance(peers, dict):
        peers = {}
    out["phones"] = [{"name": str(peer.get("HostName") or ""), "os": str(peer.get("OS")),
                      "online": bool(peer.get("Online"))}
                     for peer in peers.values() if isinstance(peer, dict)
                     and str(peer.get("OS")).lower() in {"ios", "android"}]
    if out["signed_in"]:
        out["url"] = served_url(run_tailscale("serve", "status"), port)
    return out


def phone_access(settings: Settings) -> dict[str, Any]:
    """How a phone can reach Jarvis — best way first. No tokens in here: the
    page asking already has one, and adds it to the link it shows."""
    tailscale = tailscale_status(settings.port)
    wifi = settings.host in {"0.0.0.0", "::"}
    links = []
    if tailscale["url"]:
        links.append({"via": "tailscale", "url": tailscale["url"] + PHONE_PAGE,
                      "anywhere": True, "secure": True})
    if wifi and tailscale["signed_in"] and tailscale["address"]:
        links.append({"via": "tailscale-ip", "anywhere": True, "secure": False,
                      "url": f"http://{tailscale['address']}:{settings.port}{PHONE_PAGE}"})
    address = lan_address() if wifi else None
    if address:
        links.append({"via": "wifi", "url": f"http://{address}:{settings.port}{PHONE_PAGE}",
                      "anywhere": False, "secure": False})
    return {"port": settings.port, "wifi": wifi, "tailscale": tailscale, "links": links}


def tailscale_command(*args: str) -> str:
    """A terminal line running tailscale with `args`."""
    exe = "tailscale" if shutil.which("tailscale") else tailscale_exe() or "tailscale"
    if exe != "tailscale":
        exe = f"& {ps_quote(exe)}" if WINDOWS else shlex.quote(exe)
    return " ".join([exe, *args])


def phone_link(settings: Settings, tailscale: dict[str, Any] | None = None) -> str:
    """Where to open Jarvis on a phone — or how to make that possible."""
    page = f"{PHONE_PAGE}?token={settings.api_token}"
    if tailscale and tailscale.get("url"):
        return f"{tailscale['url']}{page}  (anywhere, through Tailscale)"
    if settings.host in {"0.0.0.0", "::"}:
        address = lan_address()
        if address:
            return f"http://{address}:{settings.port}{page}  (from your phone, same Wi-Fi)"
    return (f"http://127.0.0.1:{settings.port}{page}  — for your phone, see "
            "Mothership → Connections → Phone")


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
    if not settings.key_for(settings.brain):
        # Start anyway: the dashboard's Connections tab is where keys go now.
        print(f"  No {BRAINS[settings.brain]['name']} key yet. Add one in the dashboard "
              f"(Mothership → Connections) or in {env_file}.\n"
              f"  A free Gemini key: {KEY_URL}\n")
    if not settings.api_token:
        settings.api_token = secrets.token_urlsafe(32)
        save_env_value(env_file, "JARVIS_API_TOKEN", settings.api_token)
        print(f"  Made a dashboard token and saved it to {env_file}.")
    restart = False
    with contextlib.suppress(KeyboardInterrupt):
        restart = asyncio.run(serve(settings))
    if restart:
        # A fresh process picks up host and port; it reuses this console.
        print("\n  Restarting…\n", flush=True)
        child = dict(os.environ, JARVIS_OPEN_BROWSER="false")
        return subprocess.call([sys.executable, str(Path(__file__).resolve())], env=child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
