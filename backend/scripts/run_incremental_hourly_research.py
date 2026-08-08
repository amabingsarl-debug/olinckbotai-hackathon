import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run_walk_forward_research import (
    HISTORICAL_MACRO_EVENTS,
    HISTORICAL_MARKET_LESSONS,
    INTERVAL,
    INTERVAL_MS,
    MARKET_COUNT,
    MIN_EVALUATION_YEARS,
    OUTPUT,
    RISK_CALIBRATION,
    TARGET_HISTORY_YEARS,
    STRATEGIES,
    benchmark_return,
    download_candles,
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
import httpx
import numpy as np
import pandas as pd

from app.backtesting.historical import HistoricalBacktestEngine
from app.backtesting.regime_selection import choose_regime_filter, holdout_is_eligible, training_is_eligible


CHUNK_DIR = Path("app/data/incremental_hourly")


async def main() -> None:
    end = datetime.now(timezone.utc)
    global_start = end - timedelta(days=365 * TARGET_HISTORY_YEARS)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60) as client:
        markets = await liquid_markets(client)
        for market in markets[:MARKET_COUNT]:
            await download_market_years(client, market, global_start, end)
    datasets = load_datasets()
    report = build_report(datasets, global_start, end)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"markets": report["markets"], "selected": report["selected"]}, indent=2))


async def download_market_years(client: httpx.AsyncClient, market: dict, start: datetime, end: datetime) -> None:
    symbol_dir = CHUNK_DIR / market["symbol"]
    symbol_dir.mkdir(parents=True, exist_ok=True)
    cursor = datetime(start.year, 1, 1, tzinfo=timezone.utc)
    while cursor < end:
        next_year = datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
        period_start = max(cursor, start)
        period_end = min(next_year, end)
        path = symbol_dir / f"{cursor.year}.json"
        if path.exists():
            cursor = next_year
            continue
        candles = await download_candles(client, market["symbol"], int(period_start.timestamp() * 1000), int(period_end.timestamp() * 1000))
        payload = {
            "market": market,
            "year": cursor.year,
            "interval": INTERVAL,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "candles": candles,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        cursor = next_year


def load_datasets() -> dict[str, dict]:
    datasets: dict[str, dict] = {}
    for symbol_dir in sorted(CHUNK_DIR.iterdir()):
        if not symbol_dir.is_dir():
            continue
        candles = []
        market = None
        for path in sorted(symbol_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            market = payload.get("market", market)
            candles.extend(payload.get("candles", []))
        dedup = {int(row["timestamp"]): row for row in candles}
        rows = [dedup[key] for key in sorted(dedup)]
        coverage = (int(rows[-1]["timestamp"]) - int(rows[0]["timestamp"])) / (365 * 24 * 60 * 60 * 1000) if rows else 0
        if market and coverage >= MIN_EVALUATION_YEARS:
            datasets[symbol_dir.name] = {**market, "candles": rows}
    return datasets


def build_report(datasets: dict[str, dict], start: datetime, end: datetime) -> dict:
    calibration = json.loads(RISK_CALIBRATION.read_text(encoding="utf-8"))
    risk_profile = calibration["chosen"]
    engine = HistoricalBacktestEngine(fee_pct=0.1, slippage_pct=0.05, allocation_pct=10, **risk_profile["settings"])
    evaluations = []
    for symbol, market in datasets.items():
        candles = market["candles"]
        periods = calendar_year_slices(candles)
        annual_periods = [period for period in periods if len(period) >= 500]
        coverage = history_years(candles)
        psychology = investor_behavior_snapshot(pd.DataFrame(candles))
        macro = macro_event_snapshot(pd.DataFrame(candles))
        event_exposure = historical_event_exposure(candles)
        for strategy in STRATEGIES:
            regime_selection = choose_regime_filter(engine, strategy, periods)
            regime_filter = regime_selection["chosen"]["settings"]
            full = engine.run(strategy, candles, regime_filter=regime_filter)["metrics"]
            annual = [engine.run(strategy, period, regime_filter=regime_filter)["metrics"] for period in annual_periods]
            if not annual:
                continue
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
                "history_note": "Year-by-year hourly chunks assembled; maximum available crypto history used.",
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
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": utc_iso(start_ms), "end": utc_iso(end_ms), "years": TARGET_HISTORY_YEARS},
        "timeframe": INTERVAL,
        "methodology": {
            "incremental_year_by_year": True,
            "target_history_years": TARGET_HISTORY_YEARS,
            "minimum_available_years": MIN_EVALUATION_YEARS,
            "history_policy": "Download and persist hourly chunks by market/year, then assemble the final analysis.",
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
            {"symbol": symbol, "quote_volume_24h": market["quote_volume_24h"], "candles": len(market["candles"]), "available_history_years": round(history_years(market["candles"]), 2)}
            for symbol, market in datasets.items()
        ],
        "selected": selected,
        "portfolio_validation": portfolio,
        "opportunity_radar": radar,
        "portfolio_variant_lab": variants,
        "evaluations": evaluations,
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


if __name__ == "__main__":
    asyncio.run(main())
