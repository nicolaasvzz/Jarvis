"""Market research: news and posts about the bot's symbols, scored by an AI model.

    investment-bot research            # watch: collect, score, summarise, repeat
    investment-bot research --once     # one cycle

Sources (``sources.py``) write into one SQLite file (``store.py``): Alpaca's
news feed (Benzinga, free with the Alpaca key) and, when ``X_BEARER_TOKEN`` is
set, posts on X under a hard monthly spend cap. Each new item is scored once by
Gemini (``scorer.py``): which symbols, which way, how strongly, what kind of
event. ``signals.py`` turns the scores into a mood per symbol, counting one
story once however many outlets or posts repeat it, and flags the strong ones.
The result goes to ``research.json``, which ``jarvis_status`` shows on Jarvis.

Everything here reads and judges; nothing places orders.
"""
