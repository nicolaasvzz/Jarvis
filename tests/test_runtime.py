"""Tests for the runtime wiring and the CLI's offline pieces."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.app import build_runtime
from jarvis.app.cli import main
from jarvis.brain import OllamaBrain
from jarvis.core.errors import BrainError
from jarvis.core.models import TaskStatus
from tests.helpers import ScriptedBrain


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith(("JARVIS_", "ANTHROPIC_", "LLM_")):
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


async def test_default_runtime_uses_a_local_brain_with_no_api_key() -> None:
    """A default install must come up with no API key present at all."""
    assert "ANTHROPIC_API_KEY" not in os.environ
    runtime = build_runtime()  # no brain injected, no key in env
    try:
        assert isinstance(runtime.brain, OllamaBrain)
        assert runtime.config.llm.provider == "ollama"
        assert runtime.config.llm.model == "qwen3:8b"
        # The Planner and Orchestrator hold the very same Brain instance.
        assert runtime.planner._brain is runtime.brain
        assert runtime.orchestrator._brain is runtime.brain
    finally:
        await runtime.close()


def test_missing_api_key_is_a_clear_error_when_anthropic_is_chosen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_runtime()  # no brain injected, no key in env


def test_cli_token_prints_a_strong_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token"]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 48


class TestCliBrainCommand:
    """``jarvis brain`` is how you check the model connection from a terminal."""

    def test_reports_the_local_provider_and_confirms_the_model(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def models(_self: OllamaBrain) -> list[str]:
            return ["llama3.1:8b", "qwen3:8b"]

        monkeypatch.setattr(OllamaBrain, "list_models", models)
        assert main(["brain"]) == 0
        output = capsys.readouterr().out
        assert "provider: ollama" in output
        assert "model:    qwen3:8b" in output
        assert "endpoint: http://localhost:11434" in output
        assert "is available" in output

    def test_missing_model_exits_nonzero_with_the_pull_command(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def models(_self: OllamaBrain) -> list[str]:
            return ["llama3.1:8b"]

        monkeypatch.setattr(OllamaBrain, "list_models", models)
        assert main(["brain"]) == 1
        assert "ollama pull qwen3:8b" in capsys.readouterr().err

    def test_unreachable_server_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def models(_self: OllamaBrain) -> list[str]:
            raise BrainError("Could not reach Ollama at http://localhost:11434")

        monkeypatch.setattr(OllamaBrain, "list_models", models)
        assert main(["brain"]) == 1
        assert "NOT reachable" in capsys.readouterr().err

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
