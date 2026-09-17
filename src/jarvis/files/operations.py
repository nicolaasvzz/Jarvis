"""Sandboxed filesystem operations.

Every method resolves its path arguments against a fixed root and refuses
anything that escapes it, so a mistaken (or manipulated) path cannot read or
destroy files elsewhere on the machine. This is the security-critical core
of the File Manager; the tool wrappers add no path handling of their own.
"""

from __future__ import annotations

import fnmatch
import shutil
import zipfile
from pathlib import Path

from jarvis.core.errors import JarvisError, ToolError
from jarvis.logging import get_logger

_log = get_logger(__name__)


class PathNotAllowed(JarvisError):
    """Raised when a requested path resolves outside the sandbox root."""


class FileManager:
    """Filesystem operations confined to a single root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, raw: str) -> Path:
        """Resolve ``raw`` under the root, rejecting any escape attempt."""
        candidate = (self._root / raw).expanduser()
        # resolve() collapses ".." and follows symlinks so the final check
        # cannot be fooled by either.
        resolved = candidate.resolve()
        if resolved != self._root and self._root not in resolved.parents:
            raise PathNotAllowed(
                f"Path {raw!r} is outside the allowed directory."
            )
        return resolved

    def _rel(self, path: Path) -> str:
        # Forward slashes on every platform. These paths go back to the model
        # and come round again as tool arguments, so a Windows backslash would
        # both read as an escape and make the same file look like two.
        return path.relative_to(self._root).as_posix()

    def read_text(self, path: str, max_bytes: int = 1_000_000) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}", recoverable=False)
        data = target.read_bytes()
        if len(data) > max_bytes:
            raise ToolError(
                f"File {path} is {len(data)} bytes, over the {max_bytes} limit."
            )
        return data.decode("utf-8", errors="replace")

    def read_slice(self, path: str, start: int = 0, length: int = 6000) -> dict[str, object]:
        """Read one window of a text file, with the cursor to continue from.

        Archived research runs to hundreds of thousands of characters -
        far more than the model can hold at once. This hands back a slice
        that fits and says where the next one starts, so a long source can
        be worked through in passes instead of being truncated away.
        """
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}", recoverable=False)
        if length <= 0:
            raise ToolError("length must be positive.")
        text = target.read_text(encoding="utf-8", errors="replace")
        start = max(0, start)
        chunk = text[start : start + length]
        next_start = start + len(chunk)
        return {
            "path": self._rel(target),
            "text": chunk,
            "start": start,
            "next_start": next_start,
            "total_chars": len(text),
            "more": next_start < len(text),
        }

    def write_text(self, path: str, content: str) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _log.info("wrote file", extra={"path": self._rel(target)})
        return self._rel(target)

    def write_bytes(self, path: str, data: bytes) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _log.info("wrote file", extra={"path": self._rel(target), "bytes": len(data)})
        return self._rel(target)

    def list_dir(self, path: str = ".") -> list[dict[str, object]]:
        target = self._resolve(path)
        if not target.is_dir():
            raise ToolError(f"Not a directory: {path}", recoverable=False)
        entries: list[dict[str, object]] = []
        for child in sorted(target.iterdir()):
            entries.append(
                {
                    "name": child.name,
                    "path": self._rel(child),
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return entries

    def make_dir(self, path: str) -> str:
        target = self._resolve(path)
        target.mkdir(parents=True, exist_ok=True)
        return self._rel(target)

    def move(self, source: str, destination: str) -> str:
        src = self._resolve(source)
        dst = self._resolve(destination)
        if not src.exists():
            raise ToolError(f"Source does not exist: {source}", recoverable=False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _log.info("moved", extra={"from": source, "to": destination})
        return self._rel(dst)

    def copy(self, source: str, destination: str) -> str:
        src = self._resolve(source)
        dst = self._resolve(destination)
        if not src.exists():
            raise ToolError(f"Source does not exist: {source}", recoverable=False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return self._rel(dst)

    def delete(self, path: str) -> str:
        """Delete a file or directory. Gated behind confirmation by the tool."""
        target = self._resolve(path)
        if not target.exists():
            raise ToolError(f"Nothing to delete at: {path}", recoverable=False)
        if target == self._root:
            raise PathNotAllowed("Refusing to delete the root directory.")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        _log.info("deleted", extra={"path": path})
        return self._rel(target)

    def search(self, pattern: str, path: str = ".") -> list[str]:
        """Recursively find files whose name matches a glob ``pattern``."""
        base = self._resolve(path)
        matches: list[str] = []
        for candidate in base.rglob("*"):
            if fnmatch.fnmatch(candidate.name, pattern):
                matches.append(self._rel(candidate))
        return sorted(matches)

    def compress(self, sources: list[str], archive: str) -> str:
        archive_path = self._resolve(archive)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for source in sources:
                src = self._resolve(source)
                if src.is_dir():
                    for child in src.rglob("*"):
                        if child.is_file():
                            zf.write(child, child.relative_to(self._root))
                elif src.is_file():
                    zf.write(src, src.relative_to(self._root))
                else:
                    raise ToolError(f"Nothing to compress at: {source}")
        return self._rel(archive_path)

    def extract(self, archive: str, destination: str = ".") -> list[str]:
        archive_path = self._resolve(archive)
        dest = self._resolve(destination)
        if not zipfile.is_zipfile(archive_path):
            raise ToolError(f"Not a zip archive: {archive}", recoverable=False)
        extracted: list[str] = []
        with zipfile.ZipFile(archive_path) as zf:
            for member in zf.namelist():
                # Guard against zip-slip: each member must land under dest.
                out = (dest / member).resolve()
                if out != dest and dest not in out.parents:
                    raise PathNotAllowed(f"Archive member escapes target: {member}")
                zf.extract(member, dest)
                extracted.append(self._rel(out))
        return extracted
