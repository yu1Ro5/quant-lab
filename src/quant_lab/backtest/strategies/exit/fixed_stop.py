"""実約定価格を基準とする固定率の保護注文。"""

from dataclasses import dataclass

from ...models import Bar, ExitDecision, Position
from ...validation import ConfigError, number


@dataclass(frozen=True)
class FixedStop:
    stop_loss_pct: float = 0.5
    take_profit_pct: float = 1.0
    close_at_market_end: bool = True

    def __post_init__(self) -> None:
        number(self.stop_loss_pct, "stop_loss_pct", positive=True, maximum=99.999)
        number(self.take_profit_pct, "take_profit_pct", positive=True)
        if self.close_at_market_end is not True:
            raise ConfigError("close_at_market_end: 当日決済のためtrueのみ指定可能です")

    def on_bar(self, bar: Bar, position: Position) -> ExitDecision | None:
        stop = position.entry_price * (1 - self.stop_loss_pct / 100)
        target = position.entry_price * (1 + self.take_profit_pct / 100)
        if bar.open <= stop:
            return ExitDecision(bar.open, "stop_loss", bar.start)
        # 寄付でターゲットを超えた場合でも、同足ストップ到達なら保守的に損切り。
        if bar.low <= stop:
            return ExitDecision(stop, "stop_loss", bar.end)
        if bar.open >= target:
            return ExitDecision(bar.open, "take_profit", bar.start)
        if bar.high >= target:
            return ExitDecision(target, "take_profit", bar.end)
        return None
