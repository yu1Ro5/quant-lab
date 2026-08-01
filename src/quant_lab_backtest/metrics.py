"""Backtest result aggregation."""

from dataclasses import dataclass
from typing import Iterable

from .broker import Trade


@dataclass(frozen=True)
class BacktestMetrics:
    """Minimum metrics required by the MVP."""

    total_trades: int
    win_rate: float
    total_profit_loss: float


def calculate_metrics(trades: Iterable[Trade]) -> BacktestMetrics:
    """Aggregate completed trades."""
    completed = list(trades)
    total_trades = len(completed)
    wins = sum(trade.profit_loss > 0 for trade in completed)
    return BacktestMetrics(
        total_trades=total_trades,
        win_rate=wins / total_trades if total_trades else 0.0,
        total_profit_loss=sum(trade.profit_loss for trade in completed),
    )
