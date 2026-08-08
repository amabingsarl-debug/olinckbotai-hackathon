import math

import numpy as np
import pandas as pd

from app.strategies.catalog import STRATEGIES


class BacktestEngine:
    def run(self, strategy_name: str, candles: list[dict], initial_capital: float, parameters: dict | None = None) -> dict:
        if strategy_name not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        df = pd.DataFrame(candles)
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column])
        strategy = STRATEGIES[strategy_name]
        capital = initial_capital
        position: dict | None = None
        trades: list[dict] = []
        equity_curve: list[dict] = []

        for index in range(60, len(df)):
            window = df.iloc[: index + 1]
            price = float(window["close"].iloc[-1])
            signal = strategy.generate(window, parameters)
            if position and signal.action == "sell":
                pnl = (price - position["entry_price"]) * position["quantity"]
                capital += pnl
                trades.append({**position, "exit_price": price, "pnl": pnl, "closed_index": index})
                position = None
            elif not position and signal.action == "buy":
                stake = min(capital * 0.1, capital)
                quantity = stake / price
                position = {"entry_price": price, "quantity": quantity, "opened_index": index, "reason": signal.reason}
            mark_to_market = capital
            if position:
                mark_to_market += (price - position["entry_price"]) * position["quantity"]
            equity_curve.append({"index": index, "equity": round(mark_to_market, 2)})

        metrics = self._metrics(initial_capital, capital, trades, equity_curve)
        return {"metrics": metrics, "trades": trades, "equity_curve": equity_curve}

    @staticmethod
    def _metrics(initial_capital: float, final_capital: float, trades: list[dict], equity_curve: list[dict]) -> dict:
        wins = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in trades if t["pnl"] < 0]
        returns = np.diff([p["equity"] for p in equity_curve]) if len(equity_curve) > 2 else np.array([0])
        downside = returns[returns < 0]
        peak = -math.inf
        max_drawdown = 0.0
        for point in equity_curve:
            peak = max(peak, point["equity"])
            if peak:
                max_drawdown = min(max_drawdown, (point["equity"] - peak) / peak)
        return {
            "profit": round(final_capital - initial_capital, 2),
            "drawdown": round(abs(max_drawdown) * 100, 2),
            "trades": len(trades),
            "sharpe_ratio": round(float(returns.mean() / returns.std()), 3) if returns.std() else 0.0,
            "sortino_ratio": round(float(returns.mean() / downside.std()), 3) if downside.size and downside.std() else 0.0,
            "win_rate": round((len(wins) / len(trades)) * 100, 2) if trades else 0.0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else float(len(wins) > 0),
        }
