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

## Iteration 8: 01:50

**Idea:**
- Rollover hours (21–24 server time) have wide spreads and lost money in the swing breakdown, so skip them.
- Enter slightly deeper.
- Retry the rejected-sweep rule on the new champions.
- Let swing trades run longer (a few were cut by the time limit while still winning).

**Tried, swing** (champion: learn t +0.63, check +1.36):
- Skip 21–24h: +0.86 / +1.20. Rejected: worse on check.
- Enter 25% down: −0.02 / +1.52. Rejected: worse on learn.
- Rejected sweep: +0.50 / +1.23. Rejected.
- Hold 2x: **+0.66 / +1.43**. **Kept.** Only marginally better (it changes a few time exits), but it's in line with "let winners reach the target".

**Tried, intraday** (champion: −0.04 / +0.48):
- Min 3.5R: −0.37 / +0.68
- Skip 21–24h: −0.12 / +0.65
- Rejected sweep: −0.44 / +1.20
- Enter 25% down: −0.48 / −0.41

All four are better on check but none on learn, so all rejected. Intraday has only ~55 learn trades, too few to move with confidence.

**Champions now:**
- swing: min 3.5R, max 4R, half at 2R, break 1.2, swings 3/5/5, hold 2x
- intraday: unchanged

## Iteration 9: 02:20

**Idea:**
- Swing: bank the half earlier (1.5R), allow a 4.5R cap, or give the stop a little room.
- Intraday: does swing's whole recipe transfer to the shorter charts? If the edge is real and not fitted, the same rules should help on intraday too.

**Tried, swing** (champion: learn t +0.66, check +1.43):
- Max 4.5R: +0.59 / +1.72. Rejected: worse on learn.
- Half at 1.5R: **+0.81 / +1.64** (win 42% and 47%, avg +0.061R and +0.149R). **Kept.**
- Stop room 0.2: +0.53 / +1.25. Rejected.

**Tried, intraday** (champion: −0.04 / +0.48):
- Swing's full recipe (without the higher-chart filter and strong zones): −0.66 / **−2.09**. Clearly worse.
- Swing's recipe on top of intraday's: 0.00 / +0.08
- Only bigger zone and target swings: +0.14 / +0.14

All rejected: each helps learn but hurts check.

**What that says:** swing's recipe does **not** carry over to the 4h/1h/15m charts. That's a warning sign. Part of swing's improvement may be specific to the daily/4h/1h charts, or it may be fitted. The locked final test at 04:45 will be the judge.

**Champions now:**
- swing: min 3.5R, max 4R, half at 1.5R, break 1.2, swings 3/5/5, hold 2x
- intraday: unchanged

## Iteration 10: 02:50 (robustness check)

**Question:** is swing's gain a real plateau or a sharp, fitted peak? I nudged every kept setting a little either way and doubled the costs. A fitted result collapses when you nudge it.

**Results** (champion: learn t +0.81, check +1.64):

| Change | Learn t | Check t |
|---|---|---|
| Costs 2bp (doubled) | +0.42 | +1.53 |
| Min 3.25R | +0.37 | +1.46 |
| Min 3.75R | +0.55 | +1.24 |
| Break 1.0 | +2.05 | +1.33 |
| Break 1.4 | +0.35 | +2.53 |
| Half at 1.25R | +0.90 | +1.56 |
| Half at 1.75R | +0.46 | +1.59 |
| Target swings 4 | **+1.02** | **+1.66** |
| Zone swings 4 | +0.81 | +1.93 |

**Verdict:**
- **Every neighbour stays positive on both parts**, including doubled costs. That's a broad plateau, not a spike, which is the best evidence so far that something real is there.
- Target swings 4 is better on both, so **kept** (a small step).
- By symbol: gold, USDCAD, USDZAR, GBPJPY and EURUSD carry it, while EURGBP, AUDUSD and AUDJPY lose. No market is dropped, because picking markets by their results is just fitting.
- Shorts make it; longs are flat. Noted, but not acted on.

**Champion now:** swing with min 3.5R, max 4R, half at 1.5R, break 1.2, swings 3/5/4, hold 2x. Learn +0.079R (t 1.02), check +0.149R (t 1.66).

## Iteration 11: 03:20

**Idea:**
- Swing: small refinements near the plateau (zone swings 4, break 1.1, half at 1.25R).
- Intraday: borrow swing's two clearest lessons separately: bank earlier, and bigger target swings.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Zone swings 4: +0.84 / +2.11
- Break 1.1: +1.51 / +1.25
- Half at 1.25R: +1.15 / +1.59

Each wins on one part and loses on the other. Nothing kept.

**Tried, intraday** (champion: −0.04 / +0.48):
- Half at 1.5R: −0.32 / +0.08
- Target swings 4: +0.97 / +0.28
- Half at 1.5R and hold 2x: −0.04 / +0.13

Nothing kept.

**Kept:** nothing. Both profiles have hit their plateau: everything nearby trades learn against check. I'm stopping tuning here. One more iteration would only fit noise. The locked final test runs at the 04:45 iteration.

## Iterations 12–13: 03:50 and 04:20

Holding, as decided after iteration 11: no changes and no peeking at the locked part.

## FINAL: the locked test, 04:50

The last 20% of each profile's history, never looked at during the night, tested once:

| Profile | Final trades | Win | Avg R | t | Quarters (sum R) | Verdict |
|---|---|---|---|---|---|---|
| swing | 170 | 33.5% | **−0.106** | −0.99 | +0.9, −15.7, −10.7, +7.5 | not proven |
| intraday | 57 | 31.6% | −0.118 | −0.58 | +2.2, −4.0, −0.4, −4.5 | not proven |
| scalp (videos' rules, parked) | 1219 | 24.9% | −0.354 | −8.04 | all four negative | not proven |

**What it means:**
- Swing went from −0.09R (learn) and −0.08R (check) at the start to +0.08R and +0.15R after 11 iterations, and to **−0.11R on data it had never seen**.
- The improvements did not hold. The broad plateau in iteration 10 was real for the first 80% of history, but not beyond it.
- The most likely reasons: the market behaved differently in the last stretch, and 11 rounds of keeping "better on both parts" still fitted the learn and check data. With ~250–350 trades per part, a +0.1R edge is within noise.
- The intraday warning in iteration 9 (swing's recipe made intraday worse) was the tell.

**Conclusion:** after an honest night of hand improvement, the sweep setup from the videos has no proven edge on 21 MT5 markets. It must not trade real money. The demo trader only trades proven profiles, so it will trade nothing unless forward-testing is switched on deliberately.

---

# Part 2: Wed 18 Nov, 07:50 → 11:30

The user asked for more rounds. **What changes from last night:**
- **A stricter keep rule.** A change must be better on both learn and check, **and** its check average must not get worse in 2 or more of the 3 market groups (forex, metals, indices). Last night's gains didn't hold, so this asks that a change be broad, not carried by one corner.
- **The locked final 20% has been seen once** (04:50), so a second look at the end of this part is weaker evidence. A pass would justify a demo forward test, not real money.

## Round 12: 07:55

**Saw:** after the half comes off at 1.5R, the rest sits with its stop at the entry, so a trade that ran to +3R and reversed gives back everything on the second half. Separately, unusually volatile stretches seem to produce wild sweeps that don't respect zones.

**Ideas (two new engine rules):**
- **Trail to swings:** after the partial, the stop follows each new lower-chart swing low (or high for a short) once it's behind price.
- **Skip high volatility:** no new setups while ATR is more than N times its 200-candle average.

**Tried, swing** (champion: learn t +1.02, check +1.66, check by group: forex +0.051, metals +0.102, indices +0.433):
- Trail to swings: learn +0.91 (worse), check **+1.97** (forex +0.090, metals +0.225, indices +0.338). Rejected: worse on learn. Close, though; this is the first rule that helped forex and metals on check together.
- Skip vol > 2x: +1.01 / +1.61. Rejected.
- Skip vol > 1.5x: +0.85 / +1.46. Rejected.

**Tried, intraday** (champion: −0.04 / +0.48; forex +0.014, metals +0.839, indices −0.144):
- Trail to swings: +0.07 / −0.10. Rejected.
- Skip vol > 2x: no change (it almost never triggers).
- Skip vol > 1.5x: **+0.06 / +0.51**; forex +0.025, metals unchanged, indices −0.132. Better in 2 of 3 groups and none worse. **Kept.** It's small.

**Champions now:**
- swing: unchanged
- intraday: + skip vol > 1.5x

## Round 13: 08:17

**Saw:** trailing in round 12 helped swing on check but cost on learn. It probably trails too early and gets shaken out by normal pullbacks on the way to 4R.

**Ideas (two new engine rules):**
- **Trail only after N R:** start trailing once price has gone 2.5R or 3R our way.
- **Skip a weekday:** no new setups on Fridays (weekend gaps, position squaring) or on Mondays (gap noise).

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Trail after 2.5R: +0.96 / +1.65. Rejected.
- Trail after 3R: +1.15 / +1.56. Rejected.
- Skip Fridays: learn **+1.27**, check +1.64. A hair worse on check, so rejected.
- Skip Mondays: +0.65 / +1.64. Rejected.

**Tried, intraday** (champion: +0.06 / +0.51; forex +0.025, metals +0.839, indices −0.132):
- Skip Fridays: **+0.17 / +0.90** (avg +0.040R and +0.233R); forex +0.187, metals +1.230, indices −0.159. Better in 2 groups and worse in 1, so it passes. **Kept.** Caveats: only 45 and 68 trades, and weekday filters are a classic way to fit noise. Swing's check part was flat on the same idea, which is mildly reassuring but not strong.
- Skip Mondays: −0.24 / +0.20. Rejected.
- Trail after 3R: worse. Rejected.

**Champions now:**
- swing: unchanged
- intraday: + no Friday setups

## Round 14: 08:47

**Ideas (two new engine rules):**
- **Sweep inside the zone:** the sweep's wick must stay inside the demand or supply zone, a cleaner grab than one that runs through.
- **Enter on the break:** buy at the confirmation candle's close instead of waiting for the pullback. That means no missed winners, at a worse price.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Sweep inside zone: +0.67 / +1.18. Worse on both.
- Enter on the break: +0.14 / +0.49 (forex −0.071, metals −0.138). Much worse.
- Enter on the break with min 3R: +0.42 / +0.20. Worse.

**Tried, intraday** (champion: +0.17 / +0.90):
- Sweep inside zone: 0.00 / **+1.29**. Rejected: worse on learn.
- Enter on the break: −0.07 / −0.02. Worse.

**Kept:** nothing.

**Learned:** waiting for the pullback into the new zone (the videos' entry) is clearly better than entering on the break. The swing version gives up about 0.1R a trade without it. This is one of the videos' rules that the data backs.

## Round 15: 09:17

**Idea (a new engine rule): equal-level targets.** The videos stress that stops pile up above obvious highs. Double tops and double bottoms (two untaken levels close together) should be stronger magnets than a single swing, so aim only at levels with another untaken level within N higher-chart ATRs.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- 0.25 ATR: −0.70 / +0.47
- 0.5 ATR: −0.97 / +0.26
- 1 ATR: −0.57 / +0.59

All much worse. Skipping the nearest single level means aiming further away, which lets in more far-target trades (425–463 instead of 335). Those are exactly the ones that lose.

**Tried, intraday** (champion: +0.17 / +0.90):
- 0.25 ATR: +0.83 / +0.19
- 0.5 ATR: +1.13 / +0.02

Big gains on learn, collapses on check: the overfitting pattern again. Rejected.

**Kept:** nothing.

## Round 16: 10:17

(The 09:47 timer fired while the machine was busy. This is one round.)

**Idea (a new engine rule): second-touch entries.** Until now a zone got one visit. If the setup didn't confirm in time, the zone was thrown away. Many traders say a zone that held once and is revisited is still good. Allow 2 or 3 separate visits, with price leaving the zone in between.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- 2 visits: +0.44 / +0.82 (501 and 380 trades). Worse.
- 3 visits: +0.24 / +1.12. Worse.

**Tried, intraday** (champion: +0.17 / +0.90):
- 2 visits: +1.24 / +0.34
- 3 visits: +1.25 / −0.01

Better on learn, collapses on check. Rejected.

**Kept:** nothing.

**Learned:** fresh zones only (the videos' rule) is right. Second visits add trades, and they're worse trades.

## Round 17: 10:47

**Idea (a new engine rule): take the half at the opposing zone.** The second video takes 50% off at the first opposing demand or supply zone, not at a fixed R. If that zone is at least 1R away and before the target, half comes off there; otherwise at 1.5R as before.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Half at opposing zone: +1.08 / +1.12 (forex +0.010, metals +0.011). Worse on check. Rejected.
- Trail to swings, re-measured on the final champion as a control: +0.91 / +1.97, the same as round 12. Still worse on learn.

**Tried, intraday** (champion: +0.17 / +0.90):
- Half at opposing zone: −0.08 / +0.92. Rejected.

**Kept:** nothing.

**Part 2 so far:**
- 6 rounds and 10 new rules.
- Kept for intraday: skip high volatility, and no Friday setups.
- Swing: no change in part 2.
- Confirmed the videos are right on two points: wait for the pullback into the new zone, and use each zone only once.

## FINAL, part 2: the locked test again, 11:17

This is the **second look** at the locked 20%, so it's weaker evidence than the first. I already knew the first result. No decision this morning used that data or that result.

| Profile | Final trades | Win | Avg R | t | Quarters | vs first look |
|---|---|---|---|---|---|---|
| swing | 170 | 33.5% | −0.106 | −0.99 | +0.9, −15.7, −10.7, +7.5 | unchanged (no swing change this morning) |
| intraday | 45 | 28.9% | **−0.278** | −1.34 | −3.1, −6.5, +0.6, −3.5 | **worse** (was −0.118) |
| scalp (parked) | 1219 | 24.9% | −0.354 | −8.04 | all negative | unchanged |

**What it means:**
- The only two changes kept this morning (intraday: skip high volatility, no Friday setups) passed the stricter rule, including the market-group check. They still made intraday **worse** on unseen data: −0.12R became −0.28R.
- With ~45–70 intraday trades per part, small filters mostly move noise around. Even the group check can't save decisions made on samples that small.
- Swing didn't change, because none of the 10 new rules passed for it.

**Verdict after both parts (17 rounds, about 20 new rules):** the sweep setup has no edge that survives unseen data on these 21 markets. The method did its job twice: it refused to call anything proven. The bot stays parked, with no real money.

---

# Part 3: Wed 18 Nov, 12:00 → 16:00 (15-minute rounds)

**A new final test.** The locked 20% has been looked at twice, so a third look would mean little. The final test at 15:40 runs instead on **24 fresh markets never used for any decision**, over their whole history: 16 more forex crosses, 6 more indices, platinum and palladium (`data.FRESH`, `sweep-fresh`). Tuning stays on the original 21 markets' learn and check parts, with the same strict keep rule.

## Round 18: 12:00

**Idea (a new engine rule): a moving-average trend filter.** Buy only when the higher chart's last close is above its 50- or 200-candle average, and sell only below it. It's a far simpler trend read than swing structure, which hurt swing as a filter in round 2.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- 50 average: −0.42 / +1.05
- 200 average: +0.38 / +0.86

Both worse, with about half the trades.

**Tried, intraday** (champion: +0.17 / +0.90):
- 50 average: −0.30 / +0.16
- 200 average: −1.14 / +0.40

Both worse.

**Kept:** nothing. The setup does better taking trades against the bigger trend than filtering them out. That fits the first video: trade toward the obvious liquidity even when the higher chart is bearish.
