"""exit戦略の公開インターフェース。"""

from .base import ExitStrategy
from .fixed_stop import FixedStop

__all__ = ["ExitStrategy", "FixedStop"]
