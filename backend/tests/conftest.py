import pytest

from app.models.domain import Trade, TradeSide, TradeStatus


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: tests requiring services")


@pytest.fixture
def open_trade():
    return Trade(exchange="binance", strategy="RSI", symbol="BTCUSDT", side=TradeSide.buy, status=TradeStatus.open, entry_price=50_000, quantity=0.01)
