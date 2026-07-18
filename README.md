# Jarvis

A personal AI desktop assistant that runs continuously on a Windows machine,
receives natural-language instructions from your phone, plans tasks before
executing them, and controls the computer safely — asking for confirmation
before dangerous actions.

> **Status: Feature 1 (Foundation) complete.** Configuration and Logging
> modules are implemented and tested; the remaining modules exist as
> documented placeholders. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
> for the module map and build order.

## Getting started

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[dev]"
```

Then create your local configuration (both files are gitignored):

```bash
# Non-secret settings (optional — everything has a default)
cp config/config.example.yaml config/config.yaml

# Secrets (API keys, tokens) — NEVER go in config.yaml
cp .env.example .env
```

## Running the tests

```bash
pytest
```

Lint and type-check:

```bash
ruff check .
mypy src
```

## Configuration model

Settings are typed and validated at startup (`jarvis.config`). Three layers,
highest precedence first:

1. **Environment variables** — `JARVIS_` prefix, `__` for nesting:
   `JARVIS_API__PORT=9000`, `JARVIS_LOGGING__LEVEL=DEBUG`
2. **YAML file** — first of: explicit path, `$JARVIS_CONFIG_FILE`,
   `./config/config.yaml`, per-user config dir
3. **Coded defaults** — `src/jarvis/config/schema.py`

Secrets are a separate, environment-only layer (`jarvis.config.secrets`):
they cannot be expressed in YAML at all, so they cannot be committed.

## Logging

`jarvis.logging` writes human-readable console output plus a rotating
JSON-lines file (default: the per-user log dir, e.g.
`%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl` on Windows). Each line is one JSON
object, so logs are searchable with standard tools:

```bash
# every action of one task
grep '"task-42"' jarvis.jsonl | jq .

# all errors
jq 'select(.level == "ERROR")' jarvis.jsonl
```

Bind task context once and every log record inside the block carries it:

```python
from jarvis.logging import get_logger, log_context

log = get_logger(__name__)
with log_context(task_id="task-42", tool="file_manager"):
    log.info("moving file", extra={"src": "a.txt", "dst": "b.txt"})
```

## Project layout

```
src/jarvis/          the application package (one sub-package per module)
├── config/          ✅ typed settings + secrets handling
├── logging/         ✅ structured JSON-lines logging
├── api/ brain/ browser/ desktop/ files/ memory/
│   notifications/ planner/ security/ tools/ vision/
│                    📦 documented placeholders, built one feature at a time
config/              example (committed) and local (gitignored) YAML config
docs/                architecture documentation
tests/               pytest suite
```
