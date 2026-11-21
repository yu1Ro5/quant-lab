"""予定時刻以降の最初の足始値で決済。寄付の損切りのみ先行。"""

import re
from dataclasses import dataclass

import pandas as pd

from ...models import Bar, ExitDecision, Position
from ...validation import ConfigError, integer
from .fixed_stop import FixedStop


@dataclass(frozen=True)
class TimeExit:
    stop_loss_pct: float = 0.5
    max_holding_minutes: int | None = None
    exit_time_et: str | None = None
    close_at_market_end: bool = True

    def __post_init__(self) -> None:
        FixedStop(self.stop_loss_pct, close_at_market_end=self.close_at_market_end)
        if (self.max_holding_minutes is None) == (self.exit_time_et is None):
            raise ConfigError("max_holding_minutes/exit_time_etの一方だけが必要です")
        if self.max_holding_minutes is not None:
            integer(self.max_holding_minutes, "max_holding_minutes")
            if self.max_holding_minutes <= 0 or self.max_holding_minutes % 5:
                raise ConfigError("保有時間は正の5分単位が必要です")
        if self.exit_time_et is not None:
            if not isinstance(self.exit_time_et, str) or not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5][05]", self.exit_time_et
            ):
                raise ConfigError("exit_time_etは5分境界のHH:MMが必要です")

    def on_bar(self, bar: Bar, position: Position) -> ExitDecision | None:
        stop = position.entry_price * (1 - self.stop_loss_pct / 100)
        if bar.open <= stop:
            return ExitDecision(bar.open, "stop_loss", bar.start)
        if self.max_holding_minutes is not None:
            deadline = position.entry_time + pd.Timedelta(
                minutes=self.max_holding_minutes
            )
        else:
            local = position.entry_time.tz_convert("America/New_York")
            hour, minute = map(int, str(self.exit_time_et).split(":"))
            deadline = local.normalize() + pd.Timedelta(hours=hour, minutes=minute)
        if bar.start >= deadline:
            return ExitDecision(bar.open, "time_exit", bar.start)
        if bar.low <= stop:
            return ExitDecision(stop, "stop_loss", bar.end)
        return None
