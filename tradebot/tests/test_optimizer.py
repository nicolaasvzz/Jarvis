from investment_bot.backtest.optimizer import Optimizer
from investment_bot.data.synthetic import generate_ohlcv
from investment_bot.strategies import Ensemble, SmaCross


def factory(params):
    return Ensemble(members=[SmaCross(**params)], threshold=0.05)


def test_grid_search_ranks_and_skips_invalid():
    data = {"AAA": generate_ohlcv("AAA", days=400, seed=5)}
    opt = Optimizer(
        strategy_factory=factory,
        # 20/10 is invalid (fast >= slow) and must be skipped, not crash.
        param_grid={"fast": [10, 20], "slow": [20, 60]},
        metric="sharpe",
    )
    results = opt.grid_search(data)
    assert 0 < len(results) < 4  # at least one invalid combo dropped
    scores = [r.metrics["sharpe"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_walk_forward_produces_folds():
    data = {"AAA": generate_ohlcv("AAA", days=700, seed=9)}
    opt = Optimizer(
        strategy_factory=factory,
        param_grid={"fast": [10], "slow": [40, 80]},
        metric="sharpe",
    )
    wf = opt.walk_forward(data, train_bars=300, test_bars=150)
    assert len(wf) >= 2
    assert {"fold", "params", "test_sharpe"}.issubset(wf.columns)
