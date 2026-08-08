import asyncio
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.backtesting.historical import HistoricalBacktestEngine, utc_iso
from app.strategies.catalog import STRATEGIES

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
INTERVAL = "4h"
INTERVAL_MS = 4 * 60 * 60 * 1000
MONTHS = 72
OUTPUT = Path("app/data/backtest_72m.json")
CSV_OUTPUT = Path("app/data/backtest_72m.csv")
RISK_CALIBRATION = Path("app/data/risk_calibration.json")


async def download_candles(client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    candles: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        response = await client.get(
            "https://data-api.binance.vision/api/v3/klines",
            params={"symbol": symbol, "interval": INTERVAL, "startTime": cursor, "endTime": end_ms, "limit": 1000},
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            break
        candles.extend({"timestamp": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5]} for row in rows)
        cursor = int(rows[-1][0]) + INTERVAL_MS
    return candles


async def main() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * 6)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    async with httpx.AsyncClient(timeout=60) as client:
        datasets = {symbol: await download_candles(client, symbol, start_ms, end_ms) for symbol in SYMBOLS}

    calibration = json.loads(RISK_CALIBRATION.read_text(encoding="utf-8"))
    risk_profile = calibration["chosen"]
    engine = HistoricalBacktestEngine(fee_pct=0.1, slippage_pct=0.05, allocation_pct=10, **risk_profile["settings"])
    results = []
    for symbol, candles in datasets.items():
        for strategy in STRATEGIES:
            outcome = engine.run(strategy, candles)
            results.append({"symbol": symbol, "strategy": strategy, **outcome["metrics"]})
    results.sort(key=lambda row: (row["return_pct"], row["sharpe_ratio"]), reverse=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": utc_iso(start_ms), "end": utc_iso(end_ms), "months": MONTHS},
        "timeframe": INTERVAL,
        "initial_capital": 10_000,
        "fee_pct_per_order": 0.1,
        "slippage_pct_per_order": 0.05,
        "allocation_pct": 10,
        "risk_profile": risk_profile,
        "candles": {symbol: len(rows) for symbol, rows in datasets.items()},
        "results": results,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    with CSV_OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(json.dumps({"period": report["period"], "candles": report["candles"], "top": results[:5]}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
