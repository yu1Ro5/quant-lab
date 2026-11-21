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
from typing import Any
from uuid import uuid4

import pandas as pd
import yaml

from .broker_fees import fee_model_metadata
from .config import ExperimentConfig, parse_config
from .data import DataValidationError
from .intraday_engine import ExperimentEngine
from .quality import assess_quality, read_json, sha256, write_quality
from .report import write_reports
from .sessions import prepare_sessions


def run_experiment(
    config: ExperimentConfig, *, diagnostic: bool = False, quality_only: bool = False
) -> Path:
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
    run_id = f"{config.name}_{started.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:12]}"
    directory = output / run_id
    directory.mkdir(parents=True, exist_ok=False)
    sidecar = source.with_name(source.stem + "_metadata.json")
    metadata_digest: str | None = None
    upstream: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    reference_metadata: dict[str, Any] = {}
    reference, reference_digest = None, None
    errors = []
    try:
        upstream, metadata_digest = read_json(sidecar)
    except (OSError, ValueError) as error:
        errors.append(f"入力metadata: {error}")
    for key in ("evidence_path", "daily_metadata_path", "daily_reference_path"):
        path = getattr(config.quality, key)
        if path is None:
            continue
        try:
            if not Path(path).is_file():
                raise ValueError(f"ファイルがありません: {path}")
            if key == "evidence_path":
                evidence, _ = read_json(Path(path))
            elif key == "daily_metadata_path":
                reference_metadata, _ = read_json(Path(path))
            else:
                content = Path(path).read_bytes()
                reference = pd.read_csv(io.BytesIO(content))
                reference_digest = sha256(content)
        except (OSError, ValueError) as error:
            errors.append(f"{key}: {error}")
    quality = assess_quality(
        frame,
        config,
        digest,
        metadata=upstream,
        metadata_sha256=metadata_digest,
        evidence=evidence,
        reference=reference,
        reference_metadata=reference_metadata,
        reference_sha256=reference_digest,
    )
    if errors:
        quality["checks"]["input_artifacts"] = dict(status="fail", detail=errors)
        quality["backtest_ready"] = False
        quality["reasons"].append("input_artifacts: fail")
        quality["warnings"].extend(errors)
        quality["errors"].extend(errors)
    quality["artifacts"] = {
        key: getattr(config.quality, key)
        for key in ("evidence_path", "daily_reference_path", "daily_metadata_path")
    }
    (directory / "config.yaml").write_text(
        yaml.safe_dump(config.snapshot(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    write_quality(directory, quality)
    if quality_only:
        return directory
    diagnostic = diagnostic or config.quality.mode == "diagnostic"
    hard_failure = errors or any(
        quality["checks"][name]["status"] == "fail"
        for name in ("schema", "ohlcv", "timestamps", "metadata")
    )
    if hard_failure or (not diagnostic and not quality["backtest_ready"]):
        (directory / "BLOCKED").write_text(
            "品質ゲートで正式評価を拒否\n", encoding="utf-8"
        )
        raise DataValidationError(
            f"品質検証を通過していません。品質レポート: {directory / 'quality.md'}; "
            + ", ".join(quality["reasons"])
            + "。未検証の診断には--diagnosticを明示してください。"
        )
    prepared = prepare_sessions(frame, config)
    if diagnostic:
        prepared.warnings.append(
            "診断専用の暫定結果です。正式評価には使用しないでください。"
        )
    prepared.warnings.extend(quality["warnings"])
    result = ExperimentEngine(config).run(prepared)
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
        schema_version=2,
        execution_cost_model=(
            fee_model_metadata()
            if config.execution.cost_model == "webull_jp_us_equity"
            else {"cost_model": "fixed"}
        ),
        backtest_ready=quality["backtest_ready"],
        evaluation_status="provisional" if diagnostic else "formal",
        assumptions=[
            "OHLC-only conservative stop priority",
            "complete sessions only",
            "data contract and daily checks recorded in quality.json",
        ],
    )
    try:
        write_reports(
            directory,
            result,
            config.initial_capital,
            quality=quality,
            diagnostic=diagnostic,
        )
        (directory / "run_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        # 不完全な成果物を成功結果と区別する。既存runの削除はしない。
        (directory / "FAILED").touch()
        raise
    return directory
