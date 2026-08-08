import pytest

from app.ai.trading_context_agent import TradingContextAgent
from app.services.agent_memory import AgentMemoryService


def memory_payload(symbol: str = "BTCUSDT", decision: str = "wait") -> dict:
    return {
        "symbol": symbol,
        "market_context": {"recent_win_rate": 62, "regime": "favorable"},
        "indicators": {"rsi": 54, "atr": 1.8, "backtest_profit_factor": 1.24},
        "strategy": "Volume Spike",
        "risk_level": "medium",
        "decision": decision,
        "reasoning": "Momentum is positive but risk remains capped.",
        "outcome": {"pnl": 12.5},
    }


@pytest.mark.asyncio
async def test_agent_memory_writes_a_memory(tmp_path):
    service = AgentMemoryService(demo_mode=True, storage_path=tmp_path / "memory.json")
    saved = await service.remember(memory_payload())
    assert saved.id
    assert saved.symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_agent_memory_retrieves_a_memory(tmp_path):
    service = AgentMemoryService(demo_mode=True, storage_path=tmp_path / "memory.json")
    saved = await service.remember(memory_payload())
    loaded = await service.retrieve(saved.id)
    assert loaded is not None
    assert loaded.reasoning == saved.reasoning


@pytest.mark.asyncio
async def test_agent_memory_semantic_search_returns_similar_memory(tmp_path):
    service = AgentMemoryService(demo_mode=True, storage_path=tmp_path / "memory.json")
    btc = await service.remember(memory_payload("BTCUSDT", "buy"))
    await service.remember(memory_payload("ETHUSDT", "wait") | {"reasoning": "Different volatility profile."})
    results = await service.search_similar(memory_payload("BTCUSDT", "buy"), limit=1)
    assert results[0].id == btc.id
    assert results[0].similarity and results[0].similarity > 0


def test_trading_context_agent_decision_is_enriched_by_positive_memory():
    agent = TradingContextAgent()
    positive_memory = type(
        "Memory",
        (),
        {"id": "memory-1", "decision": "buy", "outcome": {"pnl": 18.0}},
    )()
    recommendation = agent._recommend(
        "Volume Spike",
        {"recent_win_rate": 60},
        {"backtest_profit_factor": 0.8},
        {"risk_level": "medium"},
        [positive_memory],
    )
    assert recommendation["decision"] == "buy"
    assert "memory-1" in recommendation["cited_memories"]
