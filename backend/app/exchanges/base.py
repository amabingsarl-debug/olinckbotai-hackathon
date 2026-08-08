from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass
class MarketOrder:
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float | None = None


class ExchangeClient(ABC):
    name: str

    @abstractmethod
    async def ticker(self, symbol: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def candles(self, symbol: str, interval: str, limit: int = 250) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, order: MarketOrder) -> dict:
        raise NotImplementedError

    async def tradable_symbols(self, quote_asset: str = "USDT") -> list[str]:
        """Return currently tradable spot symbols when the exchange supports discovery."""
        return []

    async def market_quality(self, symbol: str) -> dict:
        return {"available": False}
