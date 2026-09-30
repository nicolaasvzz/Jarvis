"""Authentication & permission policy.

Two responsibilities:

* :class:`~jarvis.security.permissions.PermissionPolicy` decides whether a
  tool action is ``SAFE`` to run immediately or must be gated behind user
  ``CONFIRM``, and manages the lifecycle of pending approval requests.
* :class:`~jarvis.security.auth.TokenAuthenticator` verifies the shared
  token the phone client presents to the API server, using a constant-time
  comparison.

Both are deliberately small and free of I/O so they are easy to test and
reason about — they are the trust boundary of the whole system.
"""

from jarvis.security.auth import AuthError, TokenAuthenticator
from jarvis.security.permissions import PermissionPolicy

__all__ = ["AuthError", "PermissionPolicy", "TokenAuthenticator"]
