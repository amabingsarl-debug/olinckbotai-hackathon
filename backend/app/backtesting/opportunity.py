def opportunity_score(row: dict) -> float:
    holdout = row.get("holdout_metrics", {})
    return round(
        max(0.0, float(holdout.get("return_pct", 0.0))) * 3
        + max(0.0, float(row.get("expert_score", 0.0))) * 0.35
        + max(0.0, float(row.get("return_pct", 0.0))) * 0.8
        + max(0.0, float(holdout.get("profit_factor", 0.0)) - 1) * 20
        + max(0.0, float(row.get("robustness_score", 0.0))) * 0.25
        - float(holdout.get("drawdown", row.get("drawdown", 0.0))) * 1.2,
        2,
    )


def opportunity_notes(row: dict, selected_symbols: set[str], selected_pairs: set[tuple[str, str]]) -> list[str]:
    notes = []
    holdout = row.get("holdout_metrics", {})
    pair = (row.get("strategy"), row.get("symbol"))
    if pair in selected_pairs:
        notes.append("active")
    if row.get("symbol") in selected_symbols and pair not in selected_pairs:
        notes.append("same_asset_as_active_selection")
    if not row.get("eligible"):
        if float(row.get("drawdown", 0.0)) > 15:
            notes.append("full_drawdown_too_high")
        if int(row.get("positive_years", 0)) < 4:
            notes.append("insufficient_positive_years")
        if int(row.get("holdout_positive_years", 0)) < 1:
            notes.append("weak_holdout_consistency")
        if float(holdout.get("return_pct", row.get("holdout_return_pct", 0.0))) <= 0:
            notes.append("negative_holdout")
        if float(holdout.get("profit_factor", 0.0)) < 1.05:
            notes.append("holdout_profit_factor_too_low")
        if float(holdout.get("drawdown", 0.0)) > 10:
            notes.append("holdout_drawdown_too_high")
    if not notes:
        notes.append("watch_for_next_rotation")
    return notes


def opportunity_radar(evaluations: list[dict], selected: list[dict], limit: int = 10) -> list[dict]:
    selected_pairs = {(row["strategy"], row["symbol"]) for row in selected}
    selected_symbols = {row["symbol"] for row in selected}
    candidates = []
    for row in evaluations:
        if (row.get("strategy"), row.get("symbol")) in selected_pairs:
            continue
        score = opportunity_score(row)
        if score <= 0:
            continue
        candidates.append({
            "symbol": row.get("symbol"),
            "strategy": row.get("strategy"),
            "eligible": bool(row.get("eligible")),
            "opportunity_score": score,
            "return_pct": row.get("return_pct"),
            "drawdown": row.get("drawdown"),
            "profit_factor": row.get("profit_factor"),
            "holdout_return_pct": row.get("holdout_metrics", {}).get("return_pct", row.get("holdout_return_pct")),
            "holdout_drawdown_pct": row.get("holdout_metrics", {}).get("drawdown"),
            "holdout_profit_factor": row.get("holdout_metrics", {}).get("profit_factor"),
            "notes": opportunity_notes(row, selected_symbols, selected_pairs),
            "activation": "paper_watch_only",
            "expert_score": row.get("expert_score"),
            "expert_decision": row.get("expert_decision"),
        })
    candidates.sort(key=lambda row: (row["eligible"], row["opportunity_score"]), reverse=True)
    return candidates[:limit]
