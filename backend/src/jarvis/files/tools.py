"""File Manager operations exposed as Tools.

Each tool is a thin wrapper that names the operation for the LLM, describes
it, and tags the risk category. Only :func:`delete_path` carries the
``delete_files`` category, so it is the one operation the Tool Manager will
pause to confirm.
"""

from __future__ import annotations

from jarvis.files.operations import FileManager
from jarvis.tools.base import Tool


def build_file_tools(manager: FileManager) -> list[Tool]:
    """Create the full set of File Manager tools bound to ``manager``."""

    def read_file(path: str) -> str:
        """Read and return the text contents of a file."""
        return manager.read_text(path)

    def read_file_slice(path: str, start: int = 0, length: int = 6000) -> dict[str, object]:
        """Read part of a large text file, from character `start`.

        Returns the text plus "next_start" and "more". Use this instead of
        read_file for anything long (archived web pages, transcripts):
        call it repeatedly, passing the previous "next_start", until
        "more" is false.
        """
        return manager.read_slice(path, start, length)

    def write_file(path: str, content: str) -> str:
        """Create or overwrite a text file. Returns the written path."""
        return manager.write_text(path, content)

    def list_directory(path: str = ".") -> list[dict[str, object]]:
        """List the entries of a directory with their type and size."""
        return manager.list_dir(path)

    def make_directory(path: str) -> str:
        """Create a directory (and any missing parents)."""
        return manager.make_dir(path)

    def move_path(source: str, destination: str) -> str:
        """Move or rename a file or directory."""
        return manager.move(source, destination)

    def copy_path(source: str, destination: str) -> str:
        """Copy a file or directory."""
        return manager.copy(source, destination)

    def delete_path(path: str) -> str:
        """Delete a file or directory. Requires user confirmation."""
        return manager.delete(path)

    def search_files(pattern: str, path: str = ".") -> list[str]:
        """Recursively find files whose name matches a glob pattern."""
        return manager.search(pattern, path)

    def compress_files(sources: list[str], archive: str) -> str:
        """Compress files or folders into a .zip archive."""
        return manager.compress(sources, archive)

    def extract_archive(archive: str, destination: str = ".") -> list[str]:
        """Extract a .zip archive into a destination directory."""
        return manager.extract(archive, destination)

    return [
        Tool(name="read_file", description=read_file.__doc__ or "", func=read_file),
        Tool(
            name="read_file_slice",
            description=read_file_slice.__doc__ or "",
            func=read_file_slice,
        ),
        Tool(name="write_file", description=write_file.__doc__ or "", func=write_file),
        Tool(
            name="list_directory",
            description=list_directory.__doc__ or "",
            func=list_directory,
        ),
        Tool(
            name="make_directory",
            description=make_directory.__doc__ or "",
            func=make_directory,
        ),
        Tool(name="move_path", description=move_path.__doc__ or "", func=move_path),
        Tool(name="copy_path", description=copy_path.__doc__ or "", func=copy_path),
        Tool(
            name="delete_path",
            description=delete_path.__doc__ or "",
            func=delete_path,
            risk_category="delete_files",
        ),
        Tool(
            name="search_files",
            description=search_files.__doc__ or "",
            func=search_files,
        ),
        Tool(
            name="compress_files",
            description=compress_files.__doc__ or "",
            func=compress_files,
        ),
        Tool(
            name="extract_archive",
            description=extract_archive.__doc__ or "",
            func=extract_archive,
        ),
    ]
