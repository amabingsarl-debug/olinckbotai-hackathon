import math
from datetime import datetime, timezone

import pandas as pd

from app.strategies.indicators import atr, ema, macd, rsi, vwap
from app.strategies.chart_patterns import meme_momentum_actions
from app.strategies.market_regime import entry_regime_mask


class HistoricalBacktestEngine:
    def __init__(
        self,
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
        allocation_pct: float = 10.0,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 4.0,
        trailing_stop_pct: float = 1.0,
        break_even_pct: float = 1.5,
    ) -> None:
        self.fee_rate = fee_pct / 100
        self.slippage_rate = slippage_pct / 100
        self.allocation_rate = allocation_pct / 100
        self.stop_loss_rate = stop_loss_pct / 100
        self.take_profit_rate = take_profit_pct / 100
        self.trailing_stop_rate = trailing_stop_pct / 100
        self.break_even_rate = break_even_pct / 100

    def run(
        self,
        strategy: str,
        candles: list[dict],
        initial_capital: float = 10_000,
        regime_filter: dict | None = None,
    ) -> dict:
        df = pd.DataFrame(candles).copy()
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column])
        actions = self._actions(strategy, df)
        if regime_filter:
            blocked_entries = (actions == "buy") & ~entry_regime_mask(df, regime_filter)
            actions.loc[blocked_entries] = "hold"
        capital = initial_capital
        position = None
        trades: list[dict] = []
        equity_curve: list[dict] = []

        for index in range(len(df)):
            price = float(df["close"].iloc[index])
            action = actions.iloc[index]
            timestamp = self._timestamp_ms(df["timestamp"].iloc[index])
            closed_this_bar = False
            risk_exit = self._risk_exit(position, df.iloc[index]) if position else None
            if position and (risk_exit or action == "sell" or index == len(df) - 1):
                raw_exit_price = risk_exit[0] if risk_exit else price
                exit_reason = risk_exit[1] if risk_exit else ("signal" if action == "sell" else "end_of_test")
                exit_price = raw_exit_price * (1 - self.slippage_rate)
                exit_fee = exit_price * position["quantity"] * self.fee_rate
                pnl = (exit_price - position["entry_price"]) * position["quantity"] - exit_fee
                capital += pnl
                trades.append({
                    **position,
                    "exit_price": round(exit_price, 8),
                    "exit_time": timestamp,
                    "pnl": round(pnl - position["entry_fee"], 8),
                    "exit_reason": exit_reason,
                })
                position = None
                closed_this_bar = True
            elif position:
                position["high_watermark"] = max(position["high_watermark"], float(df["high"].iloc[index]))
            if position is None and not closed_this_bar and action == "buy":
                entry_price = price * (1 + self.slippage_rate)
                stake = capital * self.allocation_rate
                quantity = stake / entry_price
                entry_fee = stake * self.fee_rate
                capital -= entry_fee
                position = {
                    "entry_price": round(entry_price, 8),
                    "entry_time": timestamp,
                    "quantity": round(quantity, 8),
                    "entry_fee": round(entry_fee, 8),
                    "high_watermark": entry_price,
                }
            equity = capital
            if position:
                equity += (price - position["entry_price"]) * position["quantity"]
            equity_curve.append({"timestamp": timestamp, "equity": round(equity, 2)})

        return {
            "metrics": self._metrics(initial_capital, capital, trades, equity_curve),
            "trades": trades,
            "equity_curve": equity_curve,
        }

    @staticmethod
    def _timestamp_ms(value) -> int:
        if isinstance(value, (datetime, pd.Timestamp)):
            return int(pd.Timestamp(value).timestamp() * 1000)
        return int(value)

    def _risk_exit(self, position: dict | None, candle: pd.Series) -> tuple[float, str] | None:
        if position is None:
            return None
        entry = float(position["entry_price"])
        previous_high = float(position.get("high_watermark", entry))
        stop = entry * (1 - self.stop_loss_rate)
        if previous_high >= entry * (1 + self.break_even_rate):
            stop = max(stop, entry)
        if self.trailing_stop_rate > 0 and previous_high > entry:
            stop = max(stop, previous_high * (1 - self.trailing_stop_rate))
        take_profit = entry * (1 + self.take_profit_rate)
        low, high = float(candle["low"]), float(candle["high"])

        # The intrabar path is unknown: assume the stop is hit first when both levels trade.
        if low <= stop:
            reason = "trailing_stop" if previous_high > entry else "stop_loss"
            return stop, reason
        if high >= take_profit:
            return take_profit, "take_profit"
        return None

    @staticmethod
    def _actions(strategy: str, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        buy = pd.Series(False, index=df.index)
        sell = pd.Series(False, index=df.index)

        if strategy in {"EMA Cross", "Trend Following"}:
            fast, slow = ema(close, 12), ema(close, 26)
            buy = (fast.shift(1) <= slow.shift(1)) & (fast > slow)
            sell = (fast.shift(1) >= slow.shift(1)) & (fast < slow)
        elif strategy == "RSI":
            value = rsi(close, 14)
            buy, sell = value <= 30, value >= 70
        elif strategy == "MACD":
            line, signal = macd(close)
            buy = (line.shift(1) <= signal.shift(1)) & (line > signal)
            sell = (line.shift(1) >= signal.shift(1)) & (line < signal)
        elif strategy in {"Bollinger Bands", "Mean Reversion"}:
            mid = close.rolling(20).mean()
            deviation = close.rolling(20).std()
            buy, sell = close <= mid - 2 * deviation, close >= mid + 2 * deviation
        elif strategy == "Breakout":
            buy = close > df["high"].rolling(20).max().shift(1)
            sell = close < df["low"].rolling(20).min().shift(1)
        elif strategy == "Volume Spike":
            average = df["volume"].rolling(20).mean().shift(1)
            spike = df["volume"] > average * 2
            buy, sell = spike & (close > df["open"]), spike & (close <= df["open"])
        elif strategy == "ATR":
            available = atr(df).notna()
            buy, sell = available & (close > ema(close, 20)), available & (close <= ema(close, 20))
        elif strategy == "VWAP":
            value = vwap(df)
            buy, sell = close > value, close < value
        elif strategy == "Scalping":
            momentum, value = rsi(close, 7), vwap(df)
            buy, sell = (momentum < 45) & (close > value), (momentum > 55) & (close < value)
        elif strategy == "Swing Trading":
            trend, momentum = ema(close, 50) > ema(close, 200), rsi(close, 14)
            buy, sell = trend & (momentum < 60), (~trend) & (momentum > 40)
        elif strategy == "Meme Momentum":
            buy, sell = meme_momentum_actions(df)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        actions = pd.Series("hold", index=df.index)
        actions.loc[buy.fillna(False)] = "buy"
        actions.loc[sell.fillna(False)] = "sell"
        return actions

    @staticmethod
    def _metrics(initial_capital: float, final_capital: float, trades: list[dict], equity_curve: list[dict]) -> dict:
        equity = pd.Series([point["equity"] for point in equity_curve], dtype=float)
        returns = equity.pct_change().dropna()
        downside = returns[returns < 0]
        drawdown = equity / equity.cummax() - 1
        wins = [trade["pnl"] for trade in trades if trade["pnl"] > 0]
        losses = [trade["pnl"] for trade in trades if trade["pnl"] < 0]
        annualizer = math.sqrt(6 * 365)
        sharpe = returns.mean() / returns.std() * annualizer if not returns.empty and returns.std() else 0
        sortino = returns.mean() / downside.std() * annualizer if not downside.empty and downside.std() else 0
        return {
            "profit": round(final_capital - initial_capital, 2),
            "return_pct": round((final_capital / initial_capital - 1) * 100, 2),
            "drawdown": round(abs(float(drawdown.min())) * 100, 2),
            "trades": len(trades),
            "sharpe_ratio": round(float(sharpe), 3),
            "sortino_ratio": round(float(sortino), 3),
            "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else float(bool(wins)),
            "final_capital": round(final_capital, 2),
        }


def utc_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
