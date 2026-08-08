from __future__ import annotations

import math

import numpy as np
import pandas as pd


def historical_market_memory(
    df: pd.DataFrame,
    *,
    lookback: int = 48,
    horizon: int = 12,
    neighbors: int = 18,
) -> dict:
    """Compare the recent market shape with older similar shapes and summarize what followed."""
    if len(df) < lookback * 4 + horizon + 5:
        return {"available": False, "reason": "insufficient_market_memory", "score": 0.0}

    frame = _numeric_frame(df)
    if len(frame) < lookback * 4 + horizon + 5:
        return {"available": False, "reason": "insufficient_clean_market_memory", "score": 0.0}

    current_index = len(frame) - 1
    current_features = _feature_vector(frame, current_index, lookback)
    if current_features is None:
        return {"available": False, "reason": "current_pattern_unavailable", "score": 0.0}

    matches = []
    end = len(frame) - horizon - 1
    for index in range(lookback, end):
        features = _feature_vector(frame, index, lookback)
        if features is None:
            continue
        distance = _weighted_distance(current_features, features)
        future_return = _forward_return(frame, index, horizon)
        adverse_move = _forward_adverse_move(frame, index, horizon)
        if math.isfinite(distance) and math.isfinite(future_return):
            matches.append(
                {
                    "distance": distance,
                    "forward_return_pct": future_return,
                    "adverse_move_pct": adverse_move,
                    "index": index,
                }
            )

    if len(matches) < max(6, neighbors // 2):
        return {"available": False, "reason": "not_enough_similar_patterns", "score": 0.0}

    nearest = sorted(matches, key=lambda row: row["distance"])[:neighbors]
    forward_returns = [row["forward_return_pct"] for row in nearest]
    adverse_moves = [row["adverse_move_pct"] for row in nearest]
    positive_rate = sum(value > 0 for value in forward_returns) / len(forward_returns)
    average_return = float(np.mean(forward_returns))
    median_return = float(np.median(forward_returns))
    worst_return = float(np.min(forward_returns))
    average_adverse = float(np.mean(adverse_moves))
    similarity = max(0.0, 1.0 - float(np.mean([row["distance"] for row in nearest])) / 8.0)
    raw_edge = average_return * 0.45 + median_return * 0.35 + (positive_rate - 0.5) * 18 - average_adverse * 0.18
    score = max(-1.0, min(1.0, raw_edge / 10.0)) * similarity
    stance = "bullish" if score >= 0.18 else ("bearish" if score <= -0.18 else "neutral")

    return {
        "available": True,
        "stance": stance,
        "score": round(score, 3),
        "confidence": round(similarity, 3),
        "lookback_bars": lookback,
        "horizon_bars": horizon,
        "matches": len(nearest),
        "positive_rate_pct": round(positive_rate * 100, 2),
        "average_forward_return_pct": round(average_return, 3),
        "median_forward_return_pct": round(median_return, 3),
        "worst_forward_return_pct": round(worst_return, 3),
        "average_adverse_move_pct": round(average_adverse, 3),
    }


def _numeric_frame(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["open", "high", "low", "close", "volume"]
    frame = df[columns].copy()
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=columns).reset_index(drop=True)


def _feature_vector(frame: pd.DataFrame, index: int, lookback: int) -> dict | None:
    if index < lookback:
        return None
    window = frame.iloc[index - lookback + 1 : index + 1]
    close = window["close"]
    volume = window["volume"]
    current = float(close.iloc[-1])
    if current <= 0 or float(close.iloc[0]) <= 0:
        return None
    returns = close.pct_change().dropna()
    if returns.empty:
        return None
    high = window["high"]
    low = window["low"]
    short = max(3, lookback // 8)
    medium = max(6, lookback // 4)
    long = max(12, lookback // 2)
    return {
        "return_short": _pct(current, float(close.iloc[-short])),
        "return_medium": _pct(current, float(close.iloc[-medium])),
        "return_long": _pct(current, float(close.iloc[-long])),
        "return_full": _pct(current, float(close.iloc[0])),
        "volatility": float(returns.std() * math.sqrt(len(returns)) * 100),
        "drawdown": _drawdown_pct(close),
        "range": (float(high.max()) / max(float(low.min()), 1e-9) - 1) * 100,
        "volume_pressure": _volume_pressure(volume),
        "green_ratio": float((close.diff().dropna() > 0).mean() * 100),
    }


def _pct(new: float, old: float) -> float:
    return (new / old - 1) * 100 if old else 0.0


def _drawdown_pct(close: pd.Series) -> float:
    running_high = close.cummax()
    drawdowns = close / running_high - 1
    return abs(float(drawdowns.min() * 100))


def _volume_pressure(volume: pd.Series) -> float:
    baseline = float(volume.iloc[:-6].median()) if len(volume) > 8 else float(volume.median())
    recent = float(volume.iloc[-6:].mean()) if len(volume) >= 6 else float(volume.mean())
    return recent / max(baseline, 1e-9)


def _weighted_distance(left: dict, right: dict) -> float:
    weights = {
        "return_short": 1.1,
        "return_medium": 1.2,
        "return_long": 1.0,
        "return_full": 0.9,
        "volatility": 0.8,
        "drawdown": 0.9,
        "range": 0.7,
        "volume_pressure": 1.0,
        "green_ratio": 0.6,
    }
    total = 0.0
    for key, weight in weights.items():
        scale = 1.0 if key == "volume_pressure" else 10.0
        total += weight * abs(float(left[key]) - float(right[key])) / scale
    return total / sum(weights.values())


def _forward_return(frame: pd.DataFrame, index: int, horizon: int) -> float:
    entry = float(frame["close"].iloc[index])
    future = float(frame["close"].iloc[index + horizon])
    return _pct(future, entry)


def _forward_adverse_move(frame: pd.DataFrame, index: int, horizon: int) -> float:
    entry = float(frame["close"].iloc[index])
    low = float(frame["low"].iloc[index + 1 : index + horizon + 1].min())
    return abs(min(0.0, _pct(low, entry)))
