import pytest
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.models.domain import Trade, TradeSide, TradeStatus
from app.models.domain import StrategyConfig
from app.backtesting.historical import HistoricalBacktestEngine
from app.services.trading_engine import TradingEngine
from app.exchanges.base import MarketOrder
from app.strategies.base import Signal


def make_trade(entry: float = 100.0) -> Trade:
    return Trade(
        exchange="binance",
        strategy="RSI",
        symbol="BTCUSDT",
        side=TradeSide.buy,
        status=TradeStatus.open,
        entry_price=entry,
        quantity=1,
        metadata_json={},
    )


def quality_frame(prices: list[float] | None = None) -> pd.DataFrame:
    close = prices or [100 + index * 0.35 for index in range(80)]
    return pd.DataFrame(
        {
            "open": [value * 0.995 for value in close],
            "high": [value * 1.01 for value in close],
            "low": [value * 0.99 for value in close],
            "close": close,
            "volume": [1_000 + index for index in range(len(close))],
        }
    )


def repeating_quality_frame() -> pd.DataFrame:
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
                    "high": max(open_price, price) * 1.01,
                    "low": min(open_price, price) * 0.99,
                    "close": price,
                    "volume": 1_000 + cycle,
                }
            )
    for step in pattern:
        open_price = price
        price *= 1 + step / 100
        rows.append(
            {
                "open": open_price,
                "high": max(open_price, price) * 1.01,
                "low": min(open_price, price) * 0.99,
                "close": price,
                "volume": 1_500,
            }
        )
    return pd.DataFrame(rows)


def test_take_profit_closes_position():
    trade = make_trade()
    assert TradingEngine._exit_reason(trade, 104.0, {"take_profit_pct": 4}) == "take_profit"


def test_stop_loss_closes_position():
    trade = make_trade()
    assert TradingEngine._exit_reason(trade, 98.0, {"stop_loss_pct": 2, "trailing_stop_pct": 0}) == "stop_loss"


def test_trailing_stop_tracks_high_watermark():
    trade = make_trade()
    assert TradingEngine._exit_reason(trade, 103.0, {"trailing_stop_pct": 1, "take_profit_pct": 10}) is None
    assert trade.metadata_json["high_watermark"] == 103.0
    assert TradingEngine._exit_reason(trade, 101.9, {"trailing_stop_pct": 1, "take_profit_pct": 10}) == "trailing_stop"


def test_position_stays_open_inside_risk_limits():
    trade = make_trade()
    assert TradingEngine._exit_reason(trade, 101.0, {"stop_loss_pct": 2, "take_profit_pct": 4, "trailing_stop_pct": 1}) is None


def test_only_new_closed_candle_is_processed():
    assert not TradingEngine._is_new_candle(1_700_000_000_000, 1_700_000_000_000)
    assert not TradingEngine._is_new_candle(1_700_000_000_000, 1_699_999_999_999)
    assert TradingEngine._is_new_candle(1_700_000_000_000, 1_700_014_400_000)


def test_selection_rotation_detects_stale_pair():
    trade = make_trade()
    assert TradingEngine._is_allowed_pair(trade, {("RSI", "BTCUSDT")})
    assert not TradingEngine._is_allowed_pair(trade, {("Volume Spike", "ETHUSDT")})


def test_trade_research_context_keeps_selection_metrics():
    context = {
        "generated_at": "2026-07-02T11:33:21+00:00",
        "timeframe": "4h",
        "risk_profile": "wide",
        "selected": [{
            "symbol": "ETHUSDT",
            "strategy": "Volume Spike",
            "robustness_score": 52.4,
            "holdout_return_pct": 1.4,
            "regime_filter": {"name": "trend", "settings": {"require_uptrend": True}},
        }],
    }
    result = TradingEngine._trade_research_context(context, "ETHUSDT", "Volume Spike")
    assert result["generated_at"] == context["generated_at"]
    assert result["risk_profile"] == "wide"
    assert result["robustness_score"] == 52.4
    assert result["regime_filter"]["name"] == "trend"


def test_research_context_reads_bom_prefixed_report():
    context = TradingEngine._research_context()

    assert context["generated_at"]
    assert context["selected"]


def test_selected_research_is_pair_specific():
    context = {
        "selected": [
            {"symbol": "BTCUSDT", "strategy": "Volume Spike", "regime_filter": {"name": "trend"}},
            {"symbol": "ETHUSDT", "strategy": "Volume Spike", "regime_filter": {"name": "none"}},
        ]
    }
    selected = TradingEngine._selected_research(context, "BTCUSDT", "Volume Spike")
    assert selected["regime_filter"]["name"] == "trend"


def test_shadow_candidates_keep_profitable_non_selected_pairs():
    context = {
        "selected": [{"symbol": "BTCUSDT", "strategy": "Volume Spike"}],
        "opportunity_radar": [
            {"symbol": "BTCUSDT", "strategy": "Volume Spike", "opportunity_score": 100, "holdout_return_pct": 9},
            {"symbol": "TRXUSDT", "strategy": "Breakout", "opportunity_score": 61.9, "holdout_return_pct": 8.28},
            {"symbol": "ETHUSDT", "strategy": "Unknown", "opportunity_score": 80, "holdout_return_pct": 7},
            {"symbol": "DOGEUSDT", "strategy": "RSI", "opportunity_score": 30, "holdout_return_pct": -1},
        ],
    }
    candidates = TradingEngine._shadow_candidates(context)
    assert candidates == [
        {
            "symbol": "TRXUSDT",
            "strategy": "Breakout",
            "source": "opportunity_radar",
            "opportunity_score": 61.9,
            "holdout_return_pct": 8.28,
            "holdout_drawdown_pct": None,
            "holdout_profit_factor": None,
            "notes": [],
        }
    ]


def test_news_ranked_symbols_prioritize_positive_catalysts():
    context = {
        "symbols": [
            {"symbol": "ETHUSDT", "score": -3.0, "stance": "negative"},
            {"symbol": "BTCUSDT", "score": 4.0, "stance": "positive"},
        ]
    }
    assert TradingEngine._news_ranked_symbols(["ETHUSDT", "BTCUSDT"], context) == ["BTCUSDT", "ETHUSDT"]


def test_symbol_catalyst_defaults_to_neutral():
    catalyst = TradingEngine._symbol_catalyst({"symbols": []}, "BTCUSDT")
    assert catalyst["stance"] == "neutral"


def test_selection_guard_pauses_on_large_loss():
    trade = make_trade()
    trade.status = TradeStatus.closed
    trade.pnl = -151.0
    trade.metadata_json = {"research": {"generated_at": "research-v1"}}
    guard = TradingEngine._selection_guard([trade], {"generated_at": "research-v1"})
    assert guard["breached"]
    assert guard["reason"] == "max_selection_loss"
    assert guard["engine_action"] == "observe_only"


def test_selection_guard_recovers_on_loss_streak_without_large_drawdown():
    trades = []
    for index in range(3):
        trade = make_trade()
        trade.status = TradeStatus.closed
        trade.pnl = -2.0
        trade.closed_at = datetime.utcnow() + timedelta(minutes=index)
        trade.metadata_json = {"research": {"generated_at": "research-v1"}}
        trades.append(trade)

    guard = TradingEngine._selection_guard(trades, {"generated_at": "research-v1"})

    assert guard["breached"]
    assert guard["reason"] == "consecutive_selection_losses"
    assert guard["engine_action"] == "recover"


def test_selection_guard_ignores_old_research_trades():
    trade = make_trade()
    trade.status = TradeStatus.closed
    trade.pnl = -500.0
    trade.metadata_json = {"research": {"generated_at": "old-research"}}
    guard = TradingEngine._selection_guard([trade], {"generated_at": "research-v1"})
    assert not guard["breached"]
    assert guard["engine_action"] == "run"
    assert guard["tracked_trades"] == 0


def test_open_position_is_carried_into_new_selection_guard():
    trade = make_trade()
    trade.strategy = "Volume Spike"
    trade.symbol = "ETHUSDT"
    trade.metadata_json = {
        "research": {"generated_at": "old-research"},
        "unrealized_pnl": -151.0,
    }
    TradingEngine._track_open_selection(
        [trade],
        {("Volume Spike", "ETHUSDT")},
        "new-research",
    )
    guard = TradingEngine._selection_guard([trade], {"generated_at": "new-research"})
    assert guard["breached"]
    assert guard["reason"] == "max_selection_loss"
    assert trade.metadata_json["research"]["generated_at"] == "old-research"
    assert trade.metadata_json["selection_tracking_generation"] == "new-research"


def test_stale_pair_is_not_carried_into_new_selection_guard():
    trade = make_trade()
    TradingEngine._track_open_selection(
        [trade],
        {("Volume Spike", "ETHUSDT")},
        "new-research",
    )
    assert "selection_tracking_generation" not in trade.metadata_json


def test_active_symbols_include_research_strategy_symbols():
    configs = [
        StrategyConfig(name="Breakout", enabled=True, symbols=["TRXUSDT"], timeframe="4h", parameters={}),
        StrategyConfig(name="Volume Spike", enabled=True, symbols=["BTCUSDT", "ADAUSDT"], timeframe="4h", parameters={}),
    ]
    assert TradingEngine._active_symbols(["ETHUSDT"], configs) == ["ADAUSDT", "BTCUSDT", "ETHUSDT", "TRXUSDT"]


def test_scout_configs_add_only_strong_unselected_radar_symbols():
    context = {
        "selected": [{"symbol": "BTCUSDT", "strategy": "Volume Spike"}],
        "opportunity_radar": [
            {"symbol": "DOGEUSDT", "strategy": "Volume Spike", "opportunity_score": 108.7, "holdout_return_pct": 13.82, "holdout_profit_factor": 1.605},
            {"symbol": "XLMUSDT", "strategy": "Volume Spike", "opportunity_score": 70.01, "holdout_return_pct": 8.78, "holdout_profit_factor": 1.316},
            {"symbol": "ETHUSDT", "strategy": "Meme Momentum", "opportunity_score": 32.8, "holdout_return_pct": 0.52, "holdout_profit_factor": 1.092},
            {"symbol": "BTCUSDT", "strategy": "Scalping", "opportunity_score": 90, "holdout_return_pct": 12, "holdout_profit_factor": 1.5},
        ],
    }
    configs = [StrategyConfig(name="Volume Spike", enabled=True, symbols=["BTCUSDT"], timeframe="4h", parameters={})]
    scouts = TradingEngine._scout_strategy_configs(context, configs, ["ADAUSDT"])

    assert [(config.name, config.symbols, config.parameters["scout"]) for config in scouts] == [
        ("Volume Spike", ["DOGEUSDT"], True),
    ]


def test_scout_position_cap_is_smaller_than_core_cap():
    assert TradingEngine._position_notional_cap(500.0, scout_mode=False) == 500.0
    assert TradingEngine._position_notional_cap(500.0, scout_mode=True) == 125.0
    assert TradingEngine._position_notional_cap(500.0, scout_mode=True, dynamic_multiplier=1.2) == 150.0


def test_meme_sprint_uses_isolated_micro_capital_and_exposure():
    first_cap = TradingEngine._meme_sprint_cap(10_000, [], [], 500)
    open_trade = make_trade()
    open_trade.metadata_json = {"execution_style": "new_listing_meme_sprint", "entry_notional": 80.0}
    remaining_cap = TradingEngine._meme_sprint_cap(10_000, [open_trade], [], 500)

    assert first_cap == 25.0
    assert remaining_cap == 20.0


def test_meme_sprint_stops_after_two_consecutive_losses():
    losses = []
    for pnl in (-2.0, -1.0):
        trade = make_trade()
        trade.status = TradeStatus.closed
        trade.pnl = pnl
        trade.closed_at = datetime.utcnow()
        trade.metadata_json = {"execution_style": "new_listing_meme_sprint"}
        losses.append(trade)

    assert TradingEngine._meme_sprint_cap(10_000, [], losses, 500) == 0.0


def test_meme_sprint_recovers_most_of_stake_at_ten_percent_gain():
    trade = make_trade(entry=100.0)
    trade.quantity = 1.0
    trade.metadata_json = {
        "entry_fee": 0.1,
        "entry_notional": 100.0,
        "execution_style": "new_listing_meme_sprint",
        "risk": {"meme_sprint": True, "scout_position": True},
    }

    partial = TradingEngine._maybe_take_partial_profit(trade, 110.0, "paper")

    assert partial is not None
    assert partial["quantity"] == 0.90909091
    assert trade.quantity < 0.1


def test_meme_sprint_market_gate_rejects_illiquid_or_late_pump():
    frame = quality_frame([100 + index * 0.2 for index in range(80)])
    liquid = TradingEngine._meme_sprint_market_gate(
        frame,
        {
            "available": True,
            "spread_pct": 0.25,
            "quote_volume_24h": 900_000,
            "bid_depth_1pct": 40_000,
            "ask_depth_1pct": 35_000,
            "order_book_imbalance": 0.53,
        },
    )
    illiquid = TradingEngine._meme_sprint_market_gate(
        frame,
        {"available": True, "spread_pct": 2.0, "quote_volume_24h": 50_000},
    )

    assert liquid["allowed"] is True
    assert illiquid["allowed"] is False
    assert "spread_too_wide" in illiquid["reasons"]
    assert "insufficient_quote_volume" in illiquid["reasons"]


def test_meme_sprint_market_gate_requires_two_sided_depth():
    frame = quality_frame([100 + index * 0.2 for index in range(80)])
    result = TradingEngine._meme_sprint_market_gate(
        frame,
        {
            "available": True,
            "spread_pct": 0.2,
            "quote_volume_24h": 1_000_000,
            "bid_depth_1pct": 2_000,
            "ask_depth_1pct": 30_000,
            "order_book_imbalance": 0.06,
        },
    )

    assert result["allowed"] is False
    assert "insufficient_order_book_depth" in result["reasons"]
    assert "weak_bid_support" in result["reasons"]


def test_portfolio_correlation_detects_duplicate_market_risk():
    timestamps = [index * 14_400_000 for index in range(80)]
    candidate = quality_frame([100 + index + (index % 3) for index in range(80)])
    candidate["timestamp"] = timestamps
    matching = candidate.copy()
    matching["close"] = matching["close"] * 2
    trade = make_trade()
    trade.symbol = "ETHUSDT"

    result = TradingEngine._portfolio_correlation(
        "BTCUSDT",
        candidate,
        [trade],
        {"ETHUSDT": (matching, float(matching["close"].iloc[-1]), timestamps[-1])},
    )

    assert result["max_correlation"] > 0.99
    assert result["most_correlated_symbol"] == "ETHUSDT"


def test_dynamic_paper_slippage_increases_with_spread_and_thin_depth():
    rate = TradingEngine._execution_slippage_rate(
        500,
        {
            "available": True,
            "spread_pct": 0.8,
            "bid_depth_1pct": 8_000,
            "ask_depth_1pct": 10_000,
        },
    )
    fill = TradingEngine._paper_entry_fill(100, 500, rate)

    assert rate > TradingEngine.PAPER_SLIPPAGE_RATE
    assert fill["price"] > 100
    assert fill["slippage_pct"] == round(rate * 100, 4)


def test_execution_quality_gate_allows_deep_liquid_market():
    result = TradingEngine._execution_quality_gate(
        quality_frame([100 + index * 0.1 for index in range(80)]),
        {
            "available": True,
            "spread_pct": 0.04,
            "quote_volume_24h": 80_000_000,
            "bid_depth_1pct": 300_000,
            "ask_depth_1pct": 320_000,
            "order_book_imbalance": 0.52,
        },
    )

    assert result["allowed"] is True
    assert result["tier"] == "institutional"
    assert result["risk_multiplier"] == 1.0


def test_execution_quality_gate_blocks_wide_spread_and_thin_depth():
    result = TradingEngine._execution_quality_gate(
        quality_frame([100 + index * 0.1 for index in range(80)]),
        {
            "available": True,
            "spread_pct": 0.9,
            "quote_volume_24h": 600_000,
            "bid_depth_1pct": 6_000,
            "ask_depth_1pct": 8_000,
            "order_book_imbalance": 0.18,
        },
    )

    assert result["allowed"] is False
    assert "spread_too_wide" in result["reasons"]
    assert "insufficient_quote_volume" in result["reasons"]
    assert "insufficient_order_book_depth" in result["reasons"]
    assert "weak_bid_support" in result["reasons"]


def test_execution_quality_gate_is_stricter_for_scout_entries():
    normal = TradingEngine._execution_quality_gate(
        quality_frame([100 + index * 0.1 for index in range(80)]),
        {
            "available": True,
            "spread_pct": 0.22,
            "quote_volume_24h": 1_500_000,
            "bid_depth_1pct": 20_000,
            "ask_depth_1pct": 22_000,
            "order_book_imbalance": 0.5,
        },
    )
    scout = TradingEngine._execution_quality_gate(
        quality_frame([100 + index * 0.1 for index in range(80)]),
        {
            "available": True,
            "spread_pct": 0.22,
            "quote_volume_24h": 1_500_000,
            "bid_depth_1pct": 20_000,
            "ask_depth_1pct": 22_000,
            "order_book_imbalance": 0.5,
        },
        scout_mode=True,
    )

    assert normal["allowed"] is True
    assert scout["allowed"] is False
    assert "insufficient_quote_volume" in scout["reasons"]
    assert "insufficient_order_book_depth" in scout["reasons"]


def test_slippage_guard_blocks_costly_entries():
    result = TradingEngine._slippage_guard(0.004, scout_mode=False)

    assert result["allowed"] is False
    assert "estimated_slippage_too_high" in result["reasons"]


def test_learning_profile_reduces_repeated_losing_setup():
    trades = []
    for index, pnl in enumerate([-4.0, -3.0, 1.0]):
        trade = make_trade()
        trade.status = TradeStatus.closed
        trade.symbol = "DOGEUSDT"
        trade.strategy = "Volume Spike"
        trade.pnl = pnl
        trade.closed_at = datetime.utcnow() + timedelta(minutes=index)
        trade.metadata_json = {"execution_style": "fast_rotation_scout"}
        trades.append(trade)

    profile = TradingEngine._learning_profile(trades)
    learning = TradingEngine._learning_for_pair(profile, "DOGEUSDT", "Volume Spike", "fast_rotation_scout")

    assert not learning["allowed"]
    assert learning["status"] == "blocked_after_losses"
    assert learning["multiplier"] == 0.0


def test_dynamic_capital_multiplier_uses_quality_and_learning():
    multiplier = TradingEngine._dynamic_capital_multiplier(
        {"score": 0.86},
        {"holdout_profit_factor": 1.6},
        {"decision_stance": "positive"},
        {"multiplier": 1.18},
    )

    assert multiplier <= 1.05


def test_partial_profit_reduces_quantity_and_keeps_realized_pnl():
    trade = make_trade(entry=100.0)
    trade.quantity = 10
    trade.metadata_json = {
        "entry_fee": 1.0,
        "entry_notional": 1_000.0,
        "execution_style": "fast_rotation_scout",
        "risk": {"scout_position": True},
    }

    partial = TradingEngine._maybe_take_partial_profit(trade, 102.0, "paper")

    assert partial is not None
    assert trade.quantity == 5
    assert trade.metadata_json["partial_profit_taken"]
    assert trade.metadata_json["entry_notional"] == 500.0
    assert trade.metadata_json["partial_realized_pnl"] > 0


def test_scout_fallback_signal_accepts_fast_momentum_alignment():
    frame = quality_frame([100 + index * 0.3 for index in range(80)])
    frame.loc[79, "volume"] = 2_000

    signal = TradingEngine._scout_fallback_signal(frame, {"decision_stance": "neutral"})

    assert signal.action == "buy"
    assert signal.confidence >= 0.65


def test_trade_research_context_supports_opportunity_radar_scouts():
    context = {
        "generated_at": "research-v6",
        "timeframe": "4h",
        "risk_profile": "wide",
        "selected": [],
        "opportunity_radar": [
            {"symbol": "DOGEUSDT", "strategy": "Volume Spike", "opportunity_score": 108.7, "holdout_return_pct": 13.82, "holdout_profit_factor": 1.605}
        ],
    }
    research = TradingEngine._trade_research_context(context, "DOGEUSDT", "Volume Spike")

    assert research["source"] == "opportunity_radar"
    assert research["holdout_profit_factor"] == 1.605
    assert research["opportunity_score"] == 108.7


def test_selection_guard_pauses_mature_unprofitable_sample():
    trades = []
    for index in range(20):
        trade = make_trade()
        trade.status = TradeStatus.closed
        trade.pnl = 8.0 if index % 2 == 0 else -9.0
        trade.metadata_json = {"research": {"generated_at": "research-v2"}}
        trades.append(trade)
    guard = TradingEngine._selection_guard(trades, {"generated_at": "research-v2"})
    assert guard["breached"]
    assert guard["reason"] == "mature_sample_underperformance"
    assert guard["engine_action"] == "observe_only"


def test_selection_guard_keeps_profitable_mature_sample_running():
    trades = []
    for index in range(20):
        trade = make_trade()
        trade.status = TradeStatus.closed
        trade.pnl = 12.0 if index % 2 == 0 else -8.0
        trade.metadata_json = {"research": {"generated_at": "research-v3"}}
        trades.append(trade)
    guard = TradingEngine._selection_guard(trades, {"generated_at": "research-v3"})
    assert not guard["breached"]
    assert guard["engine_action"] == "run"


def test_selection_guard_pauses_when_portfolio_research_is_not_validated():
    context = {
        "generated_at": "research-v4",
        "portfolio_validation": {
            "eligible": False,
            "holdout": {"return_pct": -1.0, "drawdown": 8.0},
            "monte_carlo": {"confidence": "weak", "probability_positive_pct": 49.0},
        },
    }
    guard = TradingEngine._selection_guard([], context)
    assert guard["breached"]
    assert guard["reason"] == "portfolio_research_not_validated"
    assert guard["engine_action"] == "observe_only"
    assert guard["portfolio_research"]["confidence"] == "weak"


def test_selection_guard_accepts_strong_portfolio_research_without_live_losses():
    context = {
        "generated_at": "research-v5",
        "portfolio_validation": {
            "eligible": True,
            "holdout": {"return_pct": 8.5, "drawdown": 3.0},
            "monte_carlo": {"confidence": "strong", "probability_positive_pct": 95.0},
        },
    }
    guard = TradingEngine._selection_guard([], context)
    assert not guard["breached"]
    assert guard["engine_action"] == "run"
    assert guard["portfolio_research"]["reason"] == "ok"


def test_recovery_entry_blocks_recent_losing_pair():
    trade = make_trade()
    trade.status = TradeStatus.closed
    trade.strategy = "Volume Spike"
    trade.symbol = "BTCUSDT"
    trade.pnl = -2.0
    trade.closed_at = datetime.utcnow()
    trade.metadata_json = {"execution_style": "core_selection"}

    decision = TradingEngine._recovery_entry_decision(
        "BTCUSDT",
        "Volume Spike",
        "core_selection",
        {"holdout_profit_factor": 1.8},
        {"score": 0.9},
        {"status": "neutral"},
        TradingEngine._recent_loss_keys([trade]),
        scout_mode=False,
        meme_sprint=False,
    )

    assert not decision["allowed"]
    assert "same_pair_in_recent_loss_streak" in decision["reasons"]


def test_recovery_entry_allows_strong_core_setup():
    decision = TradingEngine._recovery_entry_decision(
        "TRXUSDT",
        "Breakout",
        "core_selection",
        {"holdout_profit_factor": 1.88},
        {"score": 0.84},
        {"status": "neutral"},
        set(),
        scout_mode=False,
        meme_sprint=False,
    )

    assert decision["allowed"]


def test_recovery_entry_blocks_old_moderate_backtest_edge():
    decision = TradingEngine._recovery_entry_decision(
        "ADAUSDT",
        "Volume Spike",
        "core_selection",
        {"holdout_profit_factor": 1.4},
        {"score": 0.84},
        {"status": "neutral"},
        set(),
        scout_mode=False,
        meme_sprint=False,
    )

    assert not decision["allowed"]
    assert "holdout_profit_factor_too_low" in decision["reasons"]


def test_paper_entry_fill_applies_slippage_and_fee():
    fill = TradingEngine._paper_entry_fill(100.0, 1_000.0)
    assert fill["price"] == 100.05
    assert fill["fee"] == 1.0
    assert round(fill["quantity"], 4) == 9.995


def test_paper_pnl_includes_entry_and_exit_fees():
    trade = make_trade(entry=100.05)
    trade.quantity = 9.995
    trade.metadata_json = {"entry_fee": 1.0}
    exit_fill = TradingEngine._paper_exit_fill(112.0, trade.quantity)
    pnl = TradingEngine._paper_pnl(trade, exit_fill["price"], exit_fill["fee"])
    assert round(exit_fill["price"], 3) == 111.944
    assert round(pnl, 2) == 116.76


def test_entry_quality_accepts_confirmed_momentum():
    signal = Signal("buy", 0.72, "test", {})
    quality = TradingEngine._entry_quality(signal, quality_frame(), {"decision_stance": "neutral"})
    assert quality["allowed"]
    assert quality["score"] >= 0.68


def test_entry_quality_uses_historical_market_memory():
    signal = Signal("buy", 0.72, "test", {})
    quality = TradingEngine._entry_quality(signal, repeating_quality_frame(), {"decision_stance": "neutral"})

    assert quality["allowed"]
    assert quality["market_memory"]["available"]
    assert quality["market_memory"]["stance"] == "bullish"


def test_entry_quality_blocks_weak_flat_signal():
    flat = [100.0 for _ in range(80)]
    signal = Signal("buy", 0.55, "test", {})
    quality = TradingEngine._entry_quality(signal, quality_frame(flat), {"decision_stance": "neutral"})
    assert not quality["allowed"]
    assert quality["reason"] == "weak_momentum_or_volatility"


def test_adaptive_exit_protects_gain_when_momentum_fades():
    prices = [100 + index * 0.2 for index in range(70)] + [116.0, 115.0, 113.0]
    trade = make_trade(entry=110.0)
    trade.metadata_json = {"high_watermark": 116.0}
    reason = TradingEngine._adaptive_exit_reason(
        trade,
        113.0,
        quality_frame(prices),
        {"stop_loss_pct": 10, "take_profit_pct": 20, "trailing_stop_pct": 0, "break_even_pct": 10},
        {"decision_stance": "neutral"},
    )
    assert reason == "profit_protection"


def test_adaptive_exit_closes_stagnant_trade_after_strategy_duration():
    trade = make_trade(entry=100.0)
    trade.strategy = "Volume Spike"
    trade.opened_at = datetime.utcnow() - timedelta(hours=80)
    trade.metadata_json = {"risk": {"max_hold_hours": 72}}
    reason = TradingEngine._adaptive_exit_reason(
        trade,
        100.2,
        quality_frame(),
        {"stop_loss_pct": 10, "take_profit_pct": 20, "trailing_stop_pct": 0, "break_even_pct": 10},
        {"decision_stance": "neutral"},
    )
    assert reason == "time_stop"


def test_historical_risk_prefers_stop_when_stop_and_target_trade_same_bar():
    engine = HistoricalBacktestEngine(slippage_pct=0)
    position = {"entry_price": 100.0, "high_watermark": 100.0}
    candle = __import__("pandas").Series({"low": 97.0, "high": 105.0})
    assert engine._risk_exit(position, candle) == (98.0, "stop_loss")


def test_historical_risk_uses_break_even_and_trailing_stop():
    engine = HistoricalBacktestEngine(slippage_pct=0)
    position = {"entry_price": 100.0, "high_watermark": 103.0}
    candle = __import__("pandas").Series({"low": 101.0, "high": 103.5})
    exit_price, reason = engine._risk_exit(position, candle)
    assert round(exit_price, 2) == 101.97
    assert reason == "trailing_stop"


class ExchangeThatMustNotReceivePaperOrders:
    name = "protected-exchange"

    async def place_order(self, order):
        raise AssertionError("paper order reached the private exchange API")


@pytest.mark.asyncio
async def test_paper_execution_never_calls_exchange_place_order():
    exchange = ExchangeThatMustNotReceivePaperOrders()
    order = MarketOrder(symbol="BTCUSDT", side="buy", quantity=0.01, price=50_000)
    response = await TradingEngine._execute_order(exchange, order, "paper")
    assert response["paper"]
    assert response["transmitted"] is False
    assert response["exchange"] == exchange.name


@pytest.mark.asyncio
async def test_unknown_execution_mode_is_rejected():
    exchange = ExchangeThatMustNotReceivePaperOrders()
    order = MarketOrder(symbol="BTCUSDT", side="buy", quantity=0.01)
    with pytest.raises(ValueError, match="Unsupported execution mode"):
        await TradingEngine._execute_order(exchange, order, "simulation-ish")


def valid_market_candles(now_ms: int, count: int = 3) -> list[dict]:
    return [
        {
            "timestamp": now_ms - (count - index - 1) * 4 * 60 * 60 * 1000,
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1_000 + index,
        }
        for index in range(count)
    ]


def test_market_data_accepts_fresh_consistent_candles():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    frame, price, closed_ms = TradingEngine._prepare_market_data(valid_market_candles(now_ms), now_ms=now_ms)
    assert len(frame) == 2
    assert price == 103.0
    assert closed_ms == now_ms - 4 * 60 * 60 * 1000


def test_market_data_normalizes_gateio_second_timestamps():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows = valid_market_candles(now_ms)
    for row in rows:
        row["timestamp"] //= 1000
    _, _, closed_ms = TradingEngine._prepare_market_data(rows, now_ms=now_ms)
    assert closed_ms > 1_000_000_000_000


def test_market_data_rejects_stale_or_inconsistent_candles():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale = valid_market_candles(now_ms - 12 * 60 * 60 * 1000)
    with pytest.raises(ValueError, match="stale market data"):
        TradingEngine._prepare_market_data(stale, now_ms=now_ms)
    invalid = valid_market_candles(now_ms)
    invalid[-1]["high"] = invalid[-1]["close"] - 1
    with pytest.raises(ValueError, match="candle high is inconsistent"):
        TradingEngine._prepare_market_data(invalid, now_ms=now_ms)
