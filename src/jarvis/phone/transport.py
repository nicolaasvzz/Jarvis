"""A tiny async HTTP transport abstraction.

The Telegram bridge and the push channel only need "make an HTTP request,
get status + body back". Expressing that as a one-method protocol keeps the
real ``httpx`` dependency lazy (``phone`` extra) and lets every test drive
the bridge with a scripted fake — no network, no real bot.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from jarvis.core.errors import JarvisError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx


class TransportError(JarvisError):
    """A network-level failure talking to an external HTTP service."""


@dataclass
class HttpResponse:
    """A minimal HTTP response: status code plus body."""

    status_code: int

    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return _json.loads(self.text) if self.text else None


@runtime_checkable
class HttpTransport(Protocol):
    """Anything that can perform an async HTTP request."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse: ...


class HttpxTransport:
    """The real transport, backed by a shared ``httpx.AsyncClient``."""

    def __init__(self, timeout: float = 60.0) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "Phone connectivity needs httpx. "
                'Install with: pip install "jarvis-assistant[phone]"'
            ) from exc
        self._client: httpx.AsyncClient = httpx.AsyncClient(timeout=timeout)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        try:
            response = await self._client.request(
                method, url, params=params, json=json, content=data, headers=headers
            )
        except Exception as exc:  # noqa: BLE001 - normalise every transport failure
            raise TransportError(f"HTTP {method} {url} failed: {exc}") from exc
        return HttpResponse(status_code=response.status_code, text=response.text)

    async def aclose(self) -> None:
        await self._client.aclose()
