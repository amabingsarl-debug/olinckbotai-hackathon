from datetime import datetime, timedelta

from app.backtesting.engine import BacktestEngine
from app.backtesting.benchmark import buy_and_hold_portfolio, risk_adjusted_score
from app.backtesting.historical import HistoricalBacktestEngine
from app.backtesting.opportunity import opportunity_radar
from app.backtesting.portfolio import PortfolioBacktestEngine, PortfolioCandidate
from app.backtesting.regime_selection import choose_regime_filter, holdout_is_eligible, training_is_eligible, training_score
from app.strategies.catalog import STRATEGIES
from app.strategies.chart_patterns import curve_snapshot, meme_momentum_setup
from app.strategies.market_regime import entry_regime_mask, regime_snapshot


def candles(count: int = 120) -> list[dict]:
    base = datetime(2026, 1, 1)
    return [
        {
            "timestamp": base + timedelta(hours=i),
            "open": 100 + i * 0.5,
            "high": 101 + i * 0.5,
            "low": 99 + i * 0.5,
            "close": 100 + i * 0.6,
            "volume": 1000 + i,
        }
        for i in range(count)
    ]


def test_backtest_returns_metrics():
    result = BacktestEngine().run("EMA Cross", candles(), 10_000)
    assert "metrics" in result
    assert "drawdown" in result["metrics"]
    assert isinstance(result["equity_curve"], list)


def meme_breakout_candles(count: int = 90) -> list[dict]:
    rows = candles(count)
    for index, row in enumerate(rows):
        row["timestamp"] = int((datetime(2026, 1, 1) + timedelta(hours=4 * index)).timestamp() * 1000)
        row["open"] = 100 + index * 0.05
        row["close"] = row["open"] + 0.15
        row["high"] = row["close"] + 0.2
        row["low"] = row["open"] - 0.2
        row["volume"] = 1_000
    for index in range(count - 4, count):
        rows[index]["open"] = rows[index - 1]["close"]
        rows[index]["close"] = rows[index]["open"] * 1.018
        rows[index]["high"] = rows[index]["close"] * 1.006
        rows[index]["low"] = rows[index]["open"] * 0.998
        rows[index]["volume"] = 4_000 + index * 10
    return rows


def test_regime_filter_blocks_entries_below_long_term_trend():
    rows = candles(240)
    for index, row in enumerate(rows):
        row["close"] = 300 - index * 0.5
        row["open"] = row["close"] + 1
        row["high"] = row["open"] + 1
        row["low"] = row["close"] - 1
    frame = __import__("pandas").DataFrame(rows)
    mask = entry_regime_mask(frame, {"require_uptrend": True})
    assert not bool(mask.iloc[-1])
    assert not regime_snapshot(frame, {"require_uptrend": True})["allowed"]


def test_historical_regime_filter_is_optional_and_blocks_buys():
    rows = candles(240)
    for index, row in enumerate(rows):
        row["close"] = 300 - index * 0.5
        row["open"] = row["close"] - 1
        row["high"] = row["close"] + 1
        row["low"] = row["open"] - 1
        row["volume"] = 5_000 if index % 25 == 0 else 1_000
    engine = HistoricalBacktestEngine(slippage_pct=0)
    unfiltered = engine.run("Volume Spike", rows)
    filtered = engine.run("Volume Spike", rows, regime_filter={"require_uptrend": True})
    assert unfiltered["metrics"]["trades"] > 0
    assert filtered["metrics"]["trades"] == 0


def test_curve_snapshot_detects_breakout_and_volume():
    frame = __import__("pandas").DataFrame(meme_breakout_candles())
    snapshot = curve_snapshot(frame)
    assert snapshot["available"]
    assert snapshot["breakout"]
    assert snapshot["volume_ratio"] > 2


def test_meme_momentum_strategy_requires_fast_confirmed_breakout():
    frame = __import__("pandas").DataFrame(meme_breakout_candles())
    setup = meme_momentum_setup(frame)
    assert setup["allowed"]
    signal = STRATEGIES["Meme Momentum"].generate(frame, {})
    assert signal.action == "buy"

    flat = __import__("pandas").DataFrame(candles(90))
    assert not meme_momentum_setup(flat)["allowed"]


def test_historical_backtest_supports_meme_momentum():
    result = HistoricalBacktestEngine(slippage_pct=0).run("Meme Momentum", meme_breakout_candles())
    assert "return_pct" in result["metrics"]


def test_training_score_rewards_consistency_and_risk_control():
    stable = [
        {"return_pct": 2.0, "sharpe_ratio": 0.8, "drawdown": 4.0},
        {"return_pct": 2.5, "sharpe_ratio": 0.9, "drawdown": 5.0},
    ]
    unstable = [
        {"return_pct": 8.0, "sharpe_ratio": 1.5, "drawdown": 18.0},
        {"return_pct": -6.0, "sharpe_ratio": -0.5, "drawdown": 19.0},
    ]
    assert training_score(stable) > training_score(unstable)


def test_non_volume_strategies_keep_baseline_regime():
    engine = HistoricalBacktestEngine(slippage_pct=0)
    periods = [candles(600) for _ in range(4)]
    selection = choose_regime_filter(engine, "EMA Cross", periods)
    assert selection["chosen"]["name"] == "none"
    assert len(selection["comparisons"]) == 1


def test_training_eligibility_rejects_two_positive_years_out_of_four():
    selection = {"chosen": {"training_positive_years": 2, "training_return_pct": 9.0}}
    assert not training_is_eligible(selection)
    selection["chosen"]["training_positive_years"] = 3
    assert training_is_eligible(selection)


def test_holdout_gate_requires_enough_profitable_low_drawdown_trades():
    robust = {"trades": 60, "return_pct": 2.5, "profit_factor": 1.3, "drawdown": 4.0}
    assert holdout_is_eligible(robust)
    assert not holdout_is_eligible({**robust, "trades": 29})
    assert not holdout_is_eligible({**robust, "return_pct": -0.1})
    assert not holdout_is_eligible({**robust, "profit_factor": 1.049})
    assert not holdout_is_eligible({**robust, "drawdown": 10.01})


def test_portfolio_backtest_combines_candidates_and_caps_exposure():
    rows = candles(260)
    for index, row in enumerate(rows):
        row["timestamp"] = int((datetime(2026, 1, 1) + timedelta(hours=4 * index)).timestamp() * 1000)
        row["open"] = 100 + index * 0.1
        row["close"] = row["open"] + (1.0 if index % 30 == 0 else 0.2)
        row["high"] = row["close"] + 1
        row["low"] = row["open"] - 1
        row["volume"] = 5_000 if index % 30 == 0 else 1_000
    engine = PortfolioBacktestEngine(slippage_pct=0, max_portfolio_exposure_pct=10)
    result = engine.run([
        PortfolioCandidate("AAAUSDT", "Volume Spike", rows, holdout_profit_factor=1.5),
        PortfolioCandidate("BBBUSDT", "Volume Spike", rows, holdout_profit_factor=1.0),
    ])
    assert result["metrics"]["trades"] > 0
    assert "profit_factor" in result["metrics"]
    stakes = [trade["entry_price"] * trade["quantity"] for trade in result["trades"]]
    assert max(stakes) <= 1_001


def test_portfolio_monte_carlo_confidence_scores_trade_distribution():
    profitable = [{"pnl": 12.0}, {"pnl": 8.0}, {"pnl": -3.0}, {"pnl": 10.0}, {"pnl": -2.0}] * 20
    confidence = PortfolioBacktestEngine.monte_carlo_confidence(profitable, simulations=200)
    assert confidence["probability_positive_pct"] > 95
    assert confidence["confidence"] in {"moderate", "strong"}

    empty = PortfolioBacktestEngine.monte_carlo_confidence([])
    assert empty["confidence"] == "insufficient"


def test_buy_and_hold_portfolio_benchmark_reports_risk_adjusted_score():
    rows = []
    for index in range(10):
        rows.append({
            "timestamp": index,
            "open": 100,
            "high": 100 + index,
            "low": 100,
            "close": 100 + index,
            "volume": 1000,
        })
    selected = [{"symbol": "AAAUSDT"}, {"symbol": "BBBUSDT"}]
    datasets = {
        "AAAUSDT": {"candles": rows},
        "BBBUSDT": {"candles": rows},
    }
    benchmark = buy_and_hold_portfolio(selected, datasets, start_ms=0)
    assert benchmark["return_pct"] > 0
    assert benchmark["drawdown"] == 0
    assert risk_adjusted_score(benchmark["return_pct"], benchmark["drawdown"]) > 0


def test_opportunity_radar_keeps_profitable_duplicates_on_watch_only():
    evaluations = [
        {
            "symbol": "TRXUSDT",
            "strategy": "EMA Cross",
            "eligible": True,
            "return_pct": 12.0,
            "drawdown": 5.0,
            "profit_factor": 1.3,
            "robustness_score": 60.0,
            "positive_years": 5,
            "holdout_positive_years": 2,
            "holdout_metrics": {"return_pct": 5.0, "drawdown": 2.0, "profit_factor": 1.4},
        },
        {
            "symbol": "TRXUSDT",
            "strategy": "Swing Trading",
            "eligible": True,
            "return_pct": 18.0,
            "drawdown": 7.0,
            "profit_factor": 1.2,
            "robustness_score": 58.0,
            "positive_years": 5,
            "holdout_positive_years": 2,
            "holdout_metrics": {"return_pct": 6.0, "drawdown": 3.0, "profit_factor": 1.3},
        },
    ]
    radar = opportunity_radar(evaluations, [evaluations[0]])
    assert radar[0]["strategy"] == "Swing Trading"
    assert radar[0]["activation"] == "paper_watch_only"
    assert "same_asset_as_active_selection" in radar[0]["notes"]
