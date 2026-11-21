"""当足で生き残った高値を次足の保護ラインへ反映する。"""

from dataclasses import dataclass, field

from ...models import Bar, ExitDecision, Position
from ...validation import number
from .fixed_stop import FixedStop


@dataclass
class TrailingStop:
    stop_loss_pct: float = 0.5
    trailing_stop_pct: float = 0.5
    close_at_market_end: bool = True
    _position: Position | None = field(default=None, init=False, repr=False)
    _line: float = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        FixedStop(self.stop_loss_pct, close_at_market_end=self.close_at_market_end)
        number(
            self.trailing_stop_pct, "trailing_stop_pct", positive=True, maximum=99.999
        )

    def on_bar(self, bar: Bar, position: Position) -> ExitDecision | None:
        if self._position is not position:
            self._position = position
            self._line = position.entry_price * (1 - self.stop_loss_pct / 100)
        if bar.open <= self._line:
            return ExitDecision(bar.open, "trailing_stop", bar.start)
        if bar.low <= self._line:
            return ExitDecision(self._line, "trailing_stop", bar.end)
        self._line = max(self._line, bar.high * (1 - self.trailing_stop_pct / 100))
        return None
