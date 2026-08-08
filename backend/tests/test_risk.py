from app.risk.manager import RiskManager
from app.models.domain import Trade, TradeSide, TradeStatus
from app.risk.quality import quality_allocation


def test_risk_allows_basic_trade():
    decision = RiskManager({"max_capital_per_trade": 100}).evaluate("BTCUSDT", 50_000, [], [], 10_000)
    assert decision.allowed
    assert decision.quantity > 0


def test_risk_blocks_duplicate_symbol(open_trade):
    decision = RiskManager({}).evaluate("BTCUSDT", 50_000, [open_trade], [], 10_000)
    assert not decision.allowed


def exposure_trade(symbol: str, notional: float) -> Trade:
    return Trade(
        exchange="binance",
        strategy="Volume Spike",
        symbol=symbol,
        side=TradeSide.buy,
        status=TradeStatus.open,
        entry_price=100.0,
        quantity=notional / 100.0,
        metadata_json={"entry_notional": notional},
    )


def test_risk_blocks_trade_when_portfolio_exposure_is_full():
    open_trades = [exposure_trade("BTCUSDT", 1_500), exposure_trade("ETHUSDT", 1_500)]
    decision = RiskManager({"max_capital_per_trade": 1_000}).evaluate("TRXUSDT", 1.0, open_trades, [], 10_000)
    assert not decision.allowed
    assert decision.reason == "maximum portfolio exposure reached"


def test_risk_reduces_position_to_remaining_portfolio_capacity():
    open_trades = [exposure_trade("BTCUSDT", 2_750)]
    decision = RiskManager({"max_capital_per_trade": 1_000}).evaluate("ETHUSDT", 2_000, open_trades, [], 10_000)
    assert decision.allowed
    assert decision.notional == 250.0


def test_quality_cap_reduces_weak_holdout_position_without_increasing_risk():
    allocation = quality_allocation(1.076)
    decision = RiskManager({"max_capital_per_trade": 1_000}).evaluate(
        "ETHUSDT", 2_000, [], [], 10_000, max_notional=1_000 * allocation["multiplier"]
    )
    assert allocation["tier"] == "quarantined"
    assert not allocation["risk_increase"]
    assert decision.notional == 250.0


def test_quality_allocation_keeps_strong_markets_at_existing_cap():
    assert quality_allocation(1.419)["multiplier"] == 0.75
    assert quality_allocation(1.631)["multiplier"] == 1.0


def test_default_portfolio_exposure_allows_controlled_paper_expansion():
    assert RiskManager.DEFAULT_MAX_PORTFOLIO_EXPOSURE_PCT == 30.0
