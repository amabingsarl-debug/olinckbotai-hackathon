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

- Firebase-hosted web application and a Firebase Functions API.
- A live DataHub OSS instance is deployed at `https://datahub.chapimo.com`.
- The API uses the authenticated DataHub GraphQL endpoint to retrieve governed catalog assets, then writes the latest decision metadata back through DataHub's ingest proposal endpoint.
- Official DataHub Skills are included under `.agents/skills/` for search, enrichment, lineage, quality, setup, and connector-planning workflows.
- The DataHub token is held in Firebase Secret Manager; no credential is stored in the repository or returned to the browser.

## How DataHub Is Used

The public `GET /api/agent-context?symbol=BTCUSDT` flow retrieves catalog assets from the live DataHub GraphQL API before returning its paper-only recommendation. It then updates the governed `agent_decisions` dataset with the latest symbol, decision, risk level, and timestamp through an authenticated DataHub REST ingest proposal.

The agent also has a governed market-data quality gate. The reproducible failure simulation at `GET /api/agent-context?symbol=BTCUSDT&data_health=degraded` records a `market_data_health` event in DataHub and returns `recommendation.decision: "refuse"` with `risk_level: "blocked"`. The healthy path returns `wait` only after the quality gate is approved.

This is not a generic chat wrapper around market prices. DataHub is the governed context layer that the agent reads before reasoning and contributes to after it acts.

## Links

- Demo: https://olinckbotai.web.app
- Live agent-context API: https://olinckbotai.web.app/api/agent-context?symbol=BTCUSDT
- Live DataHub catalog: https://datahub.chapimo.com
- Repository: https://github.com/amabingsarl-debug/olinckbotai-hackathon
- Sample output: `examples/datahub_agent_context_response.json`

## Demo Flow

1. Open the OlinckBotAI dashboard in paper mode.
2. Open `/api/agent-context?symbol=BTCUSDT`.
3. Show `context_used.datahub.mode: "datahub"`, the governed `agent_decisions` asset, and `datahub_record.saved: true`.
4. Trigger `https://olinckbotai.web.app/api/agent-context?symbol=BTCUSDT&data_health=degraded`.
5. Show `market_data_health`, `state: "degraded"`, `decision: "refuse"`, `risk_level: "blocked"`, and both successful DataHub records.
6. Show that real trading remains disabled.

## Pre-existing Work Disclosure

OlinckBotAI existed as a paper-trading platform before the hackathon. The DataHub context adapter, DataHub Skills integration, agent-context workflow, decision-record path, dashboard panel, examples, and DataHub documentation were added for this hackathon.
