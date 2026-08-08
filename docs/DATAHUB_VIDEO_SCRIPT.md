# OlinckBotAI - DataHub Video Script

Target duration: 2 minutes 30 seconds. Speak in English. Show the application while speaking.

## 0:00 - 0:20 - Problem

"Trading agents often react to a price signal without knowing whether the data source is trusted, the backtest is valid, or a risk rule should block the action. OlinckBotAI makes every recommendation context-aware and paper-first."

## 0:20 - 0:45 - Dashboard

Open OlinckBotAI. Point to paper mode and the Agentic Memory panel.

"This is OlinckBotAI. Real trading is disabled by default. The agent can only produce an explainable recommendation after it retrieves the trading context it needs."

## 0:45 - 1:30 - DataHub Context

Open `/api/agent-context?symbol=BTCUSDT` or refresh the dashboard panel.

"Before it recommends an action, the Trading Context Agent reads DataHub context. It checks the tracked asset, the quality of market sources, indicator definitions, eligible strategies, backtest evidence, and hard risk limits. DataHub is not just displayed in the interface. It is the context layer used before reasoning."

## 1:30 - 2:05 - Decision and Write-back

Show the recommendation, explanation, and DataHub record confirmation.

"Here, the agent recommends wait because the evidence does not clear the risk gate. After the analysis, it records the latest decision metadata back to DataHub. The next analyst or agent inherits a clear audit trail instead of a black-box recommendation."

## 2:05 - 2:30 - Outcome

"OlinckBotAI shows how DataHub can ground a financial agent in trusted context. It is reproducible, paper-first, and designed so that transparency and risk controls come before automation."
