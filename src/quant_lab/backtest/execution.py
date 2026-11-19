"""外部接続を持たない現物・買い専用の仮想約定と現金管理。"""

from dataclasses import dataclass

import pandas as pd

from .config import ExecutionConfig
from .models import CompletedTrade, EntrySignal, Position
from .validation import integer, number


@dataclass
class SimulationBroker:
    cash: float
    costs: ExecutionConfig
    position: Position | None = None

    def fill_price(self, reference: float, *, buy: bool) -> float:
        number(reference, "reference_price", positive=True)
        impact = (
            self.costs.slippage_bps_per_side + self.costs.spread_bps_per_side
        ) / 10000
        return reference * (1 + impact if buy else 1 - impact)

    def buy(
        self,
        time: pd.Timestamp,
        reference: float,
        shares: int,
        signal_time: pd.Timestamp,
        signal: EntrySignal,
    ) -> bool:
        integer(shares, "sizing.shares", minimum=0)
        if self.position is not None:
            raise RuntimeError("既にポジションがあります")
        if not shares:
            return False
        price = self.fill_price(reference, buy=True)
        debit = price * shares + self.costs.commission_usd_per_side
        if debit > self.cash:
            return False
        self.cash -= debit
        self.position = Position(signal_time, time, shares, reference, price, signal)
        return True

    def sell(
        self, time: pd.Timestamp, reference: float, reason: str, day: str, symbol: str
    ) -> CompletedTrade:
        if self.position is None:
            raise RuntimeError("ポジションがありません")
        p = self.position
        price = self.fill_price(reference, buy=False)
        commission = 2 * self.costs.commission_usd_per_side
        notional = (p.reference_price + reference) * p.shares
        slippage = notional * self.costs.slippage_bps_per_side / 10000
        spread = notional * self.costs.spread_bps_per_side / 10000
        gross = (reference - p.reference_price) * p.shares
        total = commission + slippage + spread
        self.cash += price * p.shares - self.costs.commission_usd_per_side
        self.position = None
        return CompletedTrade(
            day,
            symbol,
            p.signal_time,
            p.entry_time,
            time,
            p.shares,
            p.reference_price,
            reference,
            p.entry_price,
            price,
            reason,
            p.signal.details.get("orb_high"),
            p.signal.details.get("orb_low"),
            gross,
            commission,
            slippage,
            spread,
            total,
            gross - total,
        )
