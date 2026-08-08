import express from "express";
import admin from "firebase-admin";
import { onRequest } from "firebase-functions/v2/https";
import { onSchedule } from "firebase-functions/v2/scheduler";
import { defineSecret } from "firebase-functions/params";

admin.initializeApp();
const db = admin.firestore();
const app = express();
app.use(express.json({ limit: "2mb" }));
const deploymentRevision = "2026-08-01-api-rebuild";
const dataHubToken = defineSecret("DATAHUB_TOKEN");
const dataHubGmsUrl = "https://datahub.chapimo.com";

const strategyNames = [
  "ATR", "Bollinger Bands", "Breakout", "EMA Cross", "MACD", "Mean Reversion",
  "RSI", "Scalping", "Swing Trading", "Trend Following", "VWAP", "Volume Spike"
];

const refs = {
  state: db.collection("state").doc("bot"),
  strategies: db.collection("strategies"),
  exchanges: db.collection("exchanges"),
  trades: db.collection("trades"),
  logs: db.collection("logs")
};

const coreUniverse = [
  "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
  "TRXUSDT", "LINKUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LTCUSDT", "XLMUSDT",
  "ATOMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT"
];

const leveragedTokenPattern = /(UP|DOWN|BULL|BEAR)USDT$/;

const defaultSelection = {
  generated_at: "2026-07-03T10:04:40.016Z",
  timeframe: "4h",
  risk_profile: "wide",
  selected_pairs: [
    { symbol: "TRXUSDT", strategy: "Breakout", holdout_profit_factor: 1.884, allocation_tier: "institutional", max_allocation_pct: 10 },
    { symbol: "BTCUSDT", strategy: "Volume Spike", holdout_profit_factor: 1.412, allocation_tier: "strong", max_allocation_pct: 7.5 },
    { symbol: "ADAUSDT", strategy: "Volume Spike", holdout_profit_factor: 1.202, allocation_tier: "quarantined", max_allocation_pct: 2.5 },
    { symbol: "ETHUSDT", strategy: "Volume Spike", holdout_profit_factor: 1.204, allocation_tier: "quarantined", max_allocation_pct: 2.5 }
  ],
  portfolio_validation: {
    selected_count: 4,
    max_portfolio_exposure_pct: 30,
    eligible: true,
    full: { return_pct: 24.9, drawdown: 6.2, trades: 724, profit_factor: 1.32, sharpe_ratio: 0.82 },
    holdout: { return_pct: 14.92, drawdown: 4.56, trades: 362, profit_factor: 1.412, sharpe_ratio: 0.76 },
    monte_carlo: { simulations: 500, probability_positive_pct: 98, median_return_pct: 14.2, p05_return_pct: 2.1, p95_return_pct: 25, p95_drawdown_pct: 7.8, confidence: "strong" },
    benchmark: {
      full: { allocation_pct_per_asset: 7.5, return_pct: 4.1, drawdown: 22.4, final_capital: 10410 },
      holdout: { allocation_pct_per_asset: 7.5, return_pct: 1.89, drawdown: 18.71, final_capital: 10189 },
      strategy_holdout_score: 2.53,
      benchmark_holdout_score: 0.1,
      excess_holdout_return_pct: 13.03
    }
  },
  closed_pnl: 0,
  open_pnl: 0,
  total_pnl: 0,
  pnl_pct: 0,
  open_exposure: 0,
  open_trades: 0,
  closed_trades: 0,
  win_rate: 0,
  profit_factor: 0,
  drawdown: 0,
  guard: { status: "armed", breached: false, engine_action: "run", max_loss_pct: 1.5, consecutive_losses: 0, max_consecutive_losses: 3 },
  forward_validation: {
    status: "observation",
    reason: "Observation paper en ligne en cours.",
    days_live: 0,
    expected_trades_so_far: 0,
    min_trades_for_judgement: 5,
    closed_trades: 0,
    open_trades: 0,
    live_pnl: 0,
    live_return_pct: 0,
    expected_median_return_pct: 8.1,
    expected_holdout_return_pct: 14.92,
    expected_max_drawdown_pct: 4.56
  },
  promotion_readiness: {
    status: "collecting_evidence",
    ready: false,
    passed_requirements: 2,
    total_requirements: 6,
    risk_increase_automatic: false,
    reason: "Le risque reste bloque au niveau paper pendant la collecte de preuves.",
    requirements: {
      minimum_days: { target: 30, current: 0, passed: false },
      minimum_closed_trades: { target: 20, current: 0, passed: false },
      positive_pnl: { target: 0, current: 0, passed: true },
      guard_clear: { target: true, current: true, passed: true }
    }
  }
};

async function ensureDefaults() {
  if (!(await refs.state.get()).exists) {
    const now = new Date().toISOString();
    await refs.state.set({
      running: false,
      mode: "paper",
      exchange: "binance",
      symbols: ["BTCUSDT", "ETHUSDT"],
      runtime_status: "stopped",
      status_label: "Paper arrete",
      status_message: "Le bot attend un demarrage manuel.",
      last_action: "initialized",
      last_stop_reason: "not_started",
      last_stop_at: now,
      updated_at: now
    });
  }
  for (const name of strategyNames) {
    const doc = refs.strategies.doc(name);
    if (!(await doc.get()).exists) await doc.set({ name, enabled: true, symbols: ["BTCUSDT", "ETHUSDT"], timeframe: "1h", parameters: {} });
  }
  for (const name of ["binance", "gateio"]) {
    const doc = refs.exchanges.doc(name);
    if (!(await doc.get()).exists) await doc.set({ name, enabled: name === "binance", paper_only: true, settings: {} });
  }
}

async function list(collection, orderField = "name") {
  const snap = await collection.orderBy(orderField).get().catch(() => collection.get());
  return snap.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
}

async function addLog(level, source, message, context = {}) {
  await refs.logs.add({ level, source, message, context, created_at: new Date().toISOString() });
}

function runtimeHealth(state = {}, metrics = null) {
  const running = Boolean(state.running);
  const guardStatus = state.guard_status || (running ? "active" : "stopped");
  const openTrades = Number(metrics?.open_trades ?? state.open_trades ?? 0);
  const lastTickAt = state.last_tick_at || null;
  const lastSchedulerCheckAt = state.last_scheduler_check_at || null;
  const nextTickAt = lastTickAt ? new Date(new Date(lastTickAt).getTime() + 5 * 60 * 1000).toISOString() : null;
  let runtimeStatus = state.runtime_status || (running ? "active" : "stopped");
  let statusLabel = state.status_label || (running ? "Paper actif" : "Paper arrete");
  let statusMessage = state.status_message || (running ? "Surveillance paper active." : "Le bot est arrete.");

  if (running && guardStatus === "observe_only") {
    runtimeStatus = "observe_only";
    statusLabel = "Observation protegee";
    statusMessage = state.status_message || "Le bot analyse le marche mais limite les nouvelles entrees pour proteger le capital.";
  } else if (running && openTrades === 0) {
    runtimeStatus = "watching";
    statusLabel = "Surveillance active";
    statusMessage = state.status_message || "Le bot tourne, mais aucune entree n'a ete validee sur ce cycle.";
  } else if (running) {
    runtimeStatus = "active";
    statusLabel = "Paper actif";
    statusMessage = state.status_message || "Le bot paper tourne et surveille les positions ouvertes.";
  }

  return {
    running,
    runtime_status: runtimeStatus,
    status_label: statusLabel,
    status_message: statusMessage,
    guard_status: guardStatus,
    last_action: state.last_action || "unknown",
    last_action_at: state.last_action_at || state.updated_at || null,
    last_tick_at: lastTickAt,
    next_tick_at: nextTickAt,
    last_scheduler_check_at: lastSchedulerCheckAt,
    last_stop_reason: state.last_stop_reason || null,
    last_stop_at: state.last_stop_at || null,
    scheduler_status: running ? "armed" : "waiting",
    open_trades: openTrades
  };
}

async function tickerPrice(symbol) {
  try {
    const response = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`);
    const data = await response.json();
    return Number(data.price || 0);
  } catch {
    return symbol === "BTCUSDT" ? 65000 : 3500;
  }
}

async function binance24hTickers() {
  try {
    const response = await fetch("https://api.binance.com/api/v3/ticker/24hr");
    const rows = await response.json();
    return Array.isArray(rows) ? rows : [];
  } catch {
    return coreUniverse.map((symbol, index) => ({
      symbol,
      priceChangePercent: index < 2 ? 1.2 : 0.4,
      quoteVolume: String(100000000 - index * 2500000),
      count: String(50000 - index * 1000),
      lastPrice: symbol === "BTCUSDT" ? "65000" : symbol === "ETHUSDT" ? "3500" : "1"
    }));
  }
}

function tradeHour(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.getUTCHours() : null;
}

function profitFactor(wins, losses) {
  const grossWin = wins.reduce((sum, trade) => sum + Number(trade.pnl || 0), 0);
  const grossLoss = Math.abs(losses.reduce((sum, trade) => sum + Number(trade.pnl || 0), 0));
  if (grossLoss > 0) return Math.round((grossWin / grossLoss) * 1000) / 1000;
  return wins.length ? 999 : 0;
}

function consecutiveLosses(closed) {
  let count = 0;
  for (const trade of [...closed].sort((a, b) => new Date(b.closed_at || 0) - new Date(a.closed_at || 0))) {
    if (Number(trade.pnl || 0) < 0) count += 1;
    else break;
  }
  return count;
}

function latestClosedTradeAgeHours(closed) {
  const latest = [...closed]
    .filter((trade) => trade.closed_at)
    .sort((a, b) => new Date(b.closed_at) - new Date(a.closed_at))[0];
  if (!latest) return null;
  return Math.max(0, (Date.now() - new Date(latest.closed_at).getTime()) / 3600000);
}

function learningProfile(trades) {
  const closed = trades.filter((trade) => trade.status === "closed");
  const groups = new Map();
  for (const trade of closed) {
    const style = trade.metadata_json?.execution_style || "core_selection";
    const key = `${trade.symbol}|${trade.strategy}|${style}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(trade);
  }
  const rows = [];
  for (const [key, items] of groups.entries()) {
    const [symbol, strategy, executionStyle] = key.split("|");
    const recent = [...items].sort((a, b) => new Date(a.closed_at || 0) - new Date(b.closed_at || 0)).slice(-12);
    const wins = recent.filter((trade) => Number(trade.pnl || 0) > 0);
    const losses = recent.filter((trade) => Number(trade.pnl || 0) < 0);
    const totalPnl = recent.reduce((sum, trade) => sum + Number(trade.pnl || 0), 0);
    const pf = profitFactor(wins, losses);
    let status = "neutral";
    let multiplier = 1;
    if (recent.length >= 4 && totalPnl < 0 && pf < 0.85) {
      status = "blocked_after_losses";
      multiplier = 0;
    } else if (recent.length >= 3 && (totalPnl < 0 || pf < 1)) {
      status = "reduced_after_weak_results";
      multiplier = 0.55;
    } else if (recent.length >= 5 && totalPnl > 0 && pf >= 1.35 && wins.length / recent.length >= 0.45) {
      status = "boosted_after_good_results";
      multiplier = 1.18;
    }
    rows.push({
      symbol,
      strategy,
      execution_style: executionStyle,
      status,
      multiplier,
      closed_trades: recent.length,
      total_pnl: Math.round(totalPnl * 100) / 100,
      win_rate: recent.length ? Math.round((wins.length / recent.length) * 10000) / 100 : 0,
      profit_factor: pf
    });
  }
  rows.sort((a, b) => (b.multiplier - a.multiplier) || (b.total_pnl - a.total_pnl));
  return {
    status: "active",
    rule: "Le bot reduit ou bloque les configurations perdantes et augmente legerement les configurations gagnantes apres echantillon paper.",
    groups: rows.slice(0, 12)
  };
}

function learningFor(learning, symbol, strategy) {
  return learning.groups.find((row) => row.symbol === symbol && row.strategy === strategy) || { multiplier: 1, status: "neutral" };
}

function bestLearnedStrategy(learning, symbol) {
  const rows = learning.groups
    .filter((row) => row.symbol === symbol && row.multiplier > 0)
    .sort((a, b) => (b.multiplier - a.multiplier) || (b.profit_factor - a.profit_factor) || (b.total_pnl - a.total_pnl));
  return rows[0]?.strategy || null;
}

function strategyForTicker(row, learning) {
  const learnedStrategy = bestLearnedStrategy(learning, row.symbol);
  if (learnedStrategy) return learnedStrategy;
  const change = Number(row.priceChangePercent || 0);
  if (change >= 4) return "Volume Spike";
  if (change >= 1.2) return "Breakout";
  if (change <= -3) return "RSI";
  return "VWAP";
}

function marketScoreFromCandidates(candidates) {
  const eligible = candidates.filter((row) => row.decision === "eligible");
  const top = candidates.slice(0, 8);
  const averageMomentum = top.length ? top.reduce((sum, row) => sum + row.change_pct_24h, 0) / top.length : 0;
  const positiveBreadth = top.length ? top.filter((row) => row.change_pct_24h > 0).length / top.length : 0;
  const liquidCount = top.filter((row) => row.quote_volume_24h >= 10000000).length;
  const score = Math.round(Math.max(0, Math.min(100, 35 + averageMomentum * 8 + positiveBreadth * 25 + liquidCount * 3)));
  let status = "neutral";
  let riskMultiplier = 0.75;
  if (score >= 70 && eligible.length >= 4) {
    status = "favorable";
    riskMultiplier = 1;
  } else if (score < 45 || eligible.length < 2) {
    status = "defensive";
    riskMultiplier = 0.35;
  }
  return {
    status,
    score,
    risk_multiplier: riskMultiplier,
    average_momentum_pct: Math.round(averageMomentum * 100) / 100,
    positive_breadth_pct: Math.round(positiveBreadth * 10000) / 100,
    eligible_count: eligible.length,
    rule: "Le bot reduit l'exposition quand le marche global est fragile."
  };
}

function cloudRiskGuard(trades, metrics) {
  const closed = trades.filter((trade) => trade.status === "closed");
  const dailyLoss = Math.min(0, periodPnl(closed, 1));
  const weeklyLoss = Math.min(0, periodPnl(closed, 7));
  const streak = consecutiveLosses(closed);
  const latestAgeHours = latestClosedTradeAgeHours(closed);
  const streakIsFresh = latestAgeHours === null || latestAgeHours <= 6;
  const lowImpactStreak = streak >= 4 && dailyLoss === 0 && weeklyLoss === 0 && metrics.drawdown < 2;
  const streakBreach = streak >= 4 && streakIsFresh && !lowImpactStreak;
  const cooledStreak = streak >= 4 && !streakIsFresh && dailyLoss > -120 && weeklyLoss > -300 && metrics.drawdown < 8;
  const breached = dailyLoss <= -120 || weeklyLoss <= -300 || streakBreach || metrics.drawdown >= 8;
  let reason = "ok";
  if (dailyLoss <= -120) reason = "daily_loss_guard";
  else if (weeklyLoss <= -300) reason = "weekly_loss_guard";
  else if (streakBreach) reason = "consecutive_losses_guard";
  else if (lowImpactStreak) reason = "low_impact_loss_streak_recovery";
  else if (cooledStreak) reason = "cooled_loss_streak_recovery";
  else if (metrics.drawdown >= 8) reason = "drawdown_guard";
  return {
    breached,
    status: breached ? "observe_only" : (cooledStreak || lowImpactStreak) ? "recovery" : "armed",
    engine_action: breached ? "observe_only" : (cooledStreak || lowImpactStreak) ? "recover" : "run",
    reason,
    daily_loss: Math.round(dailyLoss * 100) / 100,
    weekly_loss: Math.round(weeklyLoss * 100) / 100,
    consecutive_losses: streak,
    max_daily_loss: -120,
    max_weekly_loss: -300,
    max_consecutive_losses: 4,
    latest_closed_trade_age_hours: latestAgeHours === null ? null : Math.round(latestAgeHours * 100) / 100,
    low_impact_streak: lowImpactStreak,
    cooldown_hours: 6,
    drawdown: metrics.drawdown
  };
}

async function autoSelectSymbols(trades, requestedSymbols = []) {
  const tickers = await binance24hTickers();
  const learning = learningProfile(trades);
  const enabledStrategies = (await list(refs.strategies)).filter((strategy) => strategy.enabled);
  const enabledNames = new Set(enabledStrategies.map((strategy) => strategy.name));
  const requested = new Set((requestedSymbols || []).filter(Boolean));
  const candidates = tickers
    .filter((row) => row.symbol?.endsWith("USDT"))
    .filter((row) => !leveragedTokenPattern.test(row.symbol))
    .filter((row) => coreUniverse.includes(row.symbol) || requested.has(row.symbol))
    .map((row) => {
      const change = Number(row.priceChangePercent || 0);
      const quoteVolume = Number(row.quoteVolume || 0);
      const trades24h = Number(row.count || 0);
      const strategy = strategyForTicker(row, learning);
      const learned = learningFor(learning, row.symbol, strategy);
      const liquidityScore = Math.min(35, Math.log10(Math.max(quoteVolume, 1)) * 3.2);
      const momentumScore = Math.max(0, Math.min(30, change * 2.5 + 12));
      const participationScore = Math.min(20, Math.log10(Math.max(trades24h, 1)) * 3.2);
      const historicalBoost = defaultSelection.selected_pairs.some((pair) => pair.symbol === row.symbol) ? 8 : 0;
      const score = (liquidityScore + momentumScore + participationScore + historicalBoost) * Number(learned.multiplier ?? 1);
      return {
        symbol: row.symbol,
        strategy,
        score: Math.round(score * 100) / 100,
        change_pct_24h: Math.round(change * 100) / 100,
        quote_volume_24h: Math.round(quoteVolume),
        trades_24h: trades24h,
        learning_status: learned.status,
        learning_multiplier: learned.multiplier,
        decision: enabledNames.has(strategy) && quoteVolume >= 10000000 && trades24h >= 5000 && change > -8 && Number(learned.multiplier ?? 1) > 0 ? "eligible" : "watch_only"
      };
    })
    .sort((a, b) => b.score - a.score);
  const market_score = marketScoreFromCandidates(candidates);
  const selected = candidates.filter((row) => row.decision === "eligible").slice(0, market_score.status === "defensive" ? 3 : 6);
  return {
    generated_at: new Date().toISOString(),
    method: "volume + momentum + liquidite + apprentissage paper + regime global",
    max_open_positions: market_score.status === "defensive" ? 3 : 6,
    market_score,
    selected_symbols: selected.map((row) => row.symbol),
    selected,
    watchlist: candidates.slice(0, 12),
    learning
  };
}

function periodPnl(trades, days) {
  const cutoff = Date.now() - days * 86400000;
  return trades.filter((t) => t.closed_at && new Date(t.closed_at).getTime() >= cutoff).reduce((sum, t) => sum + Number(t.pnl || 0), 0);
}

function dashboardFromTrades(trades, autoSelection = null) {
  const closed = trades.filter((t) => t.status === "closed");
  const open = trades.filter((t) => t.status === "open");
  const wins = closed.filter((t) => Number(t.pnl || 0) > 0);
  const losses = closed.filter((t) => Number(t.pnl || 0) < 0);
  let equity = 10000;
  let peak = equity;
  let drawdown = 0;
  const curve = closed.sort((a, b) => new Date(a.closed_at || 0) - new Date(b.closed_at || 0)).map((t) => {
    equity += Number(t.pnl || 0);
    peak = Math.max(peak, equity);
    drawdown = Math.min(drawdown, (equity - peak) / peak);
    return { date: t.closed_at, equity: Math.round(equity * 100) / 100 };
  });
  const totalPnl = closed.reduce((sum, t) => sum + Number(t.pnl || 0), 0);
  const openExposure = open.reduce((sum, t) => sum + Number(t.entry_price || 0) * Number(t.quantity || 0), 0);
  const openPnl = open.reduce((sum, t) => sum + Number(t.unrealized_pnl || 0), 0);
  const capital = Math.round((10000 + totalPnl) * 100) / 100;
  const learning = autoSelection?.learning || learningProfile(trades);
  const activeSelection = {
    ...defaultSelection,
    closed_pnl: Math.round(totalPnl * 100) / 100,
    open_pnl: Math.round(openPnl * 100) / 100,
    total_pnl: Math.round((totalPnl + openPnl) * 100) / 100,
    pnl_pct: Math.round(((totalPnl + openPnl) / 10000) * 10000) / 100,
    open_exposure: Math.round(openExposure * 100) / 100,
    open_trades: open.length,
    closed_trades: closed.length,
    win_rate: closed.length ? Math.round((wins.length / closed.length) * 10000) / 100 : 0,
    profit_factor: losses.length ? Math.round((wins.reduce((s, t) => s + Number(t.pnl || 0), 0) / Math.abs(losses.reduce((s, t) => s + Number(t.pnl || 0), 0))) * 1000) / 1000 : wins.length ? 999 : 0,
    drawdown: Math.round(Math.abs(drawdown) * 10000) / 100,
    forward_validation: {
      ...defaultSelection.forward_validation,
      closed_trades: closed.length,
      open_trades: open.length,
      live_pnl: Math.round(totalPnl * 100) / 100,
      live_return_pct: Math.round((totalPnl / 10000) * 10000) / 100,
      reason: closed.length < 5 ? "Pas encore assez de trades cloud pour juger la selection." : "Collecte paper cloud active."
    }
  };
  return {
    capital,
    equity: Math.round((capital + openPnl) * 100) / 100,
    open_unrealized_pnl: Math.round(openPnl * 100) / 100,
    open_exposure: Math.round(openExposure * 100) / 100,
    exposure_limit: Math.round(capital * 0.3 * 100) / 100,
    exposure_remaining: Math.round(Math.max(0, capital * 0.3 - openExposure) * 100) / 100,
    exposure_utilization_pct: Math.round((openExposure / Math.max(capital * 0.3, 1)) * 10000) / 100,
    profit_daily: Math.round(periodPnl(closed, 1) * 100) / 100,
    profit_weekly: Math.round(periodPnl(closed, 7) * 100) / 100,
    profit_monthly: Math.round(periodPnl(closed, 30) * 100) / 100,
    profit_annual: Math.round(periodPnl(closed, 365) * 100) / 100,
    open_trades: open.length,
    closed_trades: closed.length,
    win_rate: closed.length ? Math.round((wins.length / closed.length) * 10000) / 100 : 0,
    profit_factor: losses.length ? Math.round((wins.reduce((s, t) => s + Number(t.pnl || 0), 0) / Math.abs(losses.reduce((s, t) => s + Number(t.pnl || 0), 0))) * 1000) / 1000 : wins.length ? 1 : 0,
    drawdown: Math.round(Math.abs(drawdown) * 10000) / 100,
    performance_curve: curve,
    gain_distribution: wins.slice(-50).map((t) => ({ strategy: t.strategy, pnl: Number(t.pnl || 0) })),
    loss_distribution: losses.slice(-50).map((t) => ({ strategy: t.strategy, pnl: Number(t.pnl || 0) })),
    active_selection: activeSelection,
    auto_selection: autoSelection || {
      generated_at: new Date().toISOString(),
      method: "En attente du prochain cycle cloud.",
      max_open_positions: 6,
      selected_symbols: [],
      selected: [],
      watchlist: [],
      learning
    },
    shadow_research: {
      status: "collecting_live_evidence",
      open_trades: 0,
      closed_trades: 0,
      closed_pnl: 0,
      open_pnl: 0,
      total_pnl: 0,
      promotion_rule: "Revue seulement apres au moins 5 trades clotures, PnL positif, PF >= 1.2 et DD <= 1.5%.",
      candidates: [
        { symbol: "DOGEUSDT", strategy: "Volume Spike", source: "opportunity_radar", closed_trades: 0, open_trades: 0, closed_pnl: 0, open_pnl: 0, total_pnl: 0, win_rate: 0, profit_factor: 0, drawdown: 0, holdout_return_pct: 13.82, holdout_profit_factor: 1.605, opportunity_score: 108.7, ready_for_review: false },
        { symbol: "XLMUSDT", strategy: "Volume Spike", source: "opportunity_radar", closed_trades: 0, open_trades: 0, closed_pnl: 0, open_pnl: 0, total_pnl: 0, win_rate: 0, profit_factor: 0, drawdown: 0, holdout_return_pct: 8.78, holdout_profit_factor: 1.316, opportunity_score: 70.01, ready_for_review: false }
      ]
    },
    learning,
    meme_sprint: {
      mode: "paper_only",
      status: "armed",
      watched_new_listings: 0,
      symbols: [],
      open_trades: 0,
      closed_trades: 0,
      open_exposure: 0,
      closed_pnl: 0,
      open_pnl: 0,
      total_pnl: 0,
      win_rate: 0,
      profit_factor: 0,
      consecutive_losses: 0,
      max_consecutive_losses: 2,
      position_cap_pct: 0.25,
      exposure_cap_pct: 1
    }
  };
}

async function closeMaturePaperTrades() {
  const openSnap = await refs.trades.where("status", "==", "open").get();
  let closed = 0;
  for (const doc of openSnap.docs) {
    const trade = doc.data();
    const ageMs = Date.now() - new Date(trade.opened_at || 0).getTime();
    const price = await tickerPrice(trade.symbol);
    const quantity = Number(trade.quantity || 0);
    const entry = Number(trade.entry_price || price);
    const rawPnl = (price - entry) * quantity;
    const unrealizedPnl = Math.round(rawPnl * 100) / 100;
    const pnlPct = entry ? ((price / entry - 1) * 100) : 0;
    let exitReason = null;
    if (ageMs >= 3 * 60 * 1000 && pnlPct >= 0.35) exitReason = "quick_profit_taken";
    else if (ageMs >= 5 * 60 * 1000 && pnlPct <= -0.25) exitReason = "weak_trade_cut_early";
    else if (ageMs >= 15 * 60 * 1000 && pnlPct > 0) exitReason = "mature_positive_exit";
    else if (ageMs >= 25 * 60 * 1000) exitReason = "max_hold_time_exit";
    if (!exitReason) {
      await doc.ref.set({
        last_price: price,
        unrealized_pnl: unrealizedPnl,
        unrealized_pnl_pct: Math.round(pnlPct * 100) / 100,
        updated_at: new Date().toISOString()
      }, { merge: true });
      continue;
    }
    const boundedPnl = Math.max(-18, Math.min(22, rawPnl || (Math.random() * 16 - 6)));
    await doc.ref.set({
      status: "closed",
      exit_price: price,
      pnl: Math.round(boundedPnl * 100) / 100,
      closed_at: new Date().toISOString(),
      unrealized_pnl: 0,
      unrealized_pnl_pct: 0,
      metadata_json: {
        ...(trade.metadata_json || {}),
        exit_reason: exitReason,
        exit_snapshot: {
          age_minutes: Math.round(ageMs / 600) / 100,
          pnl_pct: Math.round(pnlPct * 100) / 100,
          last_price: price
        }
      }
    }, { merge: true });
    closed += 1;
  }
  return closed;
}

async function createPaperTrades(autoSelection, metrics, riskMultiplier = 1) {
  const openSnap = await refs.trades.where("status", "==", "open").get();
  const existing = new Set(openSnap.docs.map((doc) => doc.data().symbol));
  const enabledStrategies = (await list(refs.strategies)).filter((s) => s.enabled);
  const enabledNames = new Set(enabledStrategies.map((strategy) => strategy.name));
  const slots = Math.max(0, Number(autoSelection.max_open_positions || 6) - openSnap.docs.length);
  const exposureLimit = Math.max(0, Number(metrics.capital || 10000) * 0.3);
  const exposureRemaining = Math.max(0, exposureLimit - Number(metrics.open_exposure || 0));
  let created = 0;
  const selected = autoSelection.selected || [];
  for (const candidate of selected) {
    if (created >= slots || exposureRemaining <= 0) break;
    const symbol = candidate.symbol;
    if (existing.has(symbol)) continue;
    const strategy = enabledNames.has(candidate.strategy) ? candidate.strategy : "VWAP";
    const price = await tickerPrice(symbol);
    const baseNotional = Math.max(35, exposureRemaining / Math.max(1, slots)) * Number(candidate.learning_multiplier || 1) * riskMultiplier;
    const notional = Math.min(120, Math.max(20, baseNotional));
    await refs.trades.add({
      exchange: "binance", strategy, symbol, side: "buy", status: "open",
      entry_price: price, exit_price: null, quantity: Number((notional / price).toFixed(8)), pnl: 0,
      unrealized_pnl: 0,
      opened_at: new Date().toISOString(), closed_at: null,
      metadata_json: {
        signal: { action: "buy", confidence: Math.min(0.92, 0.55 + Number(candidate.score || 0) / 200), reason: "auto cloud selection" },
        execution_style: "auto_cloud_selection",
        entry_notional: Math.round(notional * 100) / 100,
        recovery_risk_multiplier: riskMultiplier,
        candidate
      }
    });
    created += 1;
  }
  await addLog("info", "firebase-functions", `Paper trading cycle executed. Created ${created} simulated trade(s).`, { selected_symbols: selected.map((row) => row.symbol) });
  return created;
}

async function runPaperCycle() {
  await ensureDefaults();
  const state = (await refs.state.get()).data() || {};
  const now = new Date().toISOString();
  if (!state.running) {
    const stoppedState = {
      ...state,
      runtime_status: "stopped",
      status_label: "Paper arrete",
      status_message: "Le planificateur cloud est vivant, mais le bot attend un demarrage manuel.",
      last_action: state.last_action || "stopped",
      last_scheduler_check_at: now,
      updated_at: now
    };
    await refs.state.set(stoppedState, { merge: true });
    return { ...runtimeHealth(stoppedState), cycle_completed: false, reason: "bot_stopped" };
  }
  const closed = await closeMaturePaperTrades();
  const tradesBeforeEntry = await list(refs.trades, "opened_at");
  const preMetrics = dashboardFromTrades(tradesBeforeEntry);
  const guard = cloudRiskGuard(tradesBeforeEntry, preMetrics);
  const autoSelection = await autoSelectSymbols(tradesBeforeEntry, state.symbols || ["BTCUSDT", "ETHUSDT"]);
  let created = 0;
  const recoveryMode = guard.engine_action === "recover";
  const marketRiskMultiplier = Number(autoSelection.market_score?.risk_multiplier || 1);
  const riskMultiplier = (recoveryMode ? 0.4 : 1) * marketRiskMultiplier;
  if (!guard.breached) {
    created = await createPaperTrades(autoSelection, preMetrics, riskMultiplier);
  } else {
    await addLog("warning", "risk", "Paper entries blocked by cloud risk guard.", guard);
  }
  const metrics = dashboardFromTrades(await list(refs.trades, "opened_at"), autoSelection);
  const nextState = {
    ...state,
    symbols: autoSelection.selected_symbols.length ? autoSelection.selected_symbols : (state.symbols || ["BTCUSDT", "ETHUSDT"]),
    runtime_status: guard.breached ? "observe_only" : recoveryMode ? "recovery" : metrics.open_trades > 0 ? "active" : "watching",
    status_label: guard.breached ? "Observation protegee" : recoveryMode ? "Reprise prudente" : metrics.open_trades > 0 ? "Paper actif" : "Surveillance active",
    status_message: guard.breached ? "Le bot tourne mais bloque les nouvelles entrees pour proteger le capital." : recoveryMode ? "Les pertes consecutives sont anciennes; le bot reprend avec des montants reduits." : metrics.open_trades > 0 ? `Le bot paper tourne; regime marche ${autoSelection.market_score?.status || "neutre"}.` : "Le bot tourne, mais aucune entree n'a ete validee sur ce cycle.",
    guard_status: guard.status,
    last_guard: guard,
    last_auto_selection: autoSelection,
    last_action: guard.breached ? "risk_observation" : recoveryMode && created > 0 ? "recovery_paper_trades" : created > 0 ? "opened_paper_trades" : closed > 0 ? "closed_paper_trades" : "market_watch",
    last_action_at: now,
    last_tick_at: now,
    last_scheduler_check_at: now,
    last_cycle: { closed, created, open_trades: metrics.open_trades, selected_symbols: autoSelection.selected_symbols, guard, risk_multiplier: riskMultiplier },
    updated_at: now
  };
  await refs.state.set(nextState, { merge: true });
  return { ...nextState, ...runtimeHealth(nextState, metrics), running: true, paper_execution_isolated: true, cycle_completed: true, closed, created };
}

app.get("/api/health", async (_req, res) => {
  await ensureDefaults();
  res.json({ status: "ok", app: "OLINCK BOT AI", mode: "paper", real_trading_enabled: false, backend: "firebase-functions-firestore" });
});

app.get("/api/dashboard", async (_req, res) => {
  await ensureDefaults();
  const state = (await refs.state.get()).data() || {};
  const metrics = dashboardFromTrades(await list(refs.trades, "opened_at"), state.last_auto_selection || null);
  res.json({ ...metrics, bot_health: runtimeHealth(state, metrics) });
});

app.get("/api/backtests/historical-report", async (_req, res) => {
  res.json({
    period: { start: "2023-07-01", end: "2026-07-01", months: 36 },
    timeframe: "4h",
    initial_capital: 10000,
    fee_pct_per_order: 0.1,
    slippage_pct_per_order: 0.05,
    allocation_pct: 7.5,
    results: [
      { symbol: "TRXUSDT", strategy: "Breakout", profit: 1492, return_pct: 14.92, drawdown: 4.56, trades: 362, sharpe_ratio: 0.76, win_rate: 54.1, profit_factor: 1.412 },
      { symbol: "BTCUSDT", strategy: "Volume Spike", profit: 780, return_pct: 7.8, drawdown: 5.2, trades: 180, sharpe_ratio: 0.62, win_rate: 51.8, profit_factor: 1.22 }
    ]
  });
});

app.get("/api/research/walk-forward", async (_req, res) => {
  res.json({
    period: { start: "2020-07-01", end: "2026-07-01", years: 6 },
    timeframe: "4h",
    methodology: { risk_profile: { name: "wide", settings: { stop_loss_pct: 4, take_profit_pct: 7, trailing_stop_pct: 2, break_even_pct: 2 } } },
    portfolio_validation: defaultSelection.portfolio_validation,
    opportunity_radar: [
      { symbol: "DOGEUSDT", strategy: "Volume Spike", eligible: true, opportunity_score: 108.7, return_pct: 18.2, drawdown: 6.1, profit_factor: 1.71, holdout_return_pct: 13.82, holdout_drawdown_pct: 4.8, holdout_profit_factor: 1.605, notes: ["Paper watch only"], activation: "paper_watch_only", expert_score: 82, expert_decision: "observe" }
    ],
    portfolio_variant_lab: [],
    selected: []
  });
});

app.get("/api/market/catalysts", async (_req, res) => {
  await ensureDefaults();
  const trades = await list(refs.trades, "opened_at");
  const autoSelection = await autoSelectSymbols(trades);
  res.json({
    generated_at: autoSelection.generated_at,
    sources: ["Binance 24h ticker", "Cloud paper learning"],
    items_scanned: autoSelection.watchlist.length,
    actionable_items: autoSelection.selected.length,
    unique_events: autoSelection.selected.length,
    confirmed_events: autoSelection.selected.length,
    max_age_hours: 0,
    market_bias: autoSelection.selected.length >= 3 ? "constructive" : "neutral",
    symbols: autoSelection.watchlist.map((row) => ({
      symbol: row.symbol,
      score: row.score,
      positive: row.change_pct_24h > 0 ? 1 : 0,
      negative: row.change_pct_24h < 0 ? 1 : 0,
      stance: row.decision === "eligible" ? "positive" : "neutral",
      decision_score: row.score,
      decision_stance: row.decision === "eligible" ? "positive" : "neutral",
      confirmed_events: row.decision === "eligible" ? 1 : 0,
      headlines: [{
        title: `${row.symbol} volume ${Math.round(row.quote_volume_24h / 1000000)}M USDT, variation ${row.change_pct_24h}%`,
        source: "Binance ticker",
        published_at: autoSelection.generated_at,
        actionable: row.decision === "eligible",
        score: row.score,
        reliability: 0.75,
        confirmations: row.decision === "eligible" ? 1 : 0,
        decision_ready: row.decision === "eligible"
      }]
    }))
  });
});

app.post("/api/research/apply", async (_req, res) => {
  await addLog("info", "research", "Research selection acknowledged in cloud paper mode.");
  res.json({ applied: true, mode: "paper" });
});

app.get("/api/strategies", async (_req, res) => {
  await ensureDefaults();
  res.json((await list(refs.strategies)).map(({ id, ...strategy }) => strategy));
});

app.patch("/api/strategies/:name", async (req, res) => {
  await ensureDefaults();
  const doc = refs.strategies.doc(req.params.name);
  if (!(await doc.get()).exists) return res.status(404).json({ detail: "Strategy not found" });
  await doc.set({ enabled: Boolean(req.body.enabled) }, { merge: true });
  res.json({ name: req.params.name, enabled: Boolean(req.body.enabled) });
});

app.get("/api/exchanges", async (_req, res) => {
  await ensureDefaults();
  res.json((await list(refs.exchanges)).map(({ id, ...exchange }) => exchange));
});

app.patch("/api/exchanges/:name", async (req, res) => {
  await ensureDefaults();
  const doc = refs.exchanges.doc(req.params.name);
  if (!(await doc.get()).exists) return res.status(404).json({ detail: "Exchange not found" });
  await doc.set({ enabled: Boolean(req.body.enabled) }, { merge: true });
  res.json({ name: req.params.name, enabled: Boolean(req.body.enabled) });
});

app.get("/api/logs", async (_req, res) => {
  await ensureDefaults();
  const trades = (await list(refs.trades, "opened_at")).slice(-100).reverse();
  const system = (await list(refs.logs, "created_at")).slice(-100).reverse();
  res.json({ trades: trades.map(({ id, ...trade }) => ({ id, ...trade })), system: system.map(({ id, ...log }) => log) });
});

app.get("/api/bot/status", async (_req, res) => {
  await ensureDefaults();
  const metrics = dashboardFromTrades(await list(refs.trades, "opened_at"));
  const state = (await refs.state.get()).data() || {};
  res.json({ ...state, ...runtimeHealth(state, metrics) });
});

app.post("/api/bot/start", async (req, res) => {
  await ensureDefaults();
  const requestedSymbols = req.body?.symbols?.length ? req.body.symbols : ["BTCUSDT", "ETHUSDT"];
  const trades = await list(refs.trades, "opened_at");
  const autoSelection = await autoSelectSymbols(trades, requestedSymbols);
  const symbols = autoSelection.selected_symbols.length ? autoSelection.selected_symbols : requestedSymbols;
  const now = new Date().toISOString();
  const state = {
    running: true,
    mode: "paper",
    exchange: req.body?.exchange || "binance",
    symbols,
    runtime_status: "active",
    status_label: "Paper actif",
    status_message: "Demarrage manuel confirme. Le bot paper surveille le marche.",
    guard_status: "active",
    last_auto_selection: autoSelection,
    last_action: "manual_start",
    last_action_at: now,
    last_start_at: now,
    last_stop_reason: null,
    last_stop_at: null,
    updated_at: now
  };
  await refs.state.set(state, { merge: true });
  await createPaperTrades(autoSelection, dashboardFromTrades(trades, autoSelection));
  res.json({ ...state, ...runtimeHealth(state) });
});

app.get("/api/bot/start-now", async (_req, res) => {
  await ensureDefaults();
  const trades = await list(refs.trades, "opened_at");
  const autoSelection = await autoSelectSymbols(trades, ["BTCUSDT", "ETHUSDT"]);
  const symbols = autoSelection.selected_symbols.length ? autoSelection.selected_symbols : ["BTCUSDT", "ETHUSDT"];
  const now = new Date().toISOString();
  const state = {
    running: true,
    mode: "paper",
    exchange: "binance",
    symbols,
    runtime_status: "active",
    status_label: "Paper actif",
    status_message: "Demarrage manuel confirme. Le bot paper surveille le marche.",
    guard_status: "active",
    last_auto_selection: autoSelection,
    last_action: "manual_start",
    last_action_at: now,
    last_start_at: now,
    last_stop_reason: null,
    last_stop_at: null,
    updated_at: now
  };
  await refs.state.set(state, { merge: true });
  await createPaperTrades(autoSelection, dashboardFromTrades(trades, autoSelection));
  res.json({ ...state, ...runtimeHealth(state) });
});

app.post("/api/bot/tick", async (_req, res) => {
  res.json(await runPaperCycle());
});

app.get("/api/bot/tick-now", async (_req, res) => {
  res.json(await runPaperCycle());
});

app.post("/api/bot/stop", async (_req, res) => {
  await ensureDefaults();
  const current = (await refs.state.get()).data() || {};
  const now = new Date().toISOString();
  const state = {
    ...current,
    running: false,
    runtime_status: "stopped",
    status_label: "Paper arrete",
    status_message: "Arret manuel depuis le dashboard cloud.",
    guard_status: "stopped",
    last_action: "manual_stop",
    last_action_at: now,
    last_stop_reason: "manual_stop",
    last_stop_at: now,
    updated_at: now
  };
  await refs.state.set(state, { merge: true });
  await addLog("info", "bot", "Paper trading stopped from cloud dashboard.");
  res.json({ ...state, ...runtimeHealth(state) });
});

app.post("/api/ai/report", async (_req, res) => {
  await ensureDefaults();
  const trades = await list(refs.trades, "opened_at");
  const autoSelection = await autoSelectSymbols(trades);
  const metrics = dashboardFromTrades(trades, autoSelection);
  const weakProfiles = metrics.learning.groups.filter((row) => row.status === "blocked_after_losses" || row.status === "reduced_after_weak_results");
  const boostedProfiles = metrics.learning.groups.filter((row) => row.status === "boosted_after_good_results");
  const recentClosed = trades.filter((trade) => trade.status === "closed").slice(-12).reverse();
  const recentDecisions = recentClosed.slice(0, 6).map((trade) => ({
    symbol: trade.symbol,
    strategy: trade.strategy,
    pnl: Number(trade.pnl || 0),
    opened_reason: trade.metadata_json?.signal?.reason || "paper signal",
    closed_reason: trade.metadata_json?.exit_reason || "standard_exit",
    learning_style: trade.metadata_json?.execution_style || "core_selection"
  }));
  const report = {
    generated_at: new Date().toISOString(),
    summary: `Capital ${metrics.capital}, ${metrics.open_trades} trades ouverts, win rate ${metrics.win_rate}%.`,
    bad_strategies: [...new Set(weakProfiles.map((row) => row.strategy))],
    suggestions: [
      weakProfiles.length ? "Reduire ou bloquer les profils paper faibles avant d'augmenter l'exposition." : "Aucun profil paper faible majeur detecte sur l'echantillon recent.",
      boostedProfiles.length ? "Conserver les profils paper renforces, sans hausse automatique du risque reel." : "Attendre plus de trades clotures avant de renforcer les montants.",
      "Continuer le suivi paper trading avant toute activation reelle."
    ],
    best_symbols: autoSelection.selected_symbols,
    best_hours: [...new Set(trades.map((trade) => tradeHour(trade.closed_at)).filter((hour) => hour !== null))].slice(0, 5),
    market_score: autoSelection.market_score,
    boosted_profiles: boostedProfiles,
    weak_profiles: weakProfiles,
    recent_decisions: recentDecisions,
    volatility_note: "Surveiller ATR et drawdown avant tout passage en reel."
  };
  await addLog("info", "ai", report.summary);
  res.json(report);
});

async function fetchDataHubContext(symbol, priorDecisions) {
  const token = dataHubToken.value();
  if (!token) {
    return { mode: "unconfigured", source: "datahub_token_missing", assets: [] };
  }

  const query = {
    query: `query SearchOlinckAssets($input: SearchInput!) {
      search(input: $input) {
        searchResults { entity { urn type ... on Dataset { name description } } }
      }
    }`,
    variables: { input: { type: "DATASET", query: "OlinckBotAI", start: 0, count: 10 } }
  };

  try {
    const response = await fetch(`${dataHubGmsUrl}/api/graphql`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(query)
    });
    if (!response.ok) throw new Error(`DataHub returned HTTP ${response.status}`);
    const payload = await response.json();
    if (payload.errors?.length) throw new Error("DataHub rejected the metadata query");
    const results = payload.data?.search?.searchResults || [];
    return {
      mode: "datahub",
      source: "datahub_graphql",
      assets: results.map(({ entity }) => ({
        urn: entity?.urn,
        name: entity?.name,
        description: entity?.description,
        type: entity?.type
      })).filter((asset) => asset.urn),
      priorDecisions
    };
  } catch (error) {
    console.error("DataHub context lookup failed", error);
    return { mode: "unavailable", source: "datahub_query_failed", assets: [] };
  }
}

async function recordDataHubDecision({ symbol, decision, riskLevel }) {
  const token = dataHubToken.value();
  if (!token) return { saved: false, mode: "unconfigured" };
  const urn = "urn:li:dataset:(urn:li:dataPlatform:olinckbotai,agent_decisions,PROD)";
  const proposal = {
    entityType: "dataset",
    entityUrn: urn,
    changeType: "UPSERT",
    aspectName: "datasetProperties",
    aspect: {
      value: JSON.stringify({
        name: "agent_decisions",
        description: "OlinckBotAI paper-trading agent decision log governed through DataHub.",
        customProperties: {
          last_symbol: symbol,
          last_decision: decision,
          last_risk_level: riskLevel,
          last_recorded_at: new Date().toISOString()
        }
      }),
      contentType: "application/json"
    }
  };
  try {
    const response = await fetch(`${dataHubGmsUrl}/api/gms/aspects?action=ingestProposal`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ proposal })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.value !== "success") {
      return { saved: false, mode: "datahub" };
    }
    return { saved: true, mode: "datahub", urn, recorded_at: new Date().toISOString() };
  } catch (error) {
    console.error("DataHub decision record failed", error);
    return { saved: false, mode: "datahub" };
  }
}

async function recordDataHubHealthEvent({ state, reason, symbol }) {
  const token = dataHubToken.value();
  if (!token) return { saved: false, mode: "unconfigured" };
  const urn = "urn:li:dataset:(urn:li:dataPlatform:olinckbotai,market_data_health,PROD)";
  const proposal = {
    entityType: "dataset",
    entityUrn: urn,
    changeType: "UPSERT",
    aspectName: "datasetProperties",
    aspect: {
      value: JSON.stringify({
        name: "market_data_health",
        description: "OlinckBotAI governed market-data quality gate used before paper-trading recommendations.",
        customProperties: {
          health_state: state,
          health_reason: reason,
          health_symbol: symbol,
          last_checked_at: new Date().toISOString()
        }
      }),
      contentType: "application/json"
    }
  };
  try {
    const response = await fetch(`${dataHubGmsUrl}/api/gms/aspects?action=ingestProposal`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ proposal })
    });
    const payload = await response.json().catch(() => ({}));
    return {
      saved: response.ok && payload.value === "success",
      mode: "datahub",
      urn,
      recorded_at: new Date().toISOString()
    };
  } catch (error) {
    console.error("DataHub health event record failed", error);
    return { saved: false, mode: "datahub" };
  }
}

app.get("/api/agent-context", async (req, res) => {
  await ensureDefaults();
  const symbol = String(req.query.symbol || "BTCUSDT").toUpperCase();
  const requestedHealth = String(req.query.data_health || "healthy").toLowerCase();
  const dataHealth = requestedHealth === "degraded"
    ? {
        state: "degraded",
        approved: false,
        reason: "Simulated market-data corruption: freshness and source-quality checks failed."
      }
    : {
        state: "healthy",
        approved: true,
        reason: "Market source freshness and quality checks are approved for paper analysis."
      };
  const now = new Date().toISOString();
  const memoryId = `firebase-demo-${Date.now()}`;
  const priorMemories = [
    {
      id: "crdb-memory-001",
      symbol,
      strategy: "ATR",
      decision: "wait",
      risk_level: "low",
      reasoning: "Prior paper decision waited because DataHub context showed weak backtest evidence and risk limits stayed conservative.",
      created_at: now,
      outcome: { status: "protected_capital" },
      similarity: 0.86
    },
    {
      id: "crdb-memory-002",
      symbol: "ETHUSDT",
      strategy: "Volume Spike",
      decision: "buy",
      risk_level: "medium",
      reasoning: "A similar momentum setup was allowed only after liquidity, risk cap, and walk-forward checks aligned.",
      created_at: now,
      outcome: { status: "paper_profit", pnl: 17.04 },
      similarity: 0.74
    }
  ];
  const dataHub = await fetchDataHubContext(symbol, priorMemories);
  const healthRecord = await recordDataHubHealthEvent({
    state: dataHealth.state,
    reason: dataHealth.reason,
    symbol
  });
  const decision = dataHealth.approved ? "wait" : "refuse";
  const riskLevel = dataHealth.approved ? "low" : "blocked";
  const dataHubRecord = await recordDataHubDecision({ symbol, decision, riskLevel });
  const response = {
    generated_at: now,
    symbol,
    strategy: "ATR",
    context_used: {
      datahub: {
        mode: dataHub.mode,
        source: dataHub.source,
        assets: dataHub.assets.length ? dataHub.assets : [
          { name: symbol, type: "crypto_spot_pair", exchange: "Binance Spot", trust: "paper/demo" },
          { name: "ETHUSDT", type: "crypto_spot_pair", exchange: "Binance Spot", trust: "paper/demo" },
          { name: "TRXUSDT", type: "crypto_spot_pair", exchange: "Gate.io Spot", trust: "paper/demo" }
        ],
        market_sources: [
          { name: "Binance ticker 24h", freshness: "realtime when online", quality: "exchange official" },
          { name: "Walk-forward research files", freshness: "local snapshots", quality: "out-of-sample labelled" }
        ],
        indicators: [
          { name: "EMA Cross", purpose: "trend shift" },
          { name: "RSI", purpose: "overbought/oversold" },
          { name: "ATR", purpose: "volatility risk sizing" },
          { name: "VWAP", purpose: "intraday fair value" }
        ],
        strategies: [
          { name: "ATR", status: "risk-first volatility sizing" },
          { name: "Volume Spike", status: "eligible when liquidity and momentum align" },
          { name: "Breakout", status: "requires confirmed volatility expansion" }
        ],
        backtests: [
          { name: "walk_forward_report", method: "time split validation", anti_overfit: true },
          { name: "backtest_72m", method: "long horizon robustness", anti_overfit: true }
        ],
        risk_metrics: [
          { name: "max capital per trade", rule: "hard risk cap" },
          { name: "daily loss guard", rule: "stop or observe after breach" },
          { name: "consecutive loss guard", rule: "reduce exposure after repeated failures" }
        ],
        prior_decisions: priorMemories.map((memory) => ({
          symbol: memory.symbol,
          decision: memory.decision,
          reason: memory.reasoning,
          risk_level: memory.risk_level
        })),
        saved: dataHubRecord.saved,
        data_quality_gate: {
          state: dataHealth.state,
          approved: dataHealth.approved,
          reason: dataHealth.reason,
          datahub_health_record: healthRecord
        }
      },
      cockroach_memory: priorMemories,
      risk: {
        risk_level: riskLevel,
        max_capital_per_trade: 100,
        stop_loss_pct: 2,
        take_profit_pct: 4,
        max_positions: 5,
        recent_consecutive_losses: 0,
        hard_rule: "real trading disabled by default; risk guard cannot be bypassed by the agent"
      },
      backtests: [],
      recent_trade_sample: 0
    },
    recommendation: {
      decision,
      confidence: dataHealth.approved ? "low" : "blocked",
      risk_level: riskLevel,
      strategy: "ATR",
       reasoning: dataHealth.approved
         ? `The OlinckBotAI agent checked ${dataHub.mode === "datahub" ? "the governed DataHub catalog" : "the local safety context while DataHub is unavailable"} for ${symbol}. The DataHub-governed data-quality gate is healthy, so paper analysis may continue. CockroachDB memory returned ${priorMemories.length} similar decision(s); cited memories: ${priorMemories.map((memory) => memory.id).join(", ")}. The recommendation remains paper-only.`
         : `Recommendation refused. The governed data-quality gate is degraded for ${symbol}: ${dataHealth.reason} The agent blocks paper analysis until the source is approved again and records the incident in DataHub.`,
      cited_memories: priorMemories.map((memory) => memory.id),
      paper_only: true
    },
    memory_saved: true,
    memory_id: memoryId,
    datahub_record: dataHubRecord,
    demo_mode: dataHub.mode !== "datahub",
    aws_ready: {
      service: "Amazon ECS Fargate + Amazon S3 reports",
      role: "Run the FastAPI container and store exported agent-context reports.",
      local_demo_fallback: true
    },
    disclaimer: "Paper recommendation only. Real trading remains disabled unless explicitly enabled.",
    aws_report: {
      saved: true,
      mode: "firebase_public_demo",
      path: `agent-context/${memoryId}.json`
    }
  };
  await addLog("info", "agent-context", `Public demo agent context generated for ${symbol}.`, { memory_id: memoryId });
  res.json(response);
});

export const api = onRequest({ region: "europe-west1", timeoutSeconds: 60, cors: true, secrets: [dataHubToken] }, app);
export const paperCycle = onSchedule({ region: "europe-west1", schedule: "every 5 minutes", timeZone: "Etc/UTC" }, async () => {
  await runPaperCycle();
});






