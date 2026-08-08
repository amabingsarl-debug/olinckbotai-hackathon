import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np

from app.backtesting.historical import HistoricalBacktestEngine
from app.strategies.catalog import STRATEGIES
from scripts.run_walk_forward_research import INTERVAL, YEARS, annual_slices, download_candles, robustness

OUTPUT = Path("app/data/risk_calibration.json")
PROFILES = {
    "tight": {"stop_loss_pct": 2.0, "take_profit_pct": 4.0, "trailing_stop_pct": 1.0, "break_even_pct": 1.5},
    "balanced": {"stop_loss_pct": 4.0, "take_profit_pct": 8.0, "trailing_stop_pct": 3.0, "break_even_pct": 2.5},
    "wide": {"stop_loss_pct": 6.0, "take_profit_pct": 12.0, "trailing_stop_pct": 5.0, "break_even_pct": 4.0},
    "signal_guard": {"stop_loss_pct": 8.0, "take_profit_pct": 20.0, "trailing_stop_pct": 0.0, "break_even_pct": 0.0},
}


async def main() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * YEARS)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    async with httpx.AsyncClient(timeout=60) as client:
        datasets = {
            symbol: await download_candles(client, symbol, start_ms, end_ms)
            for symbol in ["BTCUSDT", "ETHUSDT"]
        }

    profile_results = []
    for name, settings in PROFILES.items():
        engine = HistoricalBacktestEngine(fee_pct=0.1, slippage_pct=0.05, allocation_pct=10, **settings)
        evaluations = []
        for symbol, candles in datasets.items():
            periods = annual_slices(candles, start)
            for strategy in STRATEGIES:
                full = engine.run(strategy, candles)["metrics"]
                annual = [engine.run(strategy, period)["metrics"] for period in periods if len(period) >= 500]
                score, eligible = robustness(full, annual)
                evaluations.append({
                    "symbol": symbol,
                    "strategy": strategy,
                    "eligible": eligible,
                    "robustness_score": score,
                    "positive_years": sum(row["return_pct"] > 0 for row in annual),
                    "median_year_return_pct": round(float(np.median([row["return_pct"] for row in annual])), 2),
                    "holdout_return_pct": round(sum(row["return_pct"] for row in annual[-2:]), 2),
                    **full,
                })
        ranked = sorted(evaluations, key=lambda row: (row["eligible"], row["robustness_score"]), reverse=True)
        eligible_count = sum(row["eligible"] for row in evaluations)
        selection_score = round(eligible_count * 20 + float(np.mean([row["robustness_score"] for row in ranked[:5]])), 2)
        profile_results.append({
            "name": name,
            "settings": settings,
            "eligible_count": eligible_count,
            "selection_score": selection_score,
            "top": ranked[:5],
        })
    profile_results.sort(key=lambda row: row["selection_score"], reverse=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calibration_markets": ["BTCUSDT", "ETHUSDT"],
        "years": YEARS,
        "timeframe": INTERVAL,
        "chosen": profile_results[0],
        "profiles": profile_results,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
