"""Authentication for the API server.

The phone client authenticates with a single shared bearer token. The token
is compared in constant time so an attacker cannot learn it byte-by-byte
from response timing. The expected token comes from the ``JARVIS_API_TOKEN``
secret and never appears in logs.
"""

from __future__ import annotations

import hmac

from pydantic import SecretStr

from jarvis.core.errors import JarvisError
from jarvis.logging import get_logger

_log = get_logger(__name__)


class AuthError(JarvisError):
    """Raised when a client presents missing or invalid credentials."""


class TokenAuthenticator:
    """Verifies a bearer token against the configured shared secret."""

    def __init__(self, expected_token: SecretStr) -> None:
        token = expected_token.get_secret_value()
        if not token:
            raise ValueError(
                "API token is empty; set JARVIS_API_TOKEN to a non-empty value."
            )
        self._expected = token.encode("utf-8")

    def verify(self, presented: str | None) -> None:
        """Raise :class:`AuthError` unless ``presented`` matches the secret."""
        if not presented:
            _log.warning("auth attempt with no token")
            raise AuthError("Missing authentication token.")
        if not hmac.compare_digest(presented.encode("utf-8"), self._expected):
            _log.warning("auth attempt with invalid token")
            raise AuthError("Invalid authentication token.")

    @staticmethod
    def extract_bearer(header_value: str | None) -> str | None:
        """Pull the token out of an ``Authorization: Bearer <token>`` header."""
        if not header_value:
            return None
        prefix = "Bearer "
        if header_value.startswith(prefix):
            return header_value[len(prefix) :].strip()
        return header_value.strip() or None
