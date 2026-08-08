import pandas as pd

from app.strategies.base import Signal, Strategy, ensure_indicators_frame
from app.strategies.chart_patterns import meme_momentum_setup
from app.strategies.indicators import atr, ema, macd, rsi, vwap


class EmaCrossStrategy(Strategy):
    name = "EMA Cross"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        p = {"fast": 12, "slow": 26, **(parameters or {})}
        df = ensure_indicators_frame(candles)
        fast, slow = ema(df["close"], p["fast"]), ema(df["close"], p["slow"])
        if len(df) < p["slow"] + 2:
            return Signal("hold", 0.0, "not enough candles", {})
        crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
        crossed_down = fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]
        if crossed_up:
            return Signal("buy", 0.72, "fast EMA crossed above slow EMA", {"fast": fast.iloc[-1], "slow": slow.iloc[-1]})
        if crossed_down:
            return Signal("sell", 0.72, "fast EMA crossed below slow EMA", {"fast": fast.iloc[-1], "slow": slow.iloc[-1]})
        return Signal("hold", 0.35, "no EMA crossover", {})


class RsiStrategy(Strategy):
    name = "RSI"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        p = {"period": 14, "oversold": 30, "overbought": 70, **(parameters or {})}
        df = ensure_indicators_frame(candles)
        value = rsi(df["close"], p["period"]).iloc[-1]
        if value <= p["oversold"]:
            return Signal("buy", 0.68, "RSI oversold", {"rsi": float(value)})
        if value >= p["overbought"]:
            return Signal("sell", 0.68, "RSI overbought", {"rsi": float(value)})
        return Signal("hold", 0.3, "RSI neutral", {"rsi": float(value) if pd.notna(value) else None})


class MacdStrategy(Strategy):
    name = "MACD"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        df = ensure_indicators_frame(candles)
        macd_line, signal_line = macd(df["close"])
        if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
            return Signal("buy", 0.7, "MACD bullish cross", {})
        if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
            return Signal("sell", 0.7, "MACD bearish cross", {})
        return Signal("hold", 0.3, "MACD neutral", {})


class BollingerBandsStrategy(Strategy):
    name = "Bollinger Bands"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        p = {"period": 20, "std": 2, **(parameters or {})}
        df = ensure_indicators_frame(candles)
        mid = df["close"].rolling(p["period"]).mean()
        dev = df["close"].rolling(p["period"]).std()
        upper, lower = mid + p["std"] * dev, mid - p["std"] * dev
        close = df["close"].iloc[-1]
        if close <= lower.iloc[-1]:
            return Signal("buy", 0.65, "price touched lower Bollinger band", {})
        if close >= upper.iloc[-1]:
            return Signal("sell", 0.65, "price touched upper Bollinger band", {})
        return Signal("hold", 0.3, "inside Bollinger bands", {})


class BreakoutStrategy(Strategy):
    name = "Breakout"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        p = {"lookback": 20, **(parameters or {})}
        df = ensure_indicators_frame(candles)
        high = df["high"].iloc[-p["lookback"] - 1 : -1].max()
        low = df["low"].iloc[-p["lookback"] - 1 : -1].min()
        close = df["close"].iloc[-1]
        if close > high:
            return Signal("buy", 0.74, "price broke range high", {"range_high": float(high)})
        if close < low:
            return Signal("sell", 0.74, "price broke range low", {"range_low": float(low)})
        return Signal("hold", 0.28, "no breakout", {})


class VolumeSpikeStrategy(Strategy):
    name = "Volume Spike"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        p = {"lookback": 20, "multiplier": 2.0, **(parameters or {})}
        df = ensure_indicators_frame(candles)
        avg_volume = df["volume"].iloc[-p["lookback"] - 1 : -1].mean()
        spike = df["volume"].iloc[-1] > avg_volume * p["multiplier"]
        rising = df["close"].iloc[-1] > df["open"].iloc[-1]
        if spike and rising:
            return Signal("buy", 0.62, "bullish volume spike", {"avg_volume": float(avg_volume)})
        if spike and not rising:
            return Signal("sell", 0.62, "bearish volume spike", {"avg_volume": float(avg_volume)})
        return Signal("hold", 0.25, "normal volume", {})


class AtrStrategy(Strategy):
    name = "ATR"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        df = ensure_indicators_frame(candles)
        value = atr(df).iloc[-1]
        direction = df["close"].iloc[-1] > ema(df["close"], 20).iloc[-1]
        if pd.notna(value) and direction:
            return Signal("buy", 0.55, "ATR confirms trend volatility", {"atr": float(value)})
        if pd.notna(value) and not direction:
            return Signal("sell", 0.55, "ATR confirms downside volatility", {"atr": float(value)})
        return Signal("hold", 0.2, "ATR unavailable", {})


class VwapStrategy(Strategy):
    name = "VWAP"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        df = ensure_indicators_frame(candles)
        current_vwap = vwap(df).iloc[-1]
        close = df["close"].iloc[-1]
        if close > current_vwap:
            return Signal("buy", 0.56, "price above VWAP", {"vwap": float(current_vwap)})
        if close < current_vwap:
            return Signal("sell", 0.56, "price below VWAP", {"vwap": float(current_vwap)})
        return Signal("hold", 0.2, "price at VWAP", {})


class TrendFollowingStrategy(EmaCrossStrategy):
    name = "Trend Following"


class MeanReversionStrategy(BollingerBandsStrategy):
    name = "Mean Reversion"


class ScalpingStrategy(Strategy):
    name = "Scalping"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        p = {"rsi_period": 7, **(parameters or {})}
        df = ensure_indicators_frame(candles)
        rsi_value = rsi(df["close"], p["rsi_period"]).iloc[-1]
        above_vwap = df["close"].iloc[-1] > vwap(df).iloc[-1]
        if rsi_value < 45 and above_vwap:
            return Signal("buy", 0.58, "short-term pullback above VWAP", {"rsi": float(rsi_value)})
        if rsi_value > 55 and not above_vwap:
            return Signal("sell", 0.58, "short-term rejection below VWAP", {"rsi": float(rsi_value)})
        return Signal("hold", 0.25, "no scalp setup", {})


class SwingTradingStrategy(Strategy):
    name = "Swing Trading"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        df = ensure_indicators_frame(candles)
        trend = ema(df["close"], 50).iloc[-1] > ema(df["close"], 200).iloc[-1]
        momentum = rsi(df["close"], 14).iloc[-1]
        if trend and momentum < 60:
            return Signal("buy", 0.6, "bullish swing alignment", {"rsi": float(momentum)})
        if not trend and momentum > 40:
            return Signal("sell", 0.6, "bearish swing alignment", {"rsi": float(momentum)})
        return Signal("hold", 0.25, "swing setup absent", {})


class MemeMomentumStrategy(Strategy):
    name = "Meme Momentum"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        df = ensure_indicators_frame(candles)
        setup = meme_momentum_setup(df)
        if setup["allowed"]:
            snapshot = setup["snapshot"]
            confidence = min(0.9, 0.62 + min(snapshot["volume_ratio"], 6.0) * 0.03 + min(snapshot["momentum_3_pct"], 8.0) * 0.015)
            return Signal("buy", round(confidence, 3), "meme momentum breakout with volume confirmation", setup)
        snapshot = setup.get("snapshot", {})
        if snapshot.get("available") and snapshot.get("upper_wick_ratio", 0.0) > 0.55:
            return Signal("sell", 0.62, "meme momentum exhaustion wick", setup)
        return Signal("hold", 0.2, setup["reason"], setup)


STRATEGIES: dict[str, Strategy] = {
    strategy.name: strategy
    for strategy in [
        EmaCrossStrategy(),
        RsiStrategy(),
        MacdStrategy(),
        BollingerBandsStrategy(),
        BreakoutStrategy(),
        VolumeSpikeStrategy(),
        AtrStrategy(),
        VwapStrategy(),
        ScalpingStrategy(),
        SwingTradingStrategy(),
        TrendFollowingStrategy(),
        MeanReversionStrategy(),
        MemeMomentumStrategy(),
    ]
}
