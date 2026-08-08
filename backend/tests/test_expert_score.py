from datetime import datetime, timezone

import pandas as pd

from app.backtesting.expert_score import expert_conviction_score, expert_decision
from app.backtesting.macro_events import historical_event_exposure, macro_event_snapshot
from app.backtesting.market_memory import historical_market_memory
from app.backtesting.market_psychology import investor_behavior_snapshot


def test_investor_behavior_detects_parabolic_crowding():
    rows = []
    price = 100.0
    for index in range(120):
        step = 0.001 if index < 90 else 0.018
        price *= 1 + step
        rows.append(
            {
                "open": price * 0.99,
                "high": price * 1.01,
                "low": price * 0.985,
                "close": price,
                "volume": 1_000 if index < 110 else 8_000,
            }
        )
    snapshot = investor_behavior_snapshot(pd.DataFrame(rows))

    assert snapshot["available"]
    assert snapshot["risk_score"] > 0
    assert snapshot["flags"]["parabolic_acceleration"]


def test_macro_event_snapshot_detects_covid_crisis_window():
    snapshot = macro_event_snapshot(when=datetime(2020, 3, 15, tzinfo=timezone.utc))

    assert snapshot["stance"] == "risk_off"
    assert any(event["category"] == "pandemic" for event in snapshot["active_events"])


def test_historical_event_exposure_marks_covered_crises():
    rows = [
        {"timestamp": int(pd.Timestamp("2019-01-01", tz="UTC").timestamp() * 1000)},
        {"timestamp": int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000)},
    ]

    exposure = historical_event_exposure(rows)

    assert exposure["event_count"] >= 3


def test_historical_market_memory_finds_repeating_bullish_patterns():
    rows = []
    price = 100.0
    pattern = [1.0, 0.8, -0.35, 1.1, 0.7, 0.6, -0.2, 1.0, 0.5, 0.4, 0.8, 0.6]
    follow_through = [0.5, 0.7, 0.6, 0.4, 0.5, 0.4]
    for cycle in range(28):
        for step in pattern + follow_through:
            open_price = price
            price *= 1 + step / 100
            rows.append(
                {
                    "open": open_price,
                    "high": max(open_price, price) * 1.003,
                    "low": min(open_price, price) * 0.997,
                    "close": price,
                    "volume": 1_000 + cycle * 10,
                }
            )
    for step in pattern:
        open_price = price
        price *= 1 + step / 100
        rows.append(
            {
                "open": open_price,
                "high": max(open_price, price) * 1.003,
                "low": min(open_price, price) * 0.997,
                "close": price,
                "volume": 1_500,
            }
        )

    memory = historical_market_memory(pd.DataFrame(rows), lookback=12, horizon=6)

    assert memory["available"]
    assert memory["stance"] == "bullish"
    assert memory["positive_rate_pct"] > 70


def test_expert_score_rewards_holdout_edge_and_consistency():
    strong = {
        "eligible": True,
        "return_pct": 35.0,
        "drawdown": 6.0,
        "positive_years": 5,
        "holdout_positive_years": 2,
        "benchmark_return_pct": 2.0,
        "holdout_metrics": {
            "return_pct": 12.0,
            "drawdown": 3.0,
            "profit_factor": 1.6,
            "sharpe_ratio": 1.2,
            "trades": 80,
        },
    }
    weak = {
        "eligible": True,
        "return_pct": 20.0,
        "drawdown": 18.0,
        "positive_years": 3,
        "holdout_positive_years": 1,
        "benchmark_return_pct": 10.0,
        "holdout_metrics": {
            "return_pct": 3.0,
            "drawdown": 9.0,
            "profit_factor": 1.08,
            "sharpe_ratio": 0.3,
            "trades": 20,
        },
    }
    assert expert_conviction_score(strong) > expert_conviction_score(weak)


def test_expert_score_penalizes_behavioral_risk():
    base = {
        "eligible": True,
        "return_pct": 35.0,
        "drawdown": 6.0,
        "positive_years": 5,
        "holdout_positive_years": 2,
        "benchmark_return_pct": 2.0,
        "holdout_metrics": {
            "return_pct": 12.0,
            "drawdown": 3.0,
            "profit_factor": 1.6,
            "sharpe_ratio": 1.2,
            "trades": 80,
        },
    }
    calm = {**base, "behavioral_psychology": {"risk_score": 0, "stance": "normal"}}
    crowded = {**base, "behavioral_psychology": {"risk_score": 70, "stance": "risk_off"}}

    assert expert_conviction_score(calm) > expert_conviction_score(crowded)


def test_expert_decision_rejects_ineligible_rows():
    row = {"eligible": False, "expert_score": 100, "holdout_metrics": {"profit_factor": 2.0}}
    assert expert_decision(row) == "reject_until_retested"
