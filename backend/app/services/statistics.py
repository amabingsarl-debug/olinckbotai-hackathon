from datetime import datetime, timedelta
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import ListingState, ShadowTrade, Trade, TradeStatus
from app.risk.manager import RiskManager
from app.risk.quality import quality_allocation


async def dashboard_metrics(db: AsyncSession) -> dict:
    result = await db.execute(select(Trade))
    trades = list(result.scalars().all())
    shadow_result = await db.execute(select(ShadowTrade))
    shadow_trades = list(shadow_result.scalars().all())
    listing_result = await db.execute(select(ListingState))
    listing_states = list(listing_result.scalars().all())
    closed = [t for t in trades if t.status == TradeStatus.closed]
    open_trades = [t for t in trades if t.status == TradeStatus.open]
    now = datetime.utcnow()

    def pnl_since(days: int) -> float:
        cutoff = now - timedelta(days=days)
        return round(sum(t.pnl for t in closed if t.closed_at and t.closed_at >= cutoff), 2)

    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl < 0]
    capital = 10_000 + sum(t.pnl for t in closed)
    open_unrealized_pnl = round(sum(float((t.metadata_json or {}).get("unrealized_pnl", 0.0)) for t in open_trades), 2)
    open_exposure = round(
        sum(float((t.metadata_json or {}).get("entry_notional", t.entry_price * t.quantity)) for t in open_trades),
        2,
    )
    equity_now = round(capital + open_unrealized_pnl, 2)
    exposure_limit = round(max(0.0, capital) * RiskManager.DEFAULT_MAX_PORTFOLIO_EXPOSURE_PCT / 100, 2)
    exposure_remaining = round(max(0.0, exposure_limit - open_exposure), 2)
    equity = 10_000
    peak = equity
    curve = []
    drawdown = 0.0
    for trade in sorted(closed, key=lambda t: t.closed_at or now):
        equity += trade.pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, (equity - peak) / peak)
        curve.append({"date": (trade.closed_at or now).isoformat(), "equity": round(equity, 2)})
    if open_trades:
        peak = max(peak, equity_now)
        drawdown = min(drawdown, (equity_now - peak) / peak)
        curve.append({"date": now.isoformat(), "equity": equity_now})
    active_selection = _active_selection_metrics(trades)
    return {
        "capital": round(capital, 2),
        "equity": equity_now,
        "open_unrealized_pnl": open_unrealized_pnl,
        "open_exposure": open_exposure,
        "exposure_limit": exposure_limit,
        "exposure_remaining": exposure_remaining,
        "exposure_utilization_pct": round(open_exposure / exposure_limit * 100, 2) if exposure_limit else 0.0,
        "profit_daily": pnl_since(1),
        "profit_weekly": pnl_since(7),
        "profit_monthly": pnl_since(30),
        "profit_annual": pnl_since(365),
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "win_rate": round((len(wins) / len(closed)) * 100, 2) if closed else 0.0,
        "profit_factor": round(sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses)), 3) if losses else float(bool(wins)),
        "drawdown": round(abs(drawdown) * 100, 2),
        "performance_curve": curve,
        "gain_distribution": [{"strategy": t.strategy, "pnl": t.pnl} for t in wins[-50:]],
        "loss_distribution": [{"strategy": t.strategy, "pnl": t.pnl} for t in losses[-50:]],
        "active_selection": active_selection,
        "shadow_research": _shadow_research_metrics(shadow_trades, _walk_forward_report()),
        "learning": _learning_metrics(trades),
        "meme_sprint": _meme_sprint_metrics(trades, listing_states, now),
    }


def _meme_sprint_metrics(trades: list[Trade], listings: list[ListingState], now: datetime) -> dict:
    sprint = [trade for trade in trades if (trade.metadata_json or {}).get("execution_style") == "new_listing_meme_sprint"]
    open_trades = [trade for trade in sprint if trade.status == TradeStatus.open]
    closed = [trade for trade in sprint if trade.status == TradeStatus.closed]
    wins = [trade for trade in closed if trade.pnl > 0]
    losses = [trade for trade in closed if trade.pnl < 0]
    cutoff = now - timedelta(days=7)
    watched = [row for row in listings if not row.baseline and row.first_seen_at >= cutoff]
    exposure = sum(float((trade.metadata_json or {}).get("entry_notional", 0.0)) for trade in open_trades)
    open_pnl = sum(float((trade.metadata_json or {}).get("unrealized_pnl", 0.0)) for trade in open_trades)
    streak = _consecutive_losses(closed)
    return {
        "mode": "paper_only",
        "status": "paused_after_losses" if streak >= 2 else "armed",
        "watched_new_listings": len(watched),
        "symbols": sorted(row.symbol for row in watched),
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "open_exposure": round(exposure, 2),
        "closed_pnl": round(sum(trade.pnl for trade in closed), 2),
        "open_pnl": round(open_pnl, 2),
        "total_pnl": round(sum(trade.pnl for trade in closed) + open_pnl, 2),
        "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "profit_factor": _profit_factor(wins, losses),
        "consecutive_losses": streak,
        "max_consecutive_losses": 2,
        "position_cap_pct": 0.25,
        "exposure_cap_pct": 1.0,
    }


def _active_selection_metrics(trades: list[Trade]) -> dict:
    report = _walk_forward_report()
    generated_at = report.get("generated_at")
    selected = report.get("selected", [])
    selected_pair_set = {(row.get("strategy"), row.get("symbol")) for row in selected}
    tagged_closed = [
        trade
        for trade in trades
        if trade.status == TradeStatus.closed
        and (
            (trade.metadata_json or {}).get("research", {}).get("generated_at") == generated_at
            or (trade.metadata_json or {}).get("selection_tracking_generation") == generated_at
        )
    ]
    base_allocation_pct = float(report.get("methodology", {}).get("allocation_pct", 10.0))
    selected_pairs = []
    for row in selected:
        allocation = quality_allocation(row.get("holdout_metrics", {}).get("profit_factor"))
        selected_pairs.append(
            {
                "symbol": row.get("symbol"),
                "strategy": row.get("strategy"),
                "holdout_profit_factor": allocation["holdout_profit_factor"],
                "allocation_tier": allocation["tier"],
                "max_allocation_pct": round(base_allocation_pct * allocation["multiplier"], 2),
            }
        )
    closed = tagged_closed
    open_trades = [
        trade
        for trade in trades
        if trade.status == TradeStatus.open and (trade.strategy, trade.symbol) in selected_pair_set
    ]
    closed_pnl = round(sum(trade.pnl for trade in closed), 2)
    open_pnl = round(sum(float((trade.metadata_json or {}).get("unrealized_pnl", 0.0)) for trade in open_trades), 2)
    exposure = round(
        sum(float((trade.metadata_json or {}).get("entry_notional", trade.entry_price * trade.quantity)) for trade in open_trades),
        2,
    )
    wins = [trade for trade in closed if trade.pnl > 0]
    losses = [trade for trade in closed if trade.pnl < 0]
    profit_factor = _profit_factor(wins, losses)
    selection_drawdown = _selection_drawdown(closed)
    total_pnl = round(closed_pnl + open_pnl, 2)
    pnl_pct = round(total_pnl / 10_000 * 100, 3)
    consecutive_losses = _consecutive_losses(closed)
    max_loss_pct = 1.5
    max_consecutive_losses = 3
    loss_breach = pnl_pct <= -max_loss_pct
    streak_breach = consecutive_losses >= max_consecutive_losses
    guard_breached = loss_breach or streak_breach
    if loss_breach:
        guard_status = "observe_only"
        engine_action = "observe_only"
    elif streak_breach:
        guard_status = "recovery"
        engine_action = "recover"
    else:
        guard_status = "armed"
        engine_action = "run"
    forward = _forward_validation(report, generated_at, total_pnl, pnl_pct, len(closed), len(open_trades))
    promotion = _promotion_readiness(
        generated_at,
        len(closed),
        total_pnl,
        profit_factor,
        selection_drawdown,
        guard_breached,
    )
    return {
        "generated_at": generated_at,
        "timeframe": report.get("timeframe"),
        "risk_profile": report.get("methodology", {}).get("risk_profile", {}).get("name"),
        "selected_pairs": selected_pairs,
        "portfolio_validation": report.get("portfolio_validation", {}),
        "closed_pnl": closed_pnl,
        "open_pnl": open_pnl,
        "total_pnl": total_pnl,
        "pnl_pct": pnl_pct,
        "open_exposure": exposure,
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "profit_factor": profit_factor,
        "drawdown": selection_drawdown,
        "guard": {
            "status": guard_status,
            "breached": guard_breached,
            "engine_action": engine_action,
            "max_loss_pct": max_loss_pct,
            "consecutive_losses": consecutive_losses,
            "max_consecutive_losses": max_consecutive_losses,
        },
        "forward_validation": forward,
        "promotion_readiness": promotion,
    }


def _walk_forward_report() -> dict:
    report_path = Path(__file__).resolve().parents[1] / "data" / "walk_forward_report.json"
    if not report_path.exists():
        return {}
    return json.loads(report_path.read_text(encoding="utf-8-sig"))


def _shadow_research_metrics(trades: list[ShadowTrade], report: dict | None = None) -> dict:
    open_trades = [trade for trade in trades if trade.status == TradeStatus.open]
    closed = [trade for trade in trades if trade.status == TradeStatus.closed]
    open_pnl = round(sum(float((trade.metadata_json or {}).get("unrealized_pnl", 0.0)) for trade in open_trades), 2)
    closed_pnl = round(sum(trade.pnl for trade in closed), 2)
    total_pnl = round(open_pnl + closed_pnl, 2)
    grouped: dict[tuple[str, str], list[ShadowTrade]] = {}
    for trade in trades:
        grouped.setdefault((trade.strategy, trade.symbol), []).append(trade)
    research_by_pair = {
        (row.get("strategy"), row.get("symbol")): row
        for row in _shadow_research_candidates(report or {})
    }
    candidates = []
    for pair in sorted(set(grouped) | set(research_by_pair), key=lambda item: float(research_by_pair.get(item, {}).get("opportunity_score") or 0.0), reverse=True):
        strategy, symbol = pair
        rows = grouped.get(pair, [])
        candidate_closed = [trade for trade in rows if trade.status == TradeStatus.closed]
        candidate_open = [trade for trade in rows if trade.status == TradeStatus.open]
        wins = [trade for trade in candidate_closed if trade.pnl > 0]
        losses = [trade for trade in candidate_closed if trade.pnl < 0]
        candidate_open_pnl = sum(float((trade.metadata_json or {}).get("unrealized_pnl", 0.0)) for trade in candidate_open)
        candidate_total = round(sum(trade.pnl for trade in candidate_closed) + candidate_open_pnl, 2)
        drawdown = _selection_drawdown(candidate_closed)
        profit_factor = _profit_factor(wins, losses)
        ready = len(candidate_closed) >= 5 and candidate_total > 0 and profit_factor >= 1.2 and drawdown <= 1.5
        sample_meta = (rows[-1].metadata_json if rows else {}) or {}
        research = sample_meta.get("candidate") or research_by_pair.get(pair, {})
        candidates.append(
            {
                "symbol": symbol,
                "strategy": strategy,
                "source": research.get("source", "shadow"),
                "closed_trades": len(candidate_closed),
                "open_trades": len(candidate_open),
                "closed_pnl": round(sum(trade.pnl for trade in candidate_closed), 2),
                "open_pnl": round(candidate_open_pnl, 2),
                "total_pnl": candidate_total,
                "win_rate": round(len(wins) / len(candidate_closed) * 100, 2) if candidate_closed else 0.0,
                "profit_factor": profit_factor,
                "drawdown": drawdown,
                "holdout_return_pct": research.get("holdout_return_pct"),
                "holdout_profit_factor": research.get("holdout_profit_factor"),
                "opportunity_score": research.get("opportunity_score"),
                "ready_for_review": ready,
            }
        )
    candidates.sort(key=lambda row: (row["ready_for_review"], row["total_pnl"], row["profit_factor"]), reverse=True)
    return {
        "status": "collecting_live_evidence",
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "closed_pnl": closed_pnl,
        "open_pnl": open_pnl,
        "total_pnl": total_pnl,
        "candidates": candidates[:8],
        "promotion_rule": "Revue seulement apres au moins 5 trades clotures, PnL positif, PF >= 1.2 et DD <= 1.5%.",
    }


def _shadow_research_candidates(report: dict, limit: int = 8) -> list[dict]:
    selected_pairs = {(row.get("strategy"), row.get("symbol")) for row in report.get("selected", [])}
    candidates = []
    seen: set[tuple[str, str]] = set()
    for row in sorted(report.get("opportunity_radar", []), key=lambda item: float(item.get("opportunity_score", 0.0)), reverse=True):
        pair = (row.get("strategy"), row.get("symbol"))
        if pair in selected_pairs or pair in seen:
            continue
        if float(row.get("holdout_return_pct") or 0.0) <= 0:
            continue
        candidates.append(
            {
                "symbol": row.get("symbol"),
                "strategy": row.get("strategy"),
                "source": "opportunity_radar",
                "opportunity_score": row.get("opportunity_score"),
                "holdout_return_pct": row.get("holdout_return_pct"),
                "holdout_profit_factor": row.get("holdout_profit_factor"),
            }
        )
        seen.add(pair)
        if len(candidates) >= limit:
            break
    return candidates


def _learning_metrics(trades: list[Trade]) -> dict:
    closed = [trade for trade in trades if trade.status == TradeStatus.closed]
    grouped: dict[tuple[str, str, str], list[Trade]] = {}
    for trade in closed:
        style = (trade.metadata_json or {}).get("execution_style", "core_selection")
        grouped.setdefault((trade.symbol, trade.strategy, style), []).append(trade)
    rows = []
    for (symbol, strategy, style), items in grouped.items():
        recent = sorted(items, key=lambda row: row.closed_at or datetime.min)[-12:]
        wins = [trade for trade in recent if trade.pnl > 0]
        losses = [trade for trade in recent if trade.pnl < 0]
        total = round(sum(trade.pnl for trade in recent), 2)
        profit_factor = _profit_factor(wins, losses)
        if len(recent) >= 4 and total < 0 and profit_factor < 0.85:
            status = "blocked_after_losses"
            multiplier = 0.0
        elif len(recent) >= 3 and (total < 0 or profit_factor < 1.0):
            status = "reduced_after_weak_results"
            multiplier = 0.55
        elif len(recent) >= 5 and total > 0 and profit_factor >= 1.35 and (len(wins) / len(recent)) >= 0.45:
            status = "boosted_after_good_results"
            multiplier = 1.18
        else:
            status = "neutral"
            multiplier = 1.0
        rows.append(
            {
                "symbol": symbol,
                "strategy": strategy,
                "execution_style": style,
                "status": status,
                "multiplier": multiplier,
                "closed_trades": len(recent),
                "total_pnl": total,
                "win_rate": round(len(wins) / len(recent) * 100, 2) if recent else 0.0,
                "profit_factor": profit_factor,
            }
        )
    rows.sort(key=lambda row: (row["status"] == "boosted_after_good_results", row["total_pnl"]), reverse=True)
    return {
        "status": "active",
        "rule": "Le bot reduit ou bloque les configurations perdantes et augmente legerement les configurations gagnantes apres echantillon live.",
        "groups": rows[:12],
    }


def _forward_validation(report: dict, generated_at: str | None, total_pnl: float, pnl_pct: float, closed_trades: int, open_trades: int) -> dict:
    selected = report.get("selected", [])
    expected_median_pct = round(sum(float(row.get("median_year_return_pct", 0.0)) for row in selected), 2)
    expected_holdout_pct = round(
        sum(float(row.get("holdout_metrics", {}).get("return_pct", row.get("holdout_return_pct", 0.0))) for row in selected),
        2,
    )
    expected_drawdown_pct = round(max((float(row.get("drawdown", 0.0)) for row in selected), default=0.0), 2)
    portfolio_holdout = report.get("portfolio_validation", {}).get("holdout", {})
    if portfolio_holdout:
        expected_holdout_pct = round(float(portfolio_holdout.get("return_pct", expected_holdout_pct)), 2)
        expected_drawdown_pct = round(float(portfolio_holdout.get("drawdown", expected_drawdown_pct)), 2)
    days_live = _days_since(generated_at)
    expected_trade_count_annual = sum(float(row.get("trades", 0.0)) for row in selected) / max(float(report.get("period", {}).get("years", 1)), 1)
    expected_trades_so_far = round(expected_trade_count_annual * days_live / 365, 2)
    min_trades_for_judgement = max(5, round(expected_trades_so_far * 0.5))
    if closed_trades < min_trades_for_judgement:
        status = "observation"
        reason = "Pas encore assez de trades live pour juger la selection."
    elif pnl_pct < -min(1.0, max(expected_drawdown_pct * 0.25, 0.5)):
        status = "underperforming"
        reason = "La performance live est sous le seuil de vigilance."
    elif pnl_pct < 0:
        status = "watch"
        reason = "La selection est negative mais reste dans la zone de surveillance."
    else:
        status = "healthy"
        reason = "La selection live reste conforme ou positive."
    return {
        "status": status,
        "reason": reason,
        "days_live": round(days_live, 2),
        "expected_trades_so_far": expected_trades_so_far,
        "min_trades_for_judgement": min_trades_for_judgement,
        "closed_trades": closed_trades,
        "open_trades": open_trades,
        "live_pnl": round(total_pnl, 2),
        "live_return_pct": pnl_pct,
        "expected_median_return_pct": expected_median_pct,
        "expected_holdout_return_pct": expected_holdout_pct,
        "expected_max_drawdown_pct": expected_drawdown_pct,
    }


def _days_since(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        started_at = datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return 0.0
    return max(0.0, (datetime.utcnow() - started_at).total_seconds() / 86_400)


def _promotion_readiness(
    generated_at: str | None,
    closed_trades: int,
    total_pnl: float,
    profit_factor: float,
    drawdown_pct: float,
    guard_breached: bool,
) -> dict:
    days_live = _days_since(generated_at)
    requirements = {
        "minimum_days": {"target": 30, "current": round(days_live, 2), "passed": days_live >= 30},
        "minimum_closed_trades": {"target": 20, "current": closed_trades, "passed": closed_trades >= 20},
        "positive_pnl": {"target": 0, "current": round(total_pnl, 2), "passed": total_pnl > 0},
        "minimum_profit_factor": {"target": 1.2, "current": profit_factor, "passed": profit_factor >= 1.2},
        "maximum_drawdown_pct": {"target": 1.0, "current": drawdown_pct, "passed": drawdown_pct <= 1.0},
        "guard_clear": {"target": True, "current": not guard_breached, "passed": not guard_breached},
    }
    passed = sum(item["passed"] for item in requirements.values())
    ready = passed == len(requirements)
    return {
        "status": "ready_for_review" if ready else "collecting_evidence",
        "ready": ready,
        "passed_requirements": passed,
        "total_requirements": len(requirements),
        "requirements": requirements,
        "risk_increase_automatic": False,
        "reason": (
            "Echantillon paper suffisamment mature pour une revue manuelle."
            if ready
            else "Le risque reste bloque au niveau actuel pendant la collecte de preuves."
        ),
    }


def _profit_factor(wins: list[Trade], losses: list[Trade]) -> float:
    if losses:
        return round(sum(trade.pnl for trade in wins) / abs(sum(trade.pnl for trade in losses)), 3)
    return 999.0 if wins else 0.0


def _selection_drawdown(trades: list[Trade], initial_capital: float = 10_000) -> float:
    equity = initial_capital
    peak = equity
    drawdown = 0.0
    for trade in sorted(trades, key=lambda row: row.closed_at or datetime.min):
        equity += trade.pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, (equity - peak) / peak)
    return round(abs(drawdown) * 100, 3)


def _consecutive_losses(trades: list[Trade]) -> int:
    losses = 0
    for trade in sorted(trades, key=lambda row: row.closed_at or datetime.min, reverse=True):
        if trade.pnl < 0:
            losses += 1
        else:
            break
    return losses
