import type { AgentContext, Dashboard, Exchange, HistoricalBacktest, Logs, MarketCatalysts, Strategy, WalkForwardReport } from "../types/domain";

const explicitApiBase = import.meta.env.VITE_API_BASE_URL;
const isLocalApp = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const API_BASE = explicitApiBase ?? "/api";

function offlineBackendError() {
  return new Error(
    "Le site est en ligne, mais le backend de trading n'est pas encore publie sur Internet. Le dashboard live fonctionne encore sur ce PC via http://localhost:5173."
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw offlineBackendError();
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? "Erreur API");
  }
  return response.json();
}

export const api = {
  dashboard: () => request<Dashboard>("/dashboard"),
  strategies: () => request<Strategy[]>("/strategies"),
  exchanges: () => request<Exchange[]>("/exchanges"),
  logs: () => request<Logs>("/logs"),
  historicalBacktest: () => request<HistoricalBacktest>("/backtests/historical-report"),
  walkForwardReport: () => request<WalkForwardReport>("/research/walk-forward"),
  marketCatalysts: () => request<MarketCatalysts>("/market/catalysts"),
  agentContext: (symbol = "BTCUSDT") => request<AgentContext>(`/agent-context?symbol=${encodeURIComponent(symbol)}`),
  status: () => request<Record<string, unknown>>("/bot/status"),
  startBot: (symbols: string[]) => request<Record<string, unknown>>("/bot/start", { method: "POST", body: JSON.stringify({ mode: "paper", exchange: "binance", symbols }) }),
  stopBot: () => request<Record<string, unknown>>("/bot/stop", { method: "POST" }),
  toggleStrategy: (name: string, enabled: boolean) => request(`/strategies/${encodeURIComponent(name)}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  toggleExchange: (name: string, enabled: boolean) => request(`/exchanges/${encodeURIComponent(name)}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  aiReport: () => request<Record<string, unknown>>("/ai/report", { method: "POST" })
};
