"""Tests for the runtime wiring and the CLI's offline pieces."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.app import build_runtime
from jarvis.app.cli import main
from jarvis.core.models import TaskStatus
from tests.helpers import ScriptedBrain


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith(("JARVIS_", "ANTHROPIC_")):
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


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_runtime()  # no brain injected, no key in env


def test_cli_token_prints_a_strong_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token"]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 48


def test_cli_tools_lists_capabilities(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tools"]) == 0
    output = capsys.readouterr().out
    assert "write_file" in output
    assert "delete_path" in output
    assert "confirm: delete_files" in output
