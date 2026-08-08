cat > deploy_olinck_api.sh <<'EOF'
set -e
PROJECT_ID="olinckbotai"
REGION="europe-west1"
SERVICE="olinckbotai-api"
APP_DIR="$HOME/olinckbotai-api"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com --project "$PROJECT_ID"
(gcloud firestore databases create --location=eur3 --database='(default)' --project "$PROJECT_ID" || true)

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

cat > package.json <<'JSON'
{
  "name": "olinckbotai-api",
  "version": "1.0.0",
  "type": "module",
  "scripts": { "start": "node server.js" },
  "dependencies": {
    "@google-cloud/firestore": "^7.11.0",
    "express": "^4.19.2"
  }
}
JSON

cat > server.js <<'JS'
import express from 'express';
import { Firestore } from '@google-cloud/firestore';

const app = express();
const db = new Firestore();
const PORT = process.env.PORT || 8080;
const PROJECT = process.env.GOOGLE_CLOUD_PROJECT || 'olinckbotai';

app.use(express.json({ limit: '2mb' }));
app.use((req, res, next) => {
  const origin = req.headers.origin || '*';
  res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PATCH,PUT,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization');
  if (req.method === 'OPTIONS') return res.status(204).send('');
  next();
});

const strategyNames = ['ATR','Bollinger Bands','Breakout','EMA Cross','MACD','Mean Reversion','RSI','Scalping','Swing Trading','Trend Following','VWAP','Volume Spike'];
const exchangeNames = ['binance', 'gateio'];
const refs = {
  state: db.collection('state').doc('bot'),
  strategies: db.collection('strategies'),
  exchanges: db.collection('exchanges'),
  trades: db.collection('trades'),
  logs: db.collection('logs')
};

async function ensureDefaults() {
  const state = await refs.state.get();
  if (!state.exists) await refs.state.set({ running: false, mode: 'paper', exchange: 'binance', symbols: ['BTCUSDT', 'ETHUSDT'], updated_at: new Date().toISOString() });
  for (const name of strategyNames) {
    const ref = refs.strategies.doc(name);
    if (!(await ref.get()).exists) await ref.set({ name, enabled: true, symbols: ['BTCUSDT', 'ETHUSDT'], timeframe: '1h', parameters: {} });
  }
  for (const name of exchangeNames) {
    const ref = refs.exchanges.doc(name);
    if (!(await ref.get()).exists) await ref.set({ name, enabled: name === 'binance', paper_only: true, settings: {} });
  }
}

async function list(collection, orderField = 'name') {
  const snap = await collection.orderBy(orderField).get().catch(() => collection.get());
  return snap.docs.map(doc => ({ id: doc.id, ...doc.data() }));
}

function periodPnl(trades, days) {
  const cutoff = Date.now() - days * 86400000;
  return trades.filter(t => t.closed_at && new Date(t.closed_at).getTime() >= cutoff).reduce((sum, t) => sum + Number(t.pnl || 0), 0);
}

function dashboardFromTrades(trades) {
  const closed = trades.filter(t => t.status === 'closed');
  const open = trades.filter(t => t.status === 'open');
  const wins = closed.filter(t => Number(t.pnl || 0) > 0);
  const losses = closed.filter(t => Number(t.pnl || 0) < 0);
  let equity = 10000;
  let peak = equity;
  let drawdown = 0;
  const curve = closed.sort((a, b) => new Date(a.closed_at || 0) - new Date(b.closed_at || 0)).map(t => {
    equity += Number(t.pnl || 0);
    peak = Math.max(peak, equity);
    drawdown = Math.min(drawdown, (equity - peak) / peak);
    return { date: t.closed_at, equity: Math.round(equity * 100) / 100 };
  });
  const totalPnl = closed.reduce((sum, t) => sum + Number(t.pnl || 0), 0);
  return {
    capital: Math.round((10000 + totalPnl) * 100) / 100,
    profit_daily: Math.round(periodPnl(closed, 1) * 100) / 100,
    profit_weekly: Math.round(periodPnl(closed, 7) * 100) / 100,
    profit_monthly: Math.round(periodPnl(closed, 30) * 100) / 100,
    profit_annual: Math.round(periodPnl(closed, 365) * 100) / 100,
    open_trades: open.length,
    closed_trades: closed.length,
    win_rate: closed.length ? Math.round((wins.length / closed.length) * 10000) / 100 : 0,
    profit_factor: losses.length ? Math.round((wins.reduce((s, t) => s + Number(t.pnl || 0), 0) / Math.abs(losses.reduce((s, t) => s + Number(t.pnl || 0), 0))) * 1000) / 1000 : (wins.length ? 1 : 0),
    drawdown: Math.round(Math.abs(drawdown) * 10000) / 100,
    performance_curve: curve,
    gain_distribution: wins.slice(-50).map(t => ({ strategy: t.strategy, pnl: Number(t.pnl || 0) })),
    loss_distribution: losses.slice(-50).map(t => ({ strategy: t.strategy, pnl: Number(t.pnl || 0) }))
  };
}

async function addLog(level, source, message, context = {}) {
  await refs.logs.add({ level, source, message, context, created_at: new Date().toISOString() });
}

async function tickerPrice(symbol) {
  try {
    const response = await fetch('https://api.binance.com/api/v3/ticker/price?symbol=' + symbol);
    const data = await response.json();
    return Number(data.price || 0);
  } catch {
    return symbol === 'BTCUSDT' ? 65000 : 3500;
  }
}

async function createPaperTrades(symbols) {
  const openSnap = await refs.trades.where('status', '==', 'open').get();
  const existing = new Set(openSnap.docs.map(doc => doc.data().symbol));
  const enabledStrategies = (await list(refs.strategies)).filter(s => s.enabled);
  const picked = enabledStrategies.filter(s => ['VWAP', 'RSI', 'EMA Cross', 'MACD'].includes(s.name));
  let created = 0;
  for (const symbol of symbols) {
    if (existing.has(symbol)) continue;
    const price = await tickerPrice(symbol);
    const strategy = picked[created % Math.max(picked.length, 1)]?.name || 'VWAP';
    await refs.trades.add({
      exchange: 'binance', strategy, symbol, side: 'buy', status: 'open',
      entry_price: price, exit_price: null, quantity: Number((100 / price).toFixed(8)), pnl: 0,
      opened_at: new Date().toISOString(), closed_at: null,
      metadata_json: { signal: { action: 'buy', confidence: 0.6, reason: 'paper cloud signal' } }
    });
    created++;
  }
  await addLog('info', 'cloud-run', 'Paper trading cycle executed. Created ' + created + ' simulated trade(s).', { symbols });
}

app.get('/api/health', async (req, res) => {
  await ensureDefaults();
  res.json({ status: 'ok', app: 'OLINCK BOT AI', mode: 'paper', real_trading_enabled: false, project: PROJECT });
});

app.get('/api/dashboard', async (req, res) => {
  await ensureDefaults();
  res.json(dashboardFromTrades(await list(refs.trades, 'opened_at')));
});

app.get('/api/strategies', async (req, res) => {
  await ensureDefaults();
  res.json((await list(refs.strategies)).map(({ id, ...s }) => s));
});

app.patch('/api/strategies/:name', async (req, res) => {
  await ensureDefaults();
  await refs.strategies.doc(req.params.name).set({ enabled: Boolean(req.body.enabled) }, { merge: true });
  res.json({ name: req.params.name, enabled: Boolean(req.body.enabled) });
});

app.get('/api/exchanges', async (req, res) => {
  await ensureDefaults();
  res.json((await list(refs.exchanges)).map(({ id, ...e }) => e));
});

app.patch('/api/exchanges/:name', async (req, res) => {
  await ensureDefaults();
  await refs.exchanges.doc(req.params.name).set({ enabled: Boolean(req.body.enabled) }, { merge: true });
  res.json({ name: req.params.name, enabled: Boolean(req.body.enabled) });
});

app.get('/api/logs', async (req, res) => {
  await ensureDefaults();
  const trades = (await list(refs.trades, 'opened_at')).slice(-100).reverse();
  const system = (await list(refs.logs, 'created_at')).slice(-100).reverse();
  res.json({ trades: trades.map(({ id, ...t }) => ({ id, ...t })), system: system.map(({ id, ...l }) => l) });
});

app.get('/api/bot/status', async (req, res) => {
  await ensureDefaults();
  res.json((await refs.state.get()).data());
});

app.post('/api/bot/start', async (req, res) => {
  await ensureDefaults();
  const symbols = req.body?.symbols?.length ? req.body.symbols : ['BTCUSDT', 'ETHUSDT'];
  const state = { running: true, mode: 'paper', exchange: req.body?.exchange || 'binance', symbols, updated_at: new Date().toISOString() };
  await refs.state.set(state, { merge: true });
  await createPaperTrades(symbols);
  res.json(state);
});

app.post('/api/bot/stop', async (req, res) => {
  await ensureDefaults();
  const current = (await refs.state.get()).data() || {};
  const state = { ...current, running: false, updated_at: new Date().toISOString() };
  await refs.state.set(state, { merge: true });
  await addLog('info', 'bot', 'Paper trading stopped from cloud dashboard.');
  res.json(state);
});

app.post('/api/ai/report', async (req, res) => {
  await ensureDefaults();
  const trades = await list(refs.trades, 'opened_at');
  const metrics = dashboardFromTrades(trades);
  const report = {
    generated_at: new Date().toISOString(),
    summary: 'Capital ' + metrics.capital + ', ' + metrics.open_trades + ' trades ouverts, win rate ' + metrics.win_rate + '%.',
    bad_strategies: [],
    suggestions: ['Continuer le suivi paper trading avant toute activation reelle.', 'Migrer ensuite vers le moteur Python complet avec Cloud SQL.'],
    best_symbols: [...new Set(trades.map(t => t.symbol))].slice(0, 5),
    best_hours: [],
    volatility_note: 'Surveiller ATR et drawdown avant tout passage en reel.'
  };
  await addLog('info', 'ai', report.summary);
  res.json(report);
});

app.listen(PORT, () => console.log('OLINCK BOT AI cloud API listening on ' + PORT));
JS

cat > Dockerfile <<'DOCKER'
FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --omit=dev
COPY server.js ./server.js
ENV NODE_ENV=production
CMD ["npm", "start"]
DOCKER

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT="$PROJECT_ID" \
  --project "$PROJECT_ID" \
  --quiet

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')
echo "OLINCK_API_URL=$URL/api"
EOF
bash deploy_olinck_api.sh
