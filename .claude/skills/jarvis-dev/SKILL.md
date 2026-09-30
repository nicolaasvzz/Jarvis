---
name: jarvis-dev
description: Working on Jarvis — the one-file Gemini assistant (backend/jarvis.py) and its dashboard (frontend/). Use when adding or changing a tool, touching the Gemini tool loop, the approval gate for terminal commands, the dashboard's API routes, or running the tests.
---

# Working on Jarvis

Read [CLAUDE.md](../../../CLAUDE.md) first — its invariants are the rules.

## Verify a change

```bash
cd backend
python -m pytest test_jarvis.py            # fake Gemini: no key, no network, no quota
ruff check --line-length 100 --select E,F,I,UP,B,SIM .
python -m mypy --strict --ignore-missing-imports jarvis.py
```

For a real end-to-end check without a Gemini key, run `jarvis.py` with
`jarvis.GEMINI_API` pointed at a small fake server (a FastAPI app answering
`POST /v1beta/models/{model}:generateContent`) — the real tools and the
dashboard then work normally. Never add a test that calls real Gemini.

## Add a tool

1. Write an `async def my_tool(self, task: Task, ...) -> dict[str, Any]`
   method on `Jarvis`. Return a dict; report failure as `{"error": "..."}`.
2. Add a `Tool(...)` to `_tools()`: a description written for the model, a
   `params(...)` schema, the dashboard room it happens in (`web`, `workshop`,
   `library`, `archives`, `observatory`), and a one-line `describe` for the
   activity feed.
3. If it changes anything on the machine, set `risky=True` **and** call
   `await self._approve(...)` before acting.
4. Mention it in `persona.md` so the model knows when to use it, and add a
   test in `test_jarvis.py` driving it through the fake Gemini.

## Change what the dashboard receives

The frontend depends on the shapes in `frontend/API.md` (snapshot, stream
frames, tasks, approvals). Change both sides together and keep API.md true.
