# Configuration

Créer `.env` depuis `.env.example`.

## Variables principales

- `SECRET_KEY` : secret JWT.
- `TRADING_MODE` : `paper`, `real` ou `backtest`.
- `REAL_TRADING_ENABLED` : doit valoir `true` pour autoriser le réel.
- `DATABASE_URL` : connexion PostgreSQL.
- `REDIS_URL` : connexion Redis.

## Exchanges

Binance :

- `BINANCE_API_KEY`
- `BINANCE_API_SECRET`

Gate.io :

- `GATEIO_API_KEY`
- `GATEIO_API_SECRET`

Utiliser uniquement des clés Spot sans permission de retrait.

## Notifications

Telegram :

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Discord :

- `DISCORD_WEBHOOK_URL`

Email :

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `ALERT_EMAIL_TO`
