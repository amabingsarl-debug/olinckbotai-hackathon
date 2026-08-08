import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.exchanges.base import ExchangeClient, MarketOrder
from app.exchanges.http import request_public_json


class GateIoSpotClient(ExchangeClient):
    name = "gateio"
    base_url = "https://api.gateio.ws/api/v4"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.gateio_api_key
        self.api_secret = settings.gateio_api_secret

    async def ticker(self, symbol: str) -> dict:
        currency_pair = symbol.replace("USDT", "_USDT")
        async with httpx.AsyncClient(timeout=15) as client:
            rows = await request_public_json(client, "GET", f"{self.base_url}/spot/tickers", params={"currency_pair": currency_pair})
            return rows[0]

    async def candles(self, symbol: str, interval: str, limit: int = 250) -> list[dict]:
        currency_pair = symbol.replace("USDT", "_USDT")
        async with httpx.AsyncClient(timeout=20) as client:
            rows = await request_public_json(
                client,
                "GET",
                f"{self.base_url}/spot/candlesticks",
                params={"currency_pair": currency_pair, "interval": interval, "limit": limit},
            )
            return [
                {"timestamp": row[0], "volume": row[1], "close": row[2], "high": row[3], "low": row[4], "open": row[5]}
                for row in rows
            ]

    async def tradable_symbols(self, quote_asset: str = "USDT") -> list[str]:
        async with httpx.AsyncClient(timeout=20) as client:
            rows = await request_public_json(client, "GET", f"{self.base_url}/spot/currency_pairs")
        return sorted(
            row["id"].replace("_", "")
            for row in rows
            if row.get("quote") == quote_asset and row.get("trade_status") == "tradable"
        )

    async def market_quality(self, symbol: str) -> dict:
        currency_pair = symbol.replace("USDT", "_USDT")
        async with httpx.AsyncClient(timeout=15) as client:
            rows = await request_public_json(client, "GET", f"{self.base_url}/spot/tickers", params={"currency_pair": currency_pair})
            depth = await request_public_json(client, "GET", f"{self.base_url}/spot/order_book", params={"currency_pair": currency_pair, "limit": 20})
        ticker = rows[0] if rows else {}
        bid = float(ticker.get("highest_bid", 0.0))
        ask = float(ticker.get("lowest_ask", 0.0))
        midpoint = (bid + ask) / 2
        bid_depth = sum(float(price) * float(quantity) for price, quantity in depth.get("bids", []) if float(price) >= midpoint * 0.99)
        ask_depth = sum(float(price) * float(quantity) for price, quantity in depth.get("asks", []) if float(price) <= midpoint * 1.01)
        total_depth = bid_depth + ask_depth
        return {
            "available": bid > 0 and ask > bid,
            "bid": bid,
            "ask": ask,
            "spread_pct": round((ask - bid) / midpoint * 100, 4) if midpoint else 999.0,
            "quote_volume_24h": float(ticker.get("quote_volume", 0.0)),
            "trades_24h": None,
            "bid_depth_1pct": round(bid_depth, 2),
            "ask_depth_1pct": round(ask_depth, 2),
            "order_book_imbalance": round(bid_depth / total_depth, 4) if total_depth else 0.0,
        }

    async def place_order(self, order: MarketOrder) -> dict:
        settings = get_settings()
        if not settings.real_trading_enabled:
            return {"paper": True, "exchange": self.name, "order": order.__dict__}
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Gate.io API credentials are missing")
        body = {
            "currency_pair": order.symbol.replace("USDT", "_USDT"),
            "side": order.side,
            "type": "market",
            "amount": str(order.quantity),
        }
        body_string = urlencode(body)
        timestamp = str(int(time.time()))
        signature_payload = "\n".join(["POST", "/api/v4/spot/orders", "", hashlib.sha512(body_string.encode()).hexdigest(), timestamp])
        sign = hmac.new(self.api_secret.encode(), signature_payload.encode(), hashlib.sha512).hexdigest()
        headers = {"KEY": self.api_key, "Timestamp": timestamp, "SIGN": sign}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/spot/orders", data=body, headers=headers)
            response.raise_for_status()
            return response.json()
