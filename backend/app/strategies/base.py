from dataclasses import dataclass
from typing import Literal

import pandas as pd

SignalAction = Literal["buy", "sell", "hold"]


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    confidence: float
    reason: str
    metadata: dict


class Strategy:
    name = "base"

    def generate(self, candles: pd.DataFrame, parameters: dict | None = None) -> Signal:
        raise NotImplementedError


def ensure_indicators_frame(candles: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Missing candle columns: {', '.join(sorted(missing))}")
    return candles.copy().reset_index(drop=True)
