"""固定株数による現物購入。余力の最終判定はBrokerが行う。"""

from dataclasses import dataclass

from ...models import EntrySignal
from ...validation import integer


@dataclass(frozen=True)
class FixedShares:
    shares: int = 1

    def __post_init__(self) -> None:
        integer(self.shares, "shares")

    def size(self, cash: float, price: float, signal: EntrySignal) -> int:
        return self.shares
