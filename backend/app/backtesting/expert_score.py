import math


def expert_conviction_score(row: dict, years: int = 6) -> float:
    holdout = row.get("holdout_metrics", {})
    holdout_return = float(holdout.get("return_pct", row.get("holdout_return_pct", 0.0)) or 0.0)
    full_return = float(row.get("return_pct", 0.0) or 0.0)
    holdout_drawdown = float(holdout.get("drawdown", row.get("drawdown", 0.0)) or 0.0)
    full_drawdown = float(row.get("drawdown", 0.0) or 0.0)
    profit_factor = float(holdout.get("profit_factor", row.get("profit_factor", 0.0)) or 0.0)
    sharpe = float(holdout.get("sharpe_ratio", row.get("sharpe_ratio", 0.0)) or 0.0)
    trades = int(holdout.get("trades", row.get("trades", 0)) or 0)
    positive_years = int(row.get("positive_years", 0) or 0)
    holdout_positive_years = int(row.get("holdout_positive_years", 0) or 0)
    benchmark_return = float(row.get("benchmark_return_pct", 0.0) or 0.0)
    psychology = row.get("behavioral_psychology", {})
    psychology_risk = float(psychology.get("risk_score", 0.0) or 0.0)
    psychology_stance = psychology.get("stance", "normal")
    macro = row.get("macro_event_context", {})
    macro_risk = float(macro.get("risk_score", 0.0) or 0.0)
    macro_stance = macro.get("stance", "normal")

    consistency = positive_years / max(years, 1)
    holdout_consistency = holdout_positive_years / 2
    excess_return = holdout_return - benchmark_return
    sample_quality = min(math.log1p(max(trades, 0)) / math.log(121), 1.0)

    score = (
        max(0.0, holdout_return) * 3.0
        + max(0.0, excess_return) * 1.5
        + max(0.0, full_return) * 0.35
        + max(0.0, profit_factor - 1.0) * 28
        + max(0.0, min(sharpe, 2.0)) * 10
        + consistency * 18
        + holdout_consistency * 16
        + sample_quality * 8
        - holdout_drawdown * 1.4
        - max(0.0, full_drawdown - 10) * 0.8
        - psychology_risk * 0.18
        - (4 if psychology_stance == "risk_off" else 0)
        - macro_risk * 0.12
        - (3 if macro_stance == "risk_off" else 0)
    )
    return round(score, 2)


def expert_decision(row: dict) -> str:
    score = float(row.get("expert_score", 0.0) or 0.0)
    holdout = row.get("holdout_metrics", {})
    if not row.get("eligible"):
        return "reject_until_retested"
    if score >= 70 and float(holdout.get("profit_factor", 0.0) or 0.0) >= 1.4:
        return "priority_candidate"
    if score >= 45:
        return "tradable_candidate"
    return "watch_only"
