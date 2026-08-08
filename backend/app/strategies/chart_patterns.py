import math

import pandas as pd

from app.strategies.indicators import atr, ema


def curve_snapshot(df: pd.DataFrame, lookback: int = 20) -> dict:
    if len(df) < max(lookback + 2, 60):
        return {"available": False}
    frame = df.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    current = float(close.iloc[-1])
    previous_close = float(close.iloc[-2])
    range_high = float(high.iloc[-lookback - 1 : -1].max())
    range_low = float(low.iloc[-lookback - 1 : -1].min())
    range_width = max(range_high - range_low, current * 0.001)
    position_in_range = (current - range_low) / range_width
    breakout_strength_pct = (current / range_high - 1) * 100 if range_high else 0.0
    atr_value = float(atr(frame).iloc[-1])
    atr_pct = atr_value / current * 100 if current and math.isfinite(atr_value) else 0.0
    body = abs(float(frame["close"].iloc[-1]) - float(frame["open"].iloc[-1]))
    candle_range = max(float(frame["high"].iloc[-1]) - float(frame["low"].iloc[-1]), current * 0.0001)
    upper_wick = float(frame["high"].iloc[-1]) - max(float(frame["open"].iloc[-1]), float(frame["close"].iloc[-1]))
    lower_wick = min(float(frame["open"].iloc[-1]), float(frame["close"].iloc[-1])) - float(frame["low"].iloc[-1])
    average_volume = float(volume.iloc[-lookback - 1 : -1].mean())
    volume_ratio = float(volume.iloc[-1]) / average_volume if average_volume else 0.0
    volume_std = float(volume.iloc[-lookback - 1 : -1].std())
    volume_zscore = (float(volume.iloc[-1]) - average_volume) / volume_std if volume_std else 0.0
    ema20 = float(ema(close, 20).iloc[-1])
    ema50 = float(ema(close, 50).iloc[-1])
    momentum_3_pct = (current / float(close.iloc[-4]) - 1) * 100 if float(close.iloc[-4]) else 0.0
    return {
        "available": True,
        "price": current,
        "previous_close": previous_close,
        "range_high": round(range_high, 8),
        "range_low": round(range_low, 8),
        "position_in_range": round(position_in_range, 3),
        "breakout_strength_pct": round(breakout_strength_pct, 3),
        "atr_pct": round(atr_pct, 3),
        "volume_ratio": round(volume_ratio, 3),
        "volume_zscore": round(volume_zscore, 3),
        "ema20": round(ema20, 8),
        "ema50": round(ema50, 8),
        "trend_up": bool(current > ema20 > ema50),
        "momentum_3_pct": round(momentum_3_pct, 3),
        "body_ratio": round(body / candle_range, 3),
        "upper_wick_ratio": round(upper_wick / candle_range, 3),
        "lower_wick_ratio": round(lower_wick / candle_range, 3),
        "breakout": bool(current > range_high),
    }


def meme_momentum_setup(df: pd.DataFrame) -> dict:
    snapshot = curve_snapshot(df, lookback=18)
    if not snapshot.get("available"):
        return {"allowed": False, "reason": "insufficient_history", "snapshot": snapshot}
    allowed = bool(
        snapshot["breakout"]
        and snapshot["trend_up"]
        and snapshot["volume_ratio"] >= 2.2
        and snapshot["volume_zscore"] >= 1.5
        and 0.4 <= snapshot["atr_pct"] <= 14.0
        and snapshot["momentum_3_pct"] > 0.8
        and snapshot["upper_wick_ratio"] <= 0.45
        and snapshot["body_ratio"] >= 0.35
    )
    return {
        "allowed": allowed,
        "reason": "meme_momentum_confirmed" if allowed else "meme_setup_not_confirmed",
        "snapshot": snapshot,
    }


def meme_momentum_actions(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    frame = df.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    range_high = high.rolling(18).max().shift(1)
    atr_pct = atr(frame) / close.replace(0, pd.NA) * 100
    average_volume = volume.rolling(18).mean().shift(1)
    volume_std = volume.rolling(18).std().shift(1)
    volume_ratio = volume / average_volume.replace(0, pd.NA)
    volume_zscore = (volume - average_volume) / volume_std.replace(0, pd.NA)
    candle_range = (high - low).clip(lower=close * 0.0001)
    upper_wick = high - pd.concat([frame["open"], close], axis=1).max(axis=1)
    body = (close - frame["open"]).abs()
    buy = (
        (close > range_high)
        & (close > ema(close, 20))
        & (ema(close, 20) > ema(close, 50))
        & (volume_ratio >= 2.2)
        & (volume_zscore >= 1.5)
        & (atr_pct >= 0.4)
        & (atr_pct <= 14.0)
        & ((close / close.shift(3) - 1) * 100 > 0.8)
        & (upper_wick / candle_range <= 0.45)
        & (body / candle_range >= 0.35)
    )
    sell = upper_wick / candle_range > 0.55
    return buy.fillna(False), sell.fillna(False)
