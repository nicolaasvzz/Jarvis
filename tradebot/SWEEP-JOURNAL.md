# Sweep setup: improvement journal

Claude improves the sweep setup by hand, every 30 minutes, from Tue 17 Nov 2026
21:00 to Wed 18 Nov 05:00. Each entry says what the numbers showed, the idea it
led to, what was tried, and what was kept.

**The rules for keeping a change:**
- It must beat the current version on both the **learn** part (first 50% of
  history) and the **check** part (next 30%).
- The **final** 20% stays locked until the last iteration, which tests it once
  (`sweep-final`).
- Results are in R (1R = what a trade risks), after costs.
- Commands: `sweep-bench` (measure, with `--try` changes) and `sweep-final`.

Start (the videos' rules), learn / check, avg R and t:

| Profile | Learn | Check |
|---|---|---|
| swing | −0.092 (t −1.60) | −0.075 (t −1.15) |
| intraday | −0.210 (t −3.17) | −0.251 (t −4.56) |
| scalp | −0.050 (t −0.25, only 60 trades) | −0.361 (t −3.62) |

## Iteration 1: 21:00

**Saw:**
- 598 of 875 swing trades hit the stop, and trades closed within 6 hours average −0.97R.
- Trades still open after 48 hours average +1.03R.
- Far targets (5R+) do worse than 3–4R ones.

**Idea:** stops too tight (give them room), and targets too far (cap them).

**Tried:**
- Stop room 0.5 and 1.0 ATR: **worse** on both parts for swing and intraday. With wider stops fewer trades qualify for 3R, and the win rate doesn't rise.
- Max target 4R on swing: learn −0.092→−0.061, check −0.075→−0.060. **Kept.**
- Intraday, London hours only (08–15 server): learn +0.018 but check −0.223. That's overfitting, so **rejected**.

**Champions now:** swing has max target 4R. Intraday and scalp are unchanged.
