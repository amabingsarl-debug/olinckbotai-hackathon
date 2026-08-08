from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.domain import Trade, TradeStatus


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    quantity: float = 0.0
    notional: float = 0.0


class RiskManager:
    DEFAULT_MAX_PORTFOLIO_EXPOSURE_PCT = 30.0

    def __init__(self, config: dict) -> None:
        self.config = {
            "max_capital_per_trade": 100.0,
            "risk_per_trade_pct": 0.6,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 4.0,
            "max_positions": 5,
            "max_portfolio_exposure_pct": self.DEFAULT_MAX_PORTFOLIO_EXPOSURE_PCT,
            "max_daily_loss_pct": 3.0,
            "max_weekly_loss_pct": 7.0,
            "max_monthly_loss_pct": 12.0,
            "max_consecutive_losses": 4,
            **config,
        }

    def evaluate(
        self,
        symbol: str,
        price: float,
        open_trades: list[Trade],
        closed_trades: list[Trade],
        capital: float,
        max_notional: float | None = None,
    ) -> RiskDecision:
        if any(t.symbol == symbol and t.status == TradeStatus.open for t in open_trades):
            return RiskDecision(False, "duplicate open position protection")
        if len(open_trades) >= self.config["max_positions"]:
            return RiskDecision(False, "maximum simultaneous positions reached")
        if self._loss_pct(closed_trades, days=1, capital=capital) <= -self.config["max_daily_loss_pct"]:
            return RiskDecision(False, "daily loss limit reached")
        if self._loss_pct(closed_trades, days=7, capital=capital) <= -self.config["max_weekly_loss_pct"]:
            return RiskDecision(False, "weekly loss limit reached")
        if self._loss_pct(closed_trades, days=30, capital=capital) <= -self.config["max_monthly_loss_pct"]:
            return RiskDecision(False, "monthly loss limit reached")
        if self._consecutive_losses(closed_trades) >= self.config["max_consecutive_losses"]:
            return RiskDecision(False, "consecutive loss limit reached")
        current_exposure = self._open_exposure(open_trades)
        max_exposure = capital * max(0.0, float(self.config["max_portfolio_exposure_pct"])) / 100
        remaining_capacity = max(0.0, max_exposure - current_exposure)
        if remaining_capacity < 1.0:
            return RiskDecision(False, "maximum portfolio exposure reached")
        quality_cap = float("inf") if max_notional is None else max(0.0, max_notional)
        notional = min(self._position_notional(capital), remaining_capacity, quality_cap)
        quantity = round(notional / price, 8)
        if quantity <= 0:
            return RiskDecision(False, "position size too small")
        return RiskDecision(True, "risk accepted", quantity, round(notional, 2))

    @staticmethod
    def _open_exposure(open_trades: list[Trade]) -> float:
        return sum(
            float((trade.metadata_json or {}).get("entry_notional", trade.entry_price * trade.quantity))
            for trade in open_trades
            if trade.status == TradeStatus.open
        )

    def _position_notional(self, capital: float) -> float:
        max_capital = max(0.0, float(self.config.get("max_capital_per_trade", 100.0)))
        stop_loss_pct = max(0.1, float(self.config.get("stop_loss_pct", 2.0)))
        risk_per_trade_pct = max(0.0, float(self.config.get("risk_per_trade_pct", 0.6)))
        risk_budget = capital * (risk_per_trade_pct / 100)
        risk_based_notional = risk_budget / (stop_loss_pct / 100)
        return round(max(0.0, min(max_capital, capital, risk_based_notional)), 8)

    @staticmethod
    def _loss_pct(trades: list[Trade], days: int, capital: float) -> float:
        cutoff = datetime.utcnow() - timedelta(days=days)
        pnl = sum(t.pnl for t in trades if t.closed_at and t.closed_at >= cutoff)
        return (pnl / capital) * 100 if capital else 0.0

    @staticmethod
    def _consecutive_losses(trades: list[Trade]) -> int:
        losses = 0
        for trade in sorted(trades, key=lambda t: t.closed_at or datetime.min, reverse=True):
            if trade.pnl < 0:
                losses += 1
            else:
                break
        return losses
