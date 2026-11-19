"""設定と拡張戦略が共有する厳格な値検証。"""

import math
from typing import Any


class ConfigError(ValueError):
    """実験設定が不正。"""


def number(
    value: Any,
    name: str,
    *,
    minimum: float = 0,
    maximum: float | None = None,
    positive: bool = False,
) -> float:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ConfigError(f"{name}: 有限な数値が必要です")
    if (
        value < minimum
        or (positive and value == minimum)
        or (maximum is not None and value > maximum)
    ):
        raise ConfigError(f"{name}: 許容範囲外です ({value})")
    return float(value)


def integer(
    value: Any, name: str, *, minimum: int = 1, maximum: int = 1_000_000
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f"{name}: {minimum}〜{maximum}の整数が必要です")
    return value


def text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name}: 空でない文字列が必要です")
    return value


def mapping(
    value: Any, name: str, required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ConfigError(f"{name}: キーが文字列のmappingが必要です")
    missing = required - value.keys()
    unknown = value.keys() - required - (optional or set())
    if missing or unknown:
        raise ConfigError(
            f"{name}: 必須キー不足={sorted(missing)}, 未知キー={sorted(unknown)}"
        )
    return value
