"""確定した寄り付きレンジを終値で突破したときの買い。"""

from dataclasses import dataclass

import pandas as pd

from ...models import Bar, EntrySignal, Session
from ...validation import ConfigError, integer


@dataclass(frozen=True)
class Orb:
    opening_range_minutes: int = 30

    def __post_init__(self) -> None:
        integer(
            self.opening_range_minutes, "opening_range_minutes", minimum=5, maximum=180
        )
        if self.opening_range_minutes % 5:
            raise ConfigError("opening_range_minutes: 5分の倍数が必要です")

    def on_close(
        self, history: tuple[Bar, ...], session: Session
    ) -> EntrySignal | None:
        count = self.opening_range_minutes // 5
        cutoff = session.open + pd.Timedelta(minutes=self.opening_range_minutes)
        if len(history) <= count or history[-1].start < cutoff:
            return None
        opening = history[:count]
        if tuple(b.start for b in opening) != tuple(
            session.open + pd.Timedelta(minutes=i * 5) for i in range(count)
        ):
            return None
        high = max(b.high for b in opening)
        low = min(b.low for b in opening)
        if history[-1].close > high:
            return EntrySignal({"orb_high": high, "orb_low": low})
        return None
