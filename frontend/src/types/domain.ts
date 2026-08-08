export type Dashboard = {
  capital: number;
  equity: number;
  open_unrealized_pnl: number;
  open_exposure: number;
  exposure_limit: number;
  exposure_remaining: number;
  exposure_utilization_pct: number;
  profit_daily: number;
  profit_weekly: number;
  profit_monthly: number;
  profit_annual: number;
  open_trades: number;
  closed_trades: number;
  win_rate: number;
  profit_factor: number;
  drawdown: number;
  bot_health?: {
    running: boolean;
    runtime_status: string;
    status_label: string;
    status_message: string;
    guard_status: string;
    last_action: string;
    last_action_at?: string | null;
    last_tick_at?: string | null;
    next_tick_at?: string | null;
    last_scheduler_check_at?: string | null;
    last_stop_reason?: string | null;
    last_stop_at?: string | null;
    scheduler_status: string;
    open_trades: number;
  };
  auto_selection?: {
    generated_at: string;
    method: string;
    max_open_positions: number;
    market_score?: {
      status: string;
      score: number;
      risk_multiplier: number;
      average_momentum_pct: number;
      positive_breadth_pct: number;
      eligible_count: number;
      rule: string;
    };
    selected_symbols: string[];
    selected: Array<{
      symbol: string;
      strategy: string;
      score: number;
      change_pct_24h: number;
      quote_volume_24h: number;
      trades_24h: number;
      learning_status: string;
      learning_multiplier: number;
      decision: string;
    }>;
    watchlist: Array<{
      symbol: string;
      strategy: string;
      score: number;
      change_pct_24h: number;
      quote_volume_24h: number;
      trades_24h: number;
      learning_status: string;
      learning_multiplier: number;
      decision: string;
    }>;
  };
  performance_curve: Array<{ date: string; equity: number }>;
  gain_distribution: Array<{ strategy: string; pnl: number }>;
  loss_distribution: Array<{ strategy: string; pnl: number }>;
  active_selection: {
    generated_at?: string;
    timeframe?: string;
    risk_profile?: string;
    selected_pairs: Array<{
      symbol: string;
      strategy: string;
      holdout_profit_factor: number;
      allocation_tier: string;
      max_allocation_pct: number;
    }>;
    portfolio_validation?: {
      selected_count: number;
      max_portfolio_exposure_pct: number;
      eligible: boolean;
      full: {
        return_pct?: number;
        drawdown?: number;
        trades?: number;
        profit_factor?: number;
        sharpe_ratio?: number;
      };
      holdout: {
        return_pct?: number;
        drawdown?: number;
        trades?: number;
        profit_factor?: number;
        sharpe_ratio?: number;
      };
      monte_carlo?: {
        simulations: number;
        probability_positive_pct: number;
        median_return_pct: number;
        p05_return_pct: number;
        p95_return_pct: number;
        p95_drawdown_pct: number;
        confidence: string;
      };
      benchmark?: {
        full: {
          allocation_pct_per_asset?: number;
          return_pct?: number;
          drawdown?: number;
          final_capital?: number;
        };
        holdout: {
          allocation_pct_per_asset?: number;
          return_pct?: number;
          drawdown?: number;
          final_capital?: number;
        };
        strategy_holdout_score: number;
        benchmark_holdout_score: number;
        excess_holdout_return_pct: number;
      };
    };
    closed_pnl: number;
    open_pnl: number;
    total_pnl: number;
    pnl_pct: number;
    open_exposure: number;
    open_trades: number;
    closed_trades: number;
    win_rate: number;
    profit_factor: number;
    drawdown: number;
    guard: {
      status: string;
      breached: boolean;
      max_loss_pct: number;
      consecutive_losses: number;
      max_consecutive_losses: number;
      mature_trades?: number;
      profit_factor?: number | null;
      min_profit_factor?: number;
    };
    forward_validation: {
      status: string;
      reason: string;
      days_live: number;
      expected_trades_so_far: number;
      min_trades_for_judgement: number;
      closed_trades: number;
      open_trades: number;
      live_pnl: number;
      live_return_pct: number;
      expected_median_return_pct: number;
      expected_holdout_return_pct: number;
      expected_max_drawdown_pct: number;
    };
    promotion_readiness: {
      status: string;
      ready: boolean;
      passed_requirements: number;
      total_requirements: number;
      risk_increase_automatic: boolean;
      reason: string;
      requirements: Record<string, { target: number | boolean; current: number | boolean; passed: boolean }>;
    };
  };
  shadow_research?: {
    status: string;
    open_trades: number;
    closed_trades: number;
    closed_pnl: number;
    open_pnl: number;
    total_pnl: number;
    promotion_rule: string;
    candidates: Array<{
      symbol: string;
      strategy: string;
      source: string;
      closed_trades: number;
      open_trades: number;
      closed_pnl: number;
      open_pnl: number;
      total_pnl: number;
      win_rate: number;
      profit_factor: number;
      drawdown: number;
      holdout_return_pct?: number;
      holdout_profit_factor?: number;
      opportunity_score?: number;
      ready_for_review: boolean;
    }>;
  };
  learning?: {
    status: string;
    rule: string;
    groups: Array<{
      symbol: string;
      strategy: string;
      execution_style: string;
      status: string;
      multiplier: number;
      closed_trades: number;
      total_pnl: number;
      win_rate: number;
      profit_factor: number;
    }>;
  };
  meme_sprint?: {
    mode: string;
    status: string;
    watched_new_listings: number;
    symbols: string[];
    open_trades: number;
    closed_trades: number;
    open_exposure: number;
    closed_pnl: number;
    open_pnl: number;
    total_pnl: number;
    win_rate: number;
    profit_factor: number;
    consecutive_losses: number;
    max_consecutive_losses: number;
    position_cap_pct: number;
    exposure_cap_pct: number;
  };
};

export type Strategy = {
  name: string;
  enabled: boolean;
  symbols: string[];
  timeframe: string;
  parameters: Record<string, unknown>;
};

export type Exchange = {
  name: string;
  enabled: boolean;
  paper_only: boolean;
};

export type Logs = {
  trades: Array<{ id: string; exchange: string; strategy: string; symbol: string; status: string; entry_price: number; pnl: number; opened_at: string }>;
  system: Array<{ level: string; source: string; message: string; created_at: string }>;
};

export type HistoricalBacktest = {
  period: { start: string; end: string; months: number };
  timeframe: string;
  initial_capital: number;
  fee_pct_per_order: number;
  slippage_pct_per_order: number;
  allocation_pct: number;
  results: Array<{
    symbol: string;
    strategy: string;
    profit: number;
    return_pct: number;
    drawdown: number;
    trades: number;
    sharpe_ratio: number;
    win_rate: number;
    profit_factor: number;
  }>;
};

export type WalkForwardReport = {
  period: { start: string; end: string; years: number };
  timeframe: string;
  methodology: {
    risk_profile: {
      name: string;
      settings: {
        stop_loss_pct: number;
        take_profit_pct: number;
        trailing_stop_pct: number;
        break_even_pct: number;
      };
    };
  };
  portfolio_validation?: {
    selected_count: number;
    max_portfolio_exposure_pct: number;
    eligible: boolean;
    full: {
      return_pct?: number;
      drawdown?: number;
      trades?: number;
      profit_factor?: number;
      sharpe_ratio?: number;
    };
    holdout: {
      return_pct?: number;
      drawdown?: number;
      trades?: number;
      profit_factor?: number;
      sharpe_ratio?: number;
    };
    monte_carlo?: {
      simulations: number;
      probability_positive_pct: number;
      median_return_pct: number;
      p05_return_pct: number;
      p95_return_pct: number;
      p95_drawdown_pct: number;
      confidence: string;
    };
    benchmark?: {
      full: {
        allocation_pct_per_asset?: number;
        return_pct?: number;
        drawdown?: number;
        final_capital?: number;
      };
      holdout: {
        allocation_pct_per_asset?: number;
        return_pct?: number;
        drawdown?: number;
        final_capital?: number;
      };
      strategy_holdout_score: number;
      benchmark_holdout_score: number;
      excess_holdout_return_pct: number;
    };
  };
  opportunity_radar?: Array<{
    symbol: string;
    strategy: string;
    eligible: boolean;
    opportunity_score: number;
    return_pct: number;
    drawdown: number;
    profit_factor: number;
    holdout_return_pct: number;
    holdout_drawdown_pct: number;
    holdout_profit_factor: number;
    notes: string[];
    activation: string;
    expert_score?: number;
    expert_decision?: string;
  }>;
  portfolio_variant_lab?: Array<{
    replace_symbol: string;
    from_strategy: string;
    to_strategy: string;
    eligible: boolean;
    holdout_return_pct: number;
    holdout_drawdown_pct: number;
    holdout_profit_factor: number;
    monte_carlo_confidence: string;
    return_delta_pct: number;
    drawdown_delta_pct: number;
    decision: string;
  }>;
  selected: Array<{
    symbol: string;
    strategy: string;
    robustness_score: number;
    expert_score?: number;
    expert_decision?: string;
    positive_years: number;
    median_year_return_pct: number;
    worst_year_return_pct: number;
    holdout_return_pct: number;
    holdout_positive_years: number;
    holdout_metrics: {
      return_pct: number;
      drawdown: number;
      trades: number;
      profit_factor: number;
      sharpe_ratio: number;
    };
    return_pct: number;
    drawdown: number;
    trades: number;
    sharpe_ratio: number;
  }>;
};

export type MarketCatalysts = {
  generated_at: string;
  sources: string[];
  items_scanned: number;
  actionable_items: number;
  unique_events: number;
  confirmed_events: number;
  max_age_hours: number;
  market_bias: string;
  symbols: Array<{
    symbol: string;
    score: number;
    positive: number;
    negative: number;
    stance: string;
    decision_score: number;
    decision_stance: string;
    confirmed_events: number;
    headlines: Array<{
      title?: string;
      source?: string;
      url?: string;
      published_at?: string | null;
      age_hours?: number | null;
      actionable?: boolean;
      score?: number;
      reliability?: number;
      confirmations?: number;
      decision_ready?: boolean;
    }>;
  }>;
};

export type AgentContext = {
  generated_at: string;
  symbol: string;
  strategy: string;
  demo_mode: boolean;
  memory_saved: boolean;
  memory_id: string;
  disclaimer: string;
  datahub_record: {
    saved: boolean;
    mode: string;
    urn?: string;
    recorded_at?: string;
    error?: string;
  };
  aws_ready: {
    service: string;
    role: string;
    local_demo_fallback: boolean;
  };
  aws_report: {
    saved: boolean;
    mode: string;
    path?: string;
    bucket?: string;
    key?: string;
    error?: string;
  };
  recommendation: {
    decision: string;
    confidence: string;
    risk_level: string;
    strategy: string;
    reasoning: string;
    cited_memories: string[];
    paper_only: boolean;
  };
  context_used: {
    datahub: {
      mode: string;
      source: string;
      assets: Array<Record<string, unknown>>;
      market_sources: Array<Record<string, unknown>>;
      indicators: Array<Record<string, unknown>>;
      strategies: Array<Record<string, unknown>>;
      backtests: Array<Record<string, unknown>>;
      risk_metrics: Array<Record<string, unknown>>;
      prior_decisions: Array<Record<string, unknown>>;
    };
    cockroach_memory: Array<{
      id: string;
      symbol: string;
      strategy: string;
      decision: string;
      risk_level: string;
      reasoning: string;
      created_at: string;
      similarity?: number | null;
      outcome?: Record<string, unknown> | null;
    }>;
    risk: Record<string, unknown>;
    backtests: Array<Record<string, unknown>>;
    recent_trade_sample: number;
  };
};
