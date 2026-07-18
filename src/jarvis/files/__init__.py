"""File Manager.

Read, create, rename, move, copy, and (with confirmation) delete files;
make and organise folders, compress and extract archives, and search for
files. The operations live in :mod:`jarvis.files.operations` as plain,
directly-testable functions; :func:`build_file_tools` wraps them as
:class:`~jarvis.tools.base.Tool` objects with the right risk categories
(``delete_files`` for deletion) so the Tool Manager gates them correctly.

Every path is confined to a configured *root* directory. Attempts to escape
it (``..``, absolute paths elsewhere, symlink tricks) are rejected before
any filesystem call — the model never gets to touch paths outside the
sandbox.
"""

from jarvis.files.operations import FileManager, PathNotAllowed
from jarvis.files.tools import build_file_tools

__all__ = ["FileManager", "PathNotAllowed", "build_file_tools"]
