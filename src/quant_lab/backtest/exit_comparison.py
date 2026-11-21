"""明示したExit候補を独立したIS/OOS期間で比較する。"""

import json
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import yaml

from .broker_fees import fee_model_metadata
from .comparison_inputs import snapshot_inputs, verify_inputs
from .config import ComponentConfig, UniqueKeyLoader, load_config, parse_config
from .exit_report import write_exit_html
from .runner import run_experiment
from .validation import ConfigError, mapping, text


def run_exit_comparison(path: str | Path, *, diagnostic: bool = False) -> Path:
    path = Path(path).resolve()
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ConfigError(f"比較YAMLを読み込めません: {error}") from error
    root = mapping(
        raw,
        "comparison",
        {"base_config", "periods", "exit_candidates"},
        {"output_directory"},
    )
    base_path = path.parent / text(root["base_config"], "base_config")
    original = load_config(base_path)
    periods = mapping(root["periods"], "periods", {"in_sample", "out_of_sample"})
    for name, period in periods.items():
        period = mapping(period, name, {"start", "end"})
        for key in ("start", "end"):
            value = text(period[key], key)
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError as error:
                raise ConfigError("期間はYYYY-MM-DD形式が必要です") from error
        if period["start"] > period["end"]:
            raise ConfigError("期間のstartはend以前にしてください")
    if periods["in_sample"]["end"] >= periods["out_of_sample"]["start"]:
        raise ConfigError("ISの後に重複しないOOS期間が必要です")
    # 比較の共通条件は明示的に固定し、元の単発設定は保存する。
    base = replace(
        original,
        entry=ComponentConfig("orb", {"opening_range_minutes": 30}),
        sizing=ComponentConfig("fixed_shares", {"shares": 1}),
        initial_capital=5000,
        max_trades_per_day=1,
        execution=replace(
            original.execution,
            cost_model="webull_jp_us_equity",
            commission_usd_per_side=0,
            spread_bps_per_side=1,
            slippage_bps_per_side=2,
            cat_route="exchange",
            fee_schedule="2026-11-21",
        ),
    )
    candidates = root["exit_candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ConfigError("exit_candidatesは空でない配列が必要です")
    normalized = []
    names = set()
    for item in candidates:
        item = mapping(item, "candidate", {"name", "strategy", "params"})
        name = text(item["name"], "candidate.name")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name) or name in names:
            raise ConfigError("候補名は重複しない英数字・_・-が必要です")
        names.add(name)
        component = ComponentConfig(item["strategy"], item["params"])
        checked = parse_config(replace(base, exit=component).snapshot())
        normalized.append((name, checked.exit))
    output = Path(
        text(root.get("output_directory", base.output_directory), "output_directory")
    ).resolve()
    directory = (
        output
        / f"exit_comparison_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:12]}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    manifest: dict[str, Any] = dict(
        original_base_config=original.snapshot(),
        base_config_path=str(base_path.resolve()),
        comparison_config=root,
        fee_model=fee_model_metadata(),
        runs=[],
        sample_warning_threshold=30,
        assumptions=[
            "spreadはOHLC参照価格に対する片道実効負担。実測Bid/Askではない。",
            "slippageはspreadとは別の追加不利価格差。即時全量約定。",
        ],
        oos_unseen_status="未使用性は日付分割では証明されない。利用者が履歴を確認すること。",
    )
    try:
        base, artifacts = snapshot_inputs(base, directory)
        manifest["artifacts"] = artifacts
        (directory / "comparison_config.yaml").write_text(
            yaml.safe_dump(root, allow_unicode=True), encoding="utf-8"
        )
        for period_name in ("in_sample", "out_of_sample"):
            period = periods[period_name]
            for name, component in normalized:
                verify_inputs(artifacts)
                config = parse_config(
                    replace(
                        base,
                        name=name,
                        start=period["start"],
                        end=period["end"],
                        exit=component,
                        output_directory=str(directory / "runs"),
                    ).snapshot()
                )
                run = run_experiment(config, diagnostic=diagnostic)
                metrics = json.loads((run / "metrics.json").read_text())
                metadata = json.loads((run / "run_metadata.json").read_text())
                if metadata["input_sha256"] != artifacts["data"]["sha256"]:
                    raise ValueError("入力データのハッシュが一致しません")
                if metrics["total_trades"] < 30:
                    metrics["warnings"].append(
                        "取引数30未満: サンプル不足。30以上でも統計的優位性を保証しない。"
                    )
                row = dict(
                    metrics,
                    name=name,
                    period=period_name,
                    start=period["start"],
                    end=period["end"],
                    run_directory=str(run),
                )
                rows.append(row)
                manifest["runs"].append(
                    dict(
                        name=name,
                        period=period_name,
                        config=config.snapshot(),
                        metadata=metadata,
                        quality=json.loads((run / "quality.json").read_text()),
                    )
                )
        verify_inputs(artifacts)
        pd.DataFrame(rows).to_csv(directory / "comparison.csv", index=False)
        (directory / "comparison.json").write_text(
            json.dumps(
                dict(manifest, results=rows),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        write_exit_html(directory, rows, manifest)
    except Exception:
        (directory / "FAILED").touch()
        raise
    return directory
