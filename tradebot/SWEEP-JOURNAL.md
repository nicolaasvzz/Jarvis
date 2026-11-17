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

## Iteration 2: 21:25

**Saw:** most losses are fast stop-outs, so the entries are often wrong.

**Idea:** be stricter about what counts as a real setup: bigger swings for the break, stronger zones only, the higher chart's trend agreeing, entry deeper in the candle.

**Tried, swing:**
- Bigger lower-chart swings: learn better, check worse. Rejected.
- Strong zones only (2 ATR): learn +0.21R, check −0.13R. That's overfitting, so rejected.
- Higher-chart trend agreeing: worse on both. Rejected.
- Deeper entry: worse on both. Rejected.
- Bigger middle-chart swings (mtf_k 5): check −0.060→−0.025, but learn slightly worse (t −1.12→−1.15). Rejected by the rule, though it was close.

**Tried, intraday:**
- Bigger lower-chart swings (ltf_k 3): learn t −3.17→−2.71, check −4.56→−3.17. Better on both.
- Higher-chart trend agreeing: learn t −3.17→−2.19, check −4.56→−3.06. Better on both, and slightly more so. **Kept.**
- Next iteration tests the two together.

**Champions now:**
- swing: max target 4R
- intraday: higher chart agrees
- scalp: the videos' rules

Still all negative.

## Iteration 3: 21:50

**Saw:** fast stop-outs still dominate. The "confirmation" is often a weak candle that barely closes past the swing, and then price falls straight back.

**Idea (two new rules in the engine):**
- **Strong break:** the confirmation candle's body must be at least N ATRs, a real displacement like the videos' "strong move".
- **Rejected sweep:** the sweep only counts if its candle closes back past the level it ran (a wick through the stops, not a breakdown).

**Tried, swing** (champion: learn t −1.12, check −1.00):
- Rejected sweep: −1.03 / −0.16
- Break body 0.8: −0.29 / −0.82
- Break body 1.2: −0.43 / −0.36 (365 and 242 trades), the best worse-half. **Kept.**
- Bigger swings (ltf 3, mtf 5): −0.09 / −0.80

**Tried, intraday** (champion: −2.19 / −3.06):
- Bigger lower-chart swings: −1.64 / −1.66
- Rejected sweep: −1.75 / −2.35
- Break body 0.8: −0.71 / −1.08
- Break body 1.2: −0.81 / −0.16. **Kept.**

Every one of these beat the champion on both parts. The same rule (a strong break) wins on both profiles, which makes me trust it more than a one-off.

**Champions now:**
- swing: max target 4R, break body 1.2
- intraday: higher chart agrees, break body 1.2

Swing is close to zero now (−0.04R), still not positive.

**Next:** combine the winners (strong break + rejected sweep + bigger swings), and look at scalp.

## Iteration 4: 23:50

(The timer fired several times while the machine was busy. This is one iteration.)

**Saw:** with the strong-break rule, swing and intraday are near zero. The remaining stop-outs often come from tiny 2-candle "swings" being treated as structure.

**Idea:** combine the rules that won separately, and give the structure more weight with bigger swings.

**Tried, swing** (champion: learn t −0.43, check −0.36):
- Rejected sweep: −0.70 / −0.10. Rejected.
- Bigger swings (lower 3, middle 5): learn t −0.07, check **+0.65** (avg +0.067R, win 37%). **Kept.** That's the first positive check result.
- Break body 1.6: −0.66 / −0.32. Rejected.
- Half off at 1R: +0.08 / −0.22. Rejected (it's better on learn only).

**Tried, intraday** (champion: −0.81 / −0.16):
- Bigger lower-chart swings: learn t −0.44, check **+0.39**. **Kept.**
- Rejected sweep: −0.99 / +0.20. Rejected.
- Break body 1.6: −1.18 / −0.09. Rejected.
- Half off at 1R: −1.60 / −0.68. Rejected.

**Scalp:** its learn part has only 21–60 trades, because MT5 gives just ~16 months of 5-minute history. Changes can't be judged honestly on that, and the check part is clearly negative (−0.24R to −0.40R) whatever I try; costs eat 5bp stops. **Scalp is parked:** it gets no more tuning, and it won't trade.

**Champions now:**
- swing: max target 4R, break body 1.2, swing sizes 3/5
- intraday: higher chart agrees, break body 1.2, swing size 3

## Iteration 5: 00:20

**Saw, swing:**
- Targets are mostly capped at 4R now, and the 3R trades lose (−0.11R).
- Gold, USDCAD, USDZAR and GBPJPY carry it; AUDJPY and silver lose.
- Shorts are positive (+0.07R) and longs negative (−0.10R).

**Saw, intraday:**
- Far targets (5R+) lose, while 3–4R targets win.
- The 00–06 server hours lose and 06–15 win. A London-only filter overfit in iteration 1, so I'm leaving hours alone.

**Idea:** use bigger swings for the liquidity targets too, so the target is the obvious level everyone watches (the videos' "magnet"), not a minor wiggle. For intraday, stronger zones.

**Tried, swing** (champion: learn t −0.07, check +0.65):
- Target swings 5: learn t **+0.26**, check **+0.80** (avg +0.020R and +0.078R). **Kept.** Both parts are positive now.
- Min target 2.5R: −0.52 / +0.39. Rejected.
- Zones 1.6 ATR: −0.25 / +0.67. Rejected.
- Longs only: worse on both. Rejected.

**Tried, intraday** (champion: −0.44 / +0.39):
- Bigger zone swings: worse on both. Rejected.
- Bigger target swings: worse on both. Rejected.
- Min target 2.5R: −0.42 / +0.62. Better on both, but only just on learn.
- Zones 1.6 ATR: **−0.04 / +0.48** (avg −0.007R and +0.102R). **Kept**, as the best worse-half. Caveat: only 57 learn and 86 check trades.

**Champions now:**
- swing: max 4R, break 1.2, swings 3/5/5, positive on both parts but small (t +0.26 and +0.80)
- intraday: higher chart agrees, break 1.2, zones 1.6, swing size 3

## Iteration 6: 00:50

**Saw:** on swing, trades whose target is only 3R lose (−0.11R) and 4R trades win. A 3R target is often a minor level between price and the real liquidity.

**Idea:** only take swing trades whose liquidity target pays at least 3.5R, test letting winners run (no partial) or a later partial, and for intraday cap targets (5R+ lost there).

**Tried, swing** (champion: learn t +0.26, check +0.80):
- Min target 3.5R: **+0.63 / +1.36** (avg +0.053R and +0.141R, win 36–39%). **Kept.**
- No partial: +0.41 / −0.33. Rejected: banking half at 2R matters.
- Half off at 3R: +0.47 / +0.16. Rejected.
- Max 5R: +0.36 / +1.00. Better on both, but less than 3.5R.

**Tried, intraday** (champion: −0.04 / +0.48):
- Max 5R: +0.48 / +0.03. Better on learn, worse on check. Rejected.
- Max 4R: +0.14 / −0.34. Rejected.
- Min target 2.5R: −0.43 / +0.63. Rejected.
- No partial: −0.82 / +0.82. Rejected.

No intraday change.

**Champions now:**
- swing: min 3.5R, max 4R, half at 2R, break 1.2, swings 3/5/5. Learn +0.053R (t 0.63), check +0.141R (t 1.36). Getting there, but the final test needs t of 1.65 or more.
- intraday: unchanged

## Iteration 7: 01:20

**Idea:**
- **Buy cheap, sell dear** (a new rule, the SMC "premium/discount"): buy only below the middle of the higher chart's last swing range, and sell only above it.
- Also: more zones (weaker displacement), longer-lived zones, and a longer wait for the confirmation. All of these would mean more trades, which would lift t if the edge holds.

**Tried, swing** (champion: learn t +0.63, check +1.36):
- Buy cheap / sell dear: learn **+1.58** (avg +0.21R) but check +0.40. Rejected: it helps learn only.
- Zones 1.0 ATR: −0.42 / +0.44. Rejected.
- Zone life 300: +0.39 / +1.36. Rejected.
- Wait 2x: learn +1.59, check +0.70. Rejected: it helps learn only.

**Tried, intraday** (champion: −0.04 / +0.48):
- Buy cheap / sell dear: only 8 and 16 trades. Rejected.
- Zone life 300: no change.
- Wait 2x: +0.24 / −0.31. Rejected.

**Kept:** nothing.

**Note:** this is the first round where the ideas that looked best on learn all faded on check. That's the sign that the easy, real improvements are used up, and that pushing further would start fitting noise. The keep rule is doing its job.
