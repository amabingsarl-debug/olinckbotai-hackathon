# Exploitation

## Démarrer

```powershell
docker compose up --build
```

## Arrêter

```powershell
docker compose down
```

## Sauvegarder PostgreSQL

```powershell
docker compose exec postgres pg_dump -U olinck olinck > backup.sql
```

## Restaurer

```powershell
Get-Content backup.sql | docker compose exec -T postgres psql -U olinck -d olinck
```

## Validation avant déploiement

```powershell
.\scripts\validate.ps1
```

## Passage en trading réel

1. Configurer des clés API Spot sans retrait.
2. Tester longuement en paper trading.
3. Définir `REAL_TRADING_ENABLED=true`.
4. Démarrer le bot avec `mode=real` via l'API.

Le backend refuse les ordres réels si le verrou n'est pas activé.
