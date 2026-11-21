"""同じ入力の保存済みバイト列から、各費用条件でEngineを再実行する。"""

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .broker_fees import fee_model_metadata
from .comparison_inputs import snapshot_inputs, verify_inputs
from .config import UniqueKeyLoader, load_config, parse_config
from .cost_report import write_cost_reports
from .runner import run_experiment
from .validation import ConfigError, mapping, number, text


def break_even(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """実行済み点のゼロ/符号変化区間だけを報告し、直線外挿しない。"""
    exact = [r["name"] for r in rows if r["net_pnl"] == 0 and r["total_trades"] > 0]
    brackets = []
    for axis, fixed in [
        ("spread_bps_per_side", "slippage_bps_per_side"),
        ("slippage_bps_per_side", "spread_bps_per_side"),
    ]:
        for value in sorted({r[fixed] for r in rows}):
            points = sorted(
                (r for r in rows if r[fixed] == value), key=lambda r: r[axis]
            )
            for a, b in zip(points, points[1:], strict=False):
                if a["net_pnl"] * b["net_pnl"] < 0:
                    brackets.append(
                        dict(
                            axis=axis,
                            fixed_axis=fixed,
                            fixed_value=value,
                            lower=a[axis],
                            upper=b[axis],
                            scenarios=[a["name"], b["name"]],
                        )
                    )
    return dict(
        method="Engine再実行の観測点のみ。符号変化区間は推定候補であり連続性・単調性・閾値を保証しない。",
        exact_scenarios=exact,
        sign_change_intervals=brackets,
        status="候補あり"
        if exact or brackets
        else "比較範囲内で損益分岐点は見つからず",
        scope="片方のbpsを固定した観測点間のみ。取引ゼロは損益分岐点としない。",
    )


def run_cost_comparison(path: str | Path, *, diagnostic: bool = False) -> Path:
    path = Path(path).resolve()
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ConfigError(f"比較YAMLを読み込めません: {error}") from error
    root = mapping(
        raw, "comparison", {"base_config", "scenarios"}, {"output_directory"}
    )
    base_path = Path(text(root["base_config"], "base_config"))
    if not base_path.is_absolute():
        base_path = path.parent / base_path
    base = load_config(base_path)
    scenarios = root["scenarios"]
    if not isinstance(scenarios, list) or not scenarios:
        raise ConfigError("scenarios: 空でない配列が必要です")
    normalized: list[dict[str, Any]] = []
    names = set()
    for item in scenarios:
        item = mapping(
            item, "scenario", {"name", "spread_bps_per_side", "slippage_bps_per_side"}
        )
        name = text(item["name"], "scenario.name")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name) or name in names:
            raise ConfigError("scenario.name: 重複しない英数字・_・-の名前が必要です")
        names.add(name)
        spread = number(item["spread_bps_per_side"], "spread_bps_per_side")
        slip = number(item["slippage_bps_per_side"], "slippage_bps_per_side")
        if spread + slip >= 10000:
            raise ConfigError("spreadとslippageの合計は10000bps未満が必要です")
        normalized.append(
            dict(name=name, spread_bps_per_side=spread, slippage_bps_per_side=slip)
        )
    output = Path(
        text(root.get("output_directory", base.output_directory), "output_directory")
    ).resolve()
    directory = (
        output
        / f"cost_comparison_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:12]}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    try:
        base, artifacts = snapshot_inputs(base, directory)
        manifest: dict[str, Any] = dict(
            base_config_path=str(base_path.resolve()),
            base_config=base.snapshot(),
            comparison_config=root,
            artifacts=artifacts,
            scenarios=[],
            fee_model=dict(
                cost_model="webull_jp_us_equity",
                cat_route="exchange",
                fee_schedule="2026-11-21",
            ),
            assumptions=[
                "現在の料金で過去の戦略を実行したと仮定する試算。過去の実料金の再現ではない。",
                "USD決済・為替費用なし。CATは取引所経路を仮定。",
                "spreadはOHLC参照価格に対する片道実効負担。実測Bid/Askや中間値ではない。",
                "slippageはspreadとは別の追加不利価格差。即時全量約定モデル。",
            ],
        )
        manifest["fee_model"] = {**fee_model_metadata(), **manifest["fee_model"]}
        (directory / "comparison_config.yaml").write_text(
            yaml.safe_dump(root, allow_unicode=True), encoding="utf-8"
        )
    except Exception:
        (directory / "FAILED").touch()
        raise
    rows = []
    try:
        for scenario in normalized:
            costs = replace(
                base.execution,
                commission_usd_per_side=0,
                cost_model="webull_jp_us_equity",
                cat_route="exchange",
                fee_schedule="2026-11-21",
                spread_bps_per_side=scenario["spread_bps_per_side"],
                slippage_bps_per_side=scenario["slippage_bps_per_side"],
            )
            config = parse_config(
                replace(
                    base, execution=costs, output_directory=str(directory / "runs")
                ).snapshot()
            )
            run = run_experiment(config, diagnostic=diagnostic)
            metrics = json.loads((run / "metrics.json").read_text())
            metadata = json.loads((run / "run_metadata.json").read_text())
            quality = json.loads((run / "quality.json").read_text())
            if metadata["input_sha256"] != artifacts["data"]["sha256"]:
                raise ValueError("シナリオ間で入力データのハッシュが一致しません")
            row = dict(
                **metrics,
                **scenario,
                run_directory=str(run),
            )
            rows.append(row)
            manifest["scenarios"].append(
                dict(
                    **scenario,
                    run_directory=str(run),
                    config=config.snapshot(),
                    metadata=metadata,
                    quality=quality,
                )
            )
        verify_inputs(artifacts)
        manifest["break_even"] = break_even(rows)
        write_cost_reports(directory, rows, manifest)
    except Exception:
        (directory / "FAILED").touch()
        raise
    return directory
