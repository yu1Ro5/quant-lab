"""sizing戦略の差し替えインターフェース。"""

from typing import Protocol

from ...models import EntrySignal


class SizingStrategy(Protocol):
    def size(self, cash: float, price: float, signal: EntrySignal) -> int:
        """既知の現金・実約定予定価格から要求株数を返す。0は取引見送り。"""
        ...
