import pandas as pd
import pytest

from investment_bot.portfolio import Portfolio

TS = pd.Timestamp("2026-01-05")


@pytest.fixture
def portfolio() -> Portfolio:
    return Portfolio(starting_cash=100_000)


def test_open_long(portfolio):
    portfolio.apply_fill("AAPL", 100, 50.0, 1.0, TS)
    assert portfolio.cash == 100_000 - 5_000 - 1.0
    pos = portfolio.positions["AAPL"]
    assert pos.qty == 100 and pos.avg_price == 50.0


def test_scale_in_blends_average(portfolio):
    portfolio.apply_fill("AAPL", 100, 50.0, 0.0, TS)
    portfolio.apply_fill("AAPL", 100, 60.0, 0.0, TS)
    pos = portfolio.positions["AAPL"]
    assert pos.qty == 200
    assert pos.avg_price == pytest.approx(55.0)
    assert not portfolio.trades  # no round trip yet


def test_close_long_realizes_pnl(portfolio):
    portfolio.apply_fill("AAPL", 100, 50.0, 0.0, TS)
    portfolio.apply_fill("AAPL", -100, 55.0, 0.0, TS + pd.Timedelta(days=5))
    assert "AAPL" not in portfolio.positions
    assert len(portfolio.trades) == 1
    trade = portfolio.trades[0]
    assert trade.pnl == pytest.approx(500.0)
    assert trade.direction == 1
    assert portfolio.cash == pytest.approx(100_500.0)


def test_partial_close(portfolio):
    portfolio.apply_fill("AAPL", 100, 50.0, 0.0, TS)
    portfolio.apply_fill("AAPL", -40, 60.0, 0.0, TS)
    assert portfolio.positions["AAPL"].qty == 60
    assert portfolio.trades[0].pnl == pytest.approx(400.0)


def test_flip_long_to_short(portfolio):
    portfolio.apply_fill("AAPL", 100, 50.0, 0.0, TS)
    portfolio.apply_fill("AAPL", -250, 40.0, 0.0, TS)
    pos = portfolio.positions["AAPL"]
    assert pos.qty == -150
    assert pos.avg_price == 40.0  # fresh short opened at the flip price
    assert portfolio.trades[0].pnl == pytest.approx(-1000.0)  # long lost 10/share


def test_short_round_trip(portfolio):
    portfolio.apply_fill("TSLA", -50, 200.0, 0.0, TS)
    assert portfolio.cash == pytest.approx(110_000.0)
    portfolio.apply_fill("TSLA", 50, 180.0, 0.0, TS)
    assert portfolio.trades[0].pnl == pytest.approx(1000.0)
    assert portfolio.cash == pytest.approx(101_000.0)


def test_mark_and_equity(portfolio):
    portfolio.apply_fill("AAPL", 100, 50.0, 0.0, TS)
    portfolio.mark({"AAPL": 60.0}, TS)
    assert portfolio.equity == pytest.approx(95_000 + 6_000)
    series = portfolio.equity_series()
    assert list(series.values) == [pytest.approx(101_000.0)]


def test_gross_exposure_counts_shorts(portfolio):
    portfolio.apply_fill("AAPL", 100, 50.0, 0.0, TS)
    portfolio.apply_fill("TSLA", -10, 100.0, 0.0, TS)
    portfolio.mark({"AAPL": 50.0, "TSLA": 100.0}, TS)
    assert portfolio.gross_exposure == pytest.approx(6_000.0)
