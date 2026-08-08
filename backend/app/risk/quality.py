def quality_allocation(holdout_profit_factor: float | None) -> dict:
    value = float(holdout_profit_factor or 0.0)
    if value >= 1.6:
        multiplier = 1.0
        tier = "institutional"
    elif value >= 1.4:
        multiplier = 0.75
        tier = "strong"
    elif value >= 1.25:
        multiplier = 0.45
        tier = "probation"
    else:
        multiplier = 0.25
        tier = "quarantined"
    return {
        "tier": tier,
        "multiplier": multiplier,
        "holdout_profit_factor": round(value, 3),
        "risk_increase": False,
    }
