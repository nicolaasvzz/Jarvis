import numpy as np
import pandas as pd
import pytest

from investment_bot.data.synthetic import generate_ohlcv


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return generate_ohlcv("TEST", days=600, seed=7)


@pytest.fixture
def trending_up() -> pd.DataFrame:
    """Clean uptrend with mild noise — trend strategies must go long on this."""
    rng = np.random.default_rng(0)
    n = 300
    close = 100 * np.cumprod(1 + 0.004 + rng.normal(0, 0.002, n))
    index = pd.bdate_range(end="2026-01-01", periods=n)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": np.full(n, 1_000_000),
        },
        index=index,
    )


@pytest.fixture
def crashing() -> pd.DataFrame:
    """Steady decline steep enough to trip a 25% drawdown breaker."""
    n = 300
    close = 100 * np.cumprod(np.full(n, 0.995))
    index = pd.bdate_range(end="2026-01-01", periods=n)
    return pd.DataFrame(
        {
            "open": close * 1.001,
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": np.full(n, 1_000_000),
        },
        index=index,
    )
