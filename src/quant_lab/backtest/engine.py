"""ローソク足を日時順に処理するバックテストエンジン。"""

import pandas as pd

from .broker import Broker, Trade
from .strategy import Signal, Strategy


class BacktestEngine:
    """終値で判断し、次の始値でStrategyとBrokerをつないで売買する。"""

    def __init__(self, strategy: Strategy, broker: Broker) -> None:
        self.strategy = strategy
        self.broker = broker

    def run(self, data: pd.DataFrame) -> list[Trade]:
        self.broker.reset()
        if data.empty:
            return []

        pending_signal = Signal.HOLD
        previous_bar: pd.Series | None = None
        final_bar: pd.Series | None = None

        for position, (_, current_bar) in enumerate(data.iterrows()):
            timestamp = pd.Timestamp(current_bar["datetime"])
            open_price = float(current_bar["open"])

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
                pd.Timestamp(final_bar["datetime"]), float(final_bar["close"])
            )
        return list(self.broker.trades)
