"""PostToolUse hook: lint and type-check the Python file Claude just edited.

Reads the hook payload on stdin, picks out the edited file, and runs the same
checks the project uses by hand:

* ``ruff check`` on any Python file in the repo
* ``mypy --strict`` on ``backend/jarvis.py`` (the tests are not type-checked)

There is no pyproject.toml any more, so the settings are passed on the
command line — the same ones CLAUDE.md lists.

Clean file -> exit 0, silent. Problems -> print them and exit 2, which feeds
the output back to Claude so it fixes the file without re-reading it.

Anything unexpected (missing tool, bad payload) exits 0: a broken hook must
never block editing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def edited_file(payload: dict) -> Path | None:
    """Pull the edited file out of the hook payload, if there is one."""
    raw = payload.get("tool_response", {}).get("filePath") or payload.get(
        "tool_input", {}
    ).get("file_path")
    if not raw:
        return None
    path = Path(raw)
    if path.suffix != ".py" or not path.is_file():
        return None
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        return None  # outside this checkout
    return path


def run(tool: list[str], path: Path) -> str:
    """Run one checker and return its output, or "" if the file is clean."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", *tool, str(path)],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=110,
        )
    except (OSError, subprocess.SubprocessError):
        return ""  # checker missing or wedged — never block the edit
    if proc.returncode == 0:
        return ""
    return (proc.stdout + proc.stderr).strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    path = edited_file(payload)
    if path is None:
        return 0

    checks = [("ruff", ["ruff", "check", "--line-length", "100",
                        "--select", "E,F,I,UP,B,SIM"])]
    if path.resolve() == (BACKEND / "jarvis.py").resolve():
        checks.append(("mypy", ["mypy", "--strict", "--ignore-missing-imports"]))

    problems = [(name, out) for name, cmd in checks if (out := run(cmd, path))]
    if not problems:
        return 0

    rel = path.resolve().relative_to(ROOT)
    print(f"{rel} needs fixing before you move on:", file=sys.stderr)
    for name, out in problems:
        print(f"\n--- {name} ---\n{out}", file=sys.stderr)
    return 2  # blocking error: output is fed back to Claude


if __name__ == "__main__":
    sys.exit(main())
