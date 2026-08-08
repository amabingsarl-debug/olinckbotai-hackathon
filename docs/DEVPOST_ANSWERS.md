# Devpost Draft Answers

Use this file as copy-ready material for the two hackathon submissions.

## Project Name

OlinckBotAI

## One-Liner

An explainable paper-first trading agent that uses DataHub context and CockroachDB agentic memory before recommending strategies.

## Inspiration

Trading bots often optimize for short-term signals without remembering why past decisions worked or failed. OlinckBotAI was built to make an autonomous trading assistant more accountable: every recommendation must cite trusted context, similar historical decisions, and hard risk limits before it suggests an action.

## What It Does

OlinckBotAI combines a FastAPI trading backend, React dashboard, backtesting, risk controls, paper trading, DataHub AI Context, and CockroachDB-backed agentic memory.

Before recommending a strategy, the agent:

- retrieves governed trading context from DataHub or demo metadata;
- retrieves similar historical decisions from CockroachDB semantic memory;
- checks risk limits and backtest evidence;
- returns a paper-only recommendation with reasoning;
- saves the new decision, indicators, risk level, rationale, and timestamp;
- exports an agent-context report through the AWS report interface.

## How We Built It

Backend:

- FastAPI
- SQLAlchemy
- PostgreSQL
- Redis
- CockroachDB via `asyncpg`
- DataHub GraphQL adapter
- AWS S3 report writer

Frontend:

- React
- TypeScript
- Vite
- Recharts
- PWA manifest/service worker

Cloud and infrastructure:

- CockroachDB Cloud agent memory table with vector embeddings
- AWS ECS Fargate task template
- AWS S3 encrypted report bucket CloudFormation template
- Docker Compose for one-command local startup

## DataHub Usage

The `TradingContextAgent` uses `DataHubClient` to retrieve assets, market sources, indicators, strategies, backtests, risk metrics, and prior decisions before producing a recommendation. In production, it uses DataHub GMS GraphQL. In demo mode, it uses local governed metadata so judges can run the project without secrets.

After analysis, the agent records decision metadata back to DataHub or the demo record path.

## CockroachDB Usage

CockroachDB stores agent memories with:

- symbol;
- market context;
- indicators;
- strategy;
- risk level;
- final decision;
- reasoning;
- timestamp;
- outcome;
- embedding vector.

The agent performs semantic recall before every recommendation and cites the memories it used.

CockroachDB capabilities used:

- Distributed Vector Indexing with a `VECTOR(16)` embedding column and vector index.
- `ccloud CLI` setup path through `scripts/cockroach_agent_memory_setup.ps1`.

## AWS Usage

The AWS path is intentionally simple:

- ECS Fargate runs the FastAPI backend container.
- S3 stores JSON agent-context reports.

The implementation is live in code through `AWSReportStore`. When AWS credentials or account activation are unavailable, the exact report payload is written locally as a demo fallback.

## Challenges

The main challenge was building a trading-agent memory that remains useful without making the bot reckless. The agent can retrieve past decisions and improve its context, but hard risk controls and paper-only mode remain outside the agent's control.

AWS account activation was also delayed by phone verification, so the project includes a local AWS report fallback plus deployment templates ready for activation.

## Accomplishments

- Unified one product for two hackathons.
- Added explainable DataHub context retrieval.
- Added CockroachDB persistent agent memory and semantic recall.
- Added dashboard visibility into memory and recommendation reasoning.
- Preserved paper-first safety and disabled real trading by default.
- Added tests and build validation.

## What We Learned

Agentic trading systems need memory, but memory alone is not enough. The agent also needs governed context, out-of-sample validation, explicit risk rules, and audit logs. The strongest pattern is not "AI decides everything"; it is "AI recommends after retrieving context, while safety systems enforce boundaries."

## What's Next

- Activate AWS account and deploy the ECS/S3 path.
- Connect a production DataHub instance.
- Add outcome updates after each paper trade closes.
- Expand semantic memory dimensions if using a production embedding provider.
- Add judge-facing hosted demo after AWS activation.

## Demo Steps

1. Open the dashboard and show `Agentic Memory`.
2. Trigger `http://localhost:8000/api/agent-context?symbol=BTCUSDT`.
3. Show the DataHub context used by the agent.
4. Show similar CockroachDB memories and cited memory IDs.
5. Show the recommendation, saved memory confirmation, DataHub record status, AWS report status, and real-trading-disabled guard.

