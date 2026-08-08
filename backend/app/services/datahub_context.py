from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass
class DataHubContext:
    mode: str
    assets: list[dict[str, Any]]
    market_sources: list[dict[str, Any]]
    indicators: list[dict[str, Any]]
    strategies: list[dict[str, Any]]
    backtests: list[dict[str, Any]]
    risk_metrics: list[dict[str, Any]]
    prior_decisions: list[dict[str, Any]]
    saved: bool
    source: str


class DataHubClient:
    """Small DataHub adapter with a safe local demo fallback.

    Production mode uses DataHub GMS GraphQL search plus an MCP endpoint marker
    when configured. Demo mode returns curated OlinckBotAI metadata without any
    external secret.
    """

    def __init__(self, *, demo_mode: bool | None = None) -> None:
        settings = get_settings()
        self.demo_mode = settings.datahub_demo_mode if demo_mode is None else demo_mode
        self.gms_url = (settings.datahub_gms_url or "").rstrip("/")
        self.token = settings.datahub_token
        self.platform = settings.datahub_platform
        self.mcp_server_url = settings.datahub_mcp_server_url

    async def get_trading_context(self, symbol: str | None = None) -> DataHubContext:
        if self.demo_mode or not self.gms_url:
            return self._demo_context(symbol)
        return await self._datahub_context(symbol)

    async def record_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.demo_mode or not self.gms_url:
            return {
                "saved": True,
                "mode": "demo",
                "urn": f"urn:li:dataset:(urn:li:dataPlatform:{self.platform},agent_decisions_demo,PROD)",
                "recorded_at": datetime.now(UTC).isoformat(),
            }
        headers = self._headers()
        mutation = {
            "query": """
            mutation updateDataset($urn: String!, $input: DatasetUpdateInput!) {
              updateDataset(urn: $urn, input: $input)
            }
            """,
            "variables": {
                "urn": f"urn:li:dataset:(urn:li:dataPlatform:{self.platform},agent_decisions,PROD)",
                "input": {
                    "description": "OlinckBotAI trading agent decision log. Latest decision stored by API integration.",
                    "customProperties": {
                        "last_decision": str(payload.get("decision", "wait")),
                        "last_symbol": str(payload.get("symbol", "UNKNOWN")),
                        "last_risk_level": str(payload.get("risk_level", "medium")),
                        "last_recorded_at": datetime.now(UTC).isoformat(),
                    },
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(f"{self.gms_url}/api/graphql", json=mutation, headers=headers)
                response.raise_for_status()
            return {"saved": True, "mode": "datahub", "urn": mutation["variables"]["urn"], "recorded_at": datetime.now(UTC).isoformat()}
        except httpx.HTTPError as exc:
            return {"saved": False, "mode": "datahub", "error": str(exc)}

    async def _datahub_context(self, symbol: str | None) -> DataHubContext:
        headers = self._headers()
        query_text = symbol or "OlinckBotAI trading"
        query = {
            "query": """
            query search($input: SearchInput!) {
              search(input: $input) {
                searchResults {
                  entity {
                    urn
                    type
                    ... on Dataset {
                      name
                      description
                    }
                  }
                }
              }
            }
            """,
            "variables": {"input": {"type": "DATASET", "query": query_text, "start": 0, "count": 10}},
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(f"{self.gms_url}/api/graphql", json=query, headers=headers)
                response.raise_for_status()
                raw = response.json()
        except httpx.HTTPError:
            return self._demo_context(symbol, source="datahub_unavailable_demo_fallback")

        datasets = raw.get("data", {}).get("search", {}).get("searchResults", [])
        assets = [
            {
                "urn": item.get("entity", {}).get("urn"),
                "name": item.get("entity", {}).get("name"),
                "description": item.get("entity", {}).get("description"),
                "source": "DataHub GraphQL search",
            }
            for item in datasets
        ]
        fallback = self._demo_context(symbol, source="datahub_graphql")
        fallback.assets = assets or fallback.assets
        fallback.mode = "datahub"
        return fallback

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _demo_context(self, symbol: str | None, *, source: str = "local_demo_context") -> DataHubContext:
        selected_symbol = symbol or "BTCUSDT"
        return DataHubContext(
            mode="demo",
            source=source,
            saved=False,
            assets=[
                {"name": selected_symbol, "type": "crypto_spot_pair", "exchange": "Binance Spot", "trust": "paper/demo"},
                {"name": "ETHUSDT", "type": "crypto_spot_pair", "exchange": "Binance Spot", "trust": "paper/demo"},
                {"name": "TRXUSDT", "type": "crypto_spot_pair", "exchange": "Gate.io Spot", "trust": "paper/demo"},
            ],
            market_sources=[
                {"name": "Binance ticker 24h", "freshness": "realtime when online", "quality": "exchange official"},
                {"name": "Walk-forward research files", "freshness": "local snapshots", "quality": "out-of-sample labelled"},
            ],
            indicators=[
                {"name": "EMA Cross", "purpose": "trend shift"},
                {"name": "RSI", "purpose": "overbought/oversold"},
                {"name": "ATR", "purpose": "volatility risk sizing"},
                {"name": "VWAP", "purpose": "intraday fair value"},
            ],
            strategies=[
                {"name": "Volume Spike", "status": "eligible when liquidity and momentum align"},
                {"name": "Breakout", "status": "requires confirmed volatility expansion"},
                {"name": "Mean Reversion", "status": "only under capped drawdown"},
            ],
            backtests=[
                {"name": "walk_forward_report", "method": "time split validation", "anti_overfit": True},
                {"name": "backtest_72m", "method": "long horizon robustness", "anti_overfit": True},
            ],
            risk_metrics=[
                {"name": "max capital per trade", "rule": "hard risk cap"},
                {"name": "daily loss guard", "rule": "stop or observe after breach"},
                {"name": "consecutive loss guard", "rule": "reduce exposure after repeated failures"},
            ],
            prior_decisions=[
                {
                    "symbol": selected_symbol,
                    "decision": "wait",
                    "reason": "demo memory requires market score and risk confirmation before entry",
                    "risk_level": "medium",
                }
            ],
        )
