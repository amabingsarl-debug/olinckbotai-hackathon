import pandas as pd

from app.strategies.indicators import atr, ema


REGIME_FILTERS: dict[str, dict] = {
    "none": {},
    "trend": {"require_uptrend": True},
    "volatility": {"min_atr_pct": 0.5, "max_atr_pct": 6.0},
    "trend_volatility": {"require_uptrend": True, "min_atr_pct": 0.5, "max_atr_pct": 6.0},
}


def entry_regime_mask(df: pd.DataFrame, settings: dict | None = None) -> pd.Series:
    settings = settings or {}
    allowed = pd.Series(True, index=df.index, dtype=bool)
    close = pd.to_numeric(df["close"])

    if settings.get("require_uptrend"):
        trend_period = int(settings.get("trend_period", 200))
        allowed &= close > ema(close, trend_period)

    if "min_atr_pct" in settings or "max_atr_pct" in settings:
        atr_period = int(settings.get("atr_period", 14))
        atr_pct = atr(df, atr_period) / close.replace(0, pd.NA) * 100
        if "min_atr_pct" in settings:
            allowed &= atr_pct >= float(settings["min_atr_pct"])
        if "max_atr_pct" in settings:
            allowed &= atr_pct <= float(settings["max_atr_pct"])

    return allowed.fillna(False)


def regime_snapshot(df: pd.DataFrame, settings: dict | None = None) -> dict:
    settings = settings or {}
    close = pd.to_numeric(df["close"])
    atr_pct = atr(df, int(settings.get("atr_period", 14))) / close.replace(0, pd.NA) * 100
    trend_period = int(settings.get("trend_period", 200))
    trend = ema(close, trend_period)
    allowed = entry_regime_mask(df, settings)
    return {
        "allowed": bool(allowed.iloc[-1]),
        "atr_pct": round(float(atr_pct.iloc[-1]), 3) if pd.notna(atr_pct.iloc[-1]) else None,
        "above_trend": bool(close.iloc[-1] > trend.iloc[-1]),
        "trend_period": trend_period,
        "settings": settings,
    }
