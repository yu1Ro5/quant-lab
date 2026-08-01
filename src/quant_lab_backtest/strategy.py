"""差し替え可能な戦略インターフェースと最小限の高値突破戦略。"""

from abc import ABC, abstractmethod
from enum import Enum, auto

import pandas as pd


class Signal(Enum):
    """ローソク足の終了後に戦略が決める次回の動作。"""

    BUY = auto()
    SELL = auto()
    HOLD = auto()


class Strategy(ABC):
    """次に行う売買だけを判断する戦略インターフェース。"""

    @abstractmethod
    def generate_signal(
        self,
        previous_bar: pd.Series,
        current_bar: pd.Series,
        has_position: bool,
    ) -> Signal:
        """次のローソク足の始値で実行する動作を返す。"""


class BreakoutStrategy(Strategy):
    """終値が前回高値を上回れば買い、前回安値を下回れば売る戦略。"""

    def generate_signal(
        self,
        previous_bar: pd.Series,
        current_bar: pd.Series,
        has_position: bool,
    ) -> Signal:
        current_close = float(current_bar["close"])
        if not has_position and current_close > float(previous_bar["high"]):
            return Signal.BUY
        if has_position and current_close < float(previous_bar["low"]):
            return Signal.SELL
        return Signal.HOLD
