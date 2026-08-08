# DataHub Agent Outputs

This directory contains representative, anonymized outputs from the OlinckBotAI Trading Context Agent.

- `datahub_agent_context_response.json` shows the governed assets, data sources, indicators, backtests, and risk rules consulted before a paper-trading recommendation.

The example uses the local DataHub-compatible demo context so it can be reviewed without credentials. When `DATAHUB_DEMO_MODE=false`, the same contract is populated from DataHub GMS GraphQL and decision metadata is written back to DataHub.
