import numpy as np
import pandas as pd

from app.ai.supervised_predictor import supervised_market_prediction


def market_frame(rows: int = 360) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    cycle = np.sin(np.arange(rows) / 5.0) * 0.006
    returns = 0.001 + cycle + rng.normal(0, 0.0015, rows)
    close = 100 * np.exp(np.cumsum(returns))
    volume = 1_000 * (1 + np.abs(cycle) * 20) + rng.normal(0, 20, rows)
    return pd.DataFrame(
        {
            "open": close * (1 - returns / 2),
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": volume,
        }
    )


def test_predictor_uses_purged_chronological_holdout():
    result = supervised_market_prediction(market_frame())

    assert result["available"] is True
    assert 0 <= result["probability_up"] <= 1
    assert result["split"] == {"train_pct": 60, "validation_pct": 20, "test_pct": 20, "purge_bars": 3}
    assert result["validation"]["samples"] > 0
    assert result["test"]["samples"] >= 20
    assert result["regularization"]["type"] == "l2"


def test_predictor_abstains_without_enough_history():
    result = supervised_market_prediction(market_frame(80))

    assert result == {"available": False, "reason": "insufficient_history"}
