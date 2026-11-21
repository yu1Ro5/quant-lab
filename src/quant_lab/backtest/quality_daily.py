"""同一条件を確認できた参照日足だけを正式な照合に使う。"""

from typing import Any, cast

import numpy as np
import pandas as pd

from .config import ExperimentConfig
from .data import NUMERIC_COLUMNS, validate_data


def compare_daily(
    aggregated: pd.DataFrame,
    reference: pd.DataFrame | None,
    metadata: dict[str, Any],
    digest: str | None,
    config: ExperimentConfig,
    checks: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if reference is None:
        return [], dict(status="unknown", detail="日足参照データなし")
    try:
        ref = reference.copy()
        if not {"date", *NUMERIC_COLUMNS}.issubset(ref):
            raise ValueError("日足参照はdate,open,high,low,close,volumeが必要です")
        parsed = pd.to_datetime(ref.date, errors="raise")
        # 取引日は現地日付の文字列を要求。UTC日付への暗黙変換をしない。
        if (
            isinstance(parsed.dtype, pd.DatetimeTZDtype)
            or (parsed != parsed.dt.normalize()).any()
        ):
            raise ValueError("dateはタイムゾーンなしの取引日で指定してください")
        ref["date"] = parsed.dt.strftime("%Y-%m-%d")
        if ref.date.isna().any() or ref.date.duplicated().any():
            raise ValueError("日足参照の日付欠損・重複")
        validation = ref[list(NUMERIC_COLUMNS)].copy()
        validation["datetime"] = pd.to_datetime(ref.date, utc=True)
        validate_data(validation)
        ref = ref[(ref.date >= config.start) & (ref.date <= config.end)]
        left = aggregated.copy()
        if "date" not in left:
            left = pd.DataFrame(columns=["date", *NUMERIC_COLUMNS, "complete"])
        combined = left.merge(
            ref[["date", *NUMERIC_COLUMNS]],
            on="date",
            how="outer",
            suffixes=("_5m", "_reference"),
            indicator=True,
        ).sort_values("date")
        tolerance = metadata.get("tolerance", {})
        price: Any = (
            tolerance.get("price_absolute", 0.0)
            if isinstance(tolerance, dict)
            else None
        )
        volume: Any = (
            tolerance.get("volume_absolute", 0.0)
            if isinstance(tolerance, dict)
            else None
        )
        if any(
            type(v) not in (int, float) or not np.isfinite(v) or v < 0
            for v in (price, volume)
        ):
            raise ValueError("許容差は0以上の有限数値が必要です")
        # 非ゼロ許容差にもレビュー根拠が必要。無根拠な緩和でpassにしない。
        tolerance_reviewed = price == volume == 0 or bool(
            isinstance(tolerance, dict)
            and tolerance.get("reason")
            and tolerance.get("reviewed_by")
        )
        contracts = dict(
            symbol=config.data.symbol,
            timeframe="1d",
            adjustment=config.data.adjustment,
            session="regular",
            volume_unit="shares",
            timezone="America/New_York",
            dataset_sha256=digest,
        )
        missing, conflicts = [], []
        for key, value in contracts.items():
            if key not in metadata or value is None:
                missing.append(key)
            elif metadata[key] != value:
                conflicts.append(key)
        for key in ("source", "observation", "reviewed_by", "reviewed_at"):
            if not isinstance(metadata.get(key), str) or not metadata[key].strip():
                missing.append(key)
        evidence_ready = all(
            checks[name]["status"] == "pass"
            for name in (
                "timestamp_semantics",
                "session_scope",
                "adjustment",
                "volume_unit",
                "provenance",
            )
        )
        conditions = (
            not missing and not conflicts and evidence_ready and tolerance_reviewed
        )
        differences = {}
        mismatch = combined["_merge"].ne("both")
        if "complete" in combined:
            mismatch |= combined.complete.ne(True)
        for col in NUMERIC_COLUMNS:
            combined[col + "_delta"] = pd.to_numeric(
                combined[col + "_5m"]
            ) - pd.to_numeric(combined[col + "_reference"])
            combined[col + "_relative_delta"] = combined[col + "_delta"] / combined[
                col + "_reference"
            ].replace(0, np.nan)
            bound = volume if col == "volume" else price
            bad = combined[col + "_delta"].abs() > bound
            mismatch |= bad
            differences[col] = dict(
                difference_days=int(bad.sum()),
                max_absolute_delta=float(combined[col + "_delta"].abs().max())
                if combined[col + "_delta"].notna().any()
                else None,
            )
        combined["match_status"] = combined["_merge"].astype(str)
        combined["within_tolerance"] = ~mismatch
        records = (
            combined.drop(columns="_merge")
            .astype(object)
            .where(pd.notna(combined.drop(columns="_merge")), None)
            .to_dict("records")
        )
        status = (
            "fail"
            if conflicts
            else "unknown"
            if not conditions
            else "fail"
            if mismatch.any() or combined.empty
            else "pass"
        )
        return cast(list[dict[str, Any]], records), dict(
            status=status,
            detail=dict(
                conditions_verified=conditions,
                missing=missing,
                conflicts=conflicts,
                tolerance=dict(
                    price_absolute=price,
                    volume_absolute=volume,
                    reviewed=tolerance_reviewed,
                ),
                differences=differences,
                mismatch_days=int(mismatch.sum()),
                missing_reference_days=combined.loc[
                    combined["_merge"] == "left_only", "date"
                ].tolist(),
                reference_only_days=combined.loc[
                    combined["_merge"] == "right_only", "date"
                ].tolist(),
                cause="未解決の差分は補正しない。条件不明の場合は差の原因を断定しない。",
            ),
        )
    except (ValueError, TypeError, KeyError) as error:
        return [], dict(status="fail", detail=str(error))
