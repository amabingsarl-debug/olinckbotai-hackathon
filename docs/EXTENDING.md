# Extension

## Ajouter une stratégie

1. Créer une classe qui hérite de `Strategy` dans `backend/app/strategies/catalog.py`.
2. Implémenter `generate(candles, parameters)`.
3. Retourner `Signal("buy" | "sell" | "hold", confidence, reason, metadata)`.
4. Ajouter l'instance dans `STRATEGIES`.
5. Redémarrer le backend. La stratégie est amorcée automatiquement en base.

## Ajouter un exchange

1. Créer `backend/app/exchanges/bybit.py`, `okx.py` ou `kraken.py`.
2. Hériter de `ExchangeClient`.
3. Implémenter `ticker`, `candles` et `place_order`.
4. Ajouter le client dans `backend/app/exchanges/registry.py`.
5. Ajouter les variables `.env`.
6. Ajouter l'exchange dans `bootstrap_defaults`.

## Ajouter une métrique

1. Calculer la métrique dans `app/services/statistics.py`.
2. Ajouter son type dans `frontend/src/types/domain.ts`.
3. Afficher la valeur dans `frontend/src/App.tsx`.

## Ajouter un rapport IA

Modifier `backend/app/ai/analyzer.py`. Le module reçoit les trades et métriques, puis renvoie un objet JSON affichable et notifiable.
