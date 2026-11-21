"""YAML実験の読込と厳密な型・必須項目・範囲検証。"""

import re
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .registry import ENTRY, EXIT, SIZING, Registry
from .validation import ConfigError, integer, mapping, number, text

__all__ = ["ConfigError", "ExperimentConfig", "load_config", "parse_config"]


@dataclass(frozen=True)
class DataConfig:
    symbol: str
    path: str
    timeframe: str
    timezone: str
    bar_timestamp: str
    adjustment: str


@dataclass(frozen=True)
class ComponentConfig:
    strategy: str
    params: dict[str, Any]


@dataclass(frozen=True)
class ExecutionConfig:
    commission_usd_per_side: float
    slippage_bps_per_side: float
    spread_bps_per_side: float


@dataclass(frozen=True)
class QualityConfig:
    mode: str = "formal"
    evidence_path: str | None = None
    daily_reference_path: str | None = None
    daily_metadata_path: str | None = None


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    data: DataConfig
    start: str
    end: str
    entry: ComponentConfig
    exit: ComponentConfig
    sizing: ComponentConfig
    execution: ExecutionConfig
    initial_capital: float
    max_trades_per_day: int
    output_directory: str
    quality: QualityConfig = QualityConfig()

    def snapshot(self) -> dict[str, Any]:
        return dict(
            experiment={"name": self.name},
            data=asdict(self.data),
            period={"start": self.start, "end": self.end},
            entry=asdict(self.entry),
            exit=asdict(self.exit),
            sizing=asdict(self.sizing),
            execution=asdict(self.execution),
            portfolio={
                "initial_capital_usd": self.initial_capital,
                "max_trades_per_day": self.max_trades_per_day,
            },
            output={"directory": self.output_directory},
            quality=asdict(self.quality),
        )


def parse_config(raw: Any) -> ExperimentConfig:
    root = mapping(
        raw,
        "config",
        {
            "experiment",
            "data",
            "period",
            "entry",
            "exit",
            "sizing",
            "execution",
            "portfolio",
        },
        {"output", "quality"},
    )
    experiment = mapping(root["experiment"], "experiment", {"name"})
    name = text(experiment["name"], "experiment.name")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
        raise ConfigError(
            "experiment.name: 英数字・_・-のみ100文字以内で指定してください"
        )
    data = mapping(
        root["data"],
        "data",
        {"symbol", "path", "timeframe", "timezone", "bar_timestamp", "adjustment"},
    )
    for key, value in data.items():
        text(value, f"data.{key}")
    try:
        ZoneInfo(data["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(
            f"data.timezone: 不正なタイムゾーン: {data['timezone']}"
        ) from error
    if data["timeframe"] != "5m" or data["bar_timestamp"] not in ("start", "end"):
        raise ConfigError(
            "data: timeframe=5m、bar_timestamp=start/endを指定してください"
        )
    if data["adjustment"] not in ("none", "forward", "backward"):
        raise ConfigError(
            "data.adjustment: none/forward/backwardを指定してください（再調整はしません）"
        )
    period = mapping(root["period"], "period", {"start", "end"})
    for key in ("start", "end"):
        value = text(period[key], f"period.{key}")
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("YYYY-MM-DDが必要です")
        except ValueError as error:
            raise ConfigError(
                f"period.{key}: YYYY-MM-DD形式の日付が必要です"
            ) from error
    if period["start"] > period["end"]:
        raise ConfigError("period: startはend以前にしてください")
    components = []
    registries: dict[str, Registry[Any]] = {
        "entry": ENTRY,
        "exit": EXIT,
        "sizing": SIZING,
    }
    for kind, registry in registries.items():
        item = mapping(root[kind], kind, {"strategy", "params"})
        strategy = text(item["strategy"], f"{kind}.strategy")
        params = item["params"]
        if not isinstance(params, dict) or not all(isinstance(k, str) for k in params):
            raise ConfigError(f"{kind}.params: mappingが必要です")
        component = registry.create(strategy, params)
        normalized = dict(params)
        if is_dataclass(component) and not isinstance(component, type):
            values = asdict(component)
            normalized = {f.name: values[f.name] for f in fields(component) if f.init}
        components.append(ComponentConfig(strategy, normalized))
    execution = mapping(
        root["execution"],
        "execution",
        {"commission_usd_per_side", "slippage_bps_per_side", "spread_bps_per_side"},
    )
    costs = {key: number(value, f"execution.{key}") for key, value in execution.items()}
    if costs["slippage_bps_per_side"] + costs["spread_bps_per_side"] >= 10_000:
        raise ConfigError(
            "execution: slippageとspreadの合計は10000bps未満にしてください"
        )
    portfolio = mapping(
        root["portfolio"], "portfolio", {"initial_capital_usd", "max_trades_per_day"}
    )
    capital = number(
        portfolio["initial_capital_usd"], "initial_capital_usd", positive=True
    )
    max_trades = integer(portfolio["max_trades_per_day"], "max_trades_per_day")
    output = mapping(
        root.get("output", {"directory": "results"}), "output", {"directory"}
    )
    quality = mapping(
        root.get("quality", {}),
        "quality",
        set(),
        {"mode", "evidence_path", "daily_reference_path", "daily_metadata_path"},
    )
    if quality.get("mode", "formal") not in ("formal", "diagnostic"):
        raise ConfigError("quality.mode: formal/diagnosticを指定してください")
    for key in ("evidence_path", "daily_reference_path", "daily_metadata_path"):
        if quality.get(key) is not None:
            text(quality[key], f"quality.{key}")
    if quality.get("daily_metadata_path") and not quality.get("daily_reference_path"):
        raise ConfigError(
            "quality.daily_metadata_pathにはdaily_reference_pathが必要です"
        )
    return ExperimentConfig(
        name,
        DataConfig(**data),
        period["start"],
        period["end"],
        components[0],
        components[1],
        components[2],
        ExecutionConfig(**costs),
        capital,
        max_trades,
        text(output["directory"], "output.directory"),
        QualityConfig(**quality),
    )


class UniqueKeyLoader(yaml.SafeLoader):
    """YAMLの重複キーによる意図しない上書きも拒否する。"""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
            raise ConfigError("YAML: キーは重複しない文字列で指定してください")
        return super().construct_mapping(node, deep=deep)


def load_config(path: str | Path) -> ExperimentConfig:
    try:
        raw = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        return parse_config(raw)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"YAMLを読み込めません: {error}") from error
