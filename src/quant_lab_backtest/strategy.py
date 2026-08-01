"""Replaceable strategy interface and a minimal breakout example."""

from abc import ABC, abstractmethod
from enum import Enum, auto

import pandas as pd


class Signal(Enum):
    """Action requested by a strategy after a candle has closed."""

    BUY = auto()
    SELL = auto()
    HOLD = auto()


class Strategy(ABC):
    """Interface for a strategy that only decides the next action."""

    @abstractmethod
    def generate_signal(
        self,
        previous_bar: pd.Series,
        current_bar: pd.Series,
        has_position: bool,
    ) -> Signal:
        """Return the action to execute at the next candle's open."""


class BreakoutStrategy(Strategy):
    """Buy above the prior high and sell below the prior low."""

    def generate_signal(
        self,
        previous_bar: pd.Series,
        current_bar: pd.Series,
        has_position: bool,
    ) -> Signal:
        current_close = float(current_bar["Close"])
        if not has_position and current_close > float(previous_bar["High"]):
            return Signal.BUY
        if has_position and current_close < float(previous_bar["Low"]):
            return Signal.SELL
        return Signal.HOLD
