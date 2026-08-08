import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtesting.historical import HistoricalBacktestEngine
from app.risk.quality import quality_allocation
from app.strategies.market_regime import entry_regime_mask


@dataclass(frozen=True)
class PortfolioCandidate:
    symbol: str
    strategy: str
    candles: list[dict]
    regime_filter: dict | None = None
    holdout_profit_factor: float | None = None


class PortfolioBacktestEngine:
    def __init__(
        self,
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
        allocation_pct: float = 10.0,
        max_portfolio_exposure_pct: float = 20.0,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 4.0,
        trailing_stop_pct: float = 1.0,
        break_even_pct: float = 1.5,
    ) -> None:
        self.single = HistoricalBacktestEngine(
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            allocation_pct=allocation_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
            break_even_pct=break_even_pct,
        )
        self.fee_rate = fee_pct / 100
        self.slippage_rate = slippage_pct / 100
        self.allocation_pct = allocation_pct
        self.max_portfolio_exposure_pct = max_portfolio_exposure_pct

    def run(self, candidates: list[PortfolioCandidate], initial_capital: float = 10_000) -> dict:
        prepared = [self._prepare(candidate) for candidate in candidates]
        timeline = sorted({timestamp for item in prepared for timestamp in item["frame"].index})
        capital = initial_capital
        positions: dict[str, dict] = {}
        trades: list[dict] = []
        equity_curve: list[dict] = []

        for timestamp in timeline:
            bars = {item["key"]: item["frame"].loc[timestamp] for item in prepared if timestamp in item["frame"].index}
            latest_prices = {key: float(bar["close"]) for key, bar in bars.items()}
            for key, position in list(positions.items()):
                bar = bars.get(key)
                if bar is None:
                    continue
                action = str(bar["action"])
                risk_exit = self.single._risk_exit(position, bar)
                is_last_bar = timestamp == position["last_timestamp"]
                if risk_exit or action == "sell" or is_last_bar:
                    raw_exit_price = risk_exit[0] if risk_exit else float(bar["close"])
                    exit_reason = risk_exit[1] if risk_exit else ("signal" if action == "sell" else "end_of_test")
                    exit_price = raw_exit_price * (1 - self.slippage_rate)
                    exit_fee = exit_price * position["quantity"] * self.fee_rate
                    pnl = (exit_price - position["entry_price"]) * position["quantity"] - exit_fee
                    capital += pnl
                    trades.append({
                        **position,
                        "exit_price": round(exit_price, 8),
                        "exit_time": int(timestamp),
                        "pnl": round(pnl - position["entry_fee"], 8),
                        "exit_reason": exit_reason,
                    })
                    del positions[key]
                else:
                    position["high_watermark"] = max(position["high_watermark"], float(bar["high"]))

            exposure = self._open_exposure(positions)
            equity = self._equity(capital, positions, latest_prices)
            for item in prepared:
                key = item["key"]
                bar = bars.get(key)
                if bar is None or key in positions or str(bar["action"]) != "buy":
                    continue
                exposure_limit = equity * self.max_portfolio_exposure_pct / 100
                remaining_exposure = max(0.0, exposure_limit - exposure)
                allocation = quality_allocation(item["holdout_profit_factor"])
                max_notional = equity * (self.allocation_pct * allocation["multiplier"]) / 100
                stake = min(max_notional, remaining_exposure, capital)
                if stake <= 0:
                    continue
                entry_price = float(bar["close"]) * (1 + self.slippage_rate)
                quantity = stake / entry_price
                entry_fee = stake * self.fee_rate
                capital -= entry_fee
                exposure += stake
                positions[key] = {
                    "symbol": item["symbol"],
                    "strategy": item["strategy"],
                    "entry_price": round(entry_price, 8),
                    "entry_time": int(timestamp),
                    "quantity": round(quantity, 8),
                    "entry_fee": round(entry_fee, 8),
                    "high_watermark": entry_price,
                    "last_timestamp": item["last_timestamp"],
                    "allocation_tier": allocation["tier"],
                }

            prices = {**latest_prices}
            equity_curve.append({"timestamp": int(timestamp), "equity": round(self._equity(capital, positions, prices), 2)})

        return {
            "metrics": self._metrics(initial_capital, equity_curve[-1]["equity"] if equity_curve else initial_capital, trades, equity_curve),
            "trades": trades,
            "equity_curve": equity_curve,
        }

    @staticmethod
    def monte_carlo_confidence(
        trades: list[dict],
        initial_capital: float = 10_000,
        simulations: int = 1_000,
        seed: int = 42,
    ) -> dict:
        pnls = np.array([float(trade.get("pnl", 0.0)) for trade in trades], dtype=float)
        if len(pnls) == 0:
            return {
                "simulations": 0,
                "probability_positive_pct": 0.0,
                "median_return_pct": 0.0,
                "p05_return_pct": 0.0,
                "p95_return_pct": 0.0,
                "p95_drawdown_pct": 0.0,
                "confidence": "insufficient",
            }
        rng = np.random.default_rng(seed)
        returns = []
        drawdowns = []
        for _ in range(simulations):
            sample = rng.choice(pnls, size=len(pnls), replace=True)
            equity = initial_capital + np.cumsum(sample)
            peak = np.maximum.accumulate(equity)
            drawdown = np.min(equity / peak - 1) if len(equity) else 0.0
            returns.append((equity[-1] / initial_capital - 1) * 100)
            drawdowns.append(abs(drawdown) * 100)
        probability_positive = float(np.mean(np.array(returns) > 0) * 100)
        p05_return = float(np.percentile(returns, 5))
        p95_drawdown = float(np.percentile(drawdowns, 95))
        if probability_positive >= 80 and p05_return > 0 and p95_drawdown <= 8:
            confidence = "strong"
        elif probability_positive >= 65 and p95_drawdown <= 10:
            confidence = "moderate"
        else:
            confidence = "weak"
        return {
            "simulations": simulations,
            "probability_positive_pct": round(probability_positive, 2),
            "median_return_pct": round(float(np.median(returns)), 2),
            "p05_return_pct": round(p05_return, 2),
            "p95_return_pct": round(float(np.percentile(returns, 95)), 2),
            "p95_drawdown_pct": round(p95_drawdown, 2),
            "confidence": confidence,
        }

    def _prepare(self, candidate: PortfolioCandidate) -> dict:
        frame = pd.DataFrame(candidate.candles).copy()
        for column in ["timestamp", "open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column])
        actions = self.single._actions(candidate.strategy, frame)
        if candidate.regime_filter:
            blocked = (actions == "buy") & ~entry_regime_mask(frame, candidate.regime_filter)
            actions.loc[blocked] = "hold"
        frame["action"] = actions
        frame["timestamp"] = frame["timestamp"].astype("int64")
        frame = frame.set_index("timestamp").sort_index()
        key = f"{candidate.symbol}:{candidate.strategy}"
        return {
            "key": key,
            "symbol": candidate.symbol,
            "strategy": candidate.strategy,
            "frame": frame,
            "last_timestamp": int(frame.index[-1]),
            "holdout_profit_factor": candidate.holdout_profit_factor,
        }

    @staticmethod
    def _open_exposure(positions: dict[str, dict]) -> float:
        return sum(float(position["entry_price"]) * float(position["quantity"]) for position in positions.values())

    @staticmethod
    def _equity(capital: float, positions: dict[str, dict], prices: dict[str, float]) -> float:
        equity = capital
        for key, position in positions.items():
            price = prices.get(key, float(position["entry_price"]))
            equity += (price - float(position["entry_price"])) * float(position["quantity"])
        return equity

    @staticmethod
    def _metrics(initial_capital: float, final_equity: float, trades: list[dict], equity_curve: list[dict]) -> dict:
        equity = pd.Series([point["equity"] for point in equity_curve], dtype=float)
        returns = equity.pct_change().dropna()
        downside = returns[returns < 0]
        drawdown = equity / equity.cummax() - 1
        wins = [float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0]
        losses = [float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0]
        annualizer = math.sqrt(6 * 365)
        sharpe = returns.mean() / returns.std() * annualizer if not returns.empty and returns.std() else 0
        sortino = returns.mean() / downside.std() * annualizer if not downside.empty and downside.std() else 0
        return {
            "profit": round(final_equity - initial_capital, 2),
            "return_pct": round((final_equity / initial_capital - 1) * 100, 2),
            "drawdown": round(abs(float(drawdown.min())) * 100, 2) if not drawdown.empty else 0,
            "trades": len(trades),
            "sharpe_ratio": round(float(sharpe), 3),
            "sortino_ratio": round(float(sortino), 3),
            "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else float(bool(wins)),
            "final_capital": round(final_equity, 2),
        }
