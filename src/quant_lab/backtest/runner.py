"""入力の同一バイト列を識別・読込し、毎回独立した実験結果を保存。"""

import hashlib
import io
import json
import platform
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import pandas as pd
import yaml

from .config import ExperimentConfig, parse_config
from .data import DataValidationError
from .intraday_engine import ExperimentEngine
from .report import write_reports
from .sessions import prepare_sessions


def run_experiment(config: ExperimentConfig) -> Path:
    # Python API経由でも検証し、実行時の絶対パスをスナップショットへ保存する。
    config = parse_config(config.snapshot())
    source = Path(config.data.path).resolve()
    output = Path(config.output_directory).resolve()
    config = replace(
        config,
        data=replace(config.data, path=str(source)),
        output_directory=str(output),
    )
    started = datetime.now(timezone.utc)
    try:
        payload = source.read_bytes()
        frame = pd.read_parquet(io.BytesIO(payload))
    except (OSError, ValueError) as error:
        raise DataValidationError(
            f"Parquetを読み込めません: {source}: {error}"
        ) from error
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = source.with_name(source.stem + "_metadata.json")
    metadata_digest: str | None = None
    input_warnings = [
        "入力の時刻ラベル・株価調整条件は設定の申告値です。外部品質照合は実施しません。"
    ]
    if sidecar.is_file():
        try:
            sidecar_bytes = sidecar.read_bytes()
            upstream = json.loads(sidecar_bytes)
        except (OSError, ValueError) as error:
            raise DataValidationError(f"入力metadataを読めません: {error}") from error
        if not isinstance(upstream, dict):
            raise DataValidationError("入力metadataはJSON objectである必要があります")
        metadata_digest = hashlib.sha256(sidecar_bytes).hexdigest()
        for key, expected in [
            ("dataset_sha256", digest),
            ("symbol", config.data.symbol),
            ("ktype", "K_5M"),
        ]:
            if key in upstream and upstream[key] != expected:
                raise DataValidationError(
                    f"入力metadataの{key}が設定/入力と一致しません"
                )
        if upstream.get("autype") == "NONE" and config.data.adjustment != "none":
            raise DataValidationError(
                "入力metadataのautype=NONEとadjustmentが一致しません"
            )
        label = upstream.get("timestamp_label", {})
        if isinstance(label, dict):
            interpretation = label.get("interpretation", "")
            for kind in ("start", "end"):
                if (
                    interpretation in (kind, kind + "_inferred")
                    and config.data.bar_timestamp != kind
                ):
                    raise DataValidationError(
                        "入力metadataのtimestamp_labelとbar_timestampが一致しません"
                    )
            if label.get("official_confirmation") == "unconfirmed":
                input_warnings.append(
                    "入力の足時刻定義は提供元の正式確認が未完了です（終了/開始ラベルは仮定）。"
                )
        if upstream.get("backtest_ready") is False:
            input_warnings.append(
                "入力metadata: backtest_ready=false。時刻定義・日足との価格/出来高差分は未解決です。"
            )
    prepared = prepare_sessions(frame, config)
    prepared.warnings.extend(input_warnings)
    result = ExperimentEngine(config).run(prepared)
    run_id = f"{config.name}_{started.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:12]}"
    directory = output / run_id
    directory.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[3]

    def git(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    def implementation_digest() -> str:
        digest = hashlib.sha256()
        base = Path(__file__).parent
        for path in sorted(base.rglob("*.py")):
            digest.update(str(path.relative_to(base)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
        return digest.hexdigest()

    metadata = dict(
        run_id=run_id,
        started_at=started.isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        input_path=str(source),
        input_sha256=digest,
        input_metadata_sha256=metadata_digest,
        implementation_sha256=implementation_digest(),
        input_bytes=len(payload),
        input_rows=len(frame),
        git_commit=git("rev-parse", "HEAD"),
        git_status=git("status", "--porcelain"),
        python=platform.python_version(),
        versions={
            name: version(name)
            for name in [
                "quant-lab",
                "numpy",
                "pandas",
                "pyarrow",
                "exchange-calendars",
                "pyyaml",
                "plotly",
                "tzdata",
            ]
        },
        calendar="XNYS",
        schema_version=1,
        assumptions=[
            "OHLC-only conservative stop priority",
            "complete sessions only",
            "bar_timestamp and adjustment supplied by config; not externally verified",
        ],
    )
    try:
        (directory / "config.yaml").write_text(
            yaml.safe_dump(config.snapshot(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        write_reports(directory, result, config.initial_capital)
        (directory / "run_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        # 不完全な成果物を成功結果と区別する。既存runの削除はしない。
        (directory / "FAILED").touch()
        raise
    return directory
