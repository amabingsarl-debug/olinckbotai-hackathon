import numpy as np


def buy_and_hold_portfolio(selected: list[dict], datasets: dict[str, dict], start_ms: int, initial_capital: float = 10_000) -> dict:
    if not selected:
        return {"return_pct": 0.0, "drawdown": 0.0, "final_capital": initial_capital}
    allocation_pct = min(20.0, 10.0 * len(selected)) / len(selected)
    fee_rate = 0.001
    frames = {}
    for row in selected:
        candles = [candle for candle in datasets[row["symbol"]]["candles"] if int(candle["timestamp"]) >= start_ms]
        if len(candles) < 2:
            continue
        frames[row["symbol"]] = {
            int(candle["timestamp"]): float(candle["close"])
            for candle in candles
        }
    if not frames:
        return {"return_pct": 0.0, "drawdown": 0.0, "final_capital": initial_capital}
    timeline = sorted(set().union(*(set(frame) for frame in frames.values())))
    positions = {}
    cash = initial_capital
    for symbol, frame in frames.items():
        first_timestamp = min(frame)
        stake = initial_capital * allocation_pct / 100
        entry_price = frame[first_timestamp]
        quantity = (stake * (1 - fee_rate)) / entry_price
        cash -= stake
        positions[symbol] = {"quantity": quantity, "last_price": entry_price}
    equity_curve = []
    for timestamp in timeline:
        equity = cash
        for symbol, position in positions.items():
            price = frames[symbol].get(timestamp, position["last_price"])
            position["last_price"] = price
            equity += position["quantity"] * price
        equity_curve.append(equity)
    equity = np.array(equity_curve, dtype=float)
    drawdown = equity / np.maximum.accumulate(equity) - 1
    final_capital = float(equity[-1])
    return {
        "allocation_pct_per_asset": round(allocation_pct, 2),
        "return_pct": round((final_capital / initial_capital - 1) * 100, 2),
        "drawdown": round(abs(float(drawdown.min())) * 100, 2),
        "final_capital": round(final_capital, 2),
    }


def risk_adjusted_score(return_pct: float, drawdown_pct: float) -> float:
    return round(return_pct / max(drawdown_pct, 0.1), 3)
