"""Sequential backtest engine."""

import pandas as pd

from .broker import Broker, Trade
from .strategy import Signal, Strategy


class BacktestEngine:
    """Connect a strategy to a broker while avoiding same-close execution."""

    def __init__(self, strategy: Strategy, broker: Broker) -> None:
        self.strategy = strategy
        self.broker = broker

    def run(self, data: pd.DataFrame) -> list[Trade]:
        if data.empty:
            return []

        pending_signal = Signal.HOLD
        previous_bar: pd.Series | None = None
        final_bar: pd.Series | None = None

        for position, (_, current_bar) in enumerate(data.iterrows()):
            timestamp = pd.Timestamp(current_bar["Datetime"])
            open_price = float(current_bar["Open"])

            if pending_signal is Signal.BUY:
                self.broker.buy(timestamp, open_price)
            elif pending_signal is Signal.SELL:
                self.broker.sell(timestamp, open_price)

            if previous_bar is not None and position < len(data) - 1:
                pending_signal = self.strategy.generate_signal(
                    previous_bar, current_bar, self.broker.has_position
                )
            else:
                pending_signal = Signal.HOLD

            previous_bar = current_bar
            final_bar = current_bar

        if final_bar is not None:
            self.broker.close_position(
                pd.Timestamp(final_bar["Datetime"]), float(final_bar["Close"])
            )
        return list(self.broker.trades)
