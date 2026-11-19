"""entry戦略の差し替えインターフェース。"""

from typing import Protocol

from ...models import Bar, EntrySignal, Session


class EntryStrategy(Protocol):
    def on_close(
        self, history: tuple[Bar, ...], session: Session
    ) -> EntrySignal | None:
        """確定済みの当日履歴だけから次足成行シグナルを返す。

        session.barsは空。未来足や次足価格は参照できない。
        """
        ...
