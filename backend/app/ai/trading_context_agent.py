from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import BacktestRun, RiskConfig, StrategyConfig, Trade, TradeStatus
from app.services.agent_memory import AgentMemoryService
from app.services.aws_reports import AWSReportStore
from app.services.datahub_context import DataHubClient


class TradingContextAgent:
    """Agent that grounds recommendations in DataHub context and agent memory."""

    def __init__(
        self,
        *,
        datahub_client: DataHubClient | None = None,
        memory_service: AgentMemoryService | None = None,
    ) -> None:
        self.datahub = datahub_client or DataHubClient()
        self.memory = memory_service or AgentMemoryService()

    async def analyze(self, db: AsyncSession, symbol: str = "BTCUSDT", strategy: str | None = None) -> dict[str, Any]:
        strategies = (await db.execute(select(StrategyConfig).order_by(StrategyConfig.name))).scalars().all()
        risk = await db.scalar(select(RiskConfig).where(RiskConfig.name == "default"))
        selected_strategy = strategy or self._preferred_strategy(strategies, symbol)
        recent_trades = (
            await db.execute(select(Trade).where(Trade.symbol == symbol).order_by(Trade.opened_at.desc()).limit(30))
        ).scalars().all()
        backtests = (
            await db.execute(select(BacktestRun).where(BacktestRun.symbol == symbol).order_by(BacktestRun.created_at.desc()).limit(5))
        ).scalars().all()

        market_context = self._market_context(symbol, recent_trades, backtests)
        indicators = self._indicator_snapshot(symbol, selected_strategy, recent_trades, backtests)
        risk_snapshot = self._risk_snapshot(risk, recent_trades)
        query_payload = {
            "symbol": symbol,
            "strategy": selected_strategy,
            "market_context": market_context,
            "indicators": indicators,
            "risk_level": risk_snapshot["risk_level"],
            "decision": "pending",
            "reasoning": "pre-decision similarity search",
        }

        datahub_context = await self.datahub.get_trading_context(symbol)
        similar_memories = await self.memory.search_similar(query_payload, limit=5)
        recommendation = self._recommend(selected_strategy, market_context, indicators, risk_snapshot, similar_memories)
        memory_payload = {
            "symbol": symbol,
            "market_context": market_context,
            "indicators": indicators,
            "strategy": selected_strategy,
            "risk_level": recommendation["risk_level"],
            "decision": recommendation["decision"],
            "reasoning": recommendation["reasoning"],
            "created_at": datetime.utcnow().isoformat(),
        }
        saved_memory = await self.memory.remember(memory_payload)
        datahub_save = await self.datahub.record_decision(memory_payload)

        context_used = {
            "datahub": asdict(datahub_context),
            "cockroach_memory": [self._public_memory(memory) for memory in similar_memories],
            "risk": risk_snapshot,
            "backtests": [self._backtest_summary(row) for row in backtests],
            "recent_trade_sample": len(recent_trades),
        }
        response = {
            "generated_at": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "strategy": selected_strategy,
            "context_used": context_used,
            "recommendation": recommendation,
            "memory_saved": True,
            "memory_id": saved_memory.id,
            "datahub_record": datahub_save,
            "demo_mode": datahub_context.mode == "demo",
            "aws_ready": {
                "service": "Amazon ECS Fargate + Amazon S3 reports",
                "role": "Run the FastAPI container and store exported agent-context reports.",
                "local_demo_fallback": True,
            },
            "disclaimer": "Paper recommendation only. Real trading remains disabled unless explicitly enabled.",
        }
        response["aws_report"] = AWSReportStore().save_report(response)
        return response

    def _preferred_strategy(self, strategies: list[StrategyConfig], symbol: str) -> str:
        for strategy in strategies:
            if strategy.enabled and symbol in (strategy.symbols or []):
                return strategy.name
        for strategy in strategies:
            if strategy.enabled:
                return strategy.name
        return "Volume Spike"

    def _market_context(self, symbol: str, trades: list[Trade], backtests: list[BacktestRun]) -> dict[str, Any]:
        closed = [trade for trade in trades if trade.status == TradeStatus.closed]
        open_trades = [trade for trade in trades if trade.status == TradeStatus.open]
        pnl = round(sum(float(trade.pnl or 0) for trade in closed), 4)
        wins = len([trade for trade in closed if float(trade.pnl or 0) > 0])
        win_rate = round((wins / len(closed) * 100), 2) if closed else 0.0
        return {
            "symbol": symbol,
            "recent_closed_trades": len(closed),
            "recent_open_trades": len(open_trades),
            "recent_realized_pnl": pnl,
            "recent_win_rate": win_rate,
            "has_backtest_evidence": bool(backtests),
        }

    def _indicator_snapshot(self, symbol: str, strategy: str, trades: list[Trade], backtests: list[BacktestRun]) -> dict[str, Any]:
        best_backtest = self._backtest_summary(backtests[0]) if backtests else {}
        return {
            "symbol": symbol,
            "strategy": strategy,
            "evidence": "backtest_plus_live_trade_memory" if backtests else "live_trade_memory_only",
            "recent_trade_count": len(trades),
            "backtest_profit_factor": best_backtest.get("profit_factor", 0),
            "backtest_drawdown": best_backtest.get("drawdown", 0),
        }

    def _risk_snapshot(self, risk: RiskConfig | None, trades: list[Trade]) -> dict[str, Any]:
        recent_losses = 0
        for trade in trades:
            if trade.status == TradeStatus.closed and float(trade.pnl or 0) < 0:
                recent_losses += 1
            elif trade.status == TradeStatus.closed:
                break
        max_losses = risk.max_consecutive_losses if risk else 4
        risk_level = "high" if recent_losses >= max(1, max_losses - 1) else "medium"
        if risk and risk.max_capital_per_trade <= 100:
            risk_level = "low" if risk_level != "high" else "medium"
        return {
            "risk_level": risk_level,
            "max_capital_per_trade": risk.max_capital_per_trade if risk else 100.0,
            "stop_loss_pct": risk.stop_loss_pct if risk else 2.0,
            "take_profit_pct": risk.take_profit_pct if risk else 4.0,
            "max_positions": risk.max_positions if risk else 5,
            "recent_consecutive_losses": recent_losses,
            "hard_rule": "real trading disabled by default; risk guard cannot be bypassed by the agent",
        }

    def _recommend(
        self,
        strategy: str,
        market_context: dict[str, Any],
        indicators: dict[str, Any],
        risk: dict[str, Any],
        memories: list[Any],
    ) -> dict[str, Any]:
        positive_memory = any(memory.outcome and float(memory.outcome.get("pnl", 0)) > 0 for memory in memories)
        bad_memory = any(memory.decision in {"refuse", "sell"} or (memory.outcome and float(memory.outcome.get("pnl", 0)) < 0) for memory in memories)
        profit_factor = float(indicators.get("backtest_profit_factor") or 0)
        if risk["risk_level"] == "high" or bad_memory:
            decision = "wait"
            confidence = "guarded"
        elif profit_factor >= 1.15 or positive_memory:
            decision = "buy"
            confidence = "moderate"
        else:
            decision = "wait"
            confidence = "low"
        cited = [memory.id for memory in memories[:3]]
        reasoning = (
            f"DataHub context confirmed available assets, market sources, indicators and risk definitions. "
            f"CockroachDB memory returned {len(memories)} similar decision(s); cited memories: {', '.join(cited) or 'none'}. "
            f"Strategy {strategy} has profit factor {profit_factor}. Risk level is {risk['risk_level']}."
        )
        return {
            "decision": decision,
            "confidence": confidence,
            "risk_level": risk["risk_level"],
            "strategy": strategy,
            "reasoning": reasoning,
            "cited_memories": cited,
            "paper_only": True,
        }

    def _backtest_summary(self, row: BacktestRun) -> dict[str, Any]:
        metrics = row.metrics or {}
        return {
            "id": row.id,
            "strategy": row.strategy,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "profit": metrics.get("profit", 0),
            "drawdown": metrics.get("drawdown", 0),
            "profit_factor": metrics.get("profit_factor", 0),
            "sharpe_ratio": metrics.get("sharpe_ratio", 0),
        }

    def _public_memory(self, memory: Any) -> dict[str, Any]:
        return {
            "id": memory.id,
            "symbol": memory.symbol,
            "strategy": memory.strategy,
            "decision": memory.decision,
            "risk_level": memory.risk_level,
            "reasoning": memory.reasoning,
            "created_at": memory.created_at,
            "outcome": memory.outcome,
            "similarity": memory.similarity,
        }
