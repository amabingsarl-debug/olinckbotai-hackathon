from app.models.domain import ShadowTrade, Trade, TradeSide, TradeStatus
from app.services import statistics
from app.services.statistics import _active_selection_metrics, _forward_validation, _promotion_readiness, _shadow_research_metrics


def report() -> dict:
    return {
        "period": {"years": 6},
        "selected": [
            {
                "symbol": "ETHUSDT",
                "strategy": "Volume Spike",
                "median_year_return_pct": 1.5,
                "holdout_return_pct": 1.4,
                "drawdown": 4.0,
                "trades": 300,
            }
        ],
    }


def test_forward_validation_waits_for_enough_live_trades():
    result = _forward_validation(report(), "2026-07-01T00:00:00+00:00", 0.0, 0.0, closed_trades=0, open_trades=0)
    assert result["status"] == "observation"
    assert result["min_trades_for_judgement"] >= 5


def test_forward_validation_flags_underperformance_after_enough_trades():
    result = _forward_validation(report(), "2026-01-01T00:00:00+00:00", -120.0, -1.2, closed_trades=20, open_trades=0)
    assert result["status"] == "underperforming"
    assert result["expected_median_return_pct"] == 1.5


def test_promotion_never_increases_risk_automatically():
    result = _promotion_readiness("2026-01-01T00:00:00+00:00", 30, 250.0, 1.4, 0.8, False)
    assert result["ready"]
    assert result["status"] == "ready_for_review"
    assert not result["risk_increase_automatic"]


def test_promotion_waits_for_time_and_sample_size():
    result = _promotion_readiness("2026-07-02T00:00:00+00:00", 3, 20.0, 1.4, 0.2, False)
    assert not result["ready"]
    assert result["status"] == "collecting_evidence"


def test_dashboard_keeps_open_carryover_position_in_active_selection(monkeypatch):
    selected = {"symbol": "ETHUSDT", "strategy": "Volume Spike", "holdout_metrics": {"profit_factor": 1.4}}
    monkeypatch.setattr(
        statistics,
        "_walk_forward_report",
        lambda: {"generated_at": "research-test", "methodology": {"allocation_pct": 10}, "selected": [selected]},
    )
    trade = Trade(
        exchange="binance",
        strategy=selected["strategy"],
        symbol=selected["symbol"],
        side=TradeSide.buy,
        status=TradeStatus.open,
        entry_price=100.0,
        quantity=5.0,
        metadata_json={
            "research": {"generated_at": "older-generation"},
            "entry_notional": 500.0,
            "unrealized_pnl": -12.5,
        },
    )
    metrics = _active_selection_metrics([trade])
    assert metrics["open_trades"] == 1
    assert metrics["open_pnl"] == -12.5
    assert metrics["open_exposure"] == 500.0


def test_shadow_research_metrics_rank_live_candidates_separately():
    closed = ShadowTrade(
        exchange="binance",
        strategy="Breakout",
        symbol="TRXUSDT",
        side=TradeSide.buy,
        status=TradeStatus.closed,
        entry_price=0.1,
        exit_price=0.11,
        quantity=10_000,
        pnl=98.0,
        metadata_json={
            "candidate": {
                "source": "opportunity_radar",
                "holdout_return_pct": 8.28,
                "holdout_profit_factor": 1.884,
                "opportunity_score": 61.9,
            }
        },
    )
    open_trade = ShadowTrade(
        exchange="binance",
        strategy="Breakout",
        symbol="TRXUSDT",
        side=TradeSide.buy,
        status=TradeStatus.open,
        entry_price=0.1,
        quantity=10_000,
        pnl=0.0,
        metadata_json={"unrealized_pnl": 12.0, "candidate": closed.metadata_json["candidate"]},
    )
    metrics = _shadow_research_metrics([closed, open_trade])
    assert metrics["total_pnl"] == 110.0
    assert metrics["open_trades"] == 1
    assert metrics["candidates"][0]["symbol"] == "TRXUSDT"
    assert metrics["candidates"][0]["total_pnl"] == 110.0
