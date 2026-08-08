import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Bell, Bot, BrainCircuit, ChartCandlestick, CircleDollarSign, Play, Radio, ShieldCheck, Square, Target, TrendingDown, TrendingUp } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { MetricCard } from "./components/MetricCard";
import { ToggleRow } from "./components/ToggleRow";
import { api } from "./services/api";
import type { AgentContext, Dashboard, Exchange, HistoricalBacktest, Logs, MarketCatalysts, Strategy, WalkForwardReport } from "./types/domain";
import "./styles.css";

const guardLabel = (status?: string) => {
  if (status === "recovery") return "Recuperation";
  if (status === "observe_only") return "Observation";
  return "Active";
};

const guardTone = (status?: string) => {
  if (status === "observe_only") return "bad";
  if (status === "recovery") return "neutral";
  return "good";
};

const emptyDashboard: Dashboard = {
  capital: 0,
  equity: 0,
  open_unrealized_pnl: 0,
  open_exposure: 0,
  exposure_limit: 0,
  exposure_remaining: 0,
  exposure_utilization_pct: 0,
  profit_daily: 0,
  profit_weekly: 0,
  profit_monthly: 0,
  profit_annual: 0,
  open_trades: 0,
  closed_trades: 0,
  win_rate: 0,
  profit_factor: 0,
  drawdown: 0,
  performance_curve: [],
  gain_distribution: [],
  loss_distribution: [],
  active_selection: {
    selected_pairs: [],
    portfolio_validation: {
      selected_count: 0,
      max_portfolio_exposure_pct: 20,
      eligible: false,
      full: {},
      holdout: {},
      monte_carlo: {
        simulations: 0,
        probability_positive_pct: 0,
        median_return_pct: 0,
        p05_return_pct: 0,
        p95_return_pct: 0,
        p95_drawdown_pct: 0,
        confidence: "insufficient"
      },
      benchmark: {
        full: {},
        holdout: {},
        strategy_holdout_score: 0,
        benchmark_holdout_score: 0,
        excess_holdout_return_pct: 0
      }
    },
    closed_pnl: 0,
    open_pnl: 0,
    total_pnl: 0,
    open_exposure: 0,
    open_trades: 0,
    closed_trades: 0,
    win_rate: 0,
    profit_factor: 0,
    drawdown: 0,
    pnl_pct: 0,
    guard: {
      status: "armed",
      breached: false,
      max_loss_pct: 1.5,
      consecutive_losses: 0,
      max_consecutive_losses: 3
    },
    forward_validation: {
      status: "observation",
      reason: "Pas encore assez de trades live pour juger la selection.",
      days_live: 0,
      expected_trades_so_far: 0,
      min_trades_for_judgement: 5,
      closed_trades: 0,
      open_trades: 0,
      live_pnl: 0,
      live_return_pct: 0,
      expected_median_return_pct: 0,
      expected_holdout_return_pct: 0,
      expected_max_drawdown_pct: 0
    },
    promotion_readiness: {
      status: "collecting_evidence",
      ready: false,
      passed_requirements: 0,
      total_requirements: 6,
      risk_increase_automatic: false,
      reason: "Le risque reste bloque au niveau actuel pendant la collecte de preuves.",
      requirements: {}
    }
  }
};

function money(value: number) {
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD" }).format(value);
}

function textValue(value: unknown, fallback = "-") {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function dateTime(value: unknown) {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function runtimeTone(statusValue: unknown) {
  if (statusValue === "stopped") return "bad";
  if (statusValue === "observe_only") return "neutral";
  return "good";
}

export default function App() {
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [logs, setLogs] = useState<Logs>({ trades: [], system: [] });
  const [historical, setHistorical] = useState<HistoricalBacktest | null>(null);
  const [walkForward, setWalkForward] = useState<WalkForwardReport | null>(null);
  const [catalysts, setCatalysts] = useState<MarketCatalysts | null>(null);
  const [agentContext, setAgentContext] = useState<AgentContext | null>(null);
  const [status, setStatus] = useState<Record<string, unknown>>({ running: false });
  const [report, setReport] = useState<string>("Aucun rapport généré");
  const [error, setError] = useState("");
  const [botAction, setBotAction] = useState<"start" | "stop" | null>(null);

  async function refreshCore() {
    try {
      const [dash, botStatus] = await Promise.all([api.dashboard(), api.status()]);
      setDashboard(dash);
      setStatus(botStatus);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur inconnue");
    }
  }

  async function refreshSlow() {
    const [strategyList, exchangeList, logList, historicalReport, robustReport, catalystReport, agentContextReport] = await Promise.all([
      api.strategies(),
      api.exchanges(),
      api.logs(),
      api.historicalBacktest(),
      api.walkForwardReport(),
      api.marketCatalysts(),
      api.agentContext("BTCUSDT")
    ]);
    setStrategies(strategyList);
    setExchanges(exchangeList);
    setLogs(logList);
    setHistorical(historicalReport);
    setWalkForward(robustReport);
    setCatalysts(catalystReport);
    setAgentContext(agentContextReport);
  }

  async function refreshAll() {
    await refreshCore();
    refreshSlow().catch((err) => setError(err instanceof Error ? err.message : "Erreur inconnue"));
  }

  useEffect(() => {
    refreshAll();
    const coreTimer = window.setInterval(refreshCore, 15_000);
    const slowTimer = window.setInterval(() => {
      refreshSlow().catch((err) => setError(err instanceof Error ? err.message : "Erreur inconnue"));
    }, 120_000);
    return () => {
      window.clearInterval(coreTimer);
      window.clearInterval(slowTimer);
    };
  }, []);

  async function startPaperBot() {
    try {
      setBotAction("start");
      await api.startBot(["BTCUSDT", "ETHUSDT"]);
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossible de demarrer le bot");
    } finally {
      setBotAction(null);
    }
  }

  async function stopPaperBot() {
    try {
      setBotAction("stop");
      await api.stopBot();
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossible d'arreter le bot");
    } finally {
      setBotAction(null);
    }
  }

  const distribution = useMemo(() => {
    return [...dashboard.gain_distribution, ...dashboard.loss_distribution].slice(-20);
  }, [dashboard]);

  const botHealth = dashboard.bot_health ?? status;
  const isRunning = Boolean(botHealth.running ?? status.running);
  const runtimeStatus = textValue(botHealth.runtime_status, isRunning ? "active" : "stopped");
  const statusLabel = textValue(botHealth.status_label, isRunning ? "Paper actif" : "Paper arrete");
  const statusMessage = textValue(botHealth.status_message, isRunning ? "Le bot paper tourne." : "Le bot attend un demarrage manuel.");

  return (
    <main>
      <header className="topbar">
        <div>
          <p>Trading algorithmique multi-exchange {status.paper_execution_isolated ? "- Paper isolé" : ""}</p>
          <h1>OLINCK BOT AI</h1>
        </div>
        <div className="actions">
          <div className="runtime-status"><ShieldCheck size={18} />{status.running ? "Paper actif" : "Paper arrêté"}</div>
          {status.running ? (
            <button className="primary danger" type="button" onClick={stopPaperBot} disabled={botAction !== null}>
              <Square size={17} />
              {botAction === "stop" ? "Arret..." : "Arreter"}
            </button>
          ) : (
            <button className="primary" type="button" onClick={startPaperBot} disabled={botAction !== null}>
              <Play size={17} />
              {botAction === "start" ? "Demarrage..." : "Demarrer"}
            </button>
          )}
        </div>
      </header>

      <section className={`runtime-panel ${runtimeTone(runtimeStatus)}`}>
        <div>
          <strong>{statusMessage}</strong>
          <span>
            Dernier cycle: {dateTime(botHealth.last_tick_at)} | Prochain controle: {dateTime(botHealth.next_tick_at)} | Derniere action: {textValue(botHealth.last_action)}
          </span>
        </div>
        {!isRunning && (
          <span className="stop-reason">Raison: {textValue(botHealth.last_stop_reason, "attente_demarrage")}</span>
        )}
      </section>

      {error && (
        <div className="alert">
          <AlertTriangle size={18} />
          {error}
        </div>
      )}

      <section className="metrics-grid">
        <MetricCard label="Equite" value={money(dashboard.equity || dashboard.capital)} tone={(dashboard.open_unrealized_pnl ?? 0) >= 0 ? "good" : "bad"} icon={<CircleDollarSign />} />
        <MetricCard label="Capital realise" value={money(dashboard.capital)} icon={<ShieldCheck />} />
        <MetricCard label="Profit jour" value={money(dashboard.profit_daily)} tone={dashboard.profit_daily >= 0 ? "good" : "bad"} icon={<TrendingUp />} />
        <MetricCard label="Profit semaine" value={money(dashboard.profit_weekly)} tone={dashboard.profit_weekly >= 0 ? "good" : "bad"} icon={<Target />} />
        <MetricCard label="Profit mois" value={money(dashboard.profit_monthly)} tone={dashboard.profit_monthly >= 0 ? "good" : "bad"} icon={<Activity />} />
        <MetricCard label="Win Rate" value={`${dashboard.win_rate}%`} icon={<ShieldCheck />} />
        <MetricCard label="Drawdown" value={`${dashboard.drawdown}%`} tone={dashboard.drawdown > 8 ? "bad" : "neutral"} icon={<TrendingDown />} />
        <MetricCard label="Trades ouverts" value={String(dashboard.open_trades)} icon={<ChartCandlestick />} />
        <MetricCard label="PnL ouvert" value={money(dashboard.open_unrealized_pnl ?? 0)} tone={(dashboard.open_unrealized_pnl ?? 0) >= 0 ? "good" : "bad"} icon={<TrendingUp />} />
        <MetricCard label="Exposition" value={money(dashboard.open_exposure ?? 0)} icon={<Target />} />
        <MetricCard label="Capacite restante" value={money(dashboard.exposure_remaining ?? 0)} tone={(dashboard.exposure_utilization_pct ?? 0) >= 90 ? "bad" : "neutral"} icon={<ShieldCheck />} />
        <MetricCard label="Profit Factor" value={String(dashboard.profit_factor)} icon={<Bot />} />
        <MetricCard label="Etat bot" value={statusLabel} tone={runtimeTone(runtimeStatus)} icon={<Radio />} />
        <MetricCard label="Regime marche" value={`${dashboard.auto_selection?.market_score?.status ?? "neutre"} ${dashboard.auto_selection?.market_score?.score ?? 0}/100`} tone={dashboard.auto_selection?.market_score?.status === "defensive" ? "bad" : dashboard.auto_selection?.market_score?.status === "favorable" ? "good" : "neutral"} icon={<Activity />} />
      </section>

      <section className="panel wide auto-selection-panel">
        <div className="panel-head">
          <h2>Selection automatique</h2>
          <span>{dashboard.auto_selection?.selected_symbols?.length ?? 0} actifs choisis</span>
        </div>
        <p className="backtest-note">
          {dashboard.auto_selection?.method ?? "Le bot attend le prochain cycle pour classer les actifs."}
        </p>
        <p className="backtest-note">
          Regime {dashboard.auto_selection?.market_score?.status ?? "neutre"} - score {dashboard.auto_selection?.market_score?.score ?? 0}/100 - exposition x{dashboard.auto_selection?.market_score?.risk_multiplier ?? 1} - actifs eligibles {dashboard.auto_selection?.market_score?.eligible_count ?? 0}.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Actif</th><th>Strategie</th><th>Score</th><th>24h</th><th>Volume</th><th>Trades</th><th>Apprentissage</th><th>Decision</th></tr>
            </thead>
            <tbody>
              {(dashboard.auto_selection?.watchlist ?? []).slice(0, 8).map((row) => (
                <tr key={`${row.symbol}-${row.strategy}`}>
                  <td>{row.symbol.replace("USDT", "")}</td>
                  <td>{row.strategy}</td>
                  <td>{row.score}</td>
                  <td className={row.change_pct_24h >= 0 ? "positive" : "negative"}>{row.change_pct_24h}%</td>
                  <td>{money(row.quote_volume_24h)}</td>
                  <td>{row.trades_24h}</td>
                  <td>{row.learning_status} x{row.learning_multiplier}</td>
                  <td className={row.decision === "eligible" ? "positive" : ""}>{row.decision}</td>
                </tr>
              ))}
              {!(dashboard.auto_selection?.watchlist ?? []).length && (
                <tr><td colSpan={8}>Classement en attente du prochain cycle cloud</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel wide agent-memory-panel">
        <div className="panel-head">
          <h2>Agentic Memory</h2>
          <span>{agentContext ? `${agentContext.context_used.datahub.mode} + CockroachDB` : "Chargement"}</span>
        </div>
        <p className="backtest-note">
          DataHub AI Context: {agentContext?.context_used.datahub.source ?? "contexte en attente"} - {agentContext?.context_used.datahub.assets.length ?? 0} actifs, {agentContext?.context_used.datahub.indicators.length ?? 0} indicateurs, {agentContext?.context_used.datahub.backtests.length ?? 0} sources de backtest consultes.
        </p>
        <p className="backtest-note">
          Recommandation: <strong>{agentContext?.recommendation.decision ?? "wait"}</strong> sur {agentContext?.symbol ?? "BTCUSDT"} avec {agentContext?.strategy ?? "strategie"} - confiance {agentContext?.recommendation.confidence ?? "demo"} - risque {agentContext?.recommendation.risk_level ?? "medium"}.
        </p>
        <p className="backtest-note">
          {agentContext?.recommendation.reasoning ?? "L'agent attend le contexte DataHub et la memoire CockroachDB avant de recommander une action."}
        </p>
        <div className="mini-metrics">
          <MetricCard label="Souvenirs similaires" value={String(agentContext?.context_used.cockroach_memory.length ?? 0)} icon={<BrainCircuit />} />
          <MetricCard label="Memoire sauvegardee" value={agentContext?.memory_saved ? "oui" : "non"} tone={agentContext?.memory_saved ? "good" : "neutral"} icon={<ShieldCheck />} />
          <MetricCard label="DataHub record" value={agentContext?.datahub_record.saved ? "oui" : "demo"} tone={agentContext?.datahub_record.saved ? "good" : "neutral"} icon={<Radio />} />
          <MetricCard label="AWS ready" value={agentContext?.aws_ready.service ?? "ECS + S3"} icon={<Activity />} />
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Memoire</th><th>Actif</th><th>Strategie</th><th>Decision</th><th>Risque</th><th>Similarite</th><th>Raison citee</th></tr>
            </thead>
            <tbody>
              {(agentContext?.context_used.cockroach_memory ?? []).slice(0, 5).map((memory) => (
                <tr key={memory.id}>
                  <td>{memory.id.slice(0, 8)}</td>
                  <td>{memory.symbol.replace("USDT", "")}</td>
                  <td>{memory.strategy}</td>
                  <td>{memory.decision}</td>
                  <td>{memory.risk_level}</td>
                  <td>{memory.similarity ?? 0}</td>
                  <td>{memory.reasoning}</td>
                </tr>
              ))}
              {!(agentContext?.context_used.cockroach_memory ?? []).length && (
                <tr><td colSpan={7}>Premier souvenir en cours de creation dans le mode demo local.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="backtest-note">
          Confirmation: decision memorisee sous {agentContext?.memory_id ? agentContext.memory_id.slice(0, 8) : "demo"}; trading reel toujours desactive par defaut.
        </p>
      </section>

      <section className="content-grid">
        <div className="panel wide">
          <div className="panel-head">
            <h2>Selection active</h2>
            <span>{dashboard.active_selection?.risk_profile ?? "Recherche"} - {dashboard.active_selection?.timeframe ?? "4h"}</span>
          </div>
          <div className="mini-metrics">
            <MetricCard label="PnL selection" value={money(dashboard.active_selection?.total_pnl ?? 0)} tone={(dashboard.active_selection?.total_pnl ?? 0) >= 0 ? "good" : "bad"} icon={<TrendingUp />} />
            <MetricCard label="PnL ouvert selection" value={money(dashboard.active_selection?.open_pnl ?? 0)} tone={(dashboard.active_selection?.open_pnl ?? 0) >= 0 ? "good" : "bad"} icon={<Activity />} />
            <MetricCard label="Exposition selection" value={money(dashboard.active_selection?.open_exposure ?? 0)} icon={<Target />} />
            <MetricCard label="Trades selection" value={`${dashboard.active_selection?.open_trades ?? 0} ouverts / ${dashboard.active_selection?.closed_trades ?? 0} clos`} icon={<ChartCandlestick />} />
            <MetricCard label="Protection selection" value={guardLabel(dashboard.active_selection?.guard?.status)} tone={guardTone(dashboard.active_selection?.guard?.status)} icon={<ShieldCheck />} />
            <MetricCard label="Validation forward" value={dashboard.active_selection?.forward_validation?.status ?? "observation"} tone={dashboard.active_selection?.forward_validation?.status === "underperforming" ? "bad" : "neutral"} icon={<BrainCircuit />} />
            <MetricCard label="Maturite paper" value={`${dashboard.active_selection?.promotion_readiness?.passed_requirements ?? 0}/${dashboard.active_selection?.promotion_readiness?.total_requirements ?? 6}`} tone={dashboard.active_selection?.promotion_readiness?.ready ? "good" : "neutral"} icon={<ShieldCheck />} />
            <MetricCard label="Portefeuille 2 ans" value={`${dashboard.active_selection?.portfolio_validation?.holdout?.return_pct ?? 0}%`} tone={(dashboard.active_selection?.portfolio_validation?.holdout?.return_pct ?? 0) >= 0 ? "good" : "bad"} icon={<TrendingUp />} />
            <MetricCard label="DD portefeuille" value={`${dashboard.active_selection?.portfolio_validation?.holdout?.drawdown ?? 0}%`} tone={(dashboard.active_selection?.portfolio_validation?.holdout?.drawdown ?? 0) > 8 ? "bad" : "neutral"} icon={<TrendingDown />} />
            <MetricCard label="PF portefeuille" value={`${dashboard.active_selection?.portfolio_validation?.holdout?.profit_factor ?? 0}`} tone={(dashboard.active_selection?.portfolio_validation?.holdout?.profit_factor ?? 0) >= 1.05 ? "good" : "bad"} icon={<ShieldCheck />} />
            <MetricCard label="Confiance test" value={dashboard.active_selection?.portfolio_validation?.monte_carlo?.confidence ?? "insufficient"} tone={dashboard.active_selection?.portfolio_validation?.monte_carlo?.confidence === "weak" ? "bad" : "good"} icon={<BrainCircuit />} />
            <MetricCard label="Score bot" value={`${dashboard.active_selection?.portfolio_validation?.benchmark?.strategy_holdout_score ?? 0}`} tone={(dashboard.active_selection?.portfolio_validation?.benchmark?.strategy_holdout_score ?? 0) >= (dashboard.active_selection?.portfolio_validation?.benchmark?.benchmark_holdout_score ?? 0) ? "good" : "neutral"} icon={<Target />} />
            <MetricCard label="Score benchmark" value={`${dashboard.active_selection?.portfolio_validation?.benchmark?.benchmark_holdout_score ?? 0}`} icon={<ChartCandlestick />} />
          </div>
          <p className="backtest-note">
            {(dashboard.active_selection?.selected_pairs ?? []).map((pair) => `${pair.symbol.replace("USDT", "")} ${pair.strategy} max ${pair.max_allocation_pct}%`).join(" - ") || "En attente du prochain signal robuste"} - limite perte {dashboard.active_selection?.guard?.max_loss_pct ?? 1.5}% - pertes consecutives {dashboard.active_selection?.guard?.consecutive_losses ?? 0}/{dashboard.active_selection?.guard?.max_consecutive_losses ?? 3}
          </p>
          <p className="backtest-note">
            {dashboard.active_selection?.forward_validation?.reason ?? "Observation en cours."} Trades attendus: {dashboard.active_selection?.forward_validation?.expected_trades_so_far ?? 0} - seuil jugement: {dashboard.active_selection?.forward_validation?.min_trades_for_judgement ?? 5} - median historique: {dashboard.active_selection?.forward_validation?.expected_median_return_pct ?? 0}%.
          </p>
          <p className="backtest-note">
            Portefeuille valide: {dashboard.active_selection?.portfolio_validation?.eligible ? "oui" : "non"} - exposition max {dashboard.active_selection?.portfolio_validation?.max_portfolio_exposure_pct ?? 20}% - trades test 2 ans {dashboard.active_selection?.portfolio_validation?.holdout?.trades ?? 0}.
          </p>
          <p className="backtest-note">
            Test de resistance: {dashboard.active_selection?.portfolio_validation?.monte_carlo?.probability_positive_pct ?? 0}% de scenarios positifs - scenario faible {dashboard.active_selection?.portfolio_validation?.monte_carlo?.p05_return_pct ?? 0}% - DD prudent {dashboard.active_selection?.portfolio_validation?.monte_carlo?.p95_drawdown_pct ?? 0}%.
          </p>
          <p className="backtest-note">
            Bot vs benchmark 2 ans: bot {dashboard.active_selection?.portfolio_validation?.holdout?.return_pct ?? 0}% / DD {dashboard.active_selection?.portfolio_validation?.holdout?.drawdown ?? 0}% - benchmark {dashboard.active_selection?.portfolio_validation?.benchmark?.holdout?.return_pct ?? 0}% / DD {dashboard.active_selection?.portfolio_validation?.benchmark?.holdout?.drawdown ?? 0}%.
          </p>
          <p className="backtest-note">
            {dashboard.active_selection?.promotion_readiness?.reason ?? "Collecte de preuves en cours."} Aucune hausse automatique du risque.
          </p>
        </div>

        <div className="panel wide backtest-panel">
          <div className="panel-head">
            <h2>Meme Sprint</h2>
            <span>{dashboard.meme_sprint?.status === "paused_after_losses" ? "Pause securite" : "Paper arme"}</span>
          </div>
          <p className="backtest-note">
            Nouvelles cotations seulement. Plafond {dashboard.meme_sprint?.position_cap_pct ?? 0.25}% par position et {dashboard.meme_sprint?.exposure_cap_pct ?? 1}% au total.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Cotations suivies</th><th>Actifs</th><th>Ouverts</th><th>Clos</th><th>Exposition</th><th>PnL</th><th>Win</th><th>PF</th><th>Pertes consecutives</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>{dashboard.meme_sprint?.watched_new_listings ?? 0}</td>
                  <td>{dashboard.meme_sprint?.symbols?.map((symbol) => symbol.replace("USDT", "")).join(", ") || "En attente"}</td>
                  <td>{dashboard.meme_sprint?.open_trades ?? 0}</td>
                  <td>{dashboard.meme_sprint?.closed_trades ?? 0}</td>
                  <td>{money(dashboard.meme_sprint?.open_exposure ?? 0)}</td>
                  <td className={(dashboard.meme_sprint?.total_pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(dashboard.meme_sprint?.total_pnl ?? 0)}</td>
                  <td>{dashboard.meme_sprint?.win_rate ?? 0}%</td>
                  <td>{dashboard.meme_sprint?.profit_factor ?? 0}</td>
                  <td>{dashboard.meme_sprint?.consecutive_losses ?? 0}/{dashboard.meme_sprint?.max_consecutive_losses ?? 2}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel wide backtest-panel">
          <div className="panel-head">
            <h2>Apprentissage live</h2>
            <span>{dashboard.learning?.groups?.length ?? 0} profils</span>
          </div>
          <p className="backtest-note">
            {dashboard.learning?.rule ?? "Le bot collecte les resultats live pour ajuster les montants et eviter les configurations perdantes."}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Actif</th><th>Strategie</th><th>Style</th><th>Statut</th><th>Multiplicateur</th><th>Trades</th><th>PnL</th><th>Win</th><th>PF</th></tr>
              </thead>
              <tbody>
                {(dashboard.learning?.groups ?? []).map((row) => (
                  <tr key={`${row.symbol}-${row.strategy}-${row.execution_style}`}>
                    <td>{row.symbol.replace("USDT", "")}</td>
                    <td>{row.strategy}</td>
                    <td>{row.execution_style === "fast_rotation_scout" ? "scout" : "coeur"}</td>
                    <td>{row.status}</td>
                    <td className={row.multiplier > 1 ? "positive" : row.multiplier < 1 ? "negative" : ""}>{row.multiplier}</td>
                    <td>{row.closed_trades}</td>
                    <td className={row.total_pnl >= 0 ? "positive" : "negative"}>{money(row.total_pnl)}</td>
                    <td>{row.win_rate}%</td>
                    <td>{row.profit_factor}</td>
                  </tr>
                ))}
                {!(dashboard.learning?.groups ?? []).length && (
                  <tr><td colSpan={9}>Collecte de preuves en cours</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel wide backtest-panel">
          <div className="panel-head">
            <h2>Radar live fantome</h2>
            <span>{dashboard.shadow_research ? `${dashboard.shadow_research.open_trades} ouverts / ${dashboard.shadow_research.closed_trades} clos` : "Observation"}</span>
          </div>
          <p className="backtest-note">
            PnL fantome {money(dashboard.shadow_research?.total_pnl ?? 0)} - ces tests ne touchent pas au capital et servent a trouver les rotations les plus rentables.
          </p>
          <p className="backtest-note">
            {dashboard.shadow_research?.promotion_rule ?? "Collecte de preuves live sur les strategies candidates."}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Actif</th><th>Strategie</th><th>PnL live</th><th>Ouverts</th><th>Clos</th><th>Win</th><th>PF</th><th>DD</th><th>Hors ech.</th><th>Statut</th></tr>
              </thead>
              <tbody>
                {(dashboard.shadow_research?.candidates ?? []).map((row) => (
                  <tr key={`${row.symbol}-${row.strategy}`}>
                    <td>{row.symbol.replace("USDT", "")}</td>
                    <td>{row.strategy}</td>
                    <td className={row.total_pnl >= 0 ? "positive" : "negative"}>{money(row.total_pnl)}</td>
                    <td>{row.open_trades}</td>
                    <td>{row.closed_trades}</td>
                    <td>{row.win_rate}%</td>
                    <td className={row.profit_factor >= 1.2 ? "positive" : ""}>{row.profit_factor}</td>
                    <td>{row.drawdown}%</td>
                    <td>{row.holdout_return_pct ?? 0}%</td>
                    <td>{row.ready_for_review ? "a revoir" : "collecte"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel wide backtest-panel">
          <div className="panel-head">
            <h2>Radar profit</h2>
            <span>{walkForward ? `${walkForward.opportunity_radar?.length ?? 0} pistes surveillees` : "Chargement"}</span>
          </div>
          {walkForward && (
            <>
              <p className="backtest-note">
                Ces pistes restent en observation paper. Elles ne sont pas activees automatiquement afin d'eviter la concentration ou le surapprentissage.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Actif</th><th>Strategie</th><th>Score profit</th><th>Score expert</th><th>Hors echantillon</th><th>DD hors ech.</th><th>PF hors ech.</th><th>Statut</th></tr>
                  </thead>
                  <tbody>
                    {(walkForward.opportunity_radar ?? []).map((row) => (
                      <tr key={`${row.symbol}-${row.strategy}`}>
                        <td>{row.symbol.replace("USDT", "")}</td>
                        <td>{row.strategy}</td>
                        <td className={row.opportunity_score > 20 ? "positive" : ""}>{row.opportunity_score}</td>
                        <td className={(row.expert_score ?? 0) >= 45 ? "positive" : ""}>{row.expert_score ?? 0}</td>
                        <td className={row.holdout_return_pct >= 0 ? "positive" : "negative"}>{row.holdout_return_pct}%</td>
                        <td>{row.holdout_drawdown_pct}%</td>
                        <td>{row.holdout_profit_factor}</td>
                        <td>{row.expert_decision ?? row.notes.slice(0, 2).join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <div className="panel wide">
          <div className="panel-head">
            <h2>Courbe de performance</h2>
            <span>{dashboard.closed_trades} trades clôturés</span>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={dashboard.performance_curve}>
              <CartesianGrid strokeDasharray="3 3" stroke="#263340" />
              <XAxis dataKey="date" hide />
              <YAxis stroke="#8ba3b4" />
              <Tooltip />
              <Area type="monotone" dataKey="equity" stroke="#34d399" fill="#34d39933" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>Répartition PnL</h2>
            <span>Gains et pertes</span>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={distribution}>
              <CartesianGrid strokeDasharray="3 3" stroke="#263340" />
              <XAxis dataKey="strategy" hide />
              <YAxis stroke="#8ba3b4" />
              <Tooltip />
              <Bar dataKey="pnl" fill="#60a5fa" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>Stratégies</h2>
            <span>{strategies.filter((s) => s.enabled).length} actives</span>
          </div>
          <div className="scroll-list">
            {strategies.map((strategy) => (
              <ToggleRow
                key={strategy.name}
                title={strategy.name}
                detail={`${strategy.timeframe} · ${strategy.symbols.join(", ")}`}
                enabled={strategy.enabled}
                readOnly
              />
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>Exchanges</h2>
            <span>Spot uniquement</span>
          </div>
          {exchanges.map((exchange) => (
            <ToggleRow
              key={exchange.name}
              title={exchange.name.toUpperCase()}
              detail={exchange.paper_only ? "Paper trading verrouillé" : "Mode réel autorisable"}
              enabled={exchange.enabled}
              readOnly
            />
          ))}
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>Rapport IA</h2>
            <Bell size={18} />
          </div>
          <p className="report">{report}</p>
        </div>

        <div className="panel wide">
          <div className="panel-head">
            <h2>Catalyseurs marche</h2>
            <span>{catalysts ? `${catalysts.market_bias} - ${catalysts.confirmed_events}/${catalysts.unique_events} evenements confirmes` : "Chargement"}</span>
          </div>
          <div className="mini-metrics">
            {(catalysts?.symbols ?? []).slice(0, 4).map((row) => (
              <MetricCard
                key={row.symbol}
                label={row.symbol.replace("USDT", "")}
                value={`${row.decision_score}`}
                tone={row.decision_stance === "positive" ? "good" : row.decision_stance === "negative" ? "bad" : "neutral"}
                icon={<Radio />}
              />
            ))}
          </div>
          {(catalysts?.symbols ?? []).filter((row) => row.headlines.length > 0).slice(0, 3).map((row) => (
            <p className="backtest-note" key={row.symbol}>
              {row.symbol.replace("USDT", "")}: {row.decision_stance} - {row.headlines[0]?.title ?? "Aucune news forte"} {row.headlines[0]?.decision_ready ? `(confirmee, ${row.headlines[0]?.age_hours}h)` : "(observation)"}
            </p>
          ))}
        </div>

        <div className="panel wide backtest-panel">
          <div className="panel-head">
            <h2>Backtest historique</h2>
            <span>{historical ? `${historical.period.months} mois · ${historical.timeframe}` : "Chargement"}</span>
          </div>
          {historical && (
            <>
              <p className="backtest-note">
                Capital {money(historical.initial_capital)} · {historical.allocation_pct}% par position · frais {historical.fee_pct_per_order}% · glissement {historical.slippage_pct_per_order}%
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Rang</th><th>Actif</th><th>Stratégie</th><th>Rendement</th><th>Profit</th><th>Drawdown</th><th>Trades</th><th>Sharpe</th></tr>
                  </thead>
                  <tbody>
                    {historical.results.map((result, index) => (
                      <tr key={`${result.symbol}-${result.strategy}`}>
                        <td>{index + 1}</td>
                        <td>{result.symbol.replace("USDT", "")}</td>
                        <td>{result.strategy}</td>
                        <td className={result.return_pct >= 0 ? "positive" : "negative"}>{result.return_pct}%</td>
                        <td>{money(result.profit)}</td>
                        <td>{result.drawdown}%</td>
                        <td>{result.trades}</td>
                        <td>{result.sharpe_ratio}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <div className="panel wide backtest-panel">
          <div className="panel-head">
            <h2>Sélection robuste</h2>
            <span>{walkForward ? `${walkForward.period.years} fenêtres annuelles · ${walkForward.timeframe}` : "Chargement"}</span>
          </div>
          {walkForward && (
            <>
            <p className="backtest-note">
              Risque {walkForward.methodology.risk_profile.name} · stop {walkForward.methodology.risk_profile.settings.stop_loss_pct}% · objectif {walkForward.methodology.risk_profile.settings.take_profit_pct}% · trailing {walkForward.methodology.risk_profile.settings.trailing_stop_pct}%
            </p>
            <p className="backtest-note">
              Portefeuille complet hors echantillon: {walkForward.portfolio_validation?.holdout?.return_pct ?? 0}% - drawdown {walkForward.portfolio_validation?.holdout?.drawdown ?? 0}% - profit factor {walkForward.portfolio_validation?.holdout?.profit_factor ?? 0} - {walkForward.portfolio_validation?.holdout?.trades ?? 0} trades.
            </p>
            <p className="backtest-note">
              Monte Carlo: confiance {walkForward.portfolio_validation?.monte_carlo?.confidence ?? "insufficient"} - {walkForward.portfolio_validation?.monte_carlo?.probability_positive_pct ?? 0}% positifs - scenario faible {walkForward.portfolio_validation?.monte_carlo?.p05_return_pct ?? 0}%.
            </p>
            <p className="backtest-note">
              Benchmark passif: {walkForward.portfolio_validation?.benchmark?.holdout?.return_pct ?? 0}% - drawdown {walkForward.portfolio_validation?.benchmark?.holdout?.drawdown ?? 0}% - score {walkForward.portfolio_validation?.benchmark?.benchmark_holdout_score ?? 0}.
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Actif</th><th>Stratégie</th><th>Score</th><th>Années positives</th><th>Hors échantillon</th><th>PF hors éch.</th><th>DD hors éch.</th><th>Trades hors éch.</th><th>Rendement médian</th><th>Pire année</th></tr>
                </thead>
                <tbody>
                  {walkForward.selected.map((result) => (
                    <tr key={`${result.symbol}-${result.strategy}`}>
                      <td>{result.symbol.replace("USDT", "")}</td>
                      <td>{result.strategy}</td>
                      <td>{result.robustness_score}</td>
                      <td>{result.positive_years}/{walkForward.period.years}</td>
                      <td className={result.holdout_metrics.return_pct >= 0 ? "positive" : "negative"}>{result.holdout_metrics.return_pct}%</td>
                      <td className={result.holdout_metrics.profit_factor >= 1.05 ? "positive" : "negative"}>{result.holdout_metrics.profit_factor}</td>
                      <td>{result.holdout_metrics.drawdown}%</td>
                      <td>{result.holdout_metrics.trades}</td>
                      <td className="positive">{result.median_year_return_pct}%</td>
                      <td className={result.worst_year_return_pct >= 0 ? "positive" : "negative"}>{result.worst_year_return_pct}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            </>
          )}
        </div>

        <div className="panel wide backtest-panel">
          <div className="panel-head">
            <h2>Laboratoire rotations</h2>
            <span>{walkForward ? `${walkForward.portfolio_variant_lab?.length ?? 0} variantes testees` : "Chargement"}</span>
          </div>
          {walkForward && (
            <>
              <p className="backtest-note">
                Remplacements testes en paper sur portefeuille complet. Rien n'est applique automatiquement.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Actif</th><th>Remplacer</th><th>Par</th><th>Delta rendement</th><th>Delta DD</th><th>PF</th><th>Confiance</th><th>Decision</th></tr>
                  </thead>
                  <tbody>
                    {(walkForward.portfolio_variant_lab ?? []).map((row) => (
                      <tr key={`${row.replace_symbol}-${row.from_strategy}-${row.to_strategy}`}>
                        <td>{row.replace_symbol.replace("USDT", "")}</td>
                        <td>{row.from_strategy}</td>
                        <td>{row.to_strategy}</td>
                        <td className={row.return_delta_pct >= 0 ? "positive" : "negative"}>{row.return_delta_pct}%</td>
                        <td className={row.drawdown_delta_pct <= 0 ? "positive" : "negative"}>{row.drawdown_delta_pct}%</td>
                        <td>{row.holdout_profit_factor}</td>
                        <td>{row.monte_carlo_confidence}</td>
                        <td>{row.decision}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <div className="panel wide">
          <div className="panel-head">
            <h2>Journaux</h2>
            <span>Ordres, erreurs, système</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Source</th>
                  <th>Message</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {logs.system.map((line, index) => (
                  <tr key={`${line.created_at}-${index}`}>
                    <td>{line.level}</td>
                    <td>{line.source}</td>
                    <td>{line.message}</td>
                    <td>{new Date(line.created_at).toLocaleString("fr-FR")}</td>
                  </tr>
                ))}
                {logs.trades.map((trade) => (
                  <tr key={trade.id}>
                    <td>{trade.status}</td>
                    <td>{trade.exchange}</td>
                    <td>{trade.strategy} · {trade.symbol} · {money(trade.pnl)}</td>
                    <td>{new Date(trade.opened_at).toLocaleString("fr-FR")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </main>
  );
}
