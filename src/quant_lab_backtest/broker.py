"""Broker abstraction and an in-memory implementation for backtests."""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Trade:
    """One completed long trade."""

    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    profit_loss: float


class Broker(ABC):
    """Interface that can later be implemented by a paper or live broker."""

    @property
    @abstractmethod
    def has_position(self) -> bool:
        """Return whether one long position is currently open."""

    @property
    @abstractmethod
    def trades(self) -> tuple[Trade, ...]:
        """Return completed trades."""

    @abstractmethod
    def buy(self, timestamp: pd.Timestamp, price: float) -> None:
        """Open one long position."""

    @abstractmethod
    def sell(self, timestamp: pd.Timestamp, price: float) -> None:
        """Close the current long position."""

    @abstractmethod
    def close_position(self, timestamp: pd.Timestamp, price: float) -> None:
        """Close an open position, doing nothing when already flat."""


class DummyBroker(Broker):
    """A broker that records one-share trades without sending orders."""

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

    def buy(self, timestamp: pd.Timestamp, price: float) -> None:
        if self.has_position:
            raise RuntimeError("すでに1株を保有しているため追加購入できません")
        self._validate_price(price)
        self._entry_time = timestamp
        self._entry_price = float(price)

    def sell(self, timestamp: pd.Timestamp, price: float) -> None:
        if not self.has_position or self._entry_price is None or self._entry_time is None:
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
