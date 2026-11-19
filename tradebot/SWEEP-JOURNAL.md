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

## Round 19: 12:09 (what does each kept rule really do?)

**Idea:** take each of swing's kept rules out, one at a time (an ablation). If removing a rule changes nothing, it was probably noise. If removing it hurts a lot, it's carrying the version.

| Swing without… | Learn t | Check t | Check avg R |
|---|---|---|---|
| (champion) | +1.02 | +1.66 | +0.149 |
| **strong break** | +0.17 | **−0.27** | −0.015 |
| bigger swing size (ltf 3) | +0.82 | +0.92 | +0.073 |
| bigger zone swings (mtf 5) | +0.22 | +1.00 | +0.099 |
| target swings 4 | +0.34 | +1.40 | +0.134 |
| min 3.5R | +0.52 | +1.24 | +0.107 |
| 4R cap | +0.94 | +1.10 | +0.103 |
| half at 1.5R (back to 2R) | +0.98 | +1.47 | +0.156 |
| hold 2x | +0.99 | +1.73 | +0.157 |

**What it shows:**
- The **strong-break rule carries the whole version.** Without it, swing is back below zero.
- The structure swings and the 3.5R minimum each add a real slice.
- "Hold 2x" and "half at 1.5R" are about neutral. They were marginal keeps and are probably noise. Under the rule they stay, since removing them isn't better on both parts, but they shouldn't be counted as findings.

**Kept:** nothing changed.

## Round 20: 12:24 (intraday ablation)

**Idea:** the same take-each-rule-out test for intraday, where I trust the small filters least.

| Intraday without… | Learn t | Check t | Check avg R | Groups (forex / metals / indices) |
|---|---|---|---|---|
| (champion) | +0.17 | +0.90 | +0.233 | +0.187 / +1.230 / −0.159 |
| no-Fridays | +0.06 | +0.51 | +0.110 | |
| volatility filter | +0.16 | +1.03 | +0.259 | |
| both filters | −0.04 | +0.48 | +0.102 | |
| zones 1.6 | +0.43 | +0.12 | +0.019 | |
| **bigger swing size (back to 2)** | **+0.28** | **+0.96** | +0.217 | +0.204 / +1.004 / −0.081 |
| **strong break** | −1.08 | −1.14 | −0.150 | |
| higher chart agrees | −0.55 | −0.42 | −0.079 | |

**What it shows:**
- The strong break again carries the version: without it, intraday is clearly negative, the same as swing.
- The higher-chart-agrees filter is real for intraday.
- **Dropping the bigger swing size** is better on both parts and in 2 of 3 groups (metals down). **Kept.** The version is simpler and takes more trades (68 and 93).
- The volatility filter is about neutral (learn 0.17→0.16 without it). It stays by the rule, but it isn't a finding.

**Champion now:** intraday = higher chart agrees, strong break 1.2, zones 1.6, skip volatility over 1.5x, no Fridays.

## Round 21: 12:39

**Idea (a new engine rule): a real break, measured differently.** The strong-break rule looks at the candle's body. A break can also be measured by how far the close lands past the swing it breaks (N ATRs), which is closer to "price really left the level". Tried alone and paired with a looser body rule.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Close 0.3 ATR past: +0.78 / +0.91
- Close 0.6 ATR past: **+1.33** / +0.99
- Close 0.6 ATR past, body 0.8: **+1.57** / +1.17

All better on learn and worse on check. Rejected.

**Tried, intraday** (champion: +0.28 / +0.96):
- Close 0.3 ATR past: −0.20 / **+1.43**, all three groups better
- Close 0.6 ATR past: −0.14 / +1.28

Better on check, worse on learn. Rejected.

**Kept:** nothing. Each profile improves on a different half here, which is what noise looks like.

## Round 22: 12:54

**Idea (a new engine rule): a shallow sweep only.** A stop-run should poke just past the level and snap back. A sweep that runs 2+ ATRs past it may be a real breakdown, not a grab. So limit how deep the sweep may go.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- At most 0.5 ATR deep: −0.84 / +1.29 (only 44 and 35 trades)
- At most 1 ATR deep: −0.22 / +0.76
- At most 2 ATR deep: +0.53 / +1.08

**Tried, intraday** (champion: +0.28 / +0.96):
- At most 0.5 ATR deep: +0.13 / +1.50 (8 and 13 trades)
- At most 1 ATR deep: +0.32 / +0.54
- At most 2 ATR deep: −0.98 / +1.04

**Kept:** nothing.

**Learned:** most of the setups that work come from **deep** sweeps. Half of swing's trades sweep more than 2 ATRs past the level. The idea of a neat little wick is wrong for this setup: the big stop-runs are the ones that reverse.

## Round 23: 13:09

**Idea (a new engine rule, following round 22): require a deep sweep.** If deep stop-runs are the ones that reverse, demand that the sweep runs at least N ATRs past the level.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- At least 1 ATR: +0.86 / +1.54 (forex +0.166, but metals −0.162)
- At least 2 ATR: +0.53 / +0.93
- At least 3 ATR: +1.19 / −0.18

**Tried, intraday** (champion: +0.28 / +0.96):
- At least 1 ATR: +0.05 / +0.60
- At least 2 ATR: +1.12 / +0.47

**Kept:** nothing.

**Learned:** deep sweeps aren't better on their own. Shallow-only (round 22) and deep-only both lose to taking every sweep. Sweep depth isn't where the edge is. The strong break after the sweep is.

## Round 24: 13:24 (finished 14:23: the machine was asleep or stalled for about an hour)

**Idea (a new engine rule): volume on the break.** A real break should come with a burst of activity. The confirmation candle's tick volume must be at least N times its 20-candle average. (MT5 forex volume is tick count, not traded size, but it still rises with activity.)

**Tried, swing** (champion: learn t +1.02, check +1.66):
- 1.2x volume: +0.92 / +0.71
- 1.5x volume: +0.23 / +1.08
- 2x volume: +0.96 / +1.10

All worse on both parts.

**Tried, intraday** (champion: +0.28 / +0.96):
- 1.2x volume: +0.49 / −0.52
- 1.5x volume: +0.92 / −0.70

Better on learn, collapses on check.

**Kept:** nothing. Tick volume adds nothing beyond the strong-break body rule.

## Round 25: 14:26

**Idea (a new engine rule): a fast turn.** A real stop-run reverses quickly. If the break comes long after the sweep's extreme, the sweep was more likely a slow grind lower. So the break must come within N lower-chart candles of the extreme.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Within 6: +1.02 / +1.20 (avg up, but only 114 and 92 trades)
- Within 12: +0.50 / +1.39
- Within 24: +0.72 / **+1.85**

None better on both. Rejected.

**Tried, intraday** (champion: +0.28 / +0.96):
- Within 8: +1.08 / +0.62
- Within 16 (4 hours): **+0.64 / +1.23** (avg +0.166R and +0.319R); forex +0.432, metals +1.230, indices −0.344. Better in 2 groups and worse in 1. **Kept.** Note: indices got clearly worse.

**Champion now:** intraday = higher chart agrees, strong break 1.2, zones 1.6, skip volatility over 1.5x, no Fridays, turn within 16 candles.

## Round 26: 14:39

**Idea (a new engine rule): the break candle closes near its extreme.** A strong break closes near its high (for a long) rather than leaving a long upper wick, so require the close to sit in the top 30% or top 20% of the candle's range.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Top 30%: +0.85 / +1.25
- Top 20%: 0.00 / +0.66

**Tried, intraday** (champion: +0.64 / +1.23):
- Top 30%: +0.21 / +0.86
- Top 20%: +0.31 / +0.84

**Kept:** nothing. All worse on both parts. A big body is what matters (round 3). Where the candle closes within its range adds nothing.

## Round 27: 14:54

**Ideas:**
- Swing: a looser fast-turn rule (36 or 48 candles), since 24 helped check but not learn in round 25.
- Intraday: now that the fast-turn rule is in, are the Friday and volatility filters still needed? A simpler version would be preferable.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Within 36: +0.89 / +1.76. Rejected.
- Within 48: identical to the champion (no setup takes longer).

**Tried, intraday** (champion: +0.64 / +1.23):

| Intraday without… | Learn t | Check t |
|---|---|---|
| no-Fridays | +0.16 | +0.86 |
| volatility filter | +0.56 | +1.29 |
| both filters | +0.01 | +0.76 |

The Friday filter still holds its weight on this data. The volatility filter is close to neutral (slightly better on check, slightly worse on learn), so under the rule it stays.

**Kept:** nothing.

## Round 28: 15:09

**Idea (a new engine rule, from the second video): a higher-chart sweep too.** In the video the trade came right after the all-time high was taken, a stop-run on the big chart. So a long needs a higher-chart low (a daily low, for swing) to have been taken within the last N higher-chart candles. A short needs a high.

**Tried, swing** (champion: learn t +1.02, check +1.66):
- Within 3 candles: 1 and 2 trades. Far too strict.
- Within 10 daily candles: learn **+1.28**, check +1.29.
  - Average per trade nearly doubled: **+0.224R and +0.287R**, win 48% and 51%, positive in all three groups (forex +0.219, metals +0.308, indices +0.391).
  - But only 71 and 45 trades, so check t is lower than the champion's. Rejected by the rule.

**Tried, intraday** (champion: +0.64 / +1.23): within 3 gave 0 and 1 trades, and within 10 gave 11 and 10 trades. Too few to judge.

**Kept:** nothing. Swing with a recent daily sweep is the highest-quality subset found all day (about +0.25R a trade, positive everywhere). It's also too rare to prove anything: about 1 trade a month across 21 markets. Worth remembering if more markets are ever added.

## Round 29: 15:24

**Idea:** round 28's higher-chart sweep was the best quality but too rare at 10 days. Widen the window so a recent daily stop-run counts within 20 or 40 daily candles (about 1 or 2 months).

**Tried, swing** (champion: learn t +1.02, check +1.66; forex +0.051, metals +0.102, indices +0.433):
- Within 20: learn **+1.34**, check **+2.18**; avg **+0.154R and +0.287R**, win 44% and 54%; forex +0.214, metals +0.505, indices +0.321. Better on both parts, and better in 2 groups with 1 worse. **Kept.**
- Within 40: +1.36 / +1.67, but forex and indices both worse. Rejected.

**Caveat:** I picked 20 out of four windows tried (3, 10, 20, 40). That's a small search, but a search. The fresh-market test at 15:40 is the judge.

**Champion now:** swing = min 3.5R, max 4R, half at 1.5R, strong break 1.2, swings 3/5/4, hold 2x, and a daily low (or high) taken within the last 20 days.

## FINAL, part 3: 24 fresh markets, 15:39

The champions tested once on 24 markets never used for any decision (16 forex crosses, 6 indices, platinum and palladium), over their whole history:

| Profile | Trades | Win | Avg R | t | Quarters (sum R) | By group | Verdict |
|---|---|---|---|---|---|---|---|
| **swing** | 325 | 43.1% | **+0.084** | +1.05 | **+6.8, +3.8, +2.4, +14.4** | forex +0.096, metals −0.078, indices +0.086 | not proven (t under 1.65; PF 1.14, needs 1.2) |
| intraday | 153 | 20.3% | −0.463 | −3.77 | +3.6, −4.2, −32.1, −38.2 | all negative | not proven |

**What it means:**
- **Swing is positive on markets it has never seen, in all four quarters.** That's the first out-of-sample result of the whole project that points the right way. It's still too weak to call proven: +0.08R a trade with t 1.05 could be luck, and the profit factor is 1.14.
- About **+0.08R a trade at 1% risk is roughly +0.1% per trade** before any slippage beyond the cost model. It's real money only at scale, and only if it holds.
- **Intraday failed badly** on fresh markets (−0.46R). Its filters (Fridays, volatility, fast turn) were fitted to the original 21 markets. The intraday version should be dropped.

**Recommendation:**
- Forward-test **swing only** on the MT5 demo (`sweep-trade --profiles swing --also-unproven`). That's about 2–3 trades a week across the markets.
- Revisit after 3 months, or about 30 trades. No real money until the live demo results match.

## Correction, 18 Nov 19:50: how often swing trades

Part 3 said "about 2–3 trades a week". That was wrong. Measured over the full history:
- fresh 24 markets: 325 trades in 15.9 years, so **20 a year**; tuned 21 markets: 22 a year.
- Live on the 21 markets, that's **about 2 trades a month**. A 30-trade demo check takes about 15 months, not 3.

## Forward test started, 18 Nov 19:42

Swing only, on the MT5 demo (MetaQuotes-Demo, $100), 1% risk a trade, at most 3 open, halts at a 10% drop. It trades the round-29 version (PR #22 fixed demo mode, which had been trading the untuned defaults). The swing version is now **frozen**: no further changes.

# Part 4: the Fairtree bar (from 18 Nov, 19:50)

**Goal (from Nicolaas):** keep teaching the bot until it's stronger than the best fund that manages money for you.

**The bar.** Fairtree Equity Prescient is the strongest South African-managed fund over 10 years: about 12–18% a year after fees (11.7% in the 10 years to Dec 2024; about 17.7% in an earlier decade window). A version beats it only if, **on data it was never tuned on**, net of costs:
1. it returns **18%+ a year**, at a risk size where
2. the **worst drop is 25% or less** (an equity fund's drop in a bad year), and
3. it passes the luck bar: t at least 1.65, 3 of 4 quarters positive, profit factor at least 1.2, 30+ trades.

**Where swing stands** (compounded, trades in exit order):

| Markets | Trades/yr | R/yr | 1% risk | 2% risk | 3% risk |
|---|---|---|---|---|---|
| fresh 24 (never tuned) | 20 | +1.7 | +1.5%/yr, worst drop 16% | +2.7%, 30% | +3.4%, 42% |
| tuned 21 | 22 | +3.6 | +3.4%, 14% | +6.5%, 26% | +9.2%, 36% |

The gap is about 10x. Rule tweaks on swing can't close it, and swing is frozen anyway. Part 4 looks for **new kinds of trade**: different families that trade more often or earn more a trade, and portfolios that combine them.

**Rules for part 4:**
- Research runs only in `tradebot/research_lab/` (its own champions file; candles shared read-only from `data_cache`). It never touches the live bot, `sweep_champions.json` in the main folder, or the MT5 terminal: cached candles only.
- Each new family is developed on the **first 70% of time on all 45 markets** (learn = first 40%, check = next 30%). The **last 30% of time on all 45 markets** is locked for that family, and gets **one** look, when it's ready.
- A change is kept only if it's better on both learn and check, and doesn't get worse in 2 of the 3 groups.
- Every round is written here, including what failed.

## Part 4 round 1: 19:57

**Idea:** daily trend following across all 45 markets (`sweep/trend.py`). Buy a close above the last N days' high and short a close below the last N days' low. Hold until a close through the last M days' low (or high), with a hard stop 3 ATR away. One position per market. Decisions are made on the close and filled at the next open. Costs: the candle's real spread or 1bp, whichever is bigger, per side; a quarter of that again on stops; **2% a year financing** while holding. Every trade is marked to market daily, so the portfolio's worst drop is the real one. Tests: no-peek, each daily path adds up to the trade's result, costs only hurt, risk sized to the drop cap.

**Split:** calendar time from 2000-01-01. Learn runs to mid-2010, check to late 2018, and late 2018 to now is locked. Daily data for indices only starts in 2012 (the rest in 2019–22), so learn has **no indices**.

**Tried** (risk set so the worst drop is 25%):

| Version | Part | Trades/yr | Avg R | t | PF | Yearly at 25% drop |
|---|---|---|---|---|---|---|
| 50/20 (classic) | learn | 88 | −0.132 | −2.20 | 0.77 | −2.0% |
| | check | 142 | −0.169 | −3.49 | 0.69 | −3.4% |
| 20/10 (fast) | learn | 171 | −0.099 | −3.41 | 0.76 | −2.2% |
| | check | 270 | −0.098 | −3.30 | 0.77 | −3.3% |
| 100/50 (slow) | learn | 47 | −0.178 | −2.05 | 0.75 | −1.5% |
| | check | 77 | −0.256 | −2.65 | 0.67 | −3.3% |

**Where the loss comes from** (50/20, avg R, learn / check):
- as tested: −0.132 / −0.169
- without spread: −0.097 / −0.161
- also without financing: +0.016 / −0.055
- with no costs at all: +0.024 / −0.047

**Verdict: rejected.** Even with zero costs there is no edge here. Financing alone costs about 0.11R a trade, because trades last weeks. Trend following made its name on diversified futures (bonds, commodities, rates), and those are almost absent from this broker's list. On currency pairs and a few metals since 2000, breakouts don't pay.

**What it teaches for the next rounds:**
1. Any multi-week hold has to earn more than about 2% a year of its position size just to cover financing. That rules out slow, low-edge ideas.
2. A 12-month time-series momentum version (monthly rebalanced, volatility-scaled) is the last trend idea worth one round. If it's flat too, the trend family is closed.
3. Index ideas (mean reversion) can't be judged on learn, since indices have no data before 2012. They'll need their own split: learn 2012–2016, check 2016–2019, locked 2019 onward.

## Part 4 round 2: 20:12

**Idea:** the last trend idea worth a round, **time-series momentum** (`sweep/tsmom.py`). At each month's last close, every market is held long if it rose over the lookback and short if it fell. Each market is sized to 10% yearly volatility, and the portfolio is the average over the markets with data. A position decided on a close earns only from the next close on, which is a full day late and slightly pessimistic. Costs: the spread or 1bp on every change in position, plus 2% a year financing on what's held. Tests: positions change only at month ends and follow the trend, no-peek (the daily returns are identical when the future is cut off), costs only hurt, and market-months add up.

**Tried** (scaled so the worst drop is 25%; "avg R" is per market-month in units of its monthly risk):

| Version | Part | Avg R | PF | t (daily) | Yearly at 25% drop | Groups (forex / metals / indices) |
|---|---|---|---|---|---|---|
| 12 months | learn | −0.011 | 0.95 | −0.45 | −1.2% | −0.022 / +0.037 / none |
| | check | −0.056 | 0.83 | −1.01 | −2.7% | −0.100 / +0.008 / +0.030 |
| 6 months | learn | −0.035 | 0.84 | −1.57 | −2.4% | |
| | check | −0.045 | 0.86 | −0.70 | −2.2% | −0.088 / −0.011 / +0.048 |
| 3 months | learn | −0.031 | 0.86 | −1.68 | −2.0% | |
| | check | −0.063 | 0.81 | −1.41 | −3.0% | |
| 12 months, no costs (diagnostic) | learn | +0.020 | 1.11 | +1.06 | +3.5% | |
| | check | −0.009 | 0.97 | +0.45 | +1.2% | |

**Verdict: rejected, and the trend family is closed.** Even without costs, 12-month momentum makes 1–4% a year at a 25% drop: far below 18%, and negative after costs.

**The pattern from both trend rounds:** **forex is the drag** (−0.02 to −0.10), while **metals and indices lean positive** (+0.01 to +0.05). Thirty-two of the 45 markets are forex pairs, and they swamp the rest. Currency pairs since 2000 have been range-bound and mean-reverting more than trending.

**Next:** stop fighting forex. Round 3 tries the one effect equity indices are known for: they **drift up over time and recover from dips**. That means buying pullbacks in index CFDs (long only), with its own split, because index data starts in 2012: learn 2012–2016, check 2016–2019, locked 2019 onward. Few markets have history before 2019, so it'll be thin. If it shows promise, metals get the same test.

## Part 4 round 3: 20:27

**Idea:** forex was the drag in rounds 1–2, so this round leaves it out. **Buy the dip in stock indices, long only** (`sweep/dip.py`), on 12 indices. Buy at the next open when the close is above its 200-day average and the 2-day RSI is under 10. Sell at the next open after a close above the 5-day average, or after 10 days. The hard stop is 3 ATR (= 1R). Costs and financing are as in round 1, with a daily mark-to-market. Tests: RSI uses only the past, long only, paths add up, no-peek.

**Its own split** (index data starts in 2012): learn 2012-08-06 to 2018-04-23, check to 2022-08-05, locked 2022-08-05 to now. Indices only, so the forex/metals group rule doesn't apply.

**Tried** (risk set so the worst drop is 25%):

| Version | Part | Trades/yr | Win | Avg R | t | PF | Yearly at 25% drop | Quarters |
|---|---|---|---|---|---|---|---|---|
| RSI2<10 over MA200 | learn | 33 | 75% | **+0.095** | **+2.21** | 1.79 | **+16.0%** (5.9% risk) | +5.3, +4.9, +1.4, +6.2 |
| | check | 53 | 70% | −0.001 | −0.01 | 1.00 | −1.0% | −0.7, −10.1, +10.0, +0.7 |
| no trend filter | learn | 51 | 71% | +0.068 | +1.79 | 1.46 | +10.5% | |
| | check | 126 | 67% | +0.007 | +0.17 | 1.04 | −0.2% | |
| deeper dip (RSI2<5) | learn | 15 | 72% | +0.076 | +1.24 | 1.58 | +4.9% | |
| | check | 30 | 72% | +0.022 | +0.24 | 1.10 | +0.8% | |

**Why check is flat** (check data only):
- **Not the new markets.** The four indices that made learn strong are about zero on check too (US500 +0.03, US30 +0.02, US2000 −0.06, DE40 −0.02).
- **It's the period.** 2020 (the Covid crash) cost −6.2R and 2022 (the bear market) −2.5R, while 2021 gave +10.2R.
- The 36 stops cost −36.5R; everything else made +36R.

**Verdict: rejected as a candidate.** It's the best learn result of part 4: +16% a year at a 25% drop, 2012–18, in a calm bull market. But it earned nothing in 2018–22, and the keep rule needs both. It's a fair-weather strategy. That matches what's known about short-term index dip-buying: it worked for decades and has been crowded since about 2015.

**Next:** an honest reference point before more ideas. What does simply **holding the indices** do (long when above the 200-day average, sized to a volatility target), scaled to the same 25% drop? That's what a fund like Fairtree mostly earns: equity market returns. If plain held beta can't reach 18% a year at a 25% drop, no index timing rule will.

## Part 4 round 4: 20:42

**Idea:** a reality check, not a trick. What does **simply holding** the 12 stock indices earn, sized to the same 25% worst drop? That's mostly what an equity fund like Fairtree earns. If held market exposure can't get near 18% a year here, no timing rule on indices will. (`tsmom.py` gained modes `hold` and `above_ma`: long-only, decided monthly, volatility-sized per index, same costs and financing. The test covers long-only and no-peek.) The split is round 3's index split.

| Version | Part | Avg R (per index-month) | PF | t (daily) | Yearly at 25% drop | Quarters |
|---|---|---|---|---|---|---|
| hold all 12 | learn 2012–18 | +0.078 | 1.99 | +1.91 | **+14.6%** | +29.0, +7.1, −6.4, +33.9 |
| | check 2018–22 | −0.028 | 0.90 | −0.16 | **−1.6%** | −1.1, −35.9, +45.3, −25.8 |
| hold only above MA200 | learn | +0.048 | 1.86 | +1.45 | +10.7% | |
| | check | −0.013 | 0.91 | −0.16 | −1.7% | |
| hold, no financing (diagnostic) | learn | +0.093 | 2.27 | +2.28 | +19.4% | |
| | check | −0.005 | 0.98 | +0.08 | −0.4% | |

**What it means:**
- 2018–22 was a bad window for equal-weighted world indices in **price** terms: Hong Kong and Spain fell, Germany, the UK and the euro area were roughly flat, and the Covid crash forced a small size to keep the drop under 25%. The broker's index CFDs carry **no dividends**, and financing costs 2% a year. A fund gets the dividends and pays no financing.
- Holding is the benchmark, not a candidate. Its check result (−1.6%) shows **18% a year at a 25% drop was not available from index exposure on this broker in 2018–22.** Rounds 1–3 failing check is partly this window, not only the rules.
- **An important note on the bar.** Fairtree's 12–18% a year is in **rand**. Over 2014–24 the rand lost about 5% a year against the dollar, and this bot's account is in dollars. In dollar terms Fairtree's flagship made roughly 7–12% a year. For a South African goal (R10M), a dollar return of about 12–13% a year has historically been worth about 18% in rand. **The bar stays 18% in dollars**, as agreed. This is noted so the comparison is understood.

**Verdict:** not a candidate (check negative). Index exposure is closed as an edge on its own.

**Next:** relative strength **across** markets rather than each market alone. Rank all 45 by their past performance and hold only the strongest few. That doesn't need the market as a whole to rise, which is exactly what failed in 2018–22.

## Part 4 round 5: 20:57

**Idea:** **relative strength across markets** (`tsmom.py`, mode `rank`). At each month end, all 45 markets are scored by their lookback return per unit of their own volatility. The bot goes long the 5 strongest and short the 5 weakest, volatility-sized, with the same costs and financing. It doesn't need markets as a whole to rise, which is what failed in rounds 3–4. Test: the riser is mostly the long, the faller mostly the short, at most 2k positions, no-peek. The split is the 45-market one (from 2000).

| Version | Part | Avg R | PF | t (daily) | Yearly at 25% drop | Groups (forex / metals / indices) |
|---|---|---|---|---|---|---|
| 12-month rank, top/bottom 5 | learn | −0.002 | 0.97 | −0.15 | −0.6% | −0.007 / +0.023 / none |
| | check | −0.015 | 0.85 | −0.62 | −2.3% | −0.036 / +0.019 / +0.024 |
| 6-month rank | learn | −0.013 | 0.87 | −1.04 | −2.4% | |
| | check | −0.023 | 0.78 | −0.25 | −1.1% | |
| 12-month, long the top 5 only | learn | +0.003 | 1.08 | +0.58 | +1.3% | |
| | check | +0.001 | 1.02 | +0.29 | +0.5% | |

**Verdict: rejected.** It's flat everywhere, and forex drags again while metals and indices lean slightly positive, the same pattern as rounds 1–2.

**Where part 4 stands after 5 rounds:** trend, momentum, dip-buying, index holding and cross-market ranking all fail check. The one honest positive out-of-sample result in the whole project is still swing (+0.084R a trade on fresh markets, about +1.5% a year at 1% risk). These are the textbook edges, and on this broker's markets since 2000 (mostly currency pairs, index CFDs without dividends, 2% a year financing) none of them pays enough to reach 18% at a 25% drop.

**Next:** an effect that holds positions only a few days a month, so financing barely matters: the **turn of the month in stock indices**. Equity gains have historically clustered in the last trading day and the first few days of each month, when salaries and pension money flow in.

## Part 4 round 6: 21:12

**Idea:** the **turn of the month** in stock indices (`tsmom.py`, mode `tom`). Long only on the month's last weekday and the next month's first 3 weekdays, flat otherwise, volatility-sized per index. Positions are held about 4 days a month, so financing barely matters. The window comes from the weekday calendar only, never from the data's own dates, so cutting off the future changes nothing. Test: in Jan/Feb 2024 it holds 31 Jan plus 1, 2 and 5 Feb, and no-peek holds. The split is the index split from round 3.

| Version | Part | Avg R (index-month) | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|
| last 1, first 3 | learn 2012–18 | −0.020 | 0.71 | −1.13 | −3.4% |
| | check 2018–22 | −0.012 | 0.91 | +0.05 | −0.6% |
| last 2, first 4 | learn | −0.013 | 0.82 | −0.59 | −2.2% |
| | check | +0.023 | 1.19 | +0.83 | +5.2% |
| last 1, first 2 | learn | −0.008 | 0.85 | −0.64 | −2.7% |
| | check | −0.015 | 0.85 | −0.33 | −3.0% |

**Verdict: rejected.** It's negative on learn for every version. One check result is positive (+5.2%), which looks like noise when its learn result is negative. The turn-of-month effect was famous in data before about 2010 and seems to have faded since. It has also been heavily publicised.

**Six families now, all failing check** (trend, momentum, dip-buying, index holding, ranking, turn of month).

**Next:** the one consistent pattern across rounds 1, 2 and 5 is that forex drags while **metals and indices lean positive** in both learn and check. Round 7 tests trend-following on the **13 non-forex markets only**. Caveat up front: choosing that subset uses what earlier rounds showed (including check), so if it passes check, only its one locked look can say whether it's real.

## Part 4 round 7: 21:27

**Idea:** rounds 1, 2 and 5 all showed forex dragging while metals and indices leaned positive. So test the trend rules on the **16 non-forex markets** (12 indices plus gold, silver, platinum and palladium), using the 2012 index split so learn contains indices. (`trend.bench` now takes a split.)

| Version | Part | Trades/yr | Avg R | t | Yearly at 25% drop | Metals / indices |
|---|---|---|---|---|---|---|
| breakout 50/20 | learn 2012–18 | 32 | −0.222 | −2.22 | −3.7% | −0.232 / −0.215 |
| | check 2018–22 | 49 | +0.027 | +0.24 | −0.3% | +0.160 / −0.034 |
| breakout 100/50 | learn | 14 | −0.337 | −1.61 | −1.8% | −0.467 / −0.226 |
| | check | 24 | −0.045 | −0.26 | −3.4% | −0.216 / +0.051 |
| 12-month momentum | learn | (index-months) | +0.027 | +0.56 | +2.3% | −0.018 / +0.042 |
| | check | | −0.042 | −0.98 | −4.5% | +0.050 / −0.073 |
| 12-month momentum, long only | learn | | +0.034 | +0.71 | +4.0% | −0.014 / +0.050 |
| | check | | −0.000 | −0.03 | −0.8% | +0.078 / −0.026 |

**Verdict: rejected.** The "non-forex leans positive" pattern was small and came from the 2000–2018 windows. In 2012–22 it disappears: metals and indices take turns being positive, and never in both parts at once. Leaving forex out doesn't create an edge; it only removes a drag.

**Seven rounds, seven rejections.** Every daily, multi-day and multi-week idea from the plan list has now been tried. The list is done: trend, volatility sizing (built into rounds 2, 4, 5 and 6), cross-market ranking, mean reversion on indices, and holding. The portfolio-with-swing step has nothing positive to combine with.

**Next:** one shorter-term, well-documented effect that daily candles can't see. The **overnight drift** in US stock indices: historically most of their gains came while the cash market was closed, between the close and the next open. Tested on hourly candles, and only if the hourly index history reaches back far enough.

## Part 4 round 8: 21:42

**Idea:** the **overnight drift** in US stock indices (`sweep/overnight.py`). Historically most US index gains came while the cash market was closed. Hourly candles show the cash session in server time: volume jumps at about 16:30 and drops after 23:00. The rule: buy at the close of the 22:00 candle (the US close), sell at the open of the next trading day's 16:00 candle (just before the cash open), flat all day. The times are calendar only, so nothing peeks. Costs are the spread or 1bp per side, plus financing per calendar night (3 over a weekend). Markets: US500, US30, US2000 (hourly from Aug 2012) and USTEC (from 2022). Tests: each night runs close-to-next-open exactly, no-peek, and weekends cost more. The split is the index split.

**Data caveat:** the learn part has nights only from about mid-2015. Before that the hourly candles have no 22:00 candle, because the server used different session hours. Learn is effectively 2015–18.

| Version | Part | Nights | Avg a night | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|---|
| long the night, exit 16:00 | learn | 1559 | +0.011% | 1.08 | +0.43 | +1.4% |
| | check | 3220 | +0.010% | 1.04 | +0.38 | +1.5% |
| exit 17:00 (after the opening half-hour) | learn | 1559 | +0.028% | 1.18 | +1.28 | +6.7% |
| | check | 3223 | +0.013% | 1.04 | +0.45 | +1.7% |
| **control: short the night** | learn | 1559 | −0.070% | 0.62 | **−3.76** | −4.5% |
| | check | 3220 | −0.073% | 0.77 | **−2.67** | −5.9% |
| no costs (diagnostic) | learn | 1559 | +0.033% | 1.25 | +1.71 | +10.4% |
| | check | 3220 | +0.022% | 1.08 | +0.83 | +4.6% |

**What it shows:**
- **The drift is real.** Shorting the night loses decisively in both parts (t −3.8 and −2.7), and holding it long makes +0.02–0.03% a night before costs. It's the clearest real effect of the night.
- **It's too small to trade on this broker.** The spread (about 2bp a round trip) plus financing eat about two thirds of it, leaving 1.4–1.7% a year at a 25% drop. A fund holding stocks earns the night for free, because it never pays a spread to get in and out.

**Verdict: rejected for the bar.** It's real but tiny after costs.

**Where part 4 stands:** 8 rounds, 8 families, nothing within reach of 18% a year at a 25% drop on unseen data. Two effects were confirmed as real but small: the overnight drift, and swing's +0.08R a trade on fresh markets. On this broker, the costs of trading CFDs (spread, financing, no dividends) take most of what the known edges pay.

## Part 4 round 9: 21:57

**Idea:** the last item on the plan, **a portfolio of swing and a new family**. The only new effect confirmed real was the overnight drift (round 8). Two small edges that don't move together can add up to more than either alone. I tested frozen swing on the 24 fresh markets (1% risk a trade, booked on exit) plus the overnight drift. Each was scaled to the same daily volatility and the two were added, then scaled to a 25% worst drop. Overnight data really starts on 2016-01-26, so learn is 2016–18. (A research script, not bot code: `scratchpad/sweeprun/combo.py`.)

| Part | Swing alone | Overnight alone | Both | Correlation |
|---|---|---|---|---|
| learn 2016–18 | t −1.33, −12.0%/yr | t +0.43, +3.7%/yr | t −0.65, −8.7%/yr | −0.03 |
| check 2018–22 | t +0.17, +0.3%/yr | t +0.38, +1.6%/yr | t +0.39, +1.6%/yr | −0.01 |

**Verdict: rejected.** The two really are unrelated (correlation about 0), but adding two near-zero edges gives near zero. Swing on fresh markets was negative in 2016–18 and flat in 2018–22. Its positive full-history result (+0.084R) came mostly from other years, which fits its quarters: +6.8, +3.8, +2.4, +14.4.

**The plan list is now finished:** trend, momentum, volatility sizing, ranking, index dip-buying, index holding, turn of month, non-forex trend, overnight drift, and the portfolio. **Nothing has passed check, so nothing has earned a locked look.**

## Part 4 round 10: 22:21

**Idea:** the plan list is done, so this idea comes from the evidence. Forex lost every time it was traded with the trend (rounds 1, 2, 5), which hints that currency pairs **revert** over short horizons. This test is round 3's dip rule **mirrored** (`dip.py` gained `sides=0`). It buys a 2-day RSI under 10 and **sells short a 2-day RSI over 90**, exits on a close back through the 5-day average or after 10 days, with a 3 ATR stop, on the 29 forex pairs with daily data, using the 2000 split. Test: both sides trade, paths add up, no-peek. (The index-only results from round 3 are unchanged.)

| Version | Part | Trades/yr | Win | Avg R | t | PF | Yearly at 25% drop |
|---|---|---|---|---|---|---|---|
| both sides, no trend filter | learn | 659 | 58% | −0.045 | −5.69 | 0.75 | −2.5% |
| | check | 827 | 58% | −0.035 | −3.83 | 0.81 | −3.4% |
| both sides, with the MA200 filter | learn | 249 | 59% | −0.036 | −3.50 | 0.78 | −2.6% |
| | check | 327 | 56% | −0.040 | −2.85 | 0.78 | −3.3% |
| buy dips only, no filter | learn | 329 | 59% | −0.045 | −4.08 | 0.75 | −2.5% |
| | check | 411 | 59% | −0.030 | −2.71 | 0.83 | −3.4% |
| both sides, **no costs** (diagnostic) | learn | | | +0.007 | +1.52 | | |
| | check | | | −0.009 | −1.57 | | |

**Verdict: rejected.** With no costs there's no edge either (+0.007R, then −0.009R). The many small trades turn the spread into a steady loss.

**What forex taught over four rounds:** at daily horizons since 2000, currency pairs neither trend (rounds 1, 2, 5) nor revert (this round) enough to pay their spread. They're close to a coin flip. The only forex edge funds famously earn is **carry** (holding high-interest currencies against low-interest ones). That needs the broker's swap rates, which aren't in the cached data. Measuring them would mean touching the live MT5 terminal, which part 4's rules forbid.

## Part 4 round 11: 22:27

**Idea:** round 4 held only stock indices. The classic diversified version adds metals (gold tends to rise when stocks fall) and gives each market an **equal share of the risk**. It uses the existing code (`tsmom.py`, mode `hold`, volatility-sized per market, monthly, with costs and financing) and the index split. **Hindsight caveat:** gold's rise after 2018 is common knowledge, so a good check result here proves little until a locked look.

| Version | Part | Avg R (market-month) | PF | t (daily) | Yearly at 25% drop | Metals / indices |
|---|---|---|---|---|---|---|
| hold all 16 non-forex | learn 2012–18 | +0.045 | 1.31 | +1.31 | +7.0% | −0.053 / +0.078 |
| | check 2018–22 | −0.002 | 0.99 | +0.21 | +0.5% | +0.078 / −0.028 |
| hold all 16, only above MA200 | learn | +0.022 | 1.29 | +0.38 | +1.2% | |
| | check | +0.010 | 1.06 | +0.31 | +1.1% | |
| **hold gold + US500 only** | learn | +0.095 | 1.28 | +1.18 | **+6.5%** | −0.102 / +0.292 |
| | check | +0.107 | 1.29 | +0.98 | **+6.6%** | +0.126 / +0.088 |

**What it shows:**
- **Gold + US500, equal risk, simply held, is the steadiest result of part 4:** about +6.5% a year at a 25% drop in both parts. When stocks were strong (2012–18) gold lagged, and when stocks stalled (2018–22) gold carried it. That's diversification working as designed.
- It's a **passive portfolio, not a trading edge.** It earns what the two markets earn. t is about 1, and it reaches about a third of 18%. It also uses hindsight, because US500 and gold are the two winners everyone knows about.
- Adding more indices and metals made it worse (+0.5% on check). Spreading wider brought in more weak markets, not more diversification.

**Verdict:** rejected for the bar. It's recorded as the realistic reference: on this broker, about 6–7% a year at a 25% worst drop is what plain held exposure delivered, and no active rule tonight beat it on check.

## Part 4 round 12: 22:42

**Idea:** every directional idea has failed, so this one is **market-neutral: pairs** (`sweep/pairs.py`). It uses four pairs that normally move together: US500/US30, gold/silver, DE40/Euro Stoxx 50 and FRA40/Euro Stoxx 50. Each leg is sized to equal volatility. When the gap's 20-day z-score passes ±2, the next close holds it the other way (long the cheap leg, short the rich one) until the z-score crosses 0 or 20 days pass. Both legs pay spread or 1bp on every change, plus 2% a year financing. Positions earn from the next close (one day late). Tests: it profits on a synthetic gap that snaps back, trades add up to the daily series, no-peek. The split is the index split.

| Version | Part | Trades | Avg (per 1% a leg) | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|---|
| z>2, 20 days | learn | 106 | −0.161 | 0.59 | −1.79 | −4.3% |
| | check | 146 | −0.010 | 0.97 | −0.08 | −1.6% |
| z>2.5, 20 days | learn | 63 | −0.201 | 0.56 | −1.56 | −4.2% |
| | check | 89 | −0.029 | 0.92 | −0.27 | −3.2% |
| z>2, 60 days | learn | 54 | −0.280 | 0.51 | −2.05 | −4.2% |
| | check | 64 | −0.097 | 0.80 | −0.55 | −5.7% |
| z>2, 20 days, **no costs** (diagnostic) | learn | 106 | −0.052 | 0.84 | −0.57 | −2.6% |
| | check | 146 | +0.093 | 1.38 | +0.97 | +8.0% |

**Verdict: rejected.** It's negative on learn even with no costs, and after costs negative everywhere. Pairs also need 6–13x leverage to reach a 25% drop, because a market-neutral position moves very little, so every cost is multiplied too. Two legs mean two spreads and two financing charges on a small edge.

**Twelve rounds, twelve families, nothing passes check.** Next round takes stock rather than adding a thirteenth guess.

## Part 4 round 13: 22:57, stock-take and the one locked look

**All part 4 families so far** (yearly return at the size that keeps the worst drop at 25%, after costs):

| Round | Family | Learn | Check | Status |
|---|---|---|---|---|
| 1 | daily breakout trend, 45 markets | −2.0% | −3.4% | failed check |
| 2 | 12-month momentum, 45 markets | −1.2% | −2.7% | failed check |
| 3 | buy index dips (RSI2) | +16.0% | −1.0% | failed check |
| 4 | hold the 12 indices | +14.6% | −1.6% | reference only |
| 5 | cross-market ranking | −0.6% | −2.3% | failed check |
| 6 | turn of the month, indices | −3.4% | −0.6% | failed learn |
| 7 | trend/momentum on metals + indices | −3.7% to +4.0% | −0.3% to −4.5% | failed check |
| 8 | overnight drift, US indices | +1.4% | +1.5% | real, too small |
| 9 | swing + overnight portfolio | −8.7% | +1.6% | failed |
| 10 | two-sided mean reversion, forex | −2.5% | −3.4% | failed, no edge before costs |
| 11 | equal-risk hold, gold + US500 | +6.5% | +6.6% | steady, but passive and hindsight |
| 12 | pairs (market-neutral) | −4.3% | −1.6% | failed, no edge before costs |

**The one locked look.** Only one candidate held up on both learn and check: round 11's equal-risk gold + US500 hold. It got its single look at the locked part (2022-08-05 to 2026-11-18), at the size chosen on learn + check (x1.91, about 1.3x the account in positions). It's recorded in `research_lab/locked_looks.json`.

| | Yearly | Worst drop | t | PF | Quarters |
|---|---|---|---|---|---|
| as modelled (2% financing) | **+22.1%** | 14% | +2.67 | 2.25 | +3.9, +13.3, +11.4, +3.8 |
| with 4% financing | +19.0% | 15% | | | |
| **with 6% financing (realistic for 2023–26)** | **+16.0%** | 15% | | | |
| with 7% financing | +14.5% | 16% | | | |

**Verdict: does NOT clear the bar honestly.**
1. **Financing was under-modelled.** The 2% a year assumption fits 2012–22, when interest rates were near zero. From 2023 to 2025 rates were about 5%, and a broker charges roughly rate + 2.5% to hold a leveraged CFD. At a realistic 6% the locked result is +16%, under 18%. (This corrects a cost; it tunes nothing. The failed families were all judged on 2018–22, when 2% was fair, so their verdicts stand.)
2. **Hindsight.** Gold and US500 were picked in round 11 because they're the two famous winners, and the locked window (2022–26) is exactly when gold doubled and US stocks boomed. On learn and check, where luck was neutral, the same holding made +6.5% a year.
3. **It's passive.** It earns what the two markets earn; there's no trading edge in it. A real fund (or an ETF) holds the same exposure without paying a spread or CFD financing.

**What part 4 has established:**
- On this broker's 45 markets since 2000, after real costs, **no active trading rule tried here beats simply holding**, and **holding a diversified pair made about 6–7% a year at a 25% drop** in neutral years.
- Reaching 18% a year in dollars at a 25% drop needed a boom (2022–26) on top of that.
- **Next rounds:** few untried ideas remain that this data can test fairly. The rounds continue until 08:00, as agreed.

## Part 4 round 14: 23:12

**Idea:** a timeframe not yet tried, **intraday snap-backs in US indices** (`sweep/snap.py`). During the cash session (signal candles 16:00–21:00 server time), when one hourly candle falls by more than 2 typical hourly moves (the standard deviation of the last 100), buy at the next open and sell 3 candles later. It's never held overnight, so there's **no financing**, the cost that sank the multi-day ideas. Long only. One trade per market at a time. Costs are the spread or 1bp per side. Markets: US500, US30, US2000, USTEC. Tests: it signals only inside the session, holds exactly 3 hours, finds a planted bounce, no-peek. The split is the index split. (The learn part's hourly data effectively starts in 2015, as noted in round 8.)

| Version | Part | Trades | Avg a trade | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|---|
| 2 sd, hold 3h | learn | 560 | −0.040% | 0.76 | −1.34 | −3.8% |
| | check | 1160 | −0.058% | 0.79 | −2.00 | −5.6% |
| 3 sd, hold 3h | learn | 238 | −0.089% | 0.54 | −1.85 | −4.6% |
| | check | 481 | +0.001% | 1.00 | +0.02 | −0.5% |
| 2 sd, hold 1h | learn | 660 | −0.044% | 0.66 | −2.33 | −3.8% |
| | check | 1357 | −0.030% | 0.84 | −1.63 | −5.6% |
| 2 sd, 3h, **no costs** (diagnostic) | learn | 560 | −0.025% | 0.84 | −0.86 | −3.1% |
| | check | 1160 | −0.053% | 0.80 | −1.83 | −5.5% |

**Verdict: rejected.** It loses even with no costs. Big hourly drops in US indices **keep going** for the next few hours rather than bouncing (t −1.83 on check without costs). That's the opposite of the daily picture in round 3, where dips bounced over days in 2012–18.

**Next:** that suggests the mirror, **intraday momentum** (follow a big hourly move for a few hours, both ways). Honest caveat: the idea comes from this round's check numbers. Its check result will therefore be partly hindsight, and only learn plus a locked look can judge it.

## Part 4 round 15: 23:27

**Idea:** the mirror of round 14, **intraday momentum** (`snap.py`, `follow=True`). After a big hourly move during the US session (more than 2 typical moves), trade in its direction for 3 hours: short after a big drop, long after a big rise. Never overnight. Test: it trades both ways, after the same drop it's exactly the opposite of round 14's bet, no-peek. **Caveat (as flagged):** the idea came from round 14's check numbers, so learn is the fair judge.

| Version | Part | Trades | Avg a trade | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|---|
| follow 2 sd, 3h | learn | 944 | +0.005% | 1.04 | +0.30 | +0.8% |
| | check | 1955 | −0.003% | 0.99 | −0.15 | −1.6% |
| follow 3 sd, 3h | learn | 379 | +0.030% | 1.23 | +0.94 | +3.7% |
| | check | 760 | −0.044% | 0.83 | −1.26 | −6.0% |
| follow 2 sd, 1h | learn | 1187 | −0.012% | 0.88 | −1.27 | −3.4% |
| | check | 2399 | −0.021% | 0.87 | −1.54 | −5.0% |
| follow 2 sd, 3h, no costs (diagnostic) | learn | 944 | +0.020% | 1.15 | +1.12 | +8.3% |
| | check | 1955 | +0.002% | 1.01 | +0.09 | −0.3% |

**Verdict: rejected.** It's flat. In 2018–22 big drops kept going but big rises reversed, so the two sides cancel. On learn there's a faint edge before costs (+0.02% a trade), which the spread takes. Round 14's "drops keep going" was a one-sided pattern of a falling-market period, not a stable intraday effect.

**Tally:** 15 rounds, 14 families. Nothing passes check except the passive gold + US500 hold, which failed its locked look once costs were realistic (round 13).

## Part 4 round 16: 23:42

**Idea:** a pattern in gold, the steadiest market tonight. **Gold's session effect** is the reported tendency for gold to rise during Asian hours and fall during London/New York hours. Measured with the overnight module's hour logic (buy at a candle's close, sell at a later candle's open, same day, long only, no financing), on gold's own hourly history split 40/30/30: learn 2009-12 to 2016-09, check to 2021-10, locked after. (A research measurement, no new bot code. Gold has no 00:00 candle; it opens at 01:00 server time, so "Asia" is 02:00–09:00.)

| Session (long gold) | Costs | Learn: avg a day, t, yearly at 25% drop | Check: avg a day, t, yearly |
|---|---|---|---|
| Asia 02–09 | with costs | −0.019%, t −1.98, −3.4% | −0.015%, t −1.56, −2.9% |
| | **no costs** | **+0.036%, t +3.67**, +30.5% | **+0.013%, t +1.38**, +7.1% |
| Europe 09–16 | with costs | −0.107%, t −7.33, −4.0% | −0.033%, t −2.39, −5.4% |
| | no costs | −0.058%, t −3.98, −3.6% | −0.005%, t −0.39, −3.2% |
| US 16–23 | with costs | −0.063%, t −3.01, −4.1% | −0.040%, t −2.47, −5.2% |
| | no costs | −0.006%, t −0.23, −1.3% | −0.012%, t −0.76, −3.7% |

**What it shows:**
- **The Asian drift is real before costs**: positive in both parts, t +3.67 on learn. Gold falls in European hours. This matches the documented pattern, so it isn't something I mined from the data.
- **Costs kill it.** Gold's typical hourly spread is about 1bp (0.16 at a median price of about 1,573), but this trade pays about 0.055% a day, roughly 5 times that. The likely cause: entering at 02:00, right after the 00:00–01:00 daily rollover, when spreads blow out.

**Verdict: rejected as is.** Next round tests entering **later in the Asian session** (once spreads have normalised). That choice is motivated by the cost mechanics, not by picking the best check number.

## Part 4 round 17: 23:57

**Idea:** continue round 16. Enter gold's Asian drift **after spreads normalise**, with the hour picked from **learn-period spreads only**.

**Learn-period median spread by hour (bp per side):** 01:00 2.99, 02:00 2.60, 03:00 2.41, then 2.24–2.40 through the day, rising to 3.06 by 23:00. Round 16's guess was wrong: there's no rollover spike to avoid. **Gold cost 2.2–3bp a side at every hour in 2009–16**, so a round trip is at least about 4.6bp, while the Asian drift is about 3.6bp a day.

| Asia, buy at the close of the 03:00 candle, sell at 09:00 | Learn | Check |
|---|---|---|
| with costs | −0.018% a day, t −2.11, −3.3%/yr | −0.022%, t −2.76, −4.0%/yr |
| no costs | +0.031%, t +3.53 | +0.006%, t +0.73 |

**Verdict: rejected.** The drift is smaller than one round-trip spread, and it was also weaker in 2016–21 (+0.006% a day, about 6 times smaller than 2009–16). A daily in-and-out trade can't capture an edge smaller than its own cost. Only someone already holding gold collects it for free, the same lesson as the overnight drift in round 8.

**The repeating lesson:** rounds 8, 16 and 17 found real, documented time-of-day drifts (US indices overnight, gold in Asian hours). Each is 1–4bp a day, and this broker's spread is 1–3bp a side. Session-timing edges are real but too thin for a retail CFD account.

## Part 4 round 18: 00:12

**Idea:** the **Halloween effect** ("sell in May"): hold stock indices from November to April and stay flat May to October. It's one of the few calendar effects published as holding up out-of-sample across many countries and decades, and being flat half the year halves the financing that hurt round 4. (`tsmom.py`, mode `halloween`: decided at each month's last close for the month ahead, volatility-sized per index. Test: held Dec–Mar and not Jun–Sep, no-peek.) Twelve indices, index split, compared with plain holding (round 4).

| Version | Part | Avg R (index-month) | PF | t (daily) | Yearly at 25% drop | Quarters |
|---|---|---|---|---|---|---|
| Nov–Apr only | learn 2012–18 | +0.053 | 2.40 | +1.75 | +13.0% | +19.8, +4.1, −2.1, +21.8 |
| | check 2018–22 | −0.029 | 0.83 | −0.02 | −0.6% | +5.1, −39.1, +39.4, −23.5 |
| hold all year (round 4) | learn | +0.078 | 1.99 | +1.91 | +14.6% | |
| | check | −0.028 | 0.90 | −0.16 | −1.6% | |

**Verdict: rejected.** It's worse than plain holding on learn (+13.0% against +14.6%) and only marginally better on check (−0.6% against −1.6%). The keep rule needs better on both. Its winter window also caught the February–March 2020 crash, which shows a calendar rule gives no protection from shocks. Even if it worked, the best it could be is index exposure for half the year, which round 4 showed can't reach 18%.

## Part 4 round 19: 00:27

(Check of the live forward test, read-only: the process is running, sweep_trader.json was updated at 00:27, and there have been no setups since the 19:42 start.)

**Idea:** **breakout after a quiet day** (`sweep/nr7.py`, the "NR7" pattern), a short-term family not yet tried. When a day's range is the narrowest of the last 7, the next day buy above its high or sell short below its low. The stop is the other side (1R = the quiet day's range). Exit at the close 2 days after entry. On all 45 markets, with the 2000 split. A day that touches both sides can't be ordered from daily candles, so it's **booked as the worst case** (filled, then stopped). Tests: both sides trade, a loss is at most about 1R, holds are at most 3 days, no-peek.

| Version | Part | Trades/yr | Win | Avg R | t | Yearly at 25% drop | Forex / metals / indices |
|---|---|---|---|---|---|---|---|
| NR7, hold 2d | learn | 683 | 36% | −0.267 | −9.42 | −2.6% | −0.284 / +0.213 / none |
| | check | 1008 | 35% | −0.111 | −3.80 | −3.3% | −0.123 / −0.067 / −0.041 |
| NR7, exit same day | learn | 793 | 39% | −0.217 | −12.32 | −2.6% | −0.232 / +0.215 / none |
| | check | 1162 | 44% | −0.059 | −3.07 | −3.2% | −0.072 / +0.004 / +0.003 |
| NR4, hold 2d | learn | 1034 | 36% | −0.256 | −11.51 | −2.6% | |
| | check | 1551 | 37% | −0.104 | −4.10 | −3.4% | |
| NR7, no costs (diagnostic) | learn | 683 | 36% | −0.246 | −8.71 | −2.6% | |
| | check | 1008 | 36% | −0.071 | −2.43 | −3.1% | |

**Why this result can't be trusted either way:** with no costs over learn + check, **26% of the 15,466 trades are stopped on their entry day** (−1.01R each). The other 74% average **+0.35R**. Many entry-day stops are days that touched both sides, which I booked as losses because daily candles can't tell which came first. With a stop only one quiet day's range away, that assumption alone could turn a winner into a loser.

**Verdict: undecided, not kept.** Next round resolves the order of each touch-both-sides day from **hourly candles** (which side was hit first). That's a correctness fix to the simulation, not tuning: the rule stays exactly the same.

## Part 4 round 20: 00:42

**Idea:** settle round 19. On each day that touched both breakout levels, use **hourly candles** to see which side was hit first and whether the stop followed the same day (`nr7.first_touch`). The rule is unchanged. If one hourly candle touched both, or there's no hourly data, it stays worst-case. Test: the high broken first then the low means filled long and stopped; no later low means not stopped; one candle touching both means can't tell.

**What the hourly candles showed** (both-sides days, learn + check):

| Outcome | Days |
|---|---|
| confirmed stopped the same day | 1,962 |
| one hourly candle touched both (can't tell) | 162 |
| no hourly data (mostly 2000–2010) | 1,974 |
| **filled and NOT stopped** | **0** |

**My mistake in round 19:** a day that touches both levels **always** stops the trade, whichever side fills first, because the second touch *is* the stop. The worst-case booking was simply correct, and the hourly data confirms it in every checkable case. Results are unchanged (NR7 hold 2d: learn −0.267R, t −9.42; check −0.113R, t −3.86; without costs still −0.246 / −0.073).

**Verdict: rejected.** With a stop just one quiet day's range away, about a quarter of breakouts reverse through the stop on day one. The +0.35R of the other trades doesn't cover that. (The hourly resolver stays in the code; it makes the simulation exact where hourly data exists.)

## Part 4 round 21: 00:57

**Idea:** **buy stock indices after volatility spikes** (`dip.py`, `entry="volspike"`). When an index's 10-day volatility jumps above 1.5× its 100-day volatility (a panic), buy at the next open and hold 20 days, with a 3 ATR stop. This is the "fear premium": after panics, markets have historically paid buyers for taking the risk. It differs from round 3, which reacted to a 2-day price dip and held a few days. Test: entries cluster at volatility bursts, no-peek. Twelve indices, index split.

| Version | Part | Trades/yr | Win | Avg R | t | PF | Yearly at 25% drop | Quarters |
|---|---|---|---|---|---|---|---|---|
| spike x1.5, in uptrend (MA200) | learn | 7 | 67% | +0.442 | +1.99 | 2.45 | **+22.0%** | +2.0, +6.9, +4.6, +5.2 |
| | check | 16 | 56% | +0.053 | +0.31 | 1.12 | +0.3% | −2.4, −14.7, +9.2, +11.6 |
| spike x1.5, no filter | learn | 11 | 64% | +0.511 | +2.24 | 2.67 | **+28.4%** | +9.3, +10.8, +5.9, +5.1 |
| | check | 31 | 50% | −0.008 | −0.05 | 0.98 | −0.7% | −6.3, −24.7, +10.1, +19.8 |
| spike x2.0, no filter | learn | 3 | 72% | +0.689 | +1.97 | 4.04 | +14.7% | |
| | check | 13 | 46% | −0.113 | −0.45 | 0.78 | −2.1% | |

**Verdict: rejected.** It's the same story as round 3, more sharply: superb in 2012–18 (+22–28% a year, every quarter positive) and nothing in 2018–22. Buying the early Covid sell-off and the 2022 bear market meant buying panics that kept going (the −14.7 and −24.7 quarters). Then 2020's rebound paid some back.

**What 21 rounds keep showing:** every index rule that buys weakness looks brilliant on 2012–18, one long, calm bull market, and earns nothing on 2018–22. Learn-period success on indices is a poor guide here. Only the check part has been honest.

## Part 4 round 22: 01:12

**Idea:** rounds 3 and 21 both show that buying index dips works in calm markets and fails in turbulent ones. The principled fix is a **calm-market filter**: only buy round 3's dip (RSI2 under 10, over MA200) while the index's 20-day volatility is below its own 1-year median (`dip.py`, `calm=True`). **Caveat:** the idea comes from where the check failures were, so check is partly hindsight. Test: fewer trades than without the filter, no-peek. Twelve indices, index split.

| Version | Part | Trades/yr | Win | Avg R | t | PF | Yearly at 25% drop | Quarters |
|---|---|---|---|---|---|---|---|---|
| dip + calm filter | learn | 18 | 77% | **+0.111** | **+2.45** | 2.17 | +14.9% | +2.4, +1.7, +2.8, +4.3 |
| | check | 38 | 74% | **+0.038** | **+0.70** | 1.25 | **+3.7%** | −1.1, −1.4, +10.0, −1.4 |
| dip (round 3) | learn | 33 | 75% | +0.095 | +2.21 | 1.79 | +16.0% | |
| | check | 53 | 70% | −0.001 | −0.01 | 1.00 | −1.0% | |

**Verdict: kept as the dip family's version** (better average R and t on both parts). The yearly figure on learn is slightly lower because it trades about half as often. **It doesn't get a locked look:** +3.7% a year on check is nowhere near 18%. The filter removes most of the damage from turbulent periods, but what's left is a small edge.

**Champion of the dip family:** RSI2<10 over MA200, only while 20-day volatility is under its 1-year median, sell on a close over the 5-day average or after 10 days, 3 ATR stop.

## Part 4 round 23: 01:27

**Idea:** carry round 22's lesson over to round 4's plain holding. **Hold the indices only while markets are calm** (20-day volatility under its 1-year median), decided every close, with volatility sizing, costs and financing as before (`tsmom.py`, mode `calm_hold`). Research on "volatility-managed portfolios" suggests stepping aside in turbulence can lift returns at the same risk. Test: held far more in calm stretches than stormy ones, no-peek. Twelve indices, index split.

| Version | Part | Avg R (index-month) | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|
| hold only while calm | learn | +0.005 | 1.10 | +0.16 | **+0.2%** |
| | check | −0.002 | 0.92 | −0.11 | −1.3% |
| hold all the time (round 4) | learn | +0.078 | 1.99 | +1.91 | +14.6% |
| | check | −0.028 | 0.90 | −0.16 | −1.6% |

**Verdict: rejected.** It's far worse on learn (+0.2% against +14.6%) and about the same on check. Switching on a daily volatility signal pays the spread often, and it sits out the sharp rebounds that happen *inside* turbulent periods, where much of the stock market's return is made. The calm filter helps a short dip trade (round 22) because it avoids buying into a falling market. It hurts a holding strategy because it misses the recovery.

## Part 4 round 24: 01:42

**Idea:** a **robustness check of round 22's kept version** (index dip + calm filter). If "buy short dips in calm uptrends" is a real effect, it should also show on markets it was never designed on. The keep rule needs a change to hold in more than one group, and the dip family had only been measured on indices. Same rule and index split, on the 4 metals and the 29 forex pairs.

| Group | Version | Learn: trades, avg R, t, yearly at 25% drop | Check: trades, avg R, t, yearly |
|---|---|---|---|
| metals | dip + calm | 51, −0.096, −1.43, −3.6% | 35, −0.020, −0.29, −2.4% |
| | dip | 70, −0.081, −1.36, −4.0% | 98, −0.016, −0.35, −3.1% |
| forex | dip + calm | 606, −0.044, −2.30, −4.0% | 358, −0.026, −1.13, −3.6% |
| | dip | 1007, −0.012, −0.67, −2.4% | 678, −0.019, −1.10, −3.7% |
| indices (round 22) | dip + calm | 101, **+0.111**, +2.45, +14.9% | 163, **+0.038**, +0.70, +3.7% |
| | dip | 187, +0.095, +2.21, +16.0% | 228, −0.001, −0.01, −1.0% |

**Verdict: round 22's keep is withdrawn.**
- Dip-buying loses on metals and forex, with or without the filter.
- The calm filter makes average R **worse** on metals (both parts) and on forex learn. By the group rule, worse in 2 of 3 groups, it doesn't qualify.
- Its improvement on indices may just fit the one 2018–22 window it was designed around.

The dip family stays at round 3's version, which is an index-only effect that failed check. Robustness checks like this are what stop a hindsight fix from slipping through.

## Part 4 round 25: 01:57

**Idea:** improve the **cost model**, not add a strategy. Round 13 showed the flat 2% a year financing can change a verdict. It was too cheap in 2023–25, and it ignores that **shorts get paid** when rates are high. New `sweep/financing.py`: longs pay the year's US policy rate + 2.5% markup, shorts pay 2.5% − rate (negative = paid). The yearly rates are approximate public figures, with 2026 estimated, and every market is charged on the US rate (a simplification). It's wired into the monthly framework as `MomParams(finance="rates")`. Test: in 2024 a long costs far more than in 2021, a short is paid in 2024 and pays in 2021, a flat position costs nothing.

**Re-judged** (yearly at a 25% drop, learn / check):

| Strategy | Flat 2% | Rate-based | Change |
|---|---|---|---|
| gold + US500 hold (index split) | +6.5% / +6.6% | **+4.6% / +3.9%** | −2 to −3 points |
| hold 12 indices (index split) | +14.6% / −1.6% | +12.7% / −2.4% | −1 to −2 points |
| 12-month momentum, 45 markets | −1.2% / −2.7% | −1.7% / −2.9% | slightly worse, despite shorts earning carry in high-rate years |

**What it shows:** the flat 2% was **slightly generous everywhere**. Even in zero-rate years the broker's 2.5% markup is more than 2%, and in 2023–25 a long position costs about 7.5% a year. No verdict changes, because every strategy was already below the bar. The realistic reference, holding gold + US500 on CFDs, falls to about **4–5% a year at a 25% drop**.

**The lesson for the R10M plan:** for holding exposure for months, a CFD account is an expensive wrapper. A fund or ETF holds the same markets without a 2.5% + rate yearly charge. CFD bots only make sense for short holds, and every short-hold idea tested tonight was too thin to pay the spread.

## Part 4 round 26: 02:12

**Idea:** **forex carry**, the one forex edge famous among professional funds (round 10 said it needed rate data). Hold each pair in the direction of its interest-rate gap (long the high-rate currency against the low-rate one) whenever the gap is at least 1%. Earn or pay the gap daily, volatility-sized and decided monthly. A broker cut of 1.5% a year on the position plus spreads. `financing.py` gained approximate yearly policy rates for USD, EUR, GBP, JPY, CHF, AUD, NZD, CAD and ZAR (public central-bank history, rounded, typed from memory, so good to about a point). `tsmom.py` gained mode `carry`. Test: AUDJPY's 2007 gap is over 5%, held long, earns carry on a flat price; gold has no gap. The 29 forex pairs, 2000 split.

| Version | Part | Avg R (pair-month) | PF | t (daily) | Yearly at 25% drop | Quarters |
|---|---|---|---|---|---|---|
| gap ≥ 1%, 1.5% broker cut | learn 2000–10 | +0.034 | 1.16 | +1.32 | +2.3% | +27.1, +118.9, +26.1, −46.2 |
| | check 2010–18 | −0.011 | 0.95 | −0.91 | −2.0% | +30.7, −27.3, −9.5, −23.8 |
| gap ≥ 2% | learn | +0.038 | 1.27 | +1.70 | +3.1% | |
| | check | −0.002 | 0.98 | −0.87 | −1.6% | |
| gap ≥ 1%, no costs (diagnostic) | learn | +0.061 | 1.31 | +2.23 | +4.4% | |
| | check | +0.012 | 1.06 | −0.32 | −0.9% | |

**Verdict: rejected.** Carry did what history says it did. It paid in the 2000s carry boom, gave much of it back in the 2008 crash (−46R in learn's last quarter), and earned nothing in 2010–18, when rates were near zero almost everywhere and there was no gap to collect. Even at its best (learn, no costs) it's +4.4% a year at a 25% drop. Carry is a slow, crash-prone edge, not an 18% one.

**Tally: 26 rounds.** Tonight has tested every major edge family known in the literature: trend, momentum, carry, mean reversion, seasonality, volatility, pairs, and holding. None reaches 18% a year at a 25% drop after this broker's costs on unseen data.

## Part 4 round 27: 02:27

**Idea:** the plan's last item done properly: **one diversified portfolio of every family's best version** (round 9 combined only two). The legs are trend 50/20 (45 markets), 12-month momentum (45, rate-based financing), forex carry, the US overnight drift, the index dip (round 3), and the gold + US500 hold (rate-based financing). Each leg is scaled to equal daily risk **using its learn-period volatility only**, the legs are averaged, and the result is sized to a 25% drop. Common window: the 2012 index split. (A research script: `scratchpad/sweeprun/combo_all.py`.)

**Correlations of daily results (learn)** are mostly between −0.35 and +0.30. Trend and momentum share +0.46, and carry runs against momentum at −0.35. The legs really are fairly unrelated.

| Leg | Learn 2012–18: t, yearly at 25% drop | Check 2018–22: t, yearly |
|---|---|---|
| trend 50/20 | −1.17, −4.2% | −1.23, −5.2% |
| momentum 12m | −0.75, −3.6% | −2.02, −5.5% |
| carry | −1.46, −3.8% | −0.08, −1.0% |
| overnight drift | +0.43, +1.4% | +0.38, +1.5% |
| index dip | +1.67, +16.0% | −0.01, −1.0% |
| gold + US500 hold | +0.92, +4.6% | +0.65, +3.9% |
| **all combined** | **−0.12, −1.5%** | **−0.35, −3.0%** |

**Verdict: rejected.** Diversification works only on edges that are positive. Here three of the six legs lose money in both parts, and averaging zero and negative edges stays negative. Dropping the losers would mean choosing legs by their results, which is exactly the hindsight the split exists to prevent. The two legs positive in both parts (overnight drift, gold + US500) combine to the round 9 / round 11 answer: about 2–5% a year.

**The plan list is now finished in full**, including the portfolio step.

## Part 4 round 28: 02:42

**Idea:** a correctness check on the **live strategy**, not a new one. The swing backtest engine (`engine.py`) charges spreads and stop slippage but **no overnight financing**, and swing trades are held for days. Round 25 showed realistic financing is large, so this round re-prices swing's trades with it. Swing's rules stay frozen and the live bot isn't touched; this only measures. Each trade pays (US rate of its year + 2.5%) a year on a long, or (2.5% − rate) on a short, per night held. Position size per 1R = entry / |entry − stop|. (A research script: `scratchpad/sweeprun/swing_financing.py`.)

| Markets | Trades | Hold (nights) | As backtested | With financing | Cost a trade |
|---|---|---|---|---|---|
| fresh 24 (never tuned) | 325 | median 0, mean 2.9 | +0.084R, t +1.07, +1.7R/yr | **+0.048R, t +0.62, +1.0R/yr** | 0.036R |
| tuned 21 | 363 | median 1, mean 2.9 | +0.165R, t +2.18, +3.6R/yr | +0.128R, t +1.71, +2.8R/yr | 0.037R |

**What it means for the forward test:**
- Swing's edge on unseen markets shrinks by about **40%** once financing is counted: about +0.05R a trade, or **about +1% a year at 1% risk**. With t 0.62 it's indistinguishable from zero.
- Most trades close within a night, but the few long holds (some up to weeks) carry most of the financing.
- The live demo **does** pay real swaps, so its results should come in below the old backtest. That's not a sign of a bug.

**Recommendation (for Nicolaas, not done tonight):** add financing to `engine.py` so every future swing proof includes it. That changes how swing is measured, not its rules, but it touches the frozen version's numbers, so it should be his call.

## Part 4 round 29: 02:57

**Idea:** a documented intraday effect not yet tested, **"intraday momentum"** (Gao, Han, Li and Zhou, 2018): the US market's early-session move predicts its move in the last half-hour. Here: the "first hour" runs from the 15:00 candle's close to the 16:00 candle's close (server time, so it includes the 16:30 cash open). Trade the 22:00 candle (the session's last hour) in that direction, open to close: one trade a day, one hour, no financing (`sweep/lasthour.py`). Test: one trade a day on the 22:00 candle, it finds a planted follow-through, no-peek. US500, US30, US2000, USTEC, index split (hourly data really starts in 2015).

| Version | Part | Trades | Avg a trade | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|---|
| every day | learn | 1557 | −0.019% | 0.80 | −2.30 | −4.5% |
| | check | 3207 | −0.012% | 0.92 | −0.92 | −4.2% |
| only after a first hour of 0.3%+ | learn | 273 | −0.029% | 0.79 | −1.00 | −4.2% |
| | check | 1209 | −0.004% | 0.98 | −0.12 | −1.2% |
| every day, no costs (diagnostic) | learn | 1557 | −0.004% | 0.95 | −0.59 | −3.6% |
| | check | 3207 | −0.007% | 0.95 | −0.51 | −3.3% |

**Verdict: rejected.** There's no edge even before costs. The published effect uses the exact cash-market half-hours (09:30–10:00 predicting 15:30–16:00 New York time), and it was strongest before 2013 and on volatile days. On CFD hourly candles that straddle the cash open, and in 2015–22, it doesn't show. Hourly candles may be too coarse to test it fairly. That's noted, not chased.

**Note:** the full test suite runs at the FINISH step, and every new test file has passed on its own as it was added.

## Part 4 round 30: 03:12

**Idea:** **"dual momentum" rotation between gold and the US500** (Antonacci's idea, on round 11's steady pair). Each month, hold only whichever of the two had the stronger past year per unit of volatility. It uses the existing code: `tsmom.py` mode `rank`, top 1, long only, rate-based financing, index split. Caveat: the pair itself was chosen with hindsight (round 11).

| Version | Part | Avg R (month) | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|
| rotate on 12 months | learn | +0.066 | 1.49 | +1.09 | +6.3% |
| | check | +0.019 | 1.09 | +0.26 | +0.7% |
| rotate on 6 months | learn | +0.109 | 1.81 | +1.61 | +13.3% |
| | check | +0.005 | 1.02 | +0.12 | −0.1% |
| hold both (round 25) | learn | +0.071 | 1.21 | +0.92 | +4.6% |
| | check | +0.070 | 1.18 | +0.65 | **+3.9%** |

**Verdict: rejected.** Rotation beats holding both in 2012–18, when US stocks led for years. In 2018–22 it loses to simply holding both, because it keeps switching into whichever just ran and misses the other's turn. Diversification beat timing again, as in rounds 4, 11, 23 and 27.

## Part 4 round 31: 03:27, what the bar takes

**Idea:** instead of a thirtieth strategy, measure **what clearing the bar requires**. In a Monte Carlo of 2,000 random return paths per Sharpe ratio, each 4.3 years long (the length of a check window), every path is sized to the biggest leverage whose worst drop stays at 25%. How often does it then make 18%+ a year? (Picking the leverage per path with hindsight makes these odds *optimistic*.)

| Sharpe ratio | Chance a 4.3-year window clears 18%/yr at ≤25% drop |
|---|---|
| 0.30 | 10% |
| 0.50 | 19% |
| 0.75 | 36% |
| 1.00 | 54% |
| 1.25 | 74% |
| **1.50** | **87%** |
| 2.00 | 98% |

**Best Sharpe ratios found tonight on unseen data** (t divided by √years):

| Strategy | Sharpe |
|---|---|
| gold + US500 hold, rate-based financing | about 0.31 |
| index dip + calm filter (withdrawn in round 24) | about 0.34 |
| US overnight drift | about 0.18 |
| swing on fresh markets, with financing | about 0.16 |

**What it means:**
- **Clearing the bar dependably needs a Sharpe of about 1.5**, and it's a coin flip at 1.0. That's the territory of the best hedge funds, which usually rely on data, speed or costs a retail CFD account doesn't have.
- **The best found tonight is about 0.3**, a factor of about 5 short.
- **It also explains round 13's locked look.** A Sharpe-0.3 holding clears the bar in about 1 window in 10 by pure luck. Gold + US500 in 2022–26 (+22% as modelled) was that window: a boom, not an edge. That's why the bar demands unseen data **and** the luck test, and why I didn't call it a win.

## Part 4 round 32: 03:42

**Idea:** widen the one effect tonight that was real before costs, the **overnight drift** (round 8, US only). If European indices also rise while their cash market is closed, holding both regions overnight adds independent bets, the only way to raise the Sharpe ratio (round 31). DE40's hourly volume confirms the European cash session: it jumps at 10:00 server time and stays busy until 18:00. Rule (`overnight.py`, unchanged code): buy at the close of the 18:00 candle, sell at the open of the next 10:00 candle, on DE40, Euro Stoxx 50, FRA40 and UK100, index split.

| Version | Part | Nights | Avg a night | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|
| EU overnight | learn | 574 | +0.021% | +0.72 | +3.2% |
| | check | 3213 | −0.027% | −1.06 | −4.6% |
| EU overnight, no costs | learn | 574 | +0.042% | +1.45 | +8.7% |
| | check | 3213 | −0.017% | −0.62 | −3.2% |

**Verdict: rejected.** Europe shows no dependable overnight drift. Learn is thin (574 nights, only DE40 has hourly data early on), and check is negative even before costs. The US overnight drift (round 8) stays a US-only effect, which matches the research: it's strongest in US stocks.

## Part 4 round 33: 03:57

**Idea:** is the US overnight drift (round 8) **stronger in uptrends**? Hold the night only when the index's *previous* daily close is above its 200-day average (`overnight.py`, `ma_n=200`; the previous close, because the current daily candle isn't finished at the 23:00 entry). If bear-market nights are the drag, this lifts the Sharpe without touching costs. Test: a jump is only known the day after, a fall switches it off, an empty allowed set means no trades. US indices, index split.

| Version | Part | Nights | Avg a night | PF | t (daily) | Yearly at 25% drop |
|---|---|---|---|---|---|---|
| overnight, uptrend only | learn | 1478 | +0.011% | 1.08 | +0.40 | +1.3% |
| | check | 2212 | +0.017% | 1.09 | +0.92 | **+7.3%** |
| overnight, every night (round 8) | learn | 1559 | +0.011% | 1.08 | +0.43 | +1.4% |
| | check | 3220 | +0.010% | 1.04 | +0.38 | +1.5% |

**Verdict: not kept by the strict rule.** It's a dead heat on learn (2015–18 was nearly all uptrend, so the filter seldom acted: 1478 nights against 1559), and much better on check (+7.3% against +1.5%), where it skipped the late-2018, 2020 and 2022 sell-offs. The rule needs better on both, and "the same" isn't better. I won't bend the rule for a result I like. Even if kept, a Sharpe of about 0.45 (t 0.92 over 4.3 years) at +7.3% a year is far from the bar's ~1.5.

**Noted as the best lead of the night** for a future forward test: US overnight drift with an uptrend filter. It's real before costs (round 8's short-the-night control lost at t −3.8), simple, never pays a full day's financing, and avoids bear markets.

## Part 4 round 34: 04:12, the overnight family's one locked look

**Idea:** the overnight family is finished developing, so it gets its **one locked look** (2022-08-05 to 2026-11-18). By the rules this is the family's **kept** version, plain overnight long on US indices (round 8), not round 33's uptrend version, which didn't pass the keep rule. Its size was chosen on learn + check (x0.92). It's recorded in `research_lab/locked_looks.json`.

| | Nights | Avg a night | PF | t (daily) | Yearly | Worst drop | Quarters |
|---|---|---|---|---|---|---|---|
| locked, at the size chosen beforehand | 4111 | +0.002% | 1.01 | +0.16 | **+0.3%** | 16% | −20.5, +46.8, −21.1, +4.9 |

**Verdict: does not clear the bar, and shows no edge.** The US overnight drift was real before costs on learn and check (round 8's short control lost at t −3.8). Out of sample, after spread and financing, it's worth about nothing. Either the drift has faded since 2022, or the 2023–25 financing ate it (this look used the flat 2%, which round 25 showed is generous). Either way, round 33's "best lead" built on a base that doesn't hold up on unseen data, so it drops from the forward-test shortlist.

**Locked looks so far:** gold + US500 hold (round 13: +16% at realistic financing, hindsight choice, a boom window) and US overnight (+0.3%). Neither clears the bar.

## Part 4 round 35: 04:27, no new idea on purpose

After 34 rounds every major family of systematic edge has been tested, and round 31 showed that each extra idea mainly adds another chance for luck to pass as skill. So this round adds **no new strategy**. Instead:
- **Live forward test (read-only check):** the process is running, MetaQuotes-Demo, $100, Algo Trading on, not halted. There are no orders, positions or closed trades, and no setups since the 19:42 start, as expected at about 2 a month. The state file was updated at 04:27.
- **The final summary** for 08:00 is drafted (each family's learn, check and locked result against the bar).

Further rounds until 08:00 will do the same: only clearly new, documented ideas, or checks. No variations of failed ones.
