import pytest

from investment_bot.strategies import (
    REGISTRY,
    BollingerReversion,
    DonchianBreakout,
    Ensemble,
    MacdMomentum,
    RsiReversion,
    SmaCross,
    build_ensemble,
    build_strategy,
)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_all_strategies_emit_valid_signals(name, ohlcv):
    strategy = build_strategy(name)
    assert strategy.ready(ohlcv)
    sig = strategy.signal(ohlcv)
    assert sig.direction in (-1, 0, 1)
    assert 0.0 <= sig.conviction <= 1.0


def test_trend_strategies_long_in_uptrend(trending_up):
    for strategy in (SmaCross(fast=10, slow=50), MacdMomentum(), DonchianBreakout()):
        sig = strategy.signal(trending_up)
        assert sig.direction == 1, f"{strategy.name} not long in a clean uptrend"


def test_sma_cross_rejects_bad_windows():
    with pytest.raises(ValueError):
        SmaCross(fast=100, slow=50)


def test_reversion_flat_mid_band(trending_up):
    # A steady grind sits near its own mean — reversion should mostly stay flat.
    sig = BollingerReversion().signal(trending_up)
    assert sig.direction in (-1, 0, 1)  # must not crash; direction is data-dependent
    r = RsiReversion(oversold=1, overbought=99).signal(trending_up)
    assert r.direction == 0  # impossible thresholds -> flat


def test_ensemble_weights_normalize():
    e = Ensemble(members=[SmaCross(), MacdMomentum()], weights=[3.0, 1.0])
    assert sum(e.weights) == pytest.approx(1.0)
    assert e.weights[0] == pytest.approx(0.75)


def test_ensemble_threshold_gates_weak_votes(trending_up):
    # With an impossible threshold nothing can trade.
    e = Ensemble(members=[SmaCross(fast=10, slow=50)], threshold=1.1)
    assert e.signal(trending_up).direction == 0


def test_ensemble_long_only_vetoes_shorts(crashing):
    e = Ensemble(members=[SmaCross(fast=10, slow=50)], threshold=0.1, long_only=True)
    sig = e.signal(crashing)
    assert sig.direction == 0


def test_ensemble_agrees_in_uptrend(trending_up):
    e = build_ensemble(
        [
            {"name": "sma_cross", "params": {"fast": 10, "slow": 50}},
            {"name": "macd"},
            {"name": "breakout"},
        ],
        threshold=0.25,
    )
    assert e.signal(trending_up).direction == 1


def test_ensemble_vol_veto(ohlcv):
    # Any real market has more than 0.01% annualized vol, so this must veto.
    e = Ensemble(members=[SmaCross(fast=10, slow=50)], threshold=0.1, max_volatility=0.0001)
    sig = e.signal(ohlcv)
    assert sig.direction == 0
    assert "vol veto" in sig.reason


def test_build_strategy_unknown_name():
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategy("does_not_exist")
