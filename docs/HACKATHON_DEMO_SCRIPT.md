# OlinckBotAI Dual Hackathon Demo Script

Use this single flow for both hackathons:

- **Build with DataHub: The Agent Hackathon**
- **CockroachDB x AWS: Build with Agentic Memory**

## 3-Minute Video Flow

1. **Open the dashboard**
   - Show OlinckBotAI running in paper mode.
   - Mention that real trading is disabled by default.

2. **Trigger an agent recommendation**
   - Open `/api/agent-context?symbol=BTCUSDT` or refresh the dashboard panel.
   - The agent gathers market context, risk data, backtests, and strategy status.

3. **Show DataHub AI Context**
   - Explain that DataHub provides governed context: assets, data sources, indicators, strategies, backtests, and risk metrics.
   - In connected mode, this comes from DataHub GraphQL/MCP/Skills.
   - In demo mode, the same contract is fulfilled by local sample metadata.

4. **Show CockroachDB Agentic Memory**
   - The agent searches previous similar decisions using CockroachDB vector search.
   - It cites memories used in the reasoning.
   - A new decision is saved with symbol, market context, indicators, strategy, risk, decision, reasoning, timestamp, and later outcome.

5. **Show AWS report artifact**
   - Explain that each recommendation is exported as an agent-context report.
   - In AWS mode this is written to encrypted S3.
   - In local demo mode it is written to `backend/app/data/agent_context_reports/`.

## Key Talking Points

- OlinckBotAI is not a black-box trading bot.
- It uses DataHub for trusted context before reasoning.
- It uses CockroachDB vector indexing for persistent memory.
- It uses AWS S3 for durable report artifacts.
- It stays paper-first and blocks real trading unless explicitly enabled.

## Suggested Demo Command

```powershell
docker compose up --build
```

Then open:

```text
http://localhost:5173
http://localhost:8000/api/agent-context?symbol=BTCUSDT
```
