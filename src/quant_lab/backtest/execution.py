"""外部接続を持たない現物・買い専用の仮想約定と現金管理。"""

from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

from .broker_fees import Fees, webull_fees
from .config import ExecutionConfig
from .models import CompletedTrade, EntrySignal, Position
from .validation import integer, number


@dataclass
class SimulationBroker:
    cash: float
    costs: ExecutionConfig
    position: Position | None = None
    _cash: Decimal = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._cash = Decimal(str(self.cash))

    def _price(self, reference: float, *, buy: bool) -> Decimal:
        number(reference, "reference_price", positive=True)
        impact = (
            Decimal(str(self.costs.slippage_bps_per_side))
            + Decimal(str(self.costs.spread_bps_per_side))
        ) / 10000
        return Decimal(str(reference)) * (1 + impact if buy else 1 - impact)

    def _fees(self, price: Decimal, shares: int, *, buy: bool) -> Fees:
        if self.costs.cost_model == "webull_jp_us_equity":
            return webull_fees(price, shares, buy=buy, route=self.costs.cat_route)
        return Fees(commission=Decimal(str(self.costs.commission_usd_per_side)))

    def fill_price(self, reference: float, *, buy: bool) -> float:
        return float(self._price(reference, buy=buy))

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
        exact_price = self._price(reference, buy=True)
        price = float(exact_price)
        fees = self._fees(exact_price, shares, buy=True)
        debit = exact_price * shares + fees.total
        if debit > self._cash:
            return False
        self._cash -= debit
        self.cash = float(self._cash)
        self.position = Position(
            signal_time, time, shares, reference, price, signal, float(fees.total)
        )
        return True

    def sell(
        self, time: pd.Timestamp, reference: float, reason: str, day: str, symbol: str
    ) -> CompletedTrade:
        if self.position is None:
            raise RuntimeError("ポジションがありません")
        p = self.position
        exact_price = self._price(reference, buy=False)
        price = float(exact_price)
        entry_fees = self._fees(
            self._price(p.reference_price, buy=True), p.shares, buy=True
        )
        exit_fees = self._fees(exact_price, p.shares, buy=False)
        commission = float(entry_fees.commission + exit_fees.commission)
        sec = float(entry_fees.sec + exit_fees.sec)
        finra = float(entry_fees.finra + exit_fees.finra)
        cat = float(entry_fees.cat + exit_fees.cat)
        notional = (p.reference_price + reference) * p.shares
        slippage = notional * self.costs.slippage_bps_per_side / 10000
        spread = notional * self.costs.spread_bps_per_side / 10000
        gross = (reference - p.reference_price) * p.shares
        total = commission + sec + finra + cat + slippage + spread
        self._cash += exact_price * p.shares - exit_fees.total
        self.cash = float(self._cash)
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
            sec,
            finra,
            cat,
        )
