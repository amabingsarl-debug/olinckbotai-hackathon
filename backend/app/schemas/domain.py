from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class StrategyToggle(BaseModel):
    enabled: bool


class StrategyOut(BaseModel):
    name: str
    enabled: bool
    symbols: list[str]
    timeframe: str
    parameters: dict


class RiskConfigIn(BaseModel):
    max_capital_per_trade: float = 100.0
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    trailing_stop_pct: float = 1.0
    break_even_pct: float = 1.5
    max_positions: int = 5
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 7.0
    max_monthly_loss_pct: float = 12.0
    max_consecutive_losses: int = 4


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BacktestRequest(BaseModel):
    strategy: str
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    initial_capital: float = 10_000
    candles: list[Candle]
    parameters: dict = {}


class BotCommand(BaseModel):
    mode: Literal["paper", "real"] = "paper"
    exchange: str = "binance"
    symbols: list[str] = ["BTCUSDT", "ETHUSDT"]


class DashboardOut(BaseModel):
    capital: float
    equity: float
    open_unrealized_pnl: float
    open_exposure: float
    exposure_limit: float
    exposure_remaining: float
    exposure_utilization_pct: float
    profit_daily: float
    profit_weekly: float
    profit_monthly: float
    profit_annual: float
    open_trades: int
    closed_trades: int
    win_rate: float
    profit_factor: float
    drawdown: float
    performance_curve: list[dict]
    gain_distribution: list[dict]
    loss_distribution: list[dict]
    active_selection: dict
