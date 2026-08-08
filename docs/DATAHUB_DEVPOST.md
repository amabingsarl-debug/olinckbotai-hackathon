# OlinckBotAI - DataHub Devpost Submission

## Challenge Category

Agents That Do Real Work

## One-line Summary

OlinckBotAI is an explainable paper-trading agent that retrieves trusted DataHub context before making a strategy recommendation, then records its decision for future audit.

## Inspiration

Trading agents can produce unsafe recommendations when they look only at the latest price signal. They need to know which market sources are trusted, which indicators and backtests support a strategy, which risk rules apply, and what previous decisions were made. OlinckBotAI makes that context explicit before every recommendation.

## What It Does

OlinckBotAI is a paper-first algorithmic trading application. Its Trading Context Agent retrieves governed context about tracked assets, market sources, indicators, strategies, backtests, and risk limits before it recommends buy, sell, wait, or refuse. The agent explains the evidence it used, stays in paper mode, and records the decision metadata for auditability.

## How We Built It

- FastAPI backend and React/TypeScript dashboard.
- `DataHubClient` uses the DataHub GMS GraphQL API in connected mode.
- The agent reads catalog context before reasoning and writes latest decision metadata back after analysis.
- Official DataHub Skills are included under `.agents/skills/` for search, enrichment, lineage, quality, setup, and connector-planning workflows.
- A local demo context makes the project reproducible without exposing any token or financial credential.

## How DataHub Is Used

The agent calls `DataHubClient.get_trading_context()` before it recommends an action. The returned context contains the asset catalog, source freshness and quality notes, indicator definitions, strategy eligibility, backtest references, and hard risk limits. After a recommendation, `DataHubClient.record_decision()` writes the latest decision metadata to the DataHub decision dataset when connected.

This is not a generic chat wrapper around market prices. DataHub is the governed context layer that the agent reads before reasoning and contributes to after it acts.

## Links

- Demo: https://olinckbotai.web.app
- Repository: https://github.com/amabingsarl-debug/olinckbotai-hackathon
- Sample output: `examples/datahub_agent_context_response.json`

## Demo Flow

1. Open the OlinckBotAI dashboard in paper mode.
2. Open `/api/agent-context?symbol=BTCUSDT`.
3. Show the DataHub assets, sources, indicators, backtests, and risk metrics that the agent consulted.
4. Show the recommendation and its explanation.
5. Show that the decision record is saved and that real trading remains disabled.

## Pre-existing Work Disclosure

OlinckBotAI existed as a paper-trading platform before the hackathon. The DataHub context adapter, DataHub Skills integration, agent-context workflow, decision-record path, dashboard panel, examples, and DataHub documentation were added for this hackathon.
