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
    embedding VECTOR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    INDEX agent_memories_symbol_created_idx (symbol, created_at DESC)
);

CREATE VECTOR INDEX IF NOT EXISTS agent_memories_embedding_idx ON agent_memories (embedding);
