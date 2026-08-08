import pytest

from app.services.datahub_context import DataHubClient


@pytest.mark.asyncio
async def test_datahub_demo_context_contains_trading_metadata():
    client = DataHubClient(demo_mode=True)
    context = await client.get_trading_context("BTCUSDT")
    assert context.mode == "demo"
    assert context.assets
    assert context.indicators
    assert context.risk_metrics


@pytest.mark.asyncio
async def test_datahub_demo_records_decision_without_secrets():
    client = DataHubClient(demo_mode=True)
    result = await client.record_decision({"symbol": "BTCUSDT", "decision": "wait", "risk_level": "low"})
    assert result["saved"]
    assert result["mode"] == "demo"
    assert "token" not in str(result).lower()
