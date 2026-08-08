import math

import pandas as pd


HISTORICAL_MARKET_LESSONS = [
    {
        "period": "Tulip mania / South Sea Bubble / early exchange manias",
        "lesson": "Fast crowd adoption can detach price from durable demand; treat parabolic acceleration as fragile.",
        "signal": "parabolic_acceleration",
    },
    {
        "period": "1907 panic and 1929 crash",
        "lesson": "Leverage, crowded optimism and weak breadth can turn a normal pullback into forced selling.",
        "signal": "crowded_euphoria",
    },
    {
        "period": "1962 selloff and 1973-1974 bear market",
        "lesson": "Liquidity can disappear when macro stress rises; prefer smaller size when downside volume expands.",
        "signal": "distribution_pressure",
    },
    {
        "period": "1987 crash",
        "lesson": "Feedback loops amplify selling; sudden volatility expansion after a strong run deserves de-risking.",
        "signal": "volatility_feedback",
    },
    {
        "period": "1990s technology bubble",
        "lesson": "Momentum can continue longer than expected, but late-stage vertical moves need faster profit protection.",
        "signal": "late_stage_momentum",
    },
]


def investor_behavior_snapshot(df: pd.DataFrame, lookback: int = 48) -> dict:
    if len(df) < max(lookback + 5, 80):
        return {"available": False}
    frame = df.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close = frame["close"]
    volume = frame["volume"]
    current = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    returns = close.pct_change()
    momentum_6 = (current / float(close.iloc[-7]) - 1) * 100 if float(close.iloc[-7]) else 0.0
    momentum_24 = (current / float(close.iloc[-25]) - 1) * 100 if float(close.iloc[-25]) else 0.0
    acceleration = momentum_6 - (momentum_24 / 4)
    volatility = float(returns.iloc[-lookback:].std() * math.sqrt(lookback) * 100)
    prior_volatility = float(returns.iloc[-lookback * 2 : -lookback].std() * math.sqrt(lookback) * 100)
    volume_ratio = float(volume.iloc[-1]) / float(volume.iloc[-lookback - 1 : -1].mean())
    down_volume = frame.loc[close < frame["open"], "volume"].iloc[-lookback:].sum()
    up_volume = frame.loc[close >= frame["open"], "volume"].iloc[-lookback:].sum()
    distribution_ratio = float(down_volume / max(up_volume, 1.0))
    range_position = (current - float(close.iloc[-lookback:].min())) / max(float(close.iloc[-lookback:].max() - close.iloc[-lookback:].min()), current * 0.001)
    parabolic = bool(momentum_24 > 12 and (acceleration > 3 or momentum_6 > 6) and volume_ratio > 1.3)
    panic_selling = bool(momentum_6 < -4 and volume_ratio > 1.8 and distribution_ratio > 1.2)
    volatility_feedback = bool(volatility > prior_volatility * 1.8 and abs((current / previous - 1) * 100) > 2.0)
    crowded_euphoria = bool(range_position > 0.9 and momentum_24 > 8 and distribution_ratio > 1.0)
    score = 0
    score += 25 if parabolic else 0
    score += 25 if panic_selling else 0
    score += 20 if volatility_feedback else 0
    score += 15 if crowded_euphoria else 0
    score += 15 if distribution_ratio > 1.4 else 0
    stance = "risk_off" if score >= 45 else ("caution" if score >= 25 else "normal")
    return {
        "available": True,
        "stance": stance,
        "risk_score": min(score, 100),
        "momentum_6_pct": round(momentum_6, 3),
        "momentum_24_pct": round(momentum_24, 3),
        "acceleration": round(acceleration, 3),
        "volume_ratio": round(volume_ratio, 3),
        "distribution_ratio": round(distribution_ratio, 3),
        "volatility": round(volatility, 3),
        "prior_volatility": round(prior_volatility, 3),
        "range_position": round(range_position, 3),
        "flags": {
            "parabolic_acceleration": parabolic,
            "panic_selling": panic_selling,
            "volatility_feedback": volatility_feedback,
            "crowded_euphoria": crowded_euphoria,
            "distribution_pressure": distribution_ratio > 1.4,
        },
        "historical_lessons": HISTORICAL_MARKET_LESSONS,
    }
