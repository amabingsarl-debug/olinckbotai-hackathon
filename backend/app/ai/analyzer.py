from collections import defaultdict


class PerformanceAnalyzer:
    def analyze(self, trades: list[dict], metrics: dict) -> dict:
        by_strategy: dict[str, list[float]] = defaultdict(list)
        by_symbol: dict[str, list[float]] = defaultdict(list)
        by_hour: dict[int, list[float]] = defaultdict(list)
        for trade in trades:
            pnl = float(trade.get("pnl", 0))
            by_strategy[trade.get("strategy", "unknown")].append(pnl)
            by_symbol[trade.get("symbol", "unknown")].append(pnl)
            hour = trade.get("hour")
            if hour is not None:
                by_hour[int(hour)].append(pnl)

        weak = [name for name, pnls in by_strategy.items() if sum(pnls) < 0 or (sum(p > 0 for p in pnls) / len(pnls)) < 0.45]
        best_symbols = sorted(by_symbol, key=lambda s: sum(by_symbol[s]), reverse=True)[:5]
        best_hours = sorted(by_hour, key=lambda h: sum(by_hour[h]), reverse=True)[:5]
        return {
            "summary": self._summary(metrics),
            "bad_strategies": weak,
            "suggestions": self._suggestions(metrics, weak),
            "best_symbols": best_symbols,
            "best_hours": best_hours,
            "volatility_note": "Use ATR and drawdown filters during volatility spikes.",
        }

    @staticmethod
    def _summary(metrics: dict) -> str:
        return f"Profit {metrics.get('profit', 0)}, drawdown {metrics.get('drawdown', 0)}%, win rate {metrics.get('win_rate', 0)}%."

    @staticmethod
    def _suggestions(metrics: dict, weak: list[str]) -> list[str]:
        suggestions = []
        if metrics.get("drawdown", 0) > 10:
            suggestions.append("Reduce max capital per trade and tighten daily loss limits.")
        if metrics.get("profit_factor", 0) < 1.2:
            suggestions.append("Disable or retune strategies with profit factor below 1.2.")
        for strategy in weak:
            suggestions.append(f"Review parameters or disable {strategy}.")
        return suggestions or ["Current performance is acceptable; keep monitoring live slippage and fees."]
