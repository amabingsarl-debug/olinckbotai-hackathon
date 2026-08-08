import hmac
import time
from hashlib import sha256
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.exchanges.base import ExchangeClient, MarketOrder
from app.exchanges.http import request_public_json


class BinanceSpotClient(ExchangeClient):
    name = "binance"
    base_url = "https://api.binance.com"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.binance_api_key
        self.api_secret = settings.binance_api_secret

    async def ticker(self, symbol: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            return await request_public_json(client, "GET", f"{self.base_url}/api/v3/ticker/price", params={"symbol": symbol})

    async def candles(self, symbol: str, interval: str, limit: int = 250) -> list[dict]:
        async with httpx.AsyncClient(timeout=20) as client:
            rows = await request_public_json(
                client,
                "GET",
                f"{self.base_url}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            )
            return [
                {"timestamp": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5]}
                for row in rows
            ]

    async def tradable_symbols(self, quote_asset: str = "USDT") -> list[str]:
        async with httpx.AsyncClient(timeout=20) as client:
            payload = await request_public_json(client, "GET", f"{self.base_url}/api/v3/exchangeInfo")
        return sorted(
            row["symbol"]
            for row in payload.get("symbols", [])
            if row.get("status") == "TRADING"
            and row.get("quoteAsset") == quote_asset
            and row.get("isSpotTradingAllowed", True)
        )

    async def market_quality(self, symbol: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            book = await request_public_json(client, "GET", f"{self.base_url}/api/v3/ticker/bookTicker", params={"symbol": symbol})
            ticker = await request_public_json(client, "GET", f"{self.base_url}/api/v3/ticker/24hr", params={"symbol": symbol})
            depth = await request_public_json(client, "GET", f"{self.base_url}/api/v3/depth", params={"symbol": symbol, "limit": 20})
        bid = float(book.get("bidPrice", 0.0))
        ask = float(book.get("askPrice", 0.0))
        midpoint = (bid + ask) / 2
        bid_depth = sum(float(price) * float(quantity) for price, quantity in depth.get("bids", []) if float(price) >= midpoint * 0.99)
        ask_depth = sum(float(price) * float(quantity) for price, quantity in depth.get("asks", []) if float(price) <= midpoint * 1.01)
        total_depth = bid_depth + ask_depth
        return {
            "available": bid > 0 and ask > bid,
            "bid": bid,
            "ask": ask,
            "spread_pct": round((ask - bid) / midpoint * 100, 4) if midpoint else 999.0,
            "quote_volume_24h": float(ticker.get("quoteVolume", 0.0)),
            "trades_24h": int(ticker.get("count", 0)),
            "bid_depth_1pct": round(bid_depth, 2),
            "ask_depth_1pct": round(ask_depth, 2),
            "order_book_imbalance": round(bid_depth / total_depth, 4) if total_depth else 0.0,
        }

    async def place_order(self, order: MarketOrder) -> dict:
        settings = get_settings()
        if not settings.real_trading_enabled:
            return {"paper": True, "exchange": self.name, "order": order.__dict__}
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Binance API credentials are missing")
        payload = {
            "symbol": order.symbol,
            "side": order.side.upper(),
            "type": "MARKET",
            "quantity": order.quantity,
            "timestamp": int(time.time() * 1000),
        }
        query = urlencode(payload)
        payload["signature"] = hmac.new(self.api_secret.encode(), query.encode(), sha256).hexdigest()
        headers = {"X-MBX-APIKEY": self.api_key}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/api/v3/order", params=payload, headers=headers)
            response.raise_for_status()
            return response.json()
