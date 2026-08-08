from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import time

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.exchanges.base import ExchangeClient
from app.exchanges.base import MarketOrder
from app.exchanges.registry import get_exchange
from app.market.news import NewsCatalystService
from app.models.domain import BotState, ListingState, MarketCycleState, OrderLog, RiskConfig, ShadowTrade, StrategyConfig, SystemLog, Trade, TradeSide, TradeStatus
from app.risk.manager import RiskManager
from app.risk.quality import quality_allocation
from app.backtesting.macro_events import macro_event_snapshot
from app.backtesting.market_memory import historical_market_memory
from app.backtesting.market_psychology import investor_behavior_snapshot
from app.ai.supervised_predictor import supervised_market_prediction
from app.strategies.base import Signal
from app.strategies.catalog import STRATEGIES
from app.strategies.chart_patterns import curve_snapshot
from app.strategies.indicators import atr, ema
from app.strategies.market_regime import regime_snapshot


class TradingEngine:
    ACTIVE_SELECTION_MAX_LOSS_PCT = 1.5
    ACTIVE_SELECTION_MAX_CONSECUTIVE_LOSSES = 3
    ACTIVE_SELECTION_MATURE_TRADES = 20
    ACTIVE_SELECTION_MIN_PROFIT_FACTOR = 1.15
    RECOVERY_MIN_HOLDOUT_PROFIT_FACTOR = 1.55
    RECOVERY_MIN_ENTRY_SCORE = 0.82
    PAPER_FEE_RATE = 0.001
    PAPER_SLIPPAGE_RATE = 0.0005
    SCOUT_MAX_PAIRS = 2
    SCOUT_MIN_OPPORTUNITY_SCORE = 85.0
    SCOUT_MIN_HOLDOUT_RETURN_PCT = 8.0
    SCOUT_MIN_HOLDOUT_PROFIT_FACTOR = 1.45
    SCOUT_ALLOCATION_MULTIPLIER = 0.25
    SCOUT_ENTRY_TIMEFRAME = "1h"
    MEME_SPRINT_TIMEFRAME = "5m"
    MEME_SPRINT_ACTIVE_DAYS = 7
    MEME_SPRINT_POSITION_PCT = 0.25
    MEME_SPRINT_EXPOSURE_PCT = 1.0
    PARTIAL_PROFIT_FRACTION = 0.5
    PARTIAL_PROFIT_CORE_TRIGGER_PCT = 2.0
    PARTIAL_PROFIT_SCOUT_TRIGGER_PCT = 1.2
    MAX_HOLD_HOURS_BY_STRATEGY = {
        "Meme Momentum": 12,
        "Scalping": 16,
        "Volume Spike": 72,
        "Breakout": 96,
        "EMA Cross": 120,
        "Trend Following": 168,
        "Swing Trading": 168,
    }

    async def _state(self, db: AsyncSession, for_update: bool = False) -> BotState:
        query = select(BotState).where(BotState.id == "default")
        if for_update:
            query = query.with_for_update()
        state = await db.scalar(query)
        if state is None:
            state = BotState(id="default")
            db.add(state)
            await db.flush()
        return state

    @staticmethod
    def _status(state: BotState) -> dict:
        running = bool(state.running)
        runtime_status = "active" if running else "stopped"
        status_label = "Paper actif" if running and state.mode == "paper" else ("Bot actif" if running else "Paper arrete")
        status_message = (
            "Le moteur local tourne et applique les protections de risque."
            if running
            else "Le moteur local attend un demarrage manuel."
        )
        next_tick_at = state.last_tick_at + timedelta(minutes=1) if state.last_tick_at and running else None
        return {
            "running": running,
            "mode": state.mode,
            "exchange": state.exchange,
            "symbols": state.symbols,
            "paper_execution_isolated": state.mode == "paper",
            "runtime_status": runtime_status,
            "status_label": status_label,
            "status_message": status_message,
            "guard_status": "active" if running else "stopped",
            "last_action": "market_watch" if running else "manual_stop_or_not_started",
            "last_action_at": state.updated_at.isoformat() if state.updated_at else None,
            "last_tick_at": state.last_tick_at.isoformat() if state.last_tick_at else None,
            "next_tick_at": next_tick_at.isoformat() if next_tick_at else None,
            "scheduler_status": "armed" if running else "waiting",
        }

    async def start(self, db: AsyncSession, exchange_name: str, symbols: list[str], mode: str = "paper") -> dict:
        if mode not in {"paper", "real"}:
            raise ValueError(f"Unsupported execution mode: {mode}")
        if mode == "real" and not get_settings().real_trading_enabled:
            raise PermissionError("Real trading is disabled")
        state = await self._state(db, for_update=True)
        state.running = True
        state.mode = mode
        state.exchange = exchange_name
        state.symbols = symbols
        await db.commit()
        await self.tick(db)
        return await self.status(db)

    async def stop(self, db: AsyncSession) -> dict:
        state = await self._state(db, for_update=True)
        state.running = False
        await db.commit()
        return self._status(state)

    async def status(self, db: AsyncSession) -> dict:
        return self._status(await self._state(db))

    async def tick(self, db: AsyncSession) -> dict:
        state = await self._state(db, for_update=True)
        if not state.running:
            await db.commit()
            return {**self._status(state), "skipped": "bot stopped"}
        guard = await self._tick(db, state.exchange, state.symbols, state.mode)
        runtime_status = "active"
        status_label = "Paper actif" if state.mode == "paper" else "Bot actif"
        status_message = "Le moteur local tourne et applique les protections de risque."
        if guard.get("breached") and guard.get("engine_action") == "stop":
            state.running = False
            runtime_status = "stopped"
            status_label = "Paper arrete"
            status_message = "Le moteur local a ete arrete par la protection risque."
            db.add(
                SystemLog(
                    level="warning",
                    source="risk",
                    message="Paper bot paused by active selection guard",
                    context=guard,
                )
            )
        elif guard.get("breached"):
            runtime_status = "observe_only"
            status_label = "Observation protegee"
            status_message = "Le moteur local tourne mais limite les nouvelles entrees pour proteger le capital."
            db.add(
                SystemLog(
                    level="warning",
                    source="risk",
                    message="Paper bot kept alive in guarded recovery mode",
                    context=guard,
                )
            )
        state.last_tick_at = datetime.utcnow()
        await db.commit()
        return {
            **self._status(state),
            "runtime_status": runtime_status,
            "status_label": status_label,
            "status_message": status_message,
            "guard_status": guard.get("engine_action", "active"),
            "cycle_completed": True,
            "selection_guard": guard,
        }

    async def _tick(self, db: AsyncSession, exchange_name: str, symbols: list[str], mode: str = "paper") -> dict:
        exchange = get_exchange(exchange_name)
        strategies_result = await db.execute(select(StrategyConfig).where(StrategyConfig.enabled.is_(True)))
        configs = list(strategies_result.scalars().all())
        if not configs:
            configs = [StrategyConfig(name=name, enabled=True, symbols=symbols, timeframe="4h", parameters={}) for name in STRATEGIES]
        if mode == "paper":
            configs.extend(await self._new_listing_configs(db, exchange_name, exchange))
        research_context = self._research_context()
        configs.extend(self._scout_strategy_configs(research_context, configs, symbols))
        symbols = self._active_symbols(symbols, configs)
        allowed_pairs = {
            (config.name, symbol)
            for config in configs
            for symbol in (config.symbols or symbols)
        }
        news_context = await self._news_context(symbols)
        shadow_candidates = self._shadow_candidates(research_context)
        shadow_symbols = [row["symbol"] for row in shadow_candidates]
        risk_result = await db.execute(select(RiskConfig).where(RiskConfig.name == "default"))
        risk_config = risk_result.scalar_one_or_none() or RiskConfig()
        risk = RiskManager(risk_config.__dict__)
        trades_result = await db.execute(select(Trade).order_by(Trade.opened_at))
        all_trades = list(trades_result.scalars().all())
        open_trades = [t for t in all_trades if t.status.value == "open"]
        closed_trades = [t for t in all_trades if t.status.value == "closed"]
        shadow_result = await db.execute(select(ShadowTrade).order_by(ShadowTrade.opened_at))
        shadow_trades = list(shadow_result.scalars().all())
        open_shadow_trades = [t for t in shadow_trades if t.status.value == "open"]
        self._track_open_selection(open_trades, allowed_pairs, research_context.get("generated_at"))
        learning_profile = self._learning_profile(closed_trades)
        capital = 10_000 + sum(t.pnl for t in closed_trades)

        market_data: dict[str, tuple[pd.DataFrame, float, int]] = {}
        market_symbols = set(symbols + shadow_symbols + [trade.symbol for trade in open_trades] + [trade.symbol for trade in open_shadow_trades])
        for symbol in market_symbols:
            try:
                candles = await exchange.candles(symbol, "4h", 250)
                market_data[symbol] = self._prepare_market_data(candles)
            except Exception as exc:
                db.add(
                    SystemLog(
                        level="error",
                        source="market_data",
                        message="Market data rejected; symbol skipped",
                        context={"symbol": symbol, "error_type": type(exc).__name__, "error": str(exc)[:250]},
                    )
                )

        short_market_data: dict[str, tuple[pd.DataFrame, float, int, str]] = {}
        scout_symbols = {
            symbol: (config.parameters or {}).get("entry_timeframe", self.SCOUT_ENTRY_TIMEFRAME)
            for config in configs if (config.parameters or {}).get("scout")
            for symbol in (config.symbols or [])
        }
        for symbol, entry_timeframe in scout_symbols.items():
            try:
                candles = await exchange.candles(symbol, entry_timeframe, 250)
                short_market_data[symbol] = (*self._prepare_market_data(candles, maximum_age_ms=3 * 60 * 60 * 1000), entry_timeframe)
            except Exception as exc:
                db.add(
                    SystemLog(
                        level="error",
                        source="market_data",
                        message="Short timeframe data rejected; scout symbol skipped",
                        context={"symbol": symbol, "timeframe": entry_timeframe, "error_type": type(exc).__name__, "error": str(exc)[:250]},
                    )
                )

        for symbol, (short_df, short_price, closed_candle_ms, _) in short_market_data.items():
            market_data.setdefault(symbol, (short_df, short_price, closed_candle_ms))

        cycle_result = await db.execute(
            select(MarketCycleState).where(
                MarketCycleState.exchange == exchange_name,
                MarketCycleState.symbol.in_(market_symbols),
                MarketCycleState.timeframe == "4h",
            )
        )
        cycles = {row.symbol: row for row in cycle_result.scalars().all()}
        new_candle_symbols: set[str] = set()
        for symbol in market_symbols:
            if symbol not in market_data:
                continue
            closed_candle_ms = market_data[symbol][2]
            cycle = cycles.get(symbol)
            if cycle is None:
                cycle = MarketCycleState(
                    id=f"{exchange_name}:{symbol}:4h",
                    exchange=exchange_name,
                    symbol=symbol,
                    timeframe="4h",
                    last_closed_candle_ms=closed_candle_ms,
                )
                db.add(cycle)
                new_candle_symbols.add(symbol)
            elif self._is_new_candle(cycle.last_closed_candle_ms, closed_candle_ms):
                cycle.last_closed_candle_ms = closed_candle_ms
                new_candle_symbols.add(symbol)

        self._tick_shadow_candidates(
            db,
            exchange_name,
            shadow_candidates,
            open_shadow_trades,
            market_data,
            new_candle_symbols,
            risk.config,
            research_context,
            news_context,
        )

        seen_symbols: set[str] = set()
        closed_this_tick: set[str] = set()
        for trade in list(open_trades):
            if trade.symbol not in market_data:
                continue
            df, price, _ = market_data[trade.symbol]
            estimated_exit = self._paper_exit_fill(price, trade.quantity)
            unrealized_pnl = self._paper_pnl(trade, estimated_exit["price"], estimated_exit["fee"])
            trade.metadata_json = {
                **(trade.metadata_json or {}),
                "last_price": price,
                "estimated_exit_price": estimated_exit["price"],
                "estimated_exit_fee": estimated_exit["fee"],
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": round((price / trade.entry_price - 1) * 100, 4) if trade.entry_price else 0.0,
            }
            partial = self._maybe_take_partial_profit(trade, price, mode)
            if partial:
                order = MarketOrder(symbol=trade.symbol, side="sell", quantity=partial["quantity"], price=partial["price"])
                response = await self._execute_order(exchange, order, mode)
                db.add(OrderLog(exchange=exchange_name, symbol=trade.symbol, payload=order.__dict__, response=response))
                db.add(
                    SystemLog(
                        level="info",
                        source="trading",
                        message="Partial profit taken",
                        context={"trade_id": trade.id, "symbol": trade.symbol, "realized_pnl": partial["realized_pnl"], "remaining_quantity": trade.quantity},
                    )
                )
            if mode == "paper" and allowed_pairs and not self._is_allowed_pair(trade, allowed_pairs):
                reason = "selection_rotation"
            elif trade.symbol in seen_symbols:
                reason = "duplicate_cleanup"
            else:
                seen_symbols.add(trade.symbol)
                catalyst = self._symbol_catalyst(news_context, trade.symbol)
                reason = self._adaptive_exit_reason(trade, price, df, risk.config, catalyst)
            if reason is None:
                continue
            exit_fill = self._paper_exit_fill(price, trade.quantity)
            order = MarketOrder(symbol=trade.symbol, side="sell", quantity=trade.quantity, price=exit_fill["price"])
            response = await self._execute_order(exchange, order, mode)
            trade.status = TradeStatus.closed
            trade.exit_price = exit_fill["price"]
            trade.pnl = self._paper_pnl(trade, exit_fill["price"], exit_fill["fee"])
            trade.closed_at = datetime.utcnow()
            trade.metadata_json = {
                **(trade.metadata_json or {}),
                "exit_reason": reason,
                "exit_fee": exit_fill["fee"],
                "execution_model": self._paper_execution_model(),
            }
            db.add(OrderLog(exchange=exchange_name, symbol=trade.symbol, payload=order.__dict__, response=response))
            db.add(SystemLog(level="info", source="trading", message=f"Position closed: {reason}", context={"trade_id": trade.id, "symbol": trade.symbol, "pnl": trade.pnl}))
            open_trades.remove(trade)
            closed_trades.append(trade)
            closed_this_tick.add(trade.symbol)

        guard = self._selection_guard(all_trades, research_context)
        recovery_mode = mode == "paper" and guard.get("engine_action") == "recover"
        recent_loss_keys = self._recent_loss_keys(closed_trades) if recovery_mode else set()
        if mode == "paper" and guard["breached"] and not recovery_mode:
            await db.flush()
            return guard

        for symbol in self._news_ranked_symbols(symbols, news_context):
            if symbol in closed_this_tick or symbol not in market_data:
                continue
            df, price, _ = market_data[symbol]
            if any(trade.symbol == symbol for trade in open_trades):
                continue
            for config in configs:
                scout_mode = bool((config.parameters or {}).get("scout"))
                meme_sprint = bool((config.parameters or {}).get("meme_sprint"))
                if symbol not in new_candle_symbols and not scout_mode:
                    continue
                if config.symbols and symbol not in config.symbols:
                    continue
                entry_df, entry_price, entry_timeframe = self._entry_market_view(symbol, df, price, scout_mode, short_market_data)
                catalyst = self._symbol_catalyst(news_context, symbol)
                learning = self._learning_for_pair(learning_profile, symbol, config.name, "fast_rotation_scout" if scout_mode else "core_selection")
                if not learning["allowed"]:
                    db.add(
                        SystemLog(
                            level="info",
                            source="learning",
                            message="Entry skipped by live learning profile",
                            context={"symbol": symbol, "strategy": config.name, "learning": learning},
                        )
                    )
                    continue
                signal = STRATEGIES[config.name].generate(entry_df, config.parameters)
                if signal.action != "buy" and scout_mode:
                    signal = self._scout_fallback_signal(entry_df, catalyst)
                if signal.action != "buy":
                    continue
                quality = self._entry_quality(signal, entry_df, catalyst)
                if not quality["allowed"]:
                    db.add(
                        SystemLog(
                            level="info",
                            source="entry_quality",
                            message="Entry skipped by quality filter",
                            context={"symbol": symbol, "strategy": config.name, "quality": quality},
                        )
                    )
                    continue
                try:
                    market_quality = await exchange.market_quality(symbol)
                except Exception as exc:
                    market_quality = {"available": False, "error": str(exc)[:120]}
                if meme_sprint:
                    sprint_gate = self._meme_sprint_market_gate(entry_df, market_quality)
                    if not sprint_gate["allowed"]:
                        db.add(SystemLog(level="info", source="meme_sprint", message="New listing rejected by liquidity or pump filter", context={"symbol": symbol, **sprint_gate}))
                        continue
                execution_quality = self._execution_quality_gate(entry_df, market_quality, scout_mode=scout_mode, meme_sprint=meme_sprint)
                if not execution_quality["allowed"]:
                    db.add(
                        SystemLog(
                            level="info",
                            source="execution_quality",
                            message="Entry blocked by execution quality filter",
                            context={"symbol": symbol, "strategy": config.name, "execution_quality": execution_quality},
                        )
                    )
                    continue
                if catalyst.get("decision_stance", "neutral") == "negative":
                    db.add(
                        SystemLog(
                            level="warning",
                            source="news_catalyst",
                            message="Entry blocked by negative market catalyst",
                            context={"symbol": symbol, "strategy": config.name, "catalyst": catalyst},
                        )
                    )
                    continue
                selected_research = self._research_for_pair(research_context, symbol, config.name)
                if recovery_mode:
                    recovery = self._recovery_entry_decision(
                        symbol,
                        config.name,
                        "new_listing_meme_sprint" if meme_sprint else ("fast_rotation_scout" if scout_mode else "core_selection"),
                        selected_research,
                        quality,
                        learning,
                        recent_loss_keys,
                        scout_mode,
                        meme_sprint,
                    )
                    if not recovery["allowed"]:
                        db.add(
                            SystemLog(
                                level="info",
                                source="recovery",
                                message="Entry skipped by guarded recovery mode",
                                context={"symbol": symbol, "strategy": config.name, "recovery": recovery},
                            )
                        )
                        continue
                allocation = quality_allocation(self._holdout_profit_factor(selected_research))
                regime_settings = selected_research.get("regime_filter", {}).get("settings", {})
                regime = regime_snapshot(entry_df, regime_settings)
                if regime_settings and not regime["allowed"]:
                    db.add(
                        SystemLog(
                            level="info",
                            source="market_regime",
                            message="Entry blocked by validated market regime filter",
                            context={"symbol": symbol, "strategy": config.name, "regime": regime},
                        )
                    )
                    continue
                base_pair_cap = float(risk.config.get("max_capital_per_trade", 0.0)) * allocation["multiplier"]
                dynamic_multiplier = self._dynamic_capital_multiplier(quality, selected_research, catalyst, learning)
                dynamic_multiplier *= execution_quality["risk_multiplier"]
                pair_notional_cap = self._position_notional_cap(base_pair_cap, scout_mode, dynamic_multiplier)
                correlation = self._portfolio_correlation(symbol, entry_df, open_trades, market_data)
                if correlation["max_correlation"] >= 0.95:
                    db.add(SystemLog(level="info", source="correlation", message="Entry blocked by concentrated market risk", context={"symbol": symbol, **correlation}))
                    continue
                if correlation["max_correlation"] >= 0.80:
                    pair_notional_cap *= 0.5
                if meme_sprint:
                    pair_notional_cap = self._meme_sprint_cap(capital, open_trades, closed_trades, pair_notional_cap)
                    if pair_notional_cap <= 0:
                        continue
                decision = risk.evaluate(
                    symbol,
                    entry_price,
                    open_trades,
                    closed_trades,
                    capital=capital,
                    max_notional=pair_notional_cap,
                )
                if not decision.allowed:
                    db.add(SystemLog(level="warning", source="risk", message=decision.reason, context={"symbol": symbol}))
                    continue
                slippage_rate = self._execution_slippage_rate(decision.notional, market_quality)
                slippage_guard = self._slippage_guard(slippage_rate, scout_mode=scout_mode, meme_sprint=meme_sprint)
                if not slippage_guard["allowed"]:
                    db.add(
                        SystemLog(
                            level="info",
                            source="execution_quality",
                            message="Entry blocked by estimated execution cost",
                            context={"symbol": symbol, "strategy": config.name, "slippage_guard": slippage_guard},
                        )
                    )
                    continue
                entry_fill = self._paper_entry_fill(entry_price, decision.notional, slippage_rate)
                order = MarketOrder(symbol=symbol, side="buy", quantity=entry_fill["quantity"], price=entry_fill["price"])
                response = await self._execute_order(exchange, order, mode)
                db.add(OrderLog(exchange=exchange_name, symbol=symbol, payload=order.__dict__, response=response))
                trade = Trade(
                        exchange=exchange_name,
                        strategy=config.name,
                        symbol=symbol,
                        side=TradeSide.buy,
                        entry_price=entry_fill["price"],
                        quantity=entry_fill["quantity"],
                        metadata_json={
                            "signal": signal.__dict__,
                            "market_regime": regime,
                            "news_catalyst": catalyst,
                            "entry_quality": quality,
                            "execution_quality": execution_quality,
                            "slippage_guard": slippage_guard,
                            "recovery_mode": recovery_mode,
                            "market_quality": market_quality,
                            "portfolio_correlation": correlation,
                            "quality_allocation": allocation,
                            "learning_profile": learning,
                            "dynamic_capital_multiplier": dynamic_multiplier,
                            "research": self._trade_research_context(research_context, symbol, config.name),
                            "execution_style": "new_listing_meme_sprint" if meme_sprint else ("fast_rotation_scout" if scout_mode else "core_selection"),
                            "entry_timeframe": entry_timeframe,
                            "entry_notional": decision.notional,
                            "entry_market_price": entry_price,
                            "entry_fee": entry_fill["fee"],
                            "entry_slippage_pct": entry_fill["slippage_pct"],
                            "last_price": price,
                            "unrealized_pnl": round(-entry_fill["fee"], 8),
                            "unrealized_pnl_pct": 0.0,
                            "execution_model": self._paper_execution_model(),
                            "risk": {
                                "stop_loss_pct": 4.0 if meme_sprint else risk.config.get("stop_loss_pct"),
                                "take_profit_pct": 10.0 if meme_sprint else risk.config.get("take_profit_pct"),
                                "trailing_stop_pct": 4.0 if meme_sprint else risk.config.get("trailing_stop_pct"),
                                "break_even_pct": 8.0 if meme_sprint else risk.config.get("break_even_pct"),
                                "max_hold_hours": 6 if meme_sprint else self._max_hold_hours(config.name),
                                "scout_position": scout_mode,
                                "meme_sprint": meme_sprint,
                            },
                        },
                    )
                db.add(trade)
                open_trades.append(trade)
                break
        await db.flush()
        return guard

    @staticmethod
    def _is_new_candle(last_closed_candle_ms: int, closed_candle_ms: int) -> bool:
        return closed_candle_ms > last_closed_candle_ms

    @classmethod
    def _prepare_market_data(
        cls,
        candles: list[dict],
        now_ms: int | None = None,
        maximum_age_ms: int = 8 * 60 * 60 * 1000,
    ) -> tuple[pd.DataFrame, float, int]:
        if len(candles) < 3:
            raise ValueError("insufficient candle data")
        df = pd.DataFrame(candles).copy()
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            raise ValueError("missing candle fields")
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column], errors="raise")
        numeric = df[["open", "high", "low", "close", "volume"]]
        if not numeric.map(math.isfinite).all().all():
            raise ValueError("non-finite market data")
        if (df[["open", "high", "low", "close"]] <= 0).any().any() or (df["volume"] < 0).any():
            raise ValueError("non-positive price or negative volume")
        if (df["high"] < df[["open", "close", "low"]].max(axis=1)).any():
            raise ValueError("candle high is inconsistent")
        if (df["low"] > df[["open", "close", "high"]].min(axis=1)).any():
            raise ValueError("candle low is inconsistent")
        timestamps = df["timestamp"].map(cls._timestamp_ms)
        if not timestamps.is_monotonic_increasing or timestamps.duplicated().any():
            raise ValueError("candle timestamps are not strictly increasing")
        current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        latest_ms = int(timestamps.iloc[-1])
        if latest_ms > current_ms + 5 * 60 * 1000:
            raise ValueError("market data timestamp is in the future")
        if current_ms - latest_ms > maximum_age_ms:
            raise ValueError("stale market data")
        df["timestamp"] = timestamps
        price = float(df["close"].iloc[-1])
        signal_df = df.iloc[:-1].copy()
        closed_candle_ms = int(signal_df["timestamp"].iloc[-1])
        return signal_df, price, closed_candle_ms

    @staticmethod
    def _timestamp_ms(value) -> int:
        timestamp = int(value)
        return timestamp * 1000 if timestamp < 1_000_000_000_000 else timestamp

    @staticmethod
    async def _execute_order(exchange: ExchangeClient, order: MarketOrder, mode: str) -> dict:
        if mode == "paper":
            return {
                "paper": True,
                "transmitted": False,
                "exchange": exchange.name,
                "order": order.__dict__,
            }
        if mode != "real":
            raise ValueError(f"Unsupported execution mode: {mode}")
        if not get_settings().real_trading_enabled:
            raise PermissionError("Real trading is disabled")
        return await exchange.place_order(order)

    @staticmethod
    def _is_allowed_pair(trade: Trade, allowed_pairs: set[tuple[str, str]]) -> bool:
        return (trade.strategy, trade.symbol) in allowed_pairs

    @staticmethod
    def _active_symbols(state_symbols: list[str], configs: list[StrategyConfig]) -> list[str]:
        symbols = set(state_symbols)
        for config in configs:
            symbols.update(config.symbols or [])
        return sorted(symbol for symbol in symbols if symbol)

    @classmethod
    def _scout_strategy_configs(cls, context: dict, configs: list[StrategyConfig], state_symbols: list[str]) -> list[StrategyConfig]:
        selected_pairs = {(row.get("strategy"), row.get("symbol")) for row in context.get("selected", [])}
        active_symbols = set(state_symbols)
        for config in configs:
            active_symbols.update(config.symbols or [])
        scout_configs: list[StrategyConfig] = []
        seen_symbols: set[str] = set()
        for row in sorted(context.get("opportunity_radar", []), key=lambda item: float(item.get("opportunity_score", 0.0)), reverse=True):
            symbol = row.get("symbol")
            strategy = row.get("strategy")
            pair = (strategy, symbol)
            if not symbol or strategy not in STRATEGIES or pair in selected_pairs:
                continue
            if symbol in active_symbols or symbol in seen_symbols:
                continue
            if not cls._scout_candidate_allowed(row):
                continue
            scout_configs.append(
                StrategyConfig(
                    name=strategy,
                    enabled=True,
                    symbols=[symbol],
                    timeframe="4h",
                    parameters={"scout": True, "source": "opportunity_radar"},
                )
            )
            seen_symbols.add(symbol)
            if len(scout_configs) >= cls.SCOUT_MAX_PAIRS:
                break
        return scout_configs

    @classmethod
    def _scout_candidate_allowed(cls, row: dict) -> bool:
        return bool(
            float(row.get("opportunity_score") or 0.0) >= cls.SCOUT_MIN_OPPORTUNITY_SCORE
            and float(row.get("holdout_return_pct") or 0.0) >= cls.SCOUT_MIN_HOLDOUT_RETURN_PCT
            and float(row.get("holdout_profit_factor") or 0.0) >= cls.SCOUT_MIN_HOLDOUT_PROFIT_FACTOR
        )

    @staticmethod
    def _track_open_selection(
        trades: list[Trade],
        allowed_pairs: set[tuple[str, str]],
        generated_at: str | None,
    ) -> None:
        if not generated_at:
            return
        for trade in trades:
            if not TradingEngine._is_allowed_pair(trade, allowed_pairs):
                continue
            metadata = dict(trade.metadata_json or {})
            metadata["selection_tracking_generation"] = generated_at
            trade.metadata_json = metadata

    @staticmethod
    def _research_context() -> dict:
        report_path = Path(__file__).resolve().parents[1] / "data" / "walk_forward_report.json"
        if not report_path.exists():
            return {}
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        return {
            "generated_at": report.get("generated_at"),
            "timeframe": report.get("timeframe"),
            "risk_profile": report.get("methodology", {}).get("risk_profile", {}).get("name"),
            "selected": report.get("selected", []),
            "portfolio_validation": report.get("portfolio_validation", {}),
            "opportunity_radar": report.get("opportunity_radar", []),
            "portfolio_variant_lab": report.get("portfolio_variant_lab", []),
        }

    @staticmethod
    def _trade_research_context(context: dict, symbol: str, strategy: str) -> dict:
        if not context:
            return {}
        selected = TradingEngine._research_for_pair(context, symbol, strategy)
        source = "selected" if TradingEngine._selected_research(context, symbol, strategy) else ("opportunity_radar" if selected else None)
        return {
            "generated_at": context.get("generated_at"),
            "timeframe": context.get("timeframe"),
            "risk_profile": context.get("risk_profile"),
            "source": source,
            "robustness_score": selected.get("robustness_score"),
            "holdout_return_pct": selected.get("holdout_return_pct"),
            "holdout_profit_factor": TradingEngine._holdout_profit_factor(selected),
            "holdout_drawdown_pct": selected.get("holdout_drawdown_pct") or selected.get("holdout_metrics", {}).get("drawdown"),
            "quality_allocation": quality_allocation(TradingEngine._holdout_profit_factor(selected)),
            "median_year_return_pct": selected.get("median_year_return_pct"),
            "opportunity_score": selected.get("opportunity_score"),
            "regime_filter": selected.get("regime_filter"),
        }

    @staticmethod
    def _selected_research(context: dict, symbol: str, strategy: str) -> dict:
        return next(
            (row for row in context.get("selected", []) if row.get("symbol") == symbol and row.get("strategy") == strategy),
            {},
        )

    @staticmethod
    def _radar_research(context: dict, symbol: str, strategy: str) -> dict:
        return next(
            (row for row in context.get("opportunity_radar", []) if row.get("symbol") == symbol and row.get("strategy") == strategy),
            {},
        )

    @classmethod
    def _research_for_pair(cls, context: dict, symbol: str, strategy: str) -> dict:
        return cls._selected_research(context, symbol, strategy) or cls._radar_research(context, symbol, strategy)

    @staticmethod
    def _holdout_profit_factor(research: dict) -> float | None:
        return research.get("holdout_profit_factor") or research.get("holdout_metrics", {}).get("profit_factor")

    @classmethod
    def _position_notional_cap(cls, base_cap: float, scout_mode: bool = False, dynamic_multiplier: float = 1.0) -> float:
        adjusted = max(0.0, base_cap) * max(0.2, min(dynamic_multiplier, 1.35))
        if not scout_mode:
            return round(adjusted, 2)
        return round(adjusted * cls.SCOUT_ALLOCATION_MULTIPLIER, 2)

    @classmethod
    def _entry_market_view(
        cls,
        symbol: str,
        df: pd.DataFrame,
        price: float,
        scout_mode: bool,
        short_market_data: dict[str, tuple[pd.DataFrame, float, int, str]],
    ) -> tuple[pd.DataFrame, float, str]:
        if scout_mode and symbol in short_market_data:
            short_df, short_price, _, timeframe = short_market_data[symbol]
            return short_df, short_price, timeframe
        return df, price, "4h"

    @classmethod
    async def _new_listing_configs(
        cls,
        db: AsyncSession,
        exchange_name: str,
        exchange: ExchangeClient,
    ) -> list[StrategyConfig]:
        try:
            symbols = await exchange.tradable_symbols("USDT")
        except Exception as exc:
            db.add(SystemLog(level="error", source="new_listings", message="Listing scan failed", context={"error": str(exc)[:250]}))
            return []
        if not symbols:
            return []
        result = await db.execute(select(ListingState).where(ListingState.exchange == exchange_name))
        known = {row.symbol: row for row in result.scalars().all()}
        baseline_scan = not known
        now = datetime.utcnow()
        for symbol in symbols:
            row = known.get(symbol)
            if row is None:
                row = ListingState(
                    id=f"{exchange_name}:{symbol}",
                    exchange=exchange_name,
                    symbol=symbol,
                    baseline=baseline_scan,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(row)
                known[symbol] = row
            else:
                row.last_seen_at = now
        cutoff = now - timedelta(days=cls.MEME_SPRINT_ACTIVE_DAYS)
        eligible = sorted(
            row.symbol for row in known.values()
            if not row.baseline and row.first_seen_at >= cutoff and row.symbol in symbols
        )[:5]
        return [
            StrategyConfig(
                name="Meme Momentum",
                enabled=True,
                symbols=[symbol],
                timeframe=cls.MEME_SPRINT_TIMEFRAME,
                parameters={"scout": True, "meme_sprint": True, "entry_timeframe": cls.MEME_SPRINT_TIMEFRAME},
            )
            for symbol in eligible
        ]

    @classmethod
    def _meme_sprint_cap(
        cls,
        capital: float,
        open_trades: list[Trade],
        closed_trades: list[Trade],
        normal_cap: float,
    ) -> float:
        sprint_open = [trade for trade in open_trades if (trade.metadata_json or {}).get("execution_style") == "new_listing_meme_sprint"]
        exposure = sum(float((trade.metadata_json or {}).get("entry_notional", 0.0)) for trade in sprint_open)
        exposure_limit = capital * cls.MEME_SPRINT_EXPOSURE_PCT / 100
        sprint_closed = [trade for trade in closed_trades if (trade.metadata_json or {}).get("execution_style") == "new_listing_meme_sprint"]
        if cls._consecutive_losses(sprint_closed) >= 2:
            return 0.0
        position_limit = capital * cls.MEME_SPRINT_POSITION_PCT / 100
        return round(max(0.0, min(normal_cap, position_limit, exposure_limit - exposure)), 2)

    @staticmethod
    def _meme_sprint_market_gate(df: pd.DataFrame, market_quality: dict) -> dict:
        curve = curve_snapshot(df)
        spread = float(market_quality.get("spread_pct", 999.0) or 999.0)
        quote_volume = float(market_quality.get("quote_volume_24h", 0.0) or 0.0)
        bid_depth = float(market_quality.get("bid_depth_1pct", 0.0) or 0.0)
        ask_depth = float(market_quality.get("ask_depth_1pct", 0.0) or 0.0)
        imbalance = float(market_quality.get("order_book_imbalance", 0.0) or 0.0)
        momentum = float(curve.get("momentum_3_pct", 999.0) or 0.0)
        upper_wick = float(curve.get("upper_wick_ratio", 1.0) or 0.0)
        reasons = []
        if not market_quality.get("available"):
            reasons.append("order_book_unavailable")
        if spread > 1.2:
            reasons.append("spread_too_wide")
        if quote_volume < 250_000:
            reasons.append("insufficient_quote_volume")
        if min(bid_depth, ask_depth) < 5_000:
            reasons.append("insufficient_order_book_depth")
        if imbalance < 0.25:
            reasons.append("weak_bid_support")
        if imbalance > 0.9:
            reasons.append("suspicious_bid_imbalance")
        if momentum > 25:
            reasons.append("late_parabolic_entry")
        if upper_wick > 0.55:
            reasons.append("pump_exhaustion_wick")
        return {
            "allowed": not reasons,
            "reasons": reasons,
            "spread_pct": spread,
            "quote_volume_24h": round(quote_volume, 2),
            "bid_depth_1pct": round(bid_depth, 2),
            "ask_depth_1pct": round(ask_depth, 2),
            "order_book_imbalance": imbalance,
            "momentum_3_pct": momentum,
            "upper_wick_ratio": upper_wick,
        }

    @staticmethod
    def _execution_quality_gate(
        df: pd.DataFrame,
        market_quality: dict,
        *,
        scout_mode: bool = False,
        meme_sprint: bool = False,
    ) -> dict:
        curve = curve_snapshot(df)
        spread = float(market_quality.get("spread_pct", 999.0) or 999.0)
        quote_volume = float(market_quality.get("quote_volume_24h", 0.0) or 0.0)
        bid_depth = float(market_quality.get("bid_depth_1pct", 0.0) or 0.0)
        ask_depth = float(market_quality.get("ask_depth_1pct", 0.0) or 0.0)
        imbalance = float(market_quality.get("order_book_imbalance", 0.0) or 0.0)
        atr_pct = abs(float(curve.get("atr_pct", 0.0) or 0.0))
        upper_wick = float(curve.get("upper_wick_ratio", 0.0) or 0.0)
        min_depth = min(bid_depth, ask_depth)
        reasons = []

        if meme_sprint:
            limits = {"spread": 1.0, "volume": 500_000, "depth": 7_500, "low_imbalance": 0.28, "high_imbalance": 0.88}
        elif scout_mode:
            limits = {"spread": 0.25, "volume": 2_000_000, "depth": 25_000, "low_imbalance": 0.30, "high_imbalance": 0.82}
        else:
            limits = {"spread": 0.35, "volume": 1_000_000, "depth": 10_000, "low_imbalance": 0.20, "high_imbalance": 0.88}

        if not market_quality.get("available"):
            reasons.append("order_book_unavailable")
        if spread > limits["spread"]:
            reasons.append("spread_too_wide")
        if quote_volume < limits["volume"]:
            reasons.append("insufficient_quote_volume")
        if min_depth < limits["depth"]:
            reasons.append("insufficient_order_book_depth")
        if imbalance < limits["low_imbalance"]:
            reasons.append("weak_bid_support")
        if imbalance > limits["high_imbalance"]:
            reasons.append("crowded_or_manipulated_bid_side")
        if atr_pct > 7.0 and upper_wick > 0.45:
            reasons.append("volatile_exhaustion_candle")

        if reasons:
            tier = "blocked"
            multiplier = 0.0
        elif spread <= 0.08 and quote_volume >= 50_000_000 and min_depth >= 250_000:
            tier = "institutional"
            multiplier = 1.0
        elif spread <= 0.18 and quote_volume >= 10_000_000 and min_depth >= 75_000:
            tier = "deep"
            multiplier = 0.85
        else:
            tier = "tradable_thin"
            multiplier = 0.55 if scout_mode or meme_sprint else 0.65

        return {
            "allowed": not reasons,
            "tier": tier,
            "risk_multiplier": multiplier,
            "reasons": reasons,
            "spread_pct": round(spread, 4),
            "quote_volume_24h": round(quote_volume, 2),
            "bid_depth_1pct": round(bid_depth, 2),
            "ask_depth_1pct": round(ask_depth, 2),
            "order_book_imbalance": round(imbalance, 4),
            "atr_pct": round(atr_pct, 4),
            "upper_wick_ratio": round(upper_wick, 4),
        }

    @staticmethod
    def _portfolio_correlation(
        symbol: str,
        candidate: pd.DataFrame,
        open_trades: list[Trade],
        market_data: dict[str, tuple[pd.DataFrame, float, int]],
    ) -> dict:
        candidate_returns = candidate.set_index("timestamp")["close"].astype(float).pct_change().dropna()
        rows = []
        for trade in open_trades:
            if trade.symbol == symbol or trade.symbol not in market_data:
                continue
            other = market_data[trade.symbol][0].set_index("timestamp")["close"].astype(float).pct_change().dropna()
            aligned = pd.concat([candidate_returns, other], axis=1, join="inner").dropna().tail(120)
            if len(aligned) < 30:
                continue
            value = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if math.isfinite(value):
                rows.append({"symbol": trade.symbol, "correlation": round(value, 4), "samples": len(aligned)})
        strongest = max(rows, key=lambda row: row["correlation"], default=None)
        return {
            "max_correlation": max(0.0, float(strongest["correlation"])) if strongest else 0.0,
            "most_correlated_symbol": strongest["symbol"] if strongest else None,
            "samples": strongest["samples"] if strongest else 0,
        }

    @classmethod
    def _learning_profile(cls, closed_trades: list[Trade]) -> dict[tuple[str, str, str], dict]:
        grouped: dict[tuple[str, str, str], list[Trade]] = {}
        for trade in closed_trades:
            style = (trade.metadata_json or {}).get("execution_style", "core_selection")
            grouped.setdefault((trade.symbol, trade.strategy, style), []).append(trade)
        profile = {}
        for key, rows in grouped.items():
            recent = sorted(rows, key=lambda row: row.closed_at or datetime.min)[-12:]
            wins = [trade.pnl for trade in recent if trade.pnl > 0]
            losses = [trade.pnl for trade in recent if trade.pnl < 0]
            total_pnl = sum(trade.pnl for trade in recent)
            profit_factor = sum(wins) / abs(sum(losses)) if losses else (999.0 if wins else 0.0)
            win_rate = len(wins) / len(recent) if recent else 0.0
            closed_count = len(recent)
            if closed_count >= 3 and total_pnl < 0 and profit_factor < 0.95:
                multiplier = 0.0
                status = "blocked_after_losses"
            elif closed_count >= 4 and profit_factor < 1.15:
                multiplier = 0.35
                status = "reduced_after_weak_results"
            elif closed_count >= 8 and total_pnl > 0 and profit_factor >= 1.6 and win_rate >= 0.55:
                multiplier = 1.05
                status = "boosted_after_good_results"
            else:
                multiplier = 1.0
                status = "neutral"
            profile[key] = {
                "status": status,
                "allowed": multiplier > 0,
                "multiplier": multiplier,
                "closed_trades": closed_count,
                "total_pnl": round(total_pnl, 2),
                "profit_factor": round(profit_factor, 3) if profit_factor != 999.0 else 999.0,
                "win_rate": round(win_rate * 100, 2),
            }
        return profile

    @staticmethod
    def _learning_for_pair(profile: dict[tuple[str, str, str], dict], symbol: str, strategy: str, execution_style: str) -> dict:
        return profile.get(
            (symbol, strategy, execution_style),
            {
                "status": "new_or_insufficient_sample",
                "allowed": True,
                "multiplier": 1.0,
                "closed_trades": 0,
                "total_pnl": 0.0,
                "profit_factor": 0.0,
                "win_rate": 0.0,
            },
        )

    @classmethod
    def _recovery_entry_decision(
        cls,
        symbol: str,
        strategy: str,
        execution_style: str,
        research: dict,
        quality: dict,
        learning: dict,
        recent_loss_keys: set[tuple[str, str, str]],
        scout_mode: bool,
        meme_sprint: bool,
    ) -> dict:
        reasons = []
        profit_factor = cls._holdout_profit_factor(research)
        quality_score = float(quality.get("score", 0.0) or 0.0)
        if meme_sprint:
            reasons.append("meme_sprint_paused_during_recovery")
        if scout_mode:
            reasons.append("scout_paused_during_recovery")
        if (symbol, strategy, execution_style) in recent_loss_keys:
            reasons.append("same_pair_in_recent_loss_streak")
        if profit_factor is None or float(profit_factor) < cls.RECOVERY_MIN_HOLDOUT_PROFIT_FACTOR:
            reasons.append("holdout_profit_factor_too_low")
        if quality_score < cls.RECOVERY_MIN_ENTRY_SCORE:
            reasons.append("entry_quality_too_low")
        if learning.get("status") in {"blocked_after_losses", "reduced_after_weak_results"}:
            reasons.append("live_learning_not_confirmed")
        return {
            "allowed": not reasons,
            "reasons": reasons,
            "min_holdout_profit_factor": cls.RECOVERY_MIN_HOLDOUT_PROFIT_FACTOR,
            "holdout_profit_factor": round(float(profit_factor), 3) if profit_factor is not None else None,
            "min_entry_score": cls.RECOVERY_MIN_ENTRY_SCORE,
            "entry_score": round(quality_score, 3),
            "learning_status": learning.get("status"),
        }

    @staticmethod
    def _recent_loss_keys(closed_trades: list[Trade]) -> set[tuple[str, str, str]]:
        keys = set()
        for trade in sorted(closed_trades, key=lambda row: row.closed_at or datetime.min, reverse=True):
            if trade.pnl >= 0:
                break
            style = (trade.metadata_json or {}).get("execution_style", "core_selection")
            keys.add((trade.symbol, trade.strategy, style))
        return keys

    @classmethod
    def _dynamic_capital_multiplier(cls, quality: dict, research: dict, catalyst: dict, learning: dict) -> float:
        score = float(quality.get("score", 0.0))
        multiplier = 0.65
        if score >= 0.84:
            multiplier += 0.2
        elif score >= 0.76:
            multiplier += 0.1
        elif score < 0.7:
            multiplier -= 0.2
        holdout_profit_factor = float(cls._holdout_profit_factor(research) or 0.0)
        if holdout_profit_factor >= 1.7:
            multiplier += 0.1
        elif holdout_profit_factor < 1.35:
            multiplier -= 0.2
        if catalyst.get("decision_stance") == "positive":
            multiplier += 0.08
        elif catalyst.get("decision_stance") == "negative":
            multiplier -= 0.25
        multiplier *= float(learning.get("multiplier", 1.0))
        return round(max(0.15, min(multiplier, 1.05)), 3)

    @classmethod
    def _shadow_candidates(cls, context: dict, limit: int = 8) -> list[dict]:
        selected_pairs = {(row.get("strategy"), row.get("symbol")) for row in context.get("selected", [])}
        candidates: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for row in sorted(context.get("opportunity_radar", []), key=lambda item: float(item.get("opportunity_score", 0.0)), reverse=True):
            pair = (row.get("strategy"), row.get("symbol"))
            if pair in selected_pairs or pair in seen:
                continue
            if row.get("strategy") not in STRATEGIES or not row.get("symbol"):
                continue
            if float(row.get("holdout_return_pct") or 0.0) <= 0:
                continue
            candidates.append(
                {
                    "symbol": row["symbol"],
                    "strategy": row["strategy"],
                    "source": "opportunity_radar",
                    "opportunity_score": row.get("opportunity_score"),
                    "holdout_return_pct": row.get("holdout_return_pct"),
                    "holdout_drawdown_pct": row.get("holdout_drawdown_pct"),
                    "holdout_profit_factor": row.get("holdout_profit_factor"),
                    "notes": row.get("notes", []),
                }
            )
            seen.add(pair)
            if len(candidates) >= limit:
                break
        return candidates

    @classmethod
    def _tick_shadow_candidates(
        cls,
        db: AsyncSession,
        exchange_name: str,
        candidates: list[dict],
        open_shadow_trades: list[ShadowTrade],
        market_data: dict[str, tuple[pd.DataFrame, float, int]],
        new_candle_symbols: set[str],
        risk_config: dict,
        research_context: dict,
        news_context: dict,
    ) -> None:
        if not candidates:
            return
        for trade in list(open_shadow_trades):
            if trade.symbol not in market_data:
                continue
            df, price, _ = market_data[trade.symbol]
            estimated_exit = cls._paper_exit_fill(price, trade.quantity)
            unrealized_pnl = cls._paper_pnl(trade, estimated_exit["price"], estimated_exit["fee"])
            trade.metadata_json = {
                **(trade.metadata_json or {}),
                "last_price": price,
                "estimated_exit_price": estimated_exit["price"],
                "estimated_exit_fee": estimated_exit["fee"],
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": round((price / trade.entry_price - 1) * 100, 4) if trade.entry_price else 0.0,
            }
            catalyst = cls._symbol_catalyst(news_context, trade.symbol)
            reason = cls._adaptive_exit_reason(trade, price, df, risk_config, catalyst)
            if reason is None:
                continue
            exit_fill = cls._paper_exit_fill(price, trade.quantity)
            trade.status = TradeStatus.closed
            trade.exit_price = exit_fill["price"]
            trade.pnl = cls._paper_pnl(trade, exit_fill["price"], exit_fill["fee"])
            trade.closed_at = datetime.utcnow()
            trade.metadata_json = {
                **(trade.metadata_json or {}),
                "exit_reason": reason,
                "exit_fee": exit_fill["fee"],
                "shadow_execution": True,
                "execution_model": cls._paper_execution_model(),
            }
            db.add(
                SystemLog(
                    level="info",
                    source="shadow_research",
                    message="Shadow candidate closed",
                    context={"trade_id": trade.id, "symbol": trade.symbol, "strategy": trade.strategy, "pnl": trade.pnl},
                )
            )
            open_shadow_trades.remove(trade)

        open_pairs = {(trade.strategy, trade.symbol) for trade in open_shadow_trades}
        for candidate in candidates:
            symbol = candidate["symbol"]
            strategy = candidate["strategy"]
            if symbol not in new_candle_symbols or symbol not in market_data or (strategy, symbol) in open_pairs:
                continue
            catalyst = cls._symbol_catalyst(news_context, symbol)
            if catalyst.get("decision_stance", "neutral") == "negative":
                continue
            df, price, _ = market_data[symbol]
            signal = STRATEGIES[strategy].generate(df, {})
            if signal.action != "buy":
                continue
            quality = cls._entry_quality(signal, df, catalyst)
            if not quality["allowed"]:
                continue
            notional = min(1_000.0, float(risk_config.get("max_capital_per_trade", 1_000.0)))
            entry_fill = cls._paper_entry_fill(price, notional)
            trade = ShadowTrade(
                exchange=exchange_name,
                strategy=strategy,
                symbol=symbol,
                side=TradeSide.buy,
                entry_price=entry_fill["price"],
                quantity=entry_fill["quantity"],
                metadata_json={
                    "signal": signal.__dict__,
                    "candidate": candidate,
                    "research_generation": research_context.get("generated_at"),
                    "news_catalyst": catalyst,
                    "entry_quality": quality,
                    "entry_notional": notional,
                    "entry_market_price": price,
                    "entry_fee": entry_fill["fee"],
                    "last_price": price,
                    "unrealized_pnl": round(-entry_fill["fee"], 8),
                    "unrealized_pnl_pct": 0.0,
                    "shadow_execution": True,
                    "execution_model": cls._paper_execution_model(),
                    "risk": {
                        "stop_loss_pct": risk_config.get("stop_loss_pct"),
                        "take_profit_pct": risk_config.get("take_profit_pct"),
                        "trailing_stop_pct": risk_config.get("trailing_stop_pct"),
                        "break_even_pct": risk_config.get("break_even_pct"),
                        "max_hold_hours": cls._max_hold_hours(strategy),
                    },
                },
            )
            db.add(trade)
            open_shadow_trades.append(trade)
            open_pairs.add((strategy, symbol))
            db.add(
                SystemLog(
                    level="info",
                    source="shadow_research",
                    message="Shadow candidate opened",
                    context={"symbol": symbol, "strategy": strategy, "notional": notional},
                )
            )

    @staticmethod
    async def _news_context(symbols: list[str]) -> dict:
        try:
            return await NewsCatalystService().snapshot(symbols)
        except Exception:
            return {"market_bias": "neutral", "symbols": []}

    @staticmethod
    def _symbol_catalyst(news_context: dict, symbol: str) -> dict:
        return next(
            (row for row in news_context.get("symbols", []) if row.get("symbol") == symbol),
            {"symbol": symbol, "score": 0.0, "stance": "neutral", "decision_score": 0.0, "decision_stance": "neutral", "headlines": []},
        )

    @classmethod
    def _news_ranked_symbols(cls, symbols: list[str], news_context: dict) -> list[str]:
        scores = {
            row.get("symbol"): float(row.get("decision_score", row.get("score", 0.0)))
            for row in news_context.get("symbols", [])
        }
        return sorted(symbols, key=lambda symbol: scores.get(symbol, 0.0), reverse=True)

    @classmethod
    def _selection_guard(cls, trades: list[Trade], research_context: dict, initial_capital: float = 10_000) -> dict:
        portfolio_guard = cls._portfolio_research_guard(research_context)
        generated_at = research_context.get("generated_at")
        tagged = [
            trade
            for trade in trades
            if (
                (trade.metadata_json or {}).get("research", {}).get("generated_at") == generated_at
                or (trade.metadata_json or {}).get("selection_tracking_generation") == generated_at
            )
        ]
        closed = [trade for trade in tagged if trade.status == TradeStatus.closed]
        open_trades = [trade for trade in tagged if trade.status == TradeStatus.open]
        total_pnl = sum(trade.pnl for trade in closed) + sum(float((trade.metadata_json or {}).get("unrealized_pnl", 0.0)) for trade in open_trades)
        pnl_pct = (total_pnl / initial_capital) * 100 if initial_capital else 0.0
        consecutive_losses = cls._consecutive_losses(closed)
        wins = [trade.pnl for trade in closed if trade.pnl > 0]
        losses = [trade.pnl for trade in closed if trade.pnl < 0]
        profit_factor = sum(wins) / abs(sum(losses)) if losses else (float("inf") if wins else 0.0)
        loss_breach = pnl_pct <= -cls.ACTIVE_SELECTION_MAX_LOSS_PCT
        streak_breach = consecutive_losses >= cls.ACTIVE_SELECTION_MAX_CONSECUTIVE_LOSSES
        mature_breach = (
            len(closed) >= cls.ACTIVE_SELECTION_MATURE_TRADES
            and (sum(trade.pnl for trade in closed) <= 0 or profit_factor < cls.ACTIVE_SELECTION_MIN_PROFIT_FACTOR)
        )
        if portfolio_guard["breached"]:
            reason = portfolio_guard["reason"]
        elif loss_breach:
            reason = "max_selection_loss"
        elif streak_breach:
            reason = "consecutive_selection_losses"
        elif mature_breach:
            reason = "mature_sample_underperformance"
        else:
            reason = "ok"
        if not (portfolio_guard["breached"] or loss_breach or streak_breach or mature_breach):
            engine_action = "run"
        elif streak_breach and not portfolio_guard["breached"] and not loss_breach and not mature_breach:
            engine_action = "recover"
        else:
            engine_action = "observe_only"
        return {
            "breached": bool(portfolio_guard["breached"] or loss_breach or streak_breach or mature_breach),
            "reason": reason,
            "engine_action": engine_action,
            "portfolio_research": portfolio_guard,
            "total_pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 3),
            "max_loss_pct": cls.ACTIVE_SELECTION_MAX_LOSS_PCT,
            "consecutive_losses": consecutive_losses,
            "max_consecutive_losses": cls.ACTIVE_SELECTION_MAX_CONSECUTIVE_LOSSES,
            "mature_trades": cls.ACTIVE_SELECTION_MATURE_TRADES,
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
            "min_profit_factor": cls.ACTIVE_SELECTION_MIN_PROFIT_FACTOR,
            "tracked_trades": len(tagged),
        }

    @staticmethod
    def _portfolio_research_guard(research_context: dict) -> dict:
        validation = research_context.get("portfolio_validation") or {}
        if not validation:
            return {"breached": False, "reason": "ok", "available": False}
        confidence = (validation.get("monte_carlo") or {}).get("confidence")
        eligible = bool(validation.get("eligible", False))
        if not eligible:
            reason = "portfolio_research_not_validated"
        elif confidence == "weak":
            reason = "portfolio_confidence_weak"
        else:
            reason = "ok"
        return {
            "breached": reason != "ok",
            "reason": reason,
            "available": True,
            "eligible": eligible,
            "confidence": confidence,
            "holdout_return_pct": (validation.get("holdout") or {}).get("return_pct"),
            "holdout_drawdown_pct": (validation.get("holdout") or {}).get("drawdown"),
            "monte_carlo_positive_pct": (validation.get("monte_carlo") or {}).get("probability_positive_pct"),
        }

    @classmethod
    def _paper_execution_model(cls) -> dict:
        return {
            "fee_pct_per_order": round(cls.PAPER_FEE_RATE * 100, 4),
            "slippage_pct_per_order": round(cls.PAPER_SLIPPAGE_RATE * 100, 4),
        }

    @classmethod
    def _paper_entry_fill(cls, market_price: float, notional: float, slippage_rate: float | None = None) -> dict:
        applied_slippage = cls.PAPER_SLIPPAGE_RATE if slippage_rate is None else max(cls.PAPER_SLIPPAGE_RATE, slippage_rate)
        price = round(market_price * (1 + applied_slippage), 8)
        quantity = round(notional / price, 8) if price else 0.0
        fee = round(notional * cls.PAPER_FEE_RATE, 8)
        return {"price": price, "quantity": quantity, "fee": fee, "slippage_pct": round(applied_slippage * 100, 4)}

    @classmethod
    def _execution_slippage_rate(cls, notional: float, market_quality: dict | None) -> float:
        if not market_quality or not market_quality.get("available"):
            return cls.PAPER_SLIPPAGE_RATE
        half_spread = max(0.0, float(market_quality.get("spread_pct", 0.0))) / 200
        usable_depth = min(
            float(market_quality.get("bid_depth_1pct", 0.0) or 0.0),
            float(market_quality.get("ask_depth_1pct", 0.0) or 0.0),
        )
        impact = math.sqrt(max(0.0, notional) / usable_depth) * 0.001 if usable_depth else 0.02
        return min(0.02, max(cls.PAPER_SLIPPAGE_RATE, half_spread + impact))

    @staticmethod
    def _slippage_guard(slippage_rate: float, *, scout_mode: bool = False, meme_sprint: bool = False) -> dict:
        if meme_sprint:
            max_rate = 0.006
        elif scout_mode:
            max_rate = 0.0025
        else:
            max_rate = 0.003
        reasons = []
        if slippage_rate > max_rate:
            reasons.append("estimated_slippage_too_high")
        return {
            "allowed": not reasons,
            "reasons": reasons,
            "estimated_slippage_pct": round(slippage_rate * 100, 4),
            "max_slippage_pct": round(max_rate * 100, 4),
        }

    @classmethod
    def _paper_exit_fill(cls, market_price: float, quantity: float) -> dict:
        price = round(market_price * (1 - cls.PAPER_SLIPPAGE_RATE), 8)
        fee = round(price * quantity * cls.PAPER_FEE_RATE, 8)
        return {"price": price, "fee": fee}

    @staticmethod
    def _paper_pnl(trade: Trade, exit_price: float, exit_fee: float) -> float:
        entry_fee = float((trade.metadata_json or {}).get("entry_fee", 0.0))
        partial_realized = float((trade.metadata_json or {}).get("partial_realized_pnl", 0.0))
        return round(partial_realized + (exit_price - trade.entry_price) * trade.quantity - entry_fee - exit_fee, 8)

    @classmethod
    def _maybe_take_partial_profit(cls, trade: Trade, market_price: float, mode: str) -> dict | None:
        if mode != "paper" or not trade.entry_price or trade.quantity <= 0:
            return None
        metadata = dict(trade.metadata_json or {})
        if metadata.get("partial_profit_taken"):
            return None
        risk = metadata.get("risk", {})
        meme_sprint = bool(risk.get("meme_sprint"))
        scout_position = bool(risk.get("scout_position") or metadata.get("execution_style") == "fast_rotation_scout")
        trigger = 10.0 if meme_sprint else (cls.PARTIAL_PROFIT_SCOUT_TRIGGER_PCT if scout_position else cls.PARTIAL_PROFIT_CORE_TRIGGER_PCT)
        gain_pct = (market_price / trade.entry_price - 1) * 100
        if gain_pct < trigger:
            return None
        if meme_sprint:
            original_notional = float(metadata.get("entry_notional", trade.entry_price * trade.quantity))
            sell_quantity = round(min(trade.quantity, original_notional / market_price), 8)
        else:
            sell_quantity = round(trade.quantity * cls.PARTIAL_PROFIT_FRACTION, 8)
        if sell_quantity <= 0:
            return None
        exit_fill = cls._paper_exit_fill(market_price, sell_quantity)
        entry_fee = float(metadata.get("entry_fee", 0.0))
        sold_fraction = sell_quantity / (trade.quantity or sell_quantity)
        released_entry_fee = round(entry_fee * sold_fraction, 8)
        realized = round((exit_fill["price"] - trade.entry_price) * sell_quantity - released_entry_fee - exit_fill["fee"], 8)
        trade.quantity = round(trade.quantity - sell_quantity, 8)
        metadata["entry_fee"] = round(entry_fee - released_entry_fee, 8)
        metadata["partial_profit_taken"] = True
        metadata["partial_profit"] = {
            "trigger_pct": trigger,
            "quantity": sell_quantity,
            "price": exit_fill["price"],
            "fee": exit_fill["fee"],
            "realized_pnl": realized,
            "taken_at": datetime.utcnow().isoformat(),
        }
        metadata["partial_realized_pnl"] = round(float(metadata.get("partial_realized_pnl", 0.0)) + realized, 8)
        original_notional = float(metadata.get("entry_notional", trade.entry_price * (trade.quantity + sell_quantity)))
        metadata["entry_notional"] = round(original_notional * (1 - sold_fraction), 2)
        trade.metadata_json = metadata
        return {
            "quantity": sell_quantity,
            "price": exit_fill["price"],
            "fee": exit_fill["fee"],
            "realized_pnl": realized,
        }

    @classmethod
    def _max_hold_hours(cls, strategy: str) -> int:
        return cls.MAX_HOLD_HOURS_BY_STRATEGY.get(strategy, 96)

    @staticmethod
    def _entry_quality(signal, df: pd.DataFrame, catalyst: dict | None = None) -> dict:
        catalyst = catalyst or {}
        close = df["close"].astype(float)
        if len(close) < 60:
            return {"allowed": False, "reason": "insufficient_quality_history", "score": 0.0}
        current = float(close.iloc[-1])
        curve = curve_snapshot(df)
        psychology = investor_behavior_snapshot(df)
        macro = macro_event_snapshot(df)
        memory = historical_market_memory(df)
        prediction = supervised_market_prediction(df)
        ema20 = float(ema(close, 20).iloc[-1])
        ema50 = float(ema(close, 50).iloc[-1])
        atr_value = float(atr(df).iloc[-1])
        atr_pct = round(atr_value / current * 100, 3) if current and math.isfinite(atr_value) else 0.0
        momentum_3 = round((current / float(close.iloc[-4]) - 1) * 100, 3) if float(close.iloc[-4]) else 0.0
        trend_ok = current >= ema50 or current >= ema20
        volatility_ok = 0.12 <= atr_pct <= 8.0
        catalyst_bonus = 0.12 if catalyst.get("decision_stance") == "positive" else 0.0
        breakout_bonus = 0.08 if curve.get("breakout") else 0.0
        volume_bonus = 0.06 if curve.get("volume_ratio", 0.0) >= 1.6 else 0.0
        wick_penalty = -0.08 if curve.get("upper_wick_ratio", 0.0) > 0.55 else 0.0
        psychology_penalty = -0.16 if psychology.get("stance") == "risk_off" else (-0.06 if psychology.get("stance") == "caution" else 0.0)
        macro_penalty = -0.14 if macro.get("stance") == "risk_off" else (-0.05 if macro.get("stance") == "caution" else 0.0)
        memory_adjustment = max(-0.12, min(0.12, float(memory.get("score", 0.0) or 0.0) * 0.12))
        prediction_adjustment = max(-0.10, min(0.10, float(prediction.get("score", 0.0) or 0.0) * 0.10))
        score = round(
            float(signal.confidence)
            + (0.12 if trend_ok else -0.08)
            + (0.08 if momentum_3 > 0 else -0.05)
            + catalyst_bonus
            + breakout_bonus
            + volume_bonus
            + wick_penalty
            + psychology_penalty
            + macro_penalty
            + memory_adjustment
            + prediction_adjustment,
            3,
        )
        allowed = bool(
            volatility_ok
            and score >= 0.68
            and (trend_ok or catalyst.get("decision_stance") == "positive")
            and psychology.get("stance", "normal") != "risk_off"
            and macro.get("stance", "normal") != "risk_off"
            and not (memory.get("stance") == "bearish" and score < 0.78)
            and not (prediction.get("reliable") and prediction.get("stance") == "bearish" and score < 0.82)
        )
        return {
            "allowed": allowed,
            "reason": "quality_confirmed" if allowed else "weak_momentum_or_volatility",
            "score": score,
            "signal_confidence": round(float(signal.confidence), 3),
            "trend_ok": trend_ok,
            "momentum_3_pct": momentum_3,
            "atr_pct": atr_pct,
            "volatility_ok": volatility_ok,
            "catalyst": catalyst.get("decision_stance", "neutral"),
            "curve": curve,
            "investor_behavior": psychology,
            "macro_events": macro,
            "market_memory": memory,
            "supervised_prediction": prediction,
        }

    @staticmethod
    def _scout_fallback_signal(df: pd.DataFrame, catalyst: dict | None = None) -> Signal:
        catalyst = catalyst or {}
        curve = curve_snapshot(df)
        if not curve.get("available"):
            return Signal("hold", 0.0, "scout unavailable: insufficient curve history", curve)
        catalyst_positive = catalyst.get("decision_stance") == "positive"
        short_trend_ok = curve.get("trend_up") or float(curve.get("price", 0.0)) >= float(curve.get("ema20", float("inf")))
        momentum_burst = bool(
            curve.get("momentum_3_pct", 0.0) >= 1.2
            and curve.get("volume_ratio", 0.0) >= 0.5
            and curve.get("upper_wick_ratio", 1.0) <= 0.4
        )
        allowed = bool(
            short_trend_ok
            and curve.get("momentum_3_pct", 0.0) >= 0.25
            and 0.18 <= curve.get("atr_pct", 0.0) <= 9.0
            and curve.get("upper_wick_ratio", 1.0) <= 0.58
            and (curve.get("volume_ratio", 0.0) >= 1.05 or catalyst_positive or momentum_burst)
        )
        confidence = 0.6
        confidence += 0.06 if curve.get("momentum_3_pct", 0.0) >= 0.5 else 0.03
        confidence += 0.05 if curve.get("volume_ratio", 0.0) >= 1.25 else 0.02
        confidence += 0.04 if curve.get("breakout") else 0.0
        confidence += 0.05 if catalyst_positive else 0.0
        if allowed:
            return Signal("buy", round(min(confidence, 0.78), 3), "fast scout momentum alignment", {"curve": curve})
        return Signal("hold", round(confidence, 3), "fast scout setup not aligned", {"curve": curve})

    @staticmethod
    def _consecutive_losses(trades: list[Trade]) -> int:
        losses = 0
        for trade in sorted(trades, key=lambda row: row.closed_at or datetime.min, reverse=True):
            if trade.pnl < 0:
                losses += 1
            else:
                break
        return losses

    @staticmethod
    def _exit_reason(trade: Trade, price: float, config: dict) -> str | None:
        metadata = dict(trade.metadata_json or {})
        config = {**config, **(metadata.get("risk") or {})}
        high = max(float(metadata.get("high_watermark", trade.entry_price)), price)
        metadata["high_watermark"] = high
        trade.metadata_json = metadata

        stop = trade.entry_price * (1 - float(config.get("stop_loss_pct", 2.0)) / 100)
        if high >= trade.entry_price * (1 + float(config.get("break_even_pct", 1.5)) / 100):
            stop = max(stop, trade.entry_price)
        trailing_pct = float(config.get("trailing_stop_pct", 1.0))
        if trailing_pct > 0:
            stop = max(stop, high * (1 - trailing_pct / 100))
        take_profit = trade.entry_price * (1 + float(config.get("take_profit_pct", 4.0)) / 100)
        if price >= take_profit:
            return "take_profit"
        if price <= stop:
            return "trailing_stop" if high > trade.entry_price else "stop_loss"
        return None

    @classmethod
    def _adaptive_exit_reason(cls, trade: Trade, price: float, df: pd.DataFrame, config: dict, catalyst: dict | None = None) -> str | None:
        standard_exit = cls._exit_reason(trade, price, config)
        if standard_exit:
            return standard_exit
        catalyst = catalyst or {}
        if catalyst.get("decision_stance") == "negative":
            return "negative_catalyst"
        if len(df) < 25 or not trade.entry_price:
            return None
        gain_pct = (price / trade.entry_price - 1) * 100
        max_hold_hours = int((trade.metadata_json or {}).get("risk", {}).get("max_hold_hours") or cls._max_hold_hours(trade.strategy))
        if trade.opened_at:
            age_hours = (datetime.utcnow() - trade.opened_at).total_seconds() / 3600
            if age_hours >= max_hold_hours and gain_pct < 0.4:
                return "time_stop"
            if age_hours >= max_hold_hours * 1.5 and gain_pct > 0:
                return "stale_profit_rotation"
        if gain_pct < 0.8:
            return None
        close = df["close"].astype(float)
        ema20 = float(ema(close, 20).iloc[-1])
        last = float(close.iloc[-1])
        previous = float(close.iloc[-2])
        fading = last < previous and price < ema20
        metadata = dict(trade.metadata_json or {})
        high = float(metadata.get("high_watermark", trade.entry_price))
        pullback_from_high_pct = (price / high - 1) * 100 if high else 0.0
        if fading or pullback_from_high_pct <= -0.7:
            return "profit_protection"
        return None


trading_engine = TradingEngine()
