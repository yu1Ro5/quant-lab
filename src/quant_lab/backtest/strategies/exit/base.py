"""exit戦略の差し替えインターフェース。"""

from typing import Protocol

from ...models import Bar, ExitDecision, Position


class ExitStrategy(Protocol):
    def on_bar(self, bar: Bar, position: Position) -> ExitDecision | None:
        """足開始前から有効な保護注文をOHLCで評価する。

        当足の終値を見て同じ足の過去価格へ戻って約定させてはならない。
        NoneでもEngineが立会最終足終値で当日決済する。
        """
        ...
