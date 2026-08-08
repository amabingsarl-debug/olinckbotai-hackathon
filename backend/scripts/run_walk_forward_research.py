import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from app.backtesting.benchmark import buy_and_hold_portfolio, risk_adjusted_score
from app.backtesting.expert_score import expert_conviction_score, expert_decision
from app.backtesting.historical import HistoricalBacktestEngine, utc_iso
from app.backtesting.macro_events import HISTORICAL_MACRO_EVENTS, historical_event_exposure, macro_event_snapshot
from app.backtesting.market_psychology import HISTORICAL_MARKET_LESSONS, investor_behavior_snapshot
from app.backtesting.opportunity import opportunity_radar
from app.backtesting.portfolio import PortfolioBacktestEngine, PortfolioCandidate
from app.backtesting.regime_selection import choose_regime_filter, holdout_is_eligible, training_is_eligible
from app.strategies.catalog import STRATEGIES

INTERVAL = "1h"
INTERVAL_MS = 60 * 60 * 1000
YEARS = 26
TARGET_HISTORY_YEARS = 26
MIN_EVALUATION_YEARS = 3
MARKET_COUNT = 8
OUTPUT = Path("app/data/walk_forward_report.json")
RISK_CALIBRATION = Path("app/data/risk_calibration.json")
STABLE_BASES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "AEUR", "USD1", "BFUSD"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
PRIORITY_BASES = {"BTC", "ETH", "TRX", "DOGE", "ZEC", "XRP", "SOL", "BNB"}


async def get_json(client: httpx.AsyncClient, url: str, params: dict | None = None, attempts: int = 4) -> object:
    last_error = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_error = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Research download failed after {attempts} attempts: {last_error}")


async def liquid_markets(client: httpx.AsyncClient) -> list[dict]:
    exchange_info, tickers = await asyncio.gather(
        get_json(client, "https://data-api.binance.vision/api/v3/exchangeInfo"),
        get_json(client, "https://data-api.binance.vision/api/v3/ticker/24hr"),
    )
    allowed = {
        row["symbol"]: row
        for row in exchange_info["symbols"]
        if row["status"] == "TRADING"
        and row["quoteAsset"] == "USDT"
        and row.get("isSpotTradingAllowed", False)
        and row["baseAsset"] not in STABLE_BASES
        and not row["baseAsset"].endswith(LEVERAGED_SUFFIXES)
    }
    ranked = sorted(
        (
            {"symbol": row["symbol"], "base_asset": allowed[row["symbol"]]["baseAsset"], "quote_volume_24h": float(row["quoteVolume"])}
            for row in tickers
            if row["symbol"] in allowed
        ),
        key=lambda row: row["quote_volume_24h"],
        reverse=True,
    )
    priority = [row for row in ranked if row["base_asset"] in PRIORITY_BASES]
    blended = priority + [row for row in ranked if row["base_asset"] not in PRIORITY_BASES]
    seen = set()
    unique = []
    for row in blended:
        if row["symbol"] in seen:
            continue
        unique.append(row)
        seen.add(row["symbol"])
    return unique[:MARKET_COUNT]


async def download_candles(client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    candles: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        rows = await get_json(
            client,
            "https://data-api.binance.vision/api/v3/klines",
            params={"symbol": symbol, "interval": INTERVAL, "startTime": cursor, "endTime": end_ms, "limit": 1000},
        )
        if not rows:
            break
        candles.extend({"timestamp": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5]} for row in rows)
        cursor = int(rows[-1][0]) + INTERVAL_MS
    return candles


def annual_slices(candles: list[dict]) -> list[list[dict]]:
    if not candles:
        return []
    start = datetime.fromtimestamp(int(candles[0]["timestamp"]) / 1000, tz=timezone.utc)
    periods = []
    for year in range(max(1, available_years(candles))):
        period_start = int((start + timedelta(days=365 * year)).timestamp() * 1000)
        period_end = int((start + timedelta(days=365 * (year + 1))).timestamp() * 1000)
        periods.append([row for row in candles if period_start <= int(row["timestamp"]) < period_end])
    return periods


def available_years(candles: list[dict]) -> int:
    if len(candles) < 2:
        return 0
    first = int(candles[0]["timestamp"])
    last = int(candles[-1]["timestamp"])
    return max(1, round((last - first) / (365 * 24 * 60 * 60 * 1000)))


def benchmark_return(candles: list[dict], allocation_pct: float = 10.0) -> float:
    if len(candles) < 2:
        return 0.0
    raw_return = float(candles[-1]["close"]) / float(candles[0]["close"]) - 1
    return round((raw_return * allocation_pct) - 0.3, 2)


def portfolio_validation(selected: list[dict], datasets: dict[str, dict], risk_settings: dict, start: datetime) -> dict:
    if not selected:
        return {"selected_count": 0, "full": {}, "holdout": {}, "eligible": False}
    engine = PortfolioBacktestEngine(
        fee_pct=0.1,
        slippage_pct=0.05,
        allocation_pct=10,
        max_portfolio_exposure_pct=20,
        **risk_settings,
    )
    full_candidates = []
    holdout_candidates = []
    for row in selected:
        candles = datasets[row["symbol"]]["candles"]
        holdout_start = max(int(candles[0]["timestamp"]), int(candles[-1]["timestamp"]) - 2 * 365 * 24 * 60 * 60 * 1000)
        regime_filter = row.get("regime_filter", {}).get("settings", {})
        full_candidates.append(
            PortfolioCandidate(
                symbol=row["symbol"],
                strategy=row["strategy"],
                candles=candles,
                regime_filter=regime_filter,
                holdout_profit_factor=row.get("holdout_metrics", {}).get("profit_factor"),
            )
        )
        holdout_candidates.append(
            PortfolioCandidate(
                symbol=row["symbol"],
                strategy=row["strategy"],
                candles=[candle for candle in candles if int(candle["timestamp"]) >= holdout_start],
                regime_filter=regime_filter,
                holdout_profit_factor=row.get("holdout_metrics", {}).get("profit_factor"),
            )
        )
    full_result = engine.run(full_candidates)
    holdout_result = engine.run(holdout_candidates)
    full = full_result["metrics"]
    holdout = holdout_result["metrics"]
    confidence = PortfolioBacktestEngine.monte_carlo_confidence(holdout_result["trades"])
    full_benchmark = buy_and_hold_portfolio(selected, datasets, 0)
    holdout_benchmark = buy_and_hold_portfolio(selected, datasets, holdout_start)
    benchmark = {
        "full": full_benchmark,
        "holdout": holdout_benchmark,
        "strategy_holdout_score": risk_adjusted_score(holdout.get("return_pct", 0.0), holdout.get("drawdown", 0.0)),
        "benchmark_holdout_score": risk_adjusted_score(holdout_benchmark.get("return_pct", 0.0), holdout_benchmark.get("drawdown", 0.0)),
        "excess_holdout_return_pct": round(holdout.get("return_pct", 0.0) - holdout_benchmark.get("return_pct", 0.0), 2),
    }
    eligible = bool(
        holdout.get("trades", 0) >= 30
        and holdout.get("return_pct", 0.0) > 0
        and holdout.get("profit_factor", 0.0) >= 1.05
        and holdout.get("drawdown", float("inf")) <= 10
        and confidence["confidence"] in {"moderate", "strong"}
    )
    return {
        "selected_count": len(selected),
        "max_portfolio_exposure_pct": 20,
        "full": full,
        "holdout": holdout,
        "monte_carlo": confidence,
        "benchmark": benchmark,
        "eligible": eligible,
    }


def portfolio_variant_lab(
    selected: list[dict],
    radar: list[dict],
    evaluations: list[dict],
    datasets: dict[str, dict],
    risk_settings: dict,
    start: datetime,
    limit: int = 5,
) -> list[dict]:
    baseline = portfolio_validation(selected, datasets, risk_settings, start)
    baseline_return = float(baseline.get("holdout", {}).get("return_pct", 0.0))
    baseline_drawdown = float(baseline.get("holdout", {}).get("drawdown", 0.0))
    engine = PortfolioBacktestEngine(
        fee_pct=0.1,
        slippage_pct=0.05,
        allocation_pct=10,
        max_portfolio_exposure_pct=20,
        **risk_settings,
    )
    lookup = {
        (row.get("symbol"), row.get("strategy")): row
        for row in evaluations
    }
    variants = []
    for candidate in [row for row in radar if row.get("eligible")][:limit]:
        candidate_row = lookup.get((candidate["symbol"], candidate["strategy"]))
        if not candidate_row:
            continue
        replaced = next((row for row in selected if row["symbol"] == candidate["symbol"]), None)
        if not replaced:
            continue
        variant_selection = [
            candidate_row if row["symbol"] == candidate["symbol"] else row
            for row in selected
        ]
        holdout_candidates = []
        for row in variant_selection:
            row_candles = datasets[row["symbol"]]["candles"]
            holdout_start = max(int(row_candles[0]["timestamp"]), int(row_candles[-1]["timestamp"]) - 2 * 365 * 24 * 60 * 60 * 1000)
            candles = [candle for candle in row_candles if int(candle["timestamp"]) >= holdout_start]
            holdout_candidates.append(
                PortfolioCandidate(
                    symbol=row["symbol"],
                    strategy=row["strategy"],
                    candles=candles,
                    regime_filter=row.get("regime_filter", {}).get("settings", {}),
                    holdout_profit_factor=row.get("holdout_metrics", {}).get("profit_factor"),
                )
            )
        holdout_result = engine.run(holdout_candidates)
        holdout = holdout_result["metrics"]
        confidence = PortfolioBacktestEngine.monte_carlo_confidence(holdout_result["trades"], simulations=300)
        eligible = bool(
            holdout.get("trades", 0) >= 30
            and holdout.get("return_pct", 0.0) > 0
            and holdout.get("profit_factor", 0.0) >= 1.05
            and holdout.get("drawdown", float("inf")) <= 10
            and confidence["confidence"] in {"moderate", "strong"}
        )
        return_delta = round(float(holdout.get("return_pct", 0.0)) - baseline_return, 2)
        drawdown_delta = round(float(holdout.get("drawdown", 0.0)) - baseline_drawdown, 2)
        variants.append({
            "replace_symbol": candidate["symbol"],
            "from_strategy": replaced["strategy"],
            "to_strategy": candidate["strategy"],
            "eligible": eligible,
            "holdout_return_pct": holdout.get("return_pct"),
            "holdout_drawdown_pct": holdout.get("drawdown"),
            "holdout_profit_factor": holdout.get("profit_factor"),
            "monte_carlo_confidence": confidence.get("confidence"),
            "return_delta_pct": return_delta,
            "drawdown_delta_pct": drawdown_delta,
            "decision": "candidate_for_review" if eligible and return_delta > 0 and drawdown_delta <= 1 else "keep_watching",
        })
    variants.sort(key=lambda row: (row["decision"] == "candidate_for_review", row["return_delta_pct"], -row["drawdown_delta_pct"]), reverse=True)
    return variants[:limit]


def robustness(metrics: dict, annual: list[dict]) -> tuple[float, bool]:
    years = max(len(annual), 1)
    positive_years = sum(row["return_pct"] > 0 for row in annual)
    median_return = float(np.median([row["return_pct"] for row in annual]))
    holdout = annual[-2:]
    holdout_return = sum(row["return_pct"] for row in holdout)
    score = (
        positive_years / years * 35
        + max(0.0, min(metrics["sharpe_ratio"], 1.5) / 1.5) * 25
        + max(0.0, min(median_return, 8.0) / 8.0) * 20
        + max(0.0, 1 - metrics["drawdown"] / 20) * 20
    )
    eligible = (
        positive_years >= max(2, math.ceil(years * 0.55))
        and median_return > 0
        and metrics["return_pct"] > 0
        and metrics["drawdown"] <= 15
        and metrics["sharpe_ratio"] >= 0.4
        and metrics["trades"] >= 20
        and holdout_return > 0
        and sum(row["return_pct"] > 0 for row in holdout) >= 1
    )
    return round(score, 2), eligible


async def main() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * YEARS)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    datasets: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=60) as client:
        candidates = await liquid_markets(client)
        for market in candidates:
            candles = await download_candles(client, market["symbol"], start_ms, end_ms)
            coverage = (int(candles[-1]["timestamp"]) - int(candles[0]["timestamp"])) / (365 * 24 * 60 * 60 * 1000) if candles else 0
            if coverage >= MIN_EVALUATION_YEARS:
                datasets[market["symbol"]] = {**market, "candles": candles}
            if len(datasets) >= MARKET_COUNT:
                break

    calibration = json.loads(RISK_CALIBRATION.read_text(encoding="utf-8"))
    risk_profile = calibration["chosen"]
    engine = HistoricalBacktestEngine(fee_pct=0.1, slippage_pct=0.05, allocation_pct=10, **risk_profile["settings"])
    evaluations = []
    for symbol, market in datasets.items():
        candles = market["candles"]
        periods = annual_slices(candles)
        annual_periods = [period for period in periods if len(period) >= 500]
        actual_years = len(annual_periods)
        coverage = (int(candles[-1]["timestamp"]) - int(candles[0]["timestamp"])) / (365 * 24 * 60 * 60 * 1000)
        psychology = investor_behavior_snapshot(pd.DataFrame(candles))
        macro = macro_event_snapshot(pd.DataFrame(candles))
        event_exposure = historical_event_exposure(candles)
        for strategy in STRATEGIES:
            regime_selection = choose_regime_filter(engine, strategy, periods)
            regime_filter = regime_selection["chosen"]["settings"]
            full = engine.run(strategy, candles, regime_filter=regime_filter)["metrics"]
            annual = [engine.run(strategy, period, regime_filter=regime_filter)["metrics"] for period in annual_periods]
            holdout_candles = [candle for period in periods[-2:] for candle in period]
            holdout_metrics = engine.run(strategy, holdout_candles, regime_filter=regime_filter)["metrics"]
            score, eligible = robustness(full, annual)
            chosen_regime = regime_selection["chosen"]
            eligible = bool(
                eligible
                and training_is_eligible(regime_selection)
                and holdout_is_eligible(holdout_metrics)
            )
            evaluation = {
                "symbol": symbol,
                "strategy": strategy,
                "eligible": eligible,
                "target_history_years": TARGET_HISTORY_YEARS,
                "available_history_years": round(coverage, 2),
                "evaluation_years": actual_years,
                "history_note": (
                    "Maximum available crypto history used; 26 years hourly data is not available for this asset."
                    if coverage < TARGET_HISTORY_YEARS - 0.25
                    else "Full 26-year hourly target covered."
                ),
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
                "regime_filter": chosen_regime,
                "regime_comparisons": regime_selection["comparisons"],
                "annual_results": annual,
                **full,
            }
            evaluation["expert_score"] = expert_conviction_score(evaluation, years=max(actual_years, 1))
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

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": utc_iso(start_ms), "end": utc_iso(end_ms), "years": YEARS},
        "timeframe": INTERVAL,
        "methodology": {
            "target_history_years": TARGET_HISTORY_YEARS,
            "minimum_available_years": MIN_EVALUATION_YEARS,
            "history_policy": "Use the maximum hourly history available per asset; crypto markets younger than 26 years are not artificially extended.",
            "fee_pct_per_order": 0.1,
            "slippage_pct_per_order": 0.05,
            "allocation_pct": 10,
            "minimum_positive_years": 4,
            "maximum_drawdown_pct": 15,
            "minimum_sharpe": 0.4,
            "minimum_trades": 20,
            "minimum_training_positive_years": 3,
            "holdout_minimum_trades": 30,
            "holdout_minimum_profit_factor": 1.05,
            "holdout_maximum_drawdown_pct": 10,
            "portfolio_holdout_minimum_trades": 30,
            "portfolio_holdout_minimum_profit_factor": 1.05,
            "portfolio_holdout_maximum_drawdown_pct": 10,
            "portfolio_confidence_minimum": "moderate",
            "portfolio_max_exposure_pct": 20,
            "expert_knowledge_layer": {
                "purpose": "Rank markets by out-of-sample edge, risk-adjusted return, consistency, benchmark excess return, sample quality, and investor psychology risk.",
                "principles": [
                    "Time-series momentum and trend-following should be volatility-aware.",
                    "Asset selection must prefer liquid markets with proven holdout performance.",
                    "Position sizing should remain capped until live paper evidence confirms the edge.",
                    "News is a catalyst filter, not a guarantee of direction.",
                    "Historical bubbles and crashes show that crowd euphoria, leverage-like behavior, and panic volume require de-risking.",
                ],
                "pre_21st_century_market_lessons": HISTORICAL_MARKET_LESSONS,
                "macro_event_layer": {
                    "purpose": "Account for pandemics, wars, elections, holidays, regulation, oil shocks, banking stress, and crisis regimes.",
                    "event_catalog": HISTORICAL_MACRO_EVENTS,
                },
            },
            "regime_selection": "Chosen on training years only; final two available years remain holdout when available",
            "regime_filter_complexity_penalty": 2.0,
            "risk_profile": risk_profile,
        },
        "markets": [
            {
                "symbol": symbol,
                "quote_volume_24h": market["quote_volume_24h"],
                "candles": len(market["candles"]),
                "available_history_years": round((int(market["candles"][-1]["timestamp"]) - int(market["candles"][0]["timestamp"])) / (365 * 24 * 60 * 60 * 1000), 2),
            }
            for symbol, market in datasets.items()
        ],
        "selected": selected,
        "portfolio_validation": portfolio,
        "opportunity_radar": radar,
        "portfolio_variant_lab": variants,
        "evaluations": evaluations,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"markets": report["markets"], "selected": selected}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
