"""Tests for authentication and the permission policy."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from jarvis.config.schema import SecurityConfig
from jarvis.core.models import ApprovalDecision, RiskLevel
from jarvis.security import AuthError, PermissionPolicy, TokenAuthenticator


class TestTokenAuthenticator:
    def test_accepts_correct_token(self) -> None:
        auth = TokenAuthenticator(SecretStr("s3cret"))
        auth.verify("s3cret")  # no exception

    def test_rejects_wrong_and_missing_tokens(self) -> None:
        auth = TokenAuthenticator(SecretStr("s3cret"))
        with pytest.raises(AuthError):
            auth.verify("nope")
        with pytest.raises(AuthError):
            auth.verify(None)

    def test_empty_expected_token_is_configuration_error(self) -> None:
        with pytest.raises(ValueError):
            TokenAuthenticator(SecretStr(""))

    def test_extract_bearer(self) -> None:
        assert TokenAuthenticator.extract_bearer("Bearer abc") == "abc"
        assert TokenAuthenticator.extract_bearer("abc") == "abc"
        assert TokenAuthenticator.extract_bearer(None) is None


class TestPermissionPolicy:
    def _policy(self) -> PermissionPolicy:
        return PermissionPolicy(SecurityConfig(require_confirmation=["delete_files"]))

    def test_classifies_risk_by_category(self) -> None:
        policy = self._policy()
        assert policy.risk_for("delete_files") is RiskLevel.CONFIRM
        assert policy.risk_for("read_file") is RiskLevel.SAFE
        assert policy.risk_for(None) is RiskLevel.SAFE
        assert policy.requires_confirmation("delete_files") is True

    async def test_approval_lifecycle_allow(self) -> None:
        policy = self._policy()
        request = policy.create_request(
            task_id="task-1", tool="delete_path", arguments={}, reason="danger"
        )
        assert request in policy.pending()

        async def approve() -> None:
            await asyncio.sleep(0)
            policy.resolve(request.id, ApprovalDecision.ALLOW)

        decision, _ = await asyncio.gather(policy.wait_for(request.id), approve())
        assert decision is ApprovalDecision.ALLOW
        assert policy.pending() == []

    async def test_approval_lifecycle_deny(self) -> None:
        policy = self._policy()
        request = policy.create_request(
            task_id="t", tool="delete_path", arguments={}, reason="danger"
        )
        policy.resolve(request.id, ApprovalDecision.DENY)
        assert await policy.wait_for(request.id) is ApprovalDecision.DENY

    def test_resolving_unknown_or_twice_raises(self) -> None:
        policy = self._policy()
        with pytest.raises(KeyError):
            policy.resolve("appr-nope", ApprovalDecision.ALLOW)
        request = policy.create_request(
            task_id="t", tool="x", arguments={}, reason="r"
        )
        policy.resolve(request.id, ApprovalDecision.ALLOW)
        with pytest.raises(ValueError):
            policy.resolve(request.id, ApprovalDecision.ALLOW)
