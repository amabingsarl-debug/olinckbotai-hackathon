import asyncio
import csv
import gzip
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from run_walk_forward_research import (
    HISTORICAL_MACRO_EVENTS,
    HISTORICAL_MARKET_LESSONS,
    INTERVAL_MS,
    MARKET_COUNT,
    MIN_EVALUATION_YEARS,
    OUTPUT,
    RISK_CALIBRATION,
    STRATEGIES,
    TARGET_HISTORY_YEARS,
    benchmark_return,
    expert_conviction_score,
    expert_decision,
    historical_event_exposure,
    investor_behavior_snapshot,
    liquid_markets,
    macro_event_snapshot,
    opportunity_radar,
    portfolio_validation,
    portfolio_variant_lab,
    robustness,
    utc_iso,
)
from app.backtesting.historical import HistoricalBacktestEngine
from app.backtesting.regime_selection import choose_regime_filter, holdout_is_eligible, training_is_eligible


RAW_INTERVAL = "1m"
RESEARCH_INTERVAL = "1h"
CHUNK_DIR = Path("app/data/incremental_minute_monthly")
MONTHLY_SUMMARY = Path("app/data/minute_monthly_summary.json")
BINANCE_MONTHLY_ARCHIVE = "https://data.binance.vision/data/spot/monthly/klines"
HTTP_TIMEOUT_SECONDS = 240
DOWNLOAD_RETRIES = 3


async def main() -> None:
    end = datetime.now(timezone.utc)
    start = datetime(end.year - TARGET_HISTORY_YEARS, end.month, 1, tzinfo=timezone.utc)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        markets = await liquid_markets(client)
        for market in markets[:MARKET_COUNT]:
            await download_market_months(client, market, start, end)
    datasets, minute_summaries = load_hourly_datasets_from_minute_chunks()
    MONTHLY_SUMMARY.write_text(json.dumps(minute_summaries, indent=2), encoding="utf-8")
    report = build_report(datasets, minute_summaries, start, end)
    output_path = OUTPUT if report["selected"] else OUTPUT.with_name(f"{OUTPUT.stem}.minute-monthly-rejected.json")
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"markets": report["markets"], "selected": report["selected"]}, indent=2))


async def download_market_months(client: httpx.AsyncClient, market: dict, start: datetime, end: datetime) -> None:
    symbol_dir = CHUNK_DIR / market["symbol"]
    symbol_dir.mkdir(parents=True, exist_ok=True)
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    while cursor < end:
        path = symbol_dir / f"{cursor.year}-{cursor.month:02d}.json.gz"
        if not path.exists():
            try:
                candles = await download_month_archive(client, market["symbol"], cursor.year, cursor.month)
            except httpx.HTTPError as exc:
                print(f"skip_retry_later {market['symbol']} {cursor.year}-{cursor.month:02d}: {exc}")
                cursor = next_month(cursor)
                continue
            payload = {
                "market": market,
                "year": cursor.year,
                "month": cursor.month,
                "interval": RAW_INTERVAL,
                "candles": candles,
                "monthly_microstructure": monthly_microstructure(candles),
            }
            write_gzip_json(path, payload)
        cursor = next_month(cursor)


async def download_month_archive(client: httpx.AsyncClient, symbol: str, year: int, month: int) -> list[dict]:
    url = f"{BINANCE_MONTHLY_ARCHIVE}/{symbol}/{RAW_INTERVAL}/{symbol}-{RAW_INTERVAL}-{year}-{month:02d}.zip"
    response = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            response = await client.get(url)
            break
        except httpx.HTTPError:
            if attempt == DOWNLOAD_RETRIES:
                raise
            await asyncio.sleep(2 * attempt)
    if response is None:
        return []
    if response.status_code == 404:
        return []
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        csv_name = next(name for name in archive.namelist() if name.endswith(".csv"))
        with archive.open(csv_name) as file:
            text = io.TextIOWrapper(file, encoding="utf-8")
            return [
                {
                    "timestamp": int(row[0]),
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5],
                }
                for row in csv.reader(text)
                if row and row[0].isdigit()
            ]


def load_hourly_datasets_from_minute_chunks() -> tuple[dict[str, dict], dict]:
    datasets: dict[str, dict] = {}
    summaries: dict[str, list[dict]] = {}
    for symbol_dir in sorted(CHUNK_DIR.iterdir()):
        if not symbol_dir.is_dir():
            continue
        hourly_frames = []
        market = None
        symbol_summaries = []
        for path in sorted(symbol_dir.glob("*.json.gz")):
            payload = read_gzip_json(path)
            market = payload.get("market", market)
            candles = payload.get("candles", [])
            if candles:
                monthly_hourly = resample_minutes_to_hourly(pd.DataFrame(candles))
                if not monthly_hourly.empty:
                    hourly_frames.append(monthly_hourly)
            summary = payload.get("monthly_microstructure", {})
            if summary:
                symbol_summaries.append({"file": path.name, **summary})
        if not hourly_frames or market is None:
            continue
        hourly = pd.concat(hourly_frames, ignore_index=True).sort_values("timestamp")
        rows = hourly.to_dict("records")
        coverage = history_years(rows)
        if coverage >= MIN_EVALUATION_YEARS:
            datasets[symbol_dir.name] = {**market, "candles": rows, "raw_interval": RAW_INTERVAL, "research_interval": RESEARCH_INTERVAL}
            summaries[symbol_dir.name] = symbol_summaries
    return datasets, summaries


def resample_minutes_to_hourly(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    timestamps = pd.to_numeric(data["timestamp"], errors="coerce")
    data["timestamp"] = pd.to_datetime(timestamps, unit=timestamp_unit(timestamps), utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["open", "high", "low", "close", "volume"]).set_index("timestamp").sort_index()
    hourly = data.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    hourly["timestamp"] = ((hourly.index - epoch) // pd.Timedelta(milliseconds=1)).astype("int64")
    return hourly.reset_index(drop=True)


def timestamp_unit(values: pd.Series) -> str:
    sample = int(values.dropna().iloc[0]) if not values.dropna().empty else 0
    if sample > 10_000_000_000_000_000:
        return "ns"
    if sample > 10_000_000_000_000:
        return "us"
    return "ms"


def monthly_microstructure(candles: list[dict]) -> dict:
    if len(candles) < 120:
        return {"candles": len(candles), "available": False}
    frame = pd.DataFrame(candles)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close = frame["close"]
    returns = close.pct_change().dropna()
    volume = frame["volume"]
    up_minutes = int((close > frame["open"]).sum())
    down_minutes = int((close < frame["open"]).sum())
    large_moves = int((returns.abs() > returns.std() * 3).sum()) if returns.std() else 0
    return {
        "available": True,
        "candles": len(candles),
        "return_pct": round((float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100, 3) if float(close.iloc[0]) else 0.0,
        "minute_volatility_pct": round(float(returns.std() * np.sqrt(len(returns)) * 100), 3) if not returns.empty else 0.0,
        "up_minutes": up_minutes,
        "down_minutes": down_minutes,
        "buyer_pressure_pct": round(up_minutes / max(up_minutes + down_minutes, 1) * 100, 2),
        "volume_total": round(float(volume.sum()), 3),
        "volume_spike_minutes": int((volume > volume.rolling(60).mean().shift(1) * 3).sum()),
        "large_move_minutes": large_moves,
    }


def build_report(datasets: dict[str, dict], minute_summaries: dict, start: datetime, end: datetime) -> dict:
    calibration = json.loads(RISK_CALIBRATION.read_text(encoding="utf-8"))
    risk_profile = calibration["chosen"]
    engine = HistoricalBacktestEngine(fee_pct=0.1, slippage_pct=0.05, allocation_pct=10, **risk_profile["settings"])
    evaluations = []
    for symbol, market in datasets.items():
        candles = market["candles"]
        periods = calendar_year_slices(candles)
        annual_periods = [period for period in periods if len(period) >= 500]
        if not annual_periods:
            continue
        coverage = history_years(candles)
        psychology = investor_behavior_snapshot(pd.DataFrame(candles))
        macro = macro_event_snapshot(pd.DataFrame(candles))
        event_exposure = historical_event_exposure(candles)
        micro = aggregate_microstructure(minute_summaries.get(symbol, []))
        for strategy in STRATEGIES:
            regime_selection = choose_regime_filter(engine, strategy, periods)
            regime_filter = regime_selection["chosen"]["settings"]
            full = engine.run(strategy, candles, regime_filter=regime_filter)["metrics"]
            annual = [engine.run(strategy, period, regime_filter=regime_filter)["metrics"] for period in annual_periods]
            holdout_candles = [candle for period in periods[-2:] for candle in period]
            holdout_metrics = engine.run(strategy, holdout_candles, regime_filter=regime_filter)["metrics"]
            score, eligible = robustness(full, annual)
            eligible = bool(eligible and training_is_eligible(regime_selection) and holdout_is_eligible(holdout_metrics))
            evaluation = {
                "symbol": symbol,
                "strategy": strategy,
                "eligible": eligible,
                "target_history_years": TARGET_HISTORY_YEARS,
                "available_history_years": round(coverage, 2),
                "evaluation_years": len(annual),
                "history_note": "Minute data downloaded month by month; final strategy backtest uses assembled hourly bars plus minute microstructure.",
                "minute_microstructure": micro,
                "behavioral_psychology": psychology,
                "macro_event_context": macro,
                "historical_event_exposure": event_exposure,
                "robustness_score": score,
                "positive_years": sum(row["return_pct"] > 0 for row in annual),
                "median_year_return_pct": round(float(np.median([row["return_pct"] for row in annual])), 2),
                "worst_year_return_pct": min(row["return_pct"] for row in annual),
                "holdout_return_pct": round(sum(row["return_pct"] for row in annual[-2:]), 2),
                "holdout_positive_years": sum(row["return_pct"] > 0 for row in annual[-2:]),
                "holdout_metrics": holdout_metrics,
                "benchmark_return_pct": benchmark_return(candles),
                "regime_filter": regime_selection["chosen"],
                "regime_comparisons": regime_selection["comparisons"],
                "annual_results": annual,
                **full,
            }
            evaluation["expert_score"] = expert_conviction_score(evaluation, years=max(len(annual), 1))
            evaluation["expert_decision"] = expert_decision(evaluation)
            evaluations.append(evaluation)
    evaluations.sort(key=lambda row: (row["eligible"], row["expert_score"], row["robustness_score"]), reverse=True)
    selected = []
    selected_symbols = set()
    for row in evaluations:
        if row["eligible"] and row["symbol"] not in selected_symbols:
            selected.append(row)
            selected_symbols.add(row["symbol"])
        if len(selected) >= 5:
            break
    portfolio = portfolio_validation(selected, datasets, risk_profile["settings"], start)
    radar = opportunity_radar(evaluations, selected)
    variants = portfolio_variant_lab(selected, radar, evaluations, datasets, risk_profile["settings"], start)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": utc_iso(int(start.timestamp() * 1000)), "end": utc_iso(int(end.timestamp() * 1000)), "years": TARGET_HISTORY_YEARS},
        "timeframe": RESEARCH_INTERVAL,
        "raw_timeframe": RAW_INTERVAL,
        "methodology": {
            "incremental_month_by_month": True,
            "target_history_years": TARGET_HISTORY_YEARS,
            "minimum_available_years": MIN_EVALUATION_YEARS,
            "history_policy": "Download and persist official Binance spot minute archives month by month, then assemble hourly research bars and minute microstructure.",
            "interval_ms": INTERVAL_MS,
            "fee_pct_per_order": 0.1,
            "slippage_pct_per_order": 0.05,
            "allocation_pct": 10,
            "risk_profile": risk_profile,
            "pre_21st_century_market_lessons": HISTORICAL_MARKET_LESSONS,
            "macro_event_layer": {
                "purpose": "Account for pandemics, wars, elections, holidays, regulation, oil shocks, banking stress, and crisis regimes.",
                "event_catalog": HISTORICAL_MACRO_EVENTS,
            },
        },
        "markets": [
            {
                "symbol": symbol,
                "quote_volume_24h": market["quote_volume_24h"],
                "candles": len(market["candles"]),
                "available_history_years": round(history_years(market["candles"]), 2),
                "minute_months": len(minute_summaries.get(symbol, [])),
            }
            for symbol, market in datasets.items()
        ],
        "selected": selected,
        "portfolio_validation": portfolio,
        "opportunity_radar": radar,
        "portfolio_variant_lab": variants,
        "minute_monthly_summary": minute_summaries,
        "evaluations": evaluations,
    }


def aggregate_microstructure(rows: list[dict]) -> dict:
    usable = [row for row in rows if row.get("available")]
    if not usable:
        return {"available": False, "months": len(rows)}
    return {
        "available": True,
        "months": len(usable),
        "avg_buyer_pressure_pct": round(float(np.mean([row["buyer_pressure_pct"] for row in usable])), 2),
        "avg_monthly_minute_volatility_pct": round(float(np.mean([row["minute_volatility_pct"] for row in usable])), 3),
        "total_volume_spike_minutes": int(sum(row["volume_spike_minutes"] for row in usable)),
        "total_large_move_minutes": int(sum(row["large_move_minutes"] for row in usable)),
    }


def calendar_year_slices(candles: list[dict]) -> list[list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for candle in candles:
        year = datetime.fromtimestamp(int(candle["timestamp"]) / 1000, tz=timezone.utc).year
        grouped.setdefault(year, []).append(candle)
    return [grouped[year] for year in sorted(grouped)]


def history_years(candles: list[dict]) -> float:
    if len(candles) < 2:
        return 0.0
    return (int(candles[-1]["timestamp"]) - int(candles[0]["timestamp"])) / (365 * 24 * 60 * 60 * 1000)


def next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(value.year, value.month + 1, 1, tzinfo=timezone.utc)


def write_gzip_json(path: Path, payload: dict) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as file:
        json.dump(payload, file)


def read_gzip_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    asyncio.run(main())
