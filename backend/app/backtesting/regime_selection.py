import numpy as np

from app.backtesting.historical import HistoricalBacktestEngine
from app.strategies.market_regime import REGIME_FILTERS


def training_score(annual: list[dict]) -> float:
    if not annual:
        return -1_000.0
    positive_years = sum(row["return_pct"] > 0 for row in annual)
    median_return = float(np.median([row["return_pct"] for row in annual]))
    median_sharpe = float(np.median([row["sharpe_ratio"] for row in annual]))
    worst_drawdown = max(row["drawdown"] for row in annual)
    return round(
        positive_years / len(annual) * 40
        + max(0.0, min(median_return, 8.0) / 8.0) * 25
        + max(0.0, min(median_sharpe, 1.5) / 1.5) * 20
        + max(0.0, 1 - worst_drawdown / 20) * 15,
        3,
    )


def choose_regime_filter(engine: HistoricalBacktestEngine, strategy: str, periods: list[list[dict]]) -> dict:
    candidates = REGIME_FILTERS if strategy == "Volume Spike" else {"none": {}}
    comparisons = []
    for name, settings in candidates.items():
        training = [engine.run(strategy, period, regime_filter=settings)["metrics"] for period in periods[:4] if len(period) >= 500]
        raw_score = training_score(training)
        adjusted_score = raw_score - (2.0 if settings else 0.0)
        comparisons.append({
            "name": name,
            "settings": settings,
            "training_score": raw_score,
            "adjusted_score": round(adjusted_score, 3),
            "training_return_pct": round(sum(row["return_pct"] for row in training), 2),
            "training_positive_years": sum(row["return_pct"] > 0 for row in training),
        })
    comparisons.sort(key=lambda row: row["adjusted_score"], reverse=True)
    return {"chosen": comparisons[0], "comparisons": comparisons}


def training_is_eligible(regime_selection: dict, minimum_positive_years: int = 3) -> bool:
    chosen = regime_selection["chosen"]
    return bool(
        chosen["training_positive_years"] >= minimum_positive_years
        and chosen["training_return_pct"] > 0
    )


def holdout_is_eligible(
    metrics: dict,
    minimum_trades: int = 30,
    minimum_profit_factor: float = 1.05,
    maximum_drawdown_pct: float = 10.0,
) -> bool:
    return bool(
        metrics.get("trades", 0) >= minimum_trades
        and metrics.get("return_pct", 0.0) > 0
        and metrics.get("profit_factor", 0.0) >= minimum_profit_factor
        and metrics.get("drawdown", float("inf")) <= maximum_drawdown_pct
    )
