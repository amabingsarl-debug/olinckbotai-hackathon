# Architecture

OLINCK BOT AI est séparé en quatre couches.

## Backend

FastAPI expose les routes `/api` et le WebSocket `/api/ws`.

Principaux dossiers :

- `app/exchanges` : clients Binance Spot et Gate.io Spot.
- `app/strategies` : catalogue de stratégies indépendantes.
- `app/risk` : décisions de risque avant chaque ordre.
- `app/backtesting` : moteur de backtest.
- `app/ai` : analyse de performance et recommandations.
- `app/notifications` : Telegram, Discord, Email, navigateur.
- `app/services` : moteur de trading, statistiques, amorçage.

## Frontend

React TypeScript affiche :

- capital et PnL par période
- trades ouverts et clôturés
- win rate, profit factor, drawdown
- courbe de performance
- distribution gains/pertes
- stratégies et exchanges activables
- journaux système et ordres

## Données

PostgreSQL stocke utilisateurs, stratégies, exchanges, risque, trades, journaux et backtests.

Redis est prévu pour le cache, les états temps réel et les extensions de files d'attente.

## Temps réel

Le WebSocket envoie l'état du bot. Il peut être étendu pour publier ticks, signaux, alertes et positions.
