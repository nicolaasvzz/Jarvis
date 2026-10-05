## What and why

<!-- One or two sentences. Link the issue where you were given permission to work on this. -->

## Checklist

- [ ] `cd backend && python -m pytest test_jarvis.py` passes (if Jarvis changed)
- [ ] `cd tradebot && python -m pytest tests -q` passes (if the TradeBot changed)
- [ ] No API keys, tokens, `.env`, `backend/data/` or bot state files (`live_state.json`, `learned.json`, `lab.json`, `news.db`...) in the diff
- [ ] Nothing here enables live (real-money) trading by default
