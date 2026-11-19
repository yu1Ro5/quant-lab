"""安全な明示的レジストリ。新戦略はここへ登録しEngineを変更しない。"""

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from .strategies import FixedShares, FixedStop, Orb
from .strategies.entry import EntryStrategy
from .strategies.exit import ExitStrategy
from .strategies.sizing import SizingStrategy
from .validation import ConfigError

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self) -> None:
        self.factories: dict[str, Callable[..., T]] = {}

    def register(self, name: str, factory: Callable[..., T]) -> None:
        if name in self.factories:
            raise ConfigError(f"strategy登録名が重複しています: {name}")
        self.factories[name] = factory

    def create(self, name: str, params: dict[str, Any]) -> T:
        if name not in self.factories:
            raise ConfigError(f"未知のstrategy: {name}; 候補={sorted(self.factories)}")
        try:
            return self.factories[name](**params)
        except (TypeError, ValueError) as error:
            raise ConfigError(f"{name}.params: {error}") from error


ENTRY: Registry[EntryStrategy] = Registry()
EXIT: Registry[ExitStrategy] = Registry()
SIZING: Registry[SizingStrategy] = Registry()
ENTRY.register("orb", Orb)
EXIT.register("fixed_stop", FixedStop)
SIZING.register("fixed_shares", FixedShares)
