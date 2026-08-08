from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from app.core.config import get_settings


@dataclass
class AgentMemory:
    id: str
    symbol: str
    market_context: dict[str, Any]
    indicators: dict[str, Any]
    strategy: str
    risk_level: str
    decision: str
    reasoning: str
    created_at: str
    outcome: dict[str, Any] | None = None
    embedding: list[float] = field(default_factory=list)
    similarity: float | None = None


class AgentMemoryService:
    """Persistent trading-agent memory backed by CockroachDB.

    CockroachDB mode uses a VECTOR column and vector distance ordering. Demo
    mode writes to a local JSON file so the hackathon demo works without cloud
    secrets or a live cluster.
    """

    def __init__(self, *, demo_mode: bool | None = None, storage_path: Path | None = None) -> None:
        settings = get_settings()
        self.demo_mode = settings.cockroach_demo_mode if demo_mode is None else demo_mode
        self.database_url = settings.cockroach_database_url
        self.dimensions = settings.cockroach_vector_dimensions
        self.storage_path = storage_path or Path(__file__).resolve().parents[1] / "data" / "agent_memory_demo.json"

    async def remember(self, payload: dict[str, Any]) -> AgentMemory:
        memory = self._memory_from_payload(payload)
        if self.demo_mode or not self.database_url:
            memories = self._read_demo()
            memories.append(asdict(memory))
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(json.dumps(memories, indent=2), encoding="utf-8")
            return memory
        await self._ensure_schema()
        conn = await asyncpg.connect(self.database_url)
        try:
            await conn.execute(
                """
                INSERT INTO agent_memories
                    (id, symbol, market_context, indicators, strategy, risk_level, decision, reasoning, outcome, embedding, created_at)
                VALUES
                    ($1, $2, $3::JSONB, $4::JSONB, $5, $6, $7, $8, $9::JSONB, $10::VECTOR, $11)
                """,
                memory.id,
                memory.symbol,
                json.dumps(memory.market_context),
                json.dumps(memory.indicators),
                memory.strategy,
                memory.risk_level,
                memory.decision,
                memory.reasoning,
                json.dumps(memory.outcome or {}),
                self._vector_literal(memory.embedding),
                datetime.fromisoformat(memory.created_at),
            )
        finally:
            await conn.close()
        return memory

    async def retrieve(self, memory_id: str) -> AgentMemory | None:
        if self.demo_mode or not self.database_url:
            for row in self._read_demo():
                if row.get("id") == memory_id:
                    return self._memory_from_row(row)
            return None
        await self._ensure_schema()
        conn = await asyncpg.connect(self.database_url)
        try:
            row = await conn.fetchrow("SELECT * FROM agent_memories WHERE id = $1", memory_id)
        finally:
            await conn.close()
        return self._memory_from_row(dict(row)) if row else None

    async def search_similar(self, payload: dict[str, Any], limit: int = 5) -> list[AgentMemory]:
        query_embedding = self.embed(self._text_for_embedding(payload))
        if self.demo_mode or not self.database_url:
            rows = [self._memory_from_row(row) for row in self._read_demo()]
            for row in rows:
                row.similarity = self.cosine_similarity(query_embedding, row.embedding)
            return sorted(rows, key=lambda item: item.similarity or 0, reverse=True)[:limit]
        await self._ensure_schema()
        conn = await asyncpg.connect(self.database_url)
        try:
            result = await conn.fetch(
                """
                SELECT *, (embedding <-> $1::VECTOR) AS distance
                FROM agent_memories
                ORDER BY embedding <-> $1::VECTOR
                LIMIT $2
                """,
                self._vector_literal(query_embedding),
                limit,
            )
        finally:
            await conn.close()
        memories = []
        for row in result:
            memory = self._memory_from_row(dict(row))
            distance = float(row.get("distance") or 0)
            memory.similarity = round(1 / (1 + distance), 4)
            memories.append(memory)
        return memories

    async def update_outcome(self, memory_id: str, outcome: dict[str, Any]) -> AgentMemory | None:
        if self.demo_mode or not self.database_url:
            rows = self._read_demo()
            updated: AgentMemory | None = None
            for row in rows:
                if row.get("id") == memory_id:
                    row["outcome"] = outcome
                    updated = self._memory_from_row(row)
            self.storage_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            return updated
        await self._ensure_schema()
        conn = await asyncpg.connect(self.database_url)
        try:
            await conn.execute("UPDATE agent_memories SET outcome = $2::JSONB WHERE id = $1", memory_id, json.dumps(outcome))
        finally:
            await conn.close()
        return await self.retrieve(memory_id)

    async def _ensure_schema(self) -> None:
        if not self.database_url:
            return
        conn = await asyncpg.connect(self.database_url)
        try:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS agent_memories (
                    id UUID PRIMARY KEY,
                    symbol STRING NOT NULL,
                    market_context JSONB NOT NULL,
                    indicators JSONB NOT NULL,
                    strategy STRING NOT NULL,
                    risk_level STRING NOT NULL,
                    decision STRING NOT NULL,
                    reasoning STRING NOT NULL,
                    outcome JSONB,
                    embedding VECTOR({self.dimensions}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    INDEX agent_memories_symbol_created_idx (symbol, created_at DESC)
                )
                """
            )
            try:
                await conn.execute("CREATE VECTOR INDEX IF NOT EXISTS agent_memories_embedding_idx ON agent_memories (embedding)")
            except asyncpg.PostgresError:
                # Older CockroachDB clusters can store VECTOR values before vector
                # indexing is enabled. Similarity search still works via ORDER BY.
                pass
        finally:
            await conn.close()

    def _memory_from_payload(self, payload: dict[str, Any]) -> AgentMemory:
        created_at = payload.get("created_at") or datetime.now(UTC).isoformat()
        text = self._text_for_embedding(payload)
        return AgentMemory(
            id=str(payload.get("id") or uuid.uuid4()),
            symbol=str(payload.get("symbol") or "BTCUSDT"),
            market_context=dict(payload.get("market_context") or {}),
            indicators=dict(payload.get("indicators") or {}),
            strategy=str(payload.get("strategy") or "Volume Spike"),
            risk_level=str(payload.get("risk_level") or "medium"),
            decision=str(payload.get("decision") or "wait"),
            reasoning=str(payload.get("reasoning") or "No reasoning supplied."),
            created_at=created_at,
            outcome=payload.get("outcome"),
            embedding=self.embed(text),
        )

    def _memory_from_row(self, row: dict[str, Any]) -> AgentMemory:
        created = row.get("created_at")
        if isinstance(created, datetime):
            created_at = created.isoformat()
        else:
            created_at = str(created or datetime.now(UTC).isoformat())
        embedding = row.get("embedding") or []
        if isinstance(embedding, str):
            embedding = [float(part) for part in embedding.strip("[]").split(",") if part]
        return AgentMemory(
            id=str(row.get("id")),
            symbol=str(row.get("symbol")),
            market_context=self._json_dict(row.get("market_context")),
            indicators=self._json_dict(row.get("indicators")),
            strategy=str(row.get("strategy")),
            risk_level=str(row.get("risk_level")),
            decision=str(row.get("decision")),
            reasoning=str(row.get("reasoning")),
            created_at=created_at,
            outcome=self._json_dict(row.get("outcome")) or None,
            embedding=[float(value) for value in embedding],
            similarity=row.get("similarity"),
        )

    def _json_dict(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return dict(value)

    def _read_demo(self) -> list[dict[str, Any]]:
        if not self.storage_path.exists():
            return []
        return json.loads(self.storage_path.read_text(encoding="utf-8"))

    def _text_for_embedding(self, payload: dict[str, Any]) -> str:
        parts = [
            payload.get("symbol", ""),
            payload.get("strategy", ""),
            payload.get("risk_level", ""),
            payload.get("decision", ""),
            payload.get("reasoning", ""),
            json.dumps(payload.get("market_context", {}), sort_keys=True),
            json.dumps(payload.get("indicators", {}), sort_keys=True),
        ]
        return " ".join(str(part) for part in parts)

    def embed(self, text: str) -> list[float]:
        vector = [0.0 for _ in range(self.dimensions)]
        for token in text.lower().replace("/", " ").replace("_", " ").split():
            bucket = sum(ord(char) for char in token) % self.dimensions
            vector[bucket] += 1.0 + min(len(token), 12) / 12
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [round(value / norm, 6) for value in vector]

    def cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        score = sum(left[i] * right[i] for i in range(size))
        return round(max(0.0, min(1.0, score)), 4)

    def _vector_literal(self, vector: list[float]) -> str:
        return "[" + ",".join(str(float(value)) for value in vector) + "]"
