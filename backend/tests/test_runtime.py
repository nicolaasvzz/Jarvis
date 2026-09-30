"""Tests for the runtime wiring and the CLI's offline pieces."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from jarvis.app import build_runtime
from jarvis.app.cli import main, resolve_approval_interactively
from jarvis.brain import GeminiBrain
from jarvis.config.schema import SecurityConfig
from jarvis.core.errors import BrainError
from jarvis.core.models import ApprovalDecision, ApprovalRequest, TaskStatus
from jarvis.security import PermissionPolicy
from tests.helpers import ScriptedBrain


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith(("JARVIS_", "ANTHROPIC_", "GEMINI_", "GOOGLE_", "LLM_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    # Point all state at the temp dir via env overrides.
    monkeypatch.setenv("JARVIS_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_LOGGING__DIRECTORY", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_LOGGING__CONSOLE", "false")


async def test_runtime_wires_everything_with_injected_brain(tmp_path: Path) -> None:
    runtime = build_runtime(brain=ScriptedBrain([]))
    try:
        names = runtime.registry.names()
        # Universal tools are always present.
        assert "write_file" in names
        assert "remember_fact" in names
        # Workspace defaults under the data dir.
        assert str(tmp_path / "data") in str(runtime.files.root)
        # Log file exists and receives records.
        assert runtime.log_file.exists() or runtime.log_file.parent.exists()
    finally:
        await runtime.close()


async def test_runtime_end_to_end_task(tmp_path: Path) -> None:
    import json

    plan = json.dumps(
        {
            "goal": "Write hello",
            "steps": [
                {
                    "description": "Write hello.txt",
                    "tool": "write_file",
                    "arguments": {"path": "hello.txt", "content": "hi"},
                }
            ],
        }
    )
    runtime = build_runtime(brain=ScriptedBrain([plan, "Wrote hello.txt."]))
    try:
        task = await runtime.orchestrator.submit("write hello")
        task = await runtime.orchestrator.wait(task.id)
        assert task.status is TaskStatus.COMPLETED
        assert runtime.files.read_text("hello.txt") == "hi"
        # Memory persisted the task durably.
        assert runtime.memory.task_history()[0]["id"] == task.id
    finally:
        await runtime.close()


async def test_default_runtime_needs_only_a_gemini_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default install comes up on Gemini with nothing but its free key."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert "ANTHROPIC_API_KEY" not in os.environ
    runtime = build_runtime()  # no brain injected
    try:
        assert isinstance(runtime.brain, GeminiBrain)
        assert runtime.config.llm.provider == "gemini"
        assert runtime.config.llm.model == "gemini-3.8-flash"
        # The Planner and Orchestrator hold the very same Brain instance.
        assert runtime.planner._brain is runtime.brain
        assert runtime.orchestrator._brain is runtime.brain
    finally:
        await runtime.close()


def test_missing_gemini_key_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_runtime()  # no brain injected, no key in env


def test_missing_api_key_is_a_clear_error_when_anthropic_is_chosen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_runtime()  # no brain injected, no key in env


class TestInteractiveApproval:
    """``jarvis run`` asks at the terminal; with no terminal it must deny."""

    @staticmethod
    def _pending() -> tuple[PermissionPolicy, ApprovalRequest]:
        policy = PermissionPolicy(SecurityConfig())
        request = policy.create_request(
            task_id="t1", tool="delete_path", arguments={"path": "x"}, reason="r"
        )
        return policy, request

    async def test_no_terminal_denies_instead_of_hanging(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_stdin(_prompt: str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", no_stdin)
        policy, request = self._pending()
        waiter = asyncio.create_task(policy.wait_for(request.id))
        await resolve_approval_interactively(policy, request.id, request, auto_yes=False)
        # The waiting task is woken with a denial, not left hanging.
        assert await asyncio.wait_for(waiter, timeout=1) is ApprovalDecision.DENY

    async def test_yes_flag_allows_without_asking(self) -> None:
        policy, request = self._pending()
        await resolve_approval_interactively(policy, request.id, request, auto_yes=True)
        assert await policy.wait_for(request.id) is ApprovalDecision.ALLOW


def test_cli_token_prints_a_strong_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token"]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 48


class TestCliBrainCommand:
    """``jarvis brain`` is how you check the model connection from a terminal."""

    def test_reports_the_provider_and_confirms_the_model(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def describe(_self: GeminiBrain) -> dict[str, object]:
            return {"displayName": "Gemini 3.8 Flash", "inputTokenLimit": 1048576}

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(GeminiBrain, "describe_model", describe)
        assert main(["brain"]) == 0
        output = capsys.readouterr().out
        assert "provider: gemini" in output
        assert "model:    gemini-3.8-flash" in output
        assert "Gemini 3.8 Flash (1,048,576 token context)" in output
        assert "ready" in output

    def test_a_rejected_key_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def describe(_self: GeminiBrain) -> dict[str, object]:
            raise BrainError("Gemini rejected the API key")

        monkeypatch.setenv("GEMINI_API_KEY", "bad-key")
        monkeypatch.setattr(GeminiBrain, "describe_model", describe)
        assert main(["brain"]) == 1
        assert "NOT usable" in capsys.readouterr().err

    def test_gemini_without_a_key_explains_itself(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["brain"]) == 1
        err = capsys.readouterr().err
        assert "GEMINI_API_KEY" in err
        assert "aistudio.google.com" in err

    def test_anthropic_without_a_key_explains_itself(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        assert main(["brain"]) == 1
        assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_tools_lists_capabilities(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tools"]) == 0
    output = capsys.readouterr().out
    assert "write_file" in output
    assert "delete_path" in output
    assert "confirm: delete_files" in output
