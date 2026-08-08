from datetime import datetime, timezone

import pandas as pd


HISTORICAL_MACRO_EVENTS = [
    {"name": "Dot-com bubble burst", "start": "2000-03-10", "end": "2002-10-09", "category": "bubble_crash", "risk_bias": "risk_off"},
    {"name": "September 11 attacks", "start": "2001-09-11", "end": "2001-09-21", "category": "terror_shock", "risk_bias": "risk_off"},
    {"name": "Global financial crisis", "start": "2007-08-09", "end": "2009-03-09", "category": "credit_crisis", "risk_bias": "risk_off"},
    {"name": "European sovereign debt crisis", "start": "2010-04-23", "end": "2012-07-26", "category": "sovereign_debt", "risk_bias": "caution"},
    {"name": "Oil price collapse", "start": "2014-06-20", "end": "2016-02-11", "category": "oil_shock", "risk_bias": "caution"},
    {"name": "Brexit referendum shock", "start": "2016-06-23", "end": "2016-06-30", "category": "election_referendum", "risk_bias": "caution"},
    {"name": "Covid-19 pandemic crash", "start": "2020-02-20", "end": "2020-04-30", "category": "pandemic", "risk_bias": "risk_off"},
    {"name": "Covid liquidity recovery", "start": "2020-05-01", "end": "2021-11-10", "category": "liquidity_boom", "risk_bias": "bubble_watch"},
    {"name": "Russia-Ukraine invasion shock", "start": "2022-02-24", "end": "2022-04-30", "category": "war", "risk_bias": "risk_off"},
    {"name": "2022 inflation and rate shock", "start": "2022-01-03", "end": "2022-10-14", "category": "rates_inflation", "risk_bias": "caution"},
    {"name": "FTX crypto confidence shock", "start": "2022-11-06", "end": "2022-12-31", "category": "crypto_regulatory_confidence", "risk_bias": "risk_off"},
    {"name": "US regional banking stress", "start": "2023-03-08", "end": "2023-03-31", "category": "banking_stress", "risk_bias": "caution"},
]


def macro_event_snapshot(df: pd.DataFrame | None = None, when: datetime | None = None) -> dict:
    current = _current_time(df, when)
    active = [event for event in HISTORICAL_MACRO_EVENTS if _inside(current, event["start"], event["end"])]
    seasonal = _seasonal_liquidity(current)
    shock = _market_shock(df)
    risk_score = 0
    risk_score += 35 if any(event["risk_bias"] == "risk_off" for event in active) else 0
    risk_score += 18 if any(event["risk_bias"] == "caution" for event in active) else 0
    risk_score += 12 if any(event["risk_bias"] == "bubble_watch" for event in active) else 0
    risk_score += seasonal["risk_score"]
    risk_score += shock["risk_score"]
    stance = "risk_off" if risk_score >= 45 else ("caution" if risk_score >= 22 else "normal")
    return {
        "available": True,
        "timestamp": current.isoformat(),
        "stance": stance,
        "risk_score": min(risk_score, 100),
        "active_events": active,
        "seasonal_liquidity": seasonal,
        "market_shock": shock,
        "event_catalog": HISTORICAL_MACRO_EVENTS,
    }


def historical_event_exposure(candles: list[dict]) -> dict:
    if not candles:
        return {"covered_events": [], "event_count": 0}
    timestamps = [int(row["timestamp"]) for row in candles]
    first = datetime.fromtimestamp(min(timestamps) / 1000, tz=timezone.utc)
    last = datetime.fromtimestamp(max(timestamps) / 1000, tz=timezone.utc)
    covered = [event for event in HISTORICAL_MACRO_EVENTS if _overlaps(first, last, event["start"], event["end"])]
    return {
        "covered_events": covered,
        "event_count": len(covered),
        "coverage_start": first.isoformat(),
        "coverage_end": last.isoformat(),
    }


def _current_time(df: pd.DataFrame | None, when: datetime | None) -> datetime:
    if when is not None:
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    if df is not None and "timestamp" in df and len(df):
        value = int(pd.to_numeric(df["timestamp"]).iloc[-1])
        value = value * 1000 if value < 1_000_000_000_000 else value
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _inside(current: datetime, start: str, end: str) -> bool:
    start_date = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_date = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    return start_date <= current <= end_date


def _overlaps(first: datetime, last: datetime, start: str, end: str) -> bool:
    start_date = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_date = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    return first <= end_date and last >= start_date


def _seasonal_liquidity(current: datetime) -> dict:
    low_liquidity = (
        (current.month == 12 and current.day >= 20)
        or (current.month == 1 and current.day <= 3)
        or (current.month == 7 and current.day <= 5)
        or current.weekday() >= 5
    )
    election_window = current.month in {10, 11} and current.year % 4 == 0
    return {
        "low_liquidity_window": low_liquidity,
        "election_window": election_window,
        "risk_score": (10 if low_liquidity else 0) + (8 if election_window else 0),
    }


def _market_shock(df: pd.DataFrame | None) -> dict:
    if df is None or len(df) < 30:
        return {"risk_score": 0, "large_move": False, "volume_stress": False}
    frame = df.copy()
    for column in ["open", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close = frame["close"]
    volume = frame["volume"]
    move_24 = (float(close.iloc[-1]) / float(close.iloc[-25]) - 1) * 100 if float(close.iloc[-25]) else 0.0
    volume_ratio = float(volume.iloc[-1]) / max(float(volume.iloc[-25:-1].mean()), 1.0)
    large_move = abs(move_24) >= 7.0
    volume_stress = volume_ratio >= 2.0
    return {
        "risk_score": (16 if large_move else 0) + (12 if volume_stress else 0),
        "large_move": large_move,
        "volume_stress": volume_stress,
        "move_24_pct": round(move_24, 3),
        "volume_ratio": round(volume_ratio, 3),
    }
