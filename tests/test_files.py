"""Tests for the sandboxed File Manager and its tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config.schema import SecurityConfig
from jarvis.core.errors import ToolError
from jarvis.core.events import EventBus
from jarvis.core.models import ApprovalDecision
from jarvis.files import FileManager, PathNotAllowed, build_file_tools
from jarvis.security import PermissionPolicy
from jarvis.tools import ToolManager, ToolRegistry


@pytest.fixture
def manager(tmp_path: Path) -> FileManager:
    return FileManager(tmp_path / "root")


class TestSandbox:
    def test_write_then_read_roundtrip(self, manager: FileManager) -> None:
        manager.write_text("notes/todo.txt", "buy milk")
        assert manager.read_text("notes/todo.txt") == "buy milk"

    def test_escape_via_dotdot_is_blocked(self, manager: FileManager) -> None:
        with pytest.raises(PathNotAllowed):
            manager.write_text("../escape.txt", "nope")

    def test_absolute_path_outside_root_is_blocked(self, manager: FileManager) -> None:
        with pytest.raises(PathNotAllowed):
            manager.read_text("/etc/passwd")

    def test_delete_removes_file(self, manager: FileManager) -> None:
        manager.write_text("temp.txt", "x")
        manager.delete("temp.txt")
        with pytest.raises(ToolError):
            manager.read_text("temp.txt")

    def test_cannot_delete_root(self, manager: FileManager) -> None:
        with pytest.raises(PathNotAllowed):
            manager.delete(".")

    def test_list_dir_reports_types(self, manager: FileManager) -> None:
        manager.write_text("a.txt", "1")
        manager.make_dir("sub")
        entries = {e["name"]: e["type"] for e in manager.list_dir(".")}
        assert entries == {"a.txt": "file", "sub": "dir"}

    def test_move_and_copy(self, manager: FileManager) -> None:
        manager.write_text("a.txt", "hi")
        manager.copy("a.txt", "b.txt")
        manager.move("a.txt", "c.txt")
        assert manager.read_text("b.txt") == "hi"
        assert manager.read_text("c.txt") == "hi"
        with pytest.raises(ToolError):
            manager.read_text("a.txt")

    def test_search_glob(self, manager: FileManager) -> None:
        manager.write_text("photos/one.jpg", "")
        manager.write_text("photos/two.jpg", "")
        manager.write_text("photos/notes.txt", "")
        assert sorted(manager.search("*.jpg")) == [
            "photos/one.jpg",
            "photos/two.jpg",
        ]

    def test_compress_and_extract(self, manager: FileManager) -> None:
        manager.write_text("src/a.txt", "alpha")
        manager.write_text("src/b.txt", "beta")
        manager.compress(["src"], "backup.zip")
        extracted = manager.extract("backup.zip", "restored")
        assert manager.read_text("restored/src/a.txt") == "alpha"
        assert any("b.txt" in name for name in extracted)


class TestFileTools:
    async def test_delete_tool_is_gated_by_permission_policy(
        self, tmp_path: Path
    ) -> None:
        fm = FileManager(tmp_path / "root")
        fm.write_text("secret.txt", "data")

        registry = ToolRegistry()
        registry.register_all(build_file_tools(fm))
        policy = PermissionPolicy(SecurityConfig(require_confirmation=["delete_files"]))
        tool_manager = ToolManager(registry, policy, EventBus())

        # read_file is safe → runs immediately.
        read_result = await tool_manager.execute("read_file", {"path": "secret.txt"})
        assert read_result.ok and read_result.output == "data"

        # delete_path is dangerous → denying it prevents the deletion.
        import asyncio

        task = asyncio.create_task(
            tool_manager.execute("delete_path", {"path": "secret.txt"}, task_id="t")
        )
        for _ in range(100):
            await asyncio.sleep(0)
            if policy.pending():
                policy.resolve(policy.pending()[0].id, ApprovalDecision.DENY)
                break
        result = await task
        assert not result.ok
        assert fm.read_text("secret.txt") == "data"  # file survived denial
