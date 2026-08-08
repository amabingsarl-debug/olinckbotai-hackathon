from datetime import datetime
import hmac
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.analyzer import PerformanceAnalyzer
from app.ai.trading_context_agent import TradingContextAgent
from app.backtesting.engine import BacktestEngine
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.market.news import NewsCatalystService
from app.models.domain import BacktestRun, BotState, ExchangeConfig, RiskConfig, StrategyConfig, SystemLog, Trade, User
from app.notifications.manager import NotificationManager
from app.schemas.domain import BacktestRequest, BotCommand, RiskConfigIn, StrategyToggle, TokenOut, UserCreate
from app.services.statistics import dashboard_metrics
from app.services.trading_engine import trading_engine

router = APIRouter()


async def require_operator_secret(x_operator_secret: str | None = Header(default=None)) -> None:
    expected = get_settings().scheduler_secret
    if not expected or not x_operator_secret or not hmac.compare_digest(x_operator_secret, expected):
        raise HTTPException(status_code=403, detail="Operator credentials required")


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "mode": settings.trading_mode, "real_trading_enabled": settings.real_trading_enabled}


@router.post("/auth/register", response_model=TokenOut)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> dict:
    if get_settings().environment == "production":
        raise HTTPException(status_code=403, detail="Public registration is disabled")
    exists = await db.scalar(select(User).where(User.email == payload.email))
    if exists:
        raise HTTPException(status_code=409, detail="User already exists")
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    await db.commit()
    return {"access_token": create_access_token(user.email)}


@router.post("/auth/login", response_model=TokenOut)
async def login(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> dict:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token(user.email)}


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)) -> dict:
    return await dashboard_metrics(db)


@router.get("/strategies")
async def strategies(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(StrategyConfig).order_by(StrategyConfig.name))
    return [
        {"name": row.name, "enabled": row.enabled, "symbols": row.symbols, "timeframe": row.timeframe, "parameters": row.parameters}
        for row in result.scalars().all()
    ]


@router.patch("/strategies/{name}")
async def toggle_strategy(
    name: str,
    payload: StrategyToggle,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    strategy = await db.scalar(select(StrategyConfig).where(StrategyConfig.name == name))
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    strategy.enabled = payload.enabled
    await db.commit()
    return {"name": strategy.name, "enabled": strategy.enabled}


@router.get("/exchanges")
async def exchanges(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(ExchangeConfig).order_by(ExchangeConfig.name))
    return [{"name": row.name, "enabled": row.enabled, "paper_only": row.paper_only, "settings": row.settings} for row in result.scalars().all()]


@router.patch("/exchanges/{name}")
async def toggle_exchange(
    name: str,
    payload: StrategyToggle,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    exchange = await db.scalar(select(ExchangeConfig).where(ExchangeConfig.name == name))
    if not exchange:
        raise HTTPException(status_code=404, detail="Exchange not found")
    exchange.enabled = payload.enabled
    await db.commit()
    return {"name": exchange.name, "enabled": exchange.enabled}


@router.get("/risk")
async def get_risk(db: AsyncSession = Depends(get_db)) -> dict:
    risk = await db.scalar(select(RiskConfig).where(RiskConfig.name == "default"))
    if not risk:
        return {}
    return {
        "max_capital_per_trade": risk.max_capital_per_trade,
        "stop_loss_pct": risk.stop_loss_pct,
        "take_profit_pct": risk.take_profit_pct,
        "trailing_stop_pct": risk.trailing_stop_pct,
        "break_even_pct": risk.break_even_pct,
        "max_positions": risk.max_positions,
        "max_daily_loss_pct": risk.max_daily_loss_pct,
        "max_weekly_loss_pct": risk.max_weekly_loss_pct,
        "max_monthly_loss_pct": risk.max_monthly_loss_pct,
        "max_consecutive_losses": risk.max_consecutive_losses,
    }


@router.put("/risk")
async def update_risk(
    payload: RiskConfigIn,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    risk = await db.scalar(select(RiskConfig).where(RiskConfig.name == "default"))
    if not risk:
        risk = RiskConfig()
        db.add(risk)
    for key, value in payload.model_dump().items():
        setattr(risk, key, value)
    await db.commit()
    return payload.model_dump()


@router.post("/bot/start")
async def start_bot(
    payload: BotCommand,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    settings = get_settings()
    if payload.mode == "real" and not settings.real_trading_enabled:
        raise HTTPException(status_code=403, detail="Real trading is disabled. Set REAL_TRADING_ENABLED=true explicitly.")
    return await trading_engine.start(db, payload.exchange, payload.symbols, payload.mode)


@router.post("/bot/stop")
async def stop_bot(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    return await trading_engine.stop(db)


@router.get("/bot/status")
async def bot_status(db: AsyncSession = Depends(get_db)) -> dict:
    return await trading_engine.status(db)


@router.post("/bot/tick")
async def bot_tick(x_scheduler_secret: str | None = Header(default=None), db: AsyncSession = Depends(get_db)) -> dict:
    secret = get_settings().scheduler_secret
    if not secret or x_scheduler_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid scheduler credentials")
    return await trading_engine.tick(db)


@router.post("/backtests")
async def run_backtest(
    payload: BacktestRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    result = BacktestEngine().run(payload.strategy, [c.model_dump() for c in payload.candles], payload.initial_capital, payload.parameters)
    run = BacktestRun(
        strategy=payload.strategy,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        metrics=result["metrics"],
        trades=result["trades"],
        equity_curve=result["equity_curve"],
    )
    db.add(run)
    await db.commit()
    return {"id": run.id, **result}


@router.get("/backtests")
async def list_backtests(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(50))
    return [
        {
            "id": row.id,
            "strategy": row.strategy,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "metrics": row.metrics,
            "created_at": row.created_at,
        }
        for row in result.scalars().all()
    ]


@router.get("/backtests/historical-report")
async def historical_backtest_report() -> dict:
    report_path = Path(__file__).resolve().parents[1] / "data" / "backtest_72m.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Historical report is not available")
    return json.loads(report_path.read_text(encoding="utf-8-sig"))


@router.get("/research/walk-forward")
async def walk_forward_report() -> dict:
    report_path = Path(__file__).resolve().parents[1] / "data" / "walk_forward_report.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Walk-forward report is not available")
    return json.loads(report_path.read_text(encoding="utf-8-sig"))


@router.get("/market/catalysts")
async def market_catalysts() -> dict:
    report = await walk_forward_report()
    symbols = sorted({row["symbol"] for row in report.get("selected", [])})
    return await NewsCatalystService().snapshot(symbols)


@router.post("/research/apply")
async def apply_research_selection(
    x_scheduler_secret: str | None = Header(default=None), db: AsyncSession = Depends(get_db)
) -> dict:
    settings = get_settings()
    if not settings.scheduler_secret or x_scheduler_secret != settings.scheduler_secret:
        raise HTTPException(status_code=403, detail="Invalid research credentials")
    report = await walk_forward_report()
    selected_by_strategy: dict[str, list[str]] = {}
    for row in report["selected"]:
        selected_by_strategy.setdefault(row["strategy"], []).append(row["symbol"])
    selected_symbols = sorted({row["symbol"] for row in report["selected"]})
    result = await db.execute(select(StrategyConfig))
    for config in result.scalars().all():
        config.enabled = config.name in selected_by_strategy
        config.symbols = selected_by_strategy.get(config.name, [])
        if config.enabled:
            config.timeframe = report["timeframe"]
    risk = await db.scalar(select(RiskConfig).where(RiskConfig.name == "default"))
    if risk is None:
        risk = RiskConfig(name="default")
        db.add(risk)
    for key, value in report["methodology"]["risk_profile"]["settings"].items():
        setattr(risk, key, value)
    initial_capital = float(report["methodology"].get("initial_capital", 10_000))
    fast_rotation_allocation_pct = min(float(report["methodology"].get("allocation_pct", 10)), 5.0)
    risk.max_capital_per_trade = round(initial_capital * fast_rotation_allocation_pct / 100, 2)
    risk.max_positions = max(int(risk.max_positions or 0), min(8, len(selected_symbols) + 2))
    state = await db.scalar(select(BotState).where(BotState.id == "default"))
    if state is None:
        state = BotState(id="default")
        db.add(state)
    state.symbols = selected_symbols
    await db.commit()
    return {
        "mode": "paper",
        "timeframe": report["timeframe"],
        "risk_profile": report["methodology"]["risk_profile"],
        "symbols": selected_symbols,
        "max_capital_per_trade": risk.max_capital_per_trade,
        "max_positions": risk.max_positions,
        "selected": report["selected"],
    }


@router.get("/logs")
async def logs(db: AsyncSession = Depends(get_db)) -> dict:
    trades = (await db.execute(select(Trade).order_by(Trade.opened_at.desc()).limit(100))).scalars().all()
    systems = (await db.execute(select(SystemLog).order_by(SystemLog.created_at.desc()).limit(100))).scalars().all()
    return {
        "trades": [
            {
                "id": t.id,
                "exchange": t.exchange,
                "strategy": t.strategy,
                "symbol": t.symbol,
                "status": t.status,
                "entry_price": t.entry_price,
                "pnl": t.pnl,
                "opened_at": t.opened_at,
            }
            for t in trades
        ],
        "system": [{"level": s.level, "source": s.source, "message": s.message, "created_at": s.created_at} for s in systems],
    }


@router.post("/ai/report")
async def ai_report(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    metrics = await dashboard_metrics(db)
    result = await db.execute(select(Trade))
    trades = [
        {
            "strategy": t.strategy,
            "symbol": t.symbol,
            "pnl": t.pnl,
            "hour": (t.closed_at or t.opened_at).hour,
        }
        for t in result.scalars().all()
    ]
    report = PerformanceAnalyzer().analyze(trades, metrics)
    await NotificationManager().send("OLINCK BOT AI report", report["summary"])
    return {"generated_at": datetime.utcnow(), **report}


@router.get("/agent-context")
async def agent_context(symbol: str = "BTCUSDT", strategy: str | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    return await TradingContextAgent().analyze(db, symbol=symbol.upper(), strategy=strategy)


@router.post("/agent-context")
async def agent_context_decision(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_operator_secret),
) -> dict:
    symbol = str(payload.get("symbol") or "BTCUSDT").upper()
    strategy = payload.get("strategy")
    return await TradingContextAgent().analyze(db, symbol=symbol, strategy=strategy)


@router.websocket("/ws")
async def websocket_status(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({"type": "status", "payload": trading_engine.last_status, "timestamp": datetime.utcnow().isoformat()})
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
