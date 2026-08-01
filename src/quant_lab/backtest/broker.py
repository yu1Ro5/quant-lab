"""Brokerの抽象インターフェースとバックテスト用のメモリ内実装。"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Trade:
    """購入から売却まで完了した1回分の取引。"""

    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    profit_loss: float


class Broker(ABC):
    """将来ペーパートレードや実売買へ差し替えるためのインターフェース。"""

    @property
    @abstractmethod
    def has_position(self) -> bool:
        """現在1株を保有しているか返す。"""

    @property
    @abstractmethod
    def trades(self) -> tuple[Trade, ...]:
        """購入から売却まで完了した取引を返す。"""

    @abstractmethod
    def reset(self) -> None:
        """保有状態と完了した取引を初期状態へ戻す。"""

    @abstractmethod
    def buy(self, timestamp: pd.Timestamp, price: float) -> None:
        """指定日時と価格で1株を購入する。"""

    @abstractmethod
    def sell(self, timestamp: pd.Timestamp, price: float) -> None:
        """保有中の1株を指定日時と価格で売却する。"""

    @abstractmethod
    def close_position(self, timestamp: pd.Timestamp, price: float) -> None:
        """株を保有していれば売却し、保有していなければ何もしない。"""


class DummyBroker(Broker):
    """外部へ注文を送らず、1株分の売買を内部に記録するBroker。"""

    def __init__(self) -> None:
        self._entry_time: pd.Timestamp | None = None
        self._entry_price: float | None = None
        self._trades: list[Trade] = []

    @property
    def has_position(self) -> bool:
        return self._entry_time is not None

    @property
    def trades(self) -> tuple[Trade, ...]:
        return tuple(self._trades)

    def reset(self) -> None:
        self._entry_time = None
        self._entry_price = None
        self._trades.clear()

    def buy(self, timestamp: pd.Timestamp, price: float) -> None:
        if self.has_position:
            raise RuntimeError("すでに1株を保有しているため追加購入できません")
        self._validate_price(price)
        self._entry_time = timestamp
        self._entry_price = float(price)

    def sell(self, timestamp: pd.Timestamp, price: float) -> None:
        if (
            not self.has_position
            or self._entry_price is None
            or self._entry_time is None
        ):
            raise RuntimeError("保有している株がないため売却できません")
        self._validate_price(price)
        exit_price = float(price)
        self._trades.append(
            Trade(
                entry_time=self._entry_time,
                entry_price=self._entry_price,
                exit_time=timestamp,
                exit_price=exit_price,
                profit_loss=exit_price - self._entry_price,
            )
        )
        self._entry_time = None
        self._entry_price = None

    def close_position(self, timestamp: pd.Timestamp, price: float) -> None:
        if self.has_position:
            self.sell(timestamp, price)

    @staticmethod
    def _validate_price(price: float) -> None:
        if not math.isfinite(price) or price <= 0:
            raise ValueError("売買価格は0より大きい有限値にしてください")
