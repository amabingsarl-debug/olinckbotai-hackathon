from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import ExchangeConfig, RiskConfig, StrategyConfig, User
from app.core.security import hash_password
from app.strategies.catalog import STRATEGIES


async def bootstrap_defaults(db: AsyncSession) -> None:
    for exchange in ["binance", "gateio"]:
        exists = await db.scalar(select(ExchangeConfig).where(ExchangeConfig.name == exchange))
        if not exists:
            db.add(ExchangeConfig(name=exchange, enabled=(exchange == "binance"), paper_only=True))
    for name in STRATEGIES:
        exists = await db.scalar(select(StrategyConfig).where(StrategyConfig.name == name))
        if not exists:
            db.add(StrategyConfig(name=name, enabled=False, symbols=[], timeframe="1h", parameters={}))
    risk = await db.scalar(select(RiskConfig).where(RiskConfig.name == "default"))
    if not risk:
        db.add(RiskConfig())
    admin = await db.scalar(select(User).where(User.email == "admin@olinck.local"))
    if not admin:
        db.add(User(email="admin@olinck.local", password_hash=hash_password("ChangeMe123!"), is_admin=True))
    await db.commit()
