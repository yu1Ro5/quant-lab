"""保存OHLCVの再検証。申告metadataのreadyフラグを信用しない。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]
import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype

from .config import ExperimentConfig
from .data import NUMERIC_COLUMNS, REQUIRED_COLUMNS, DataValidationError
from .quality_daily import compare_daily
from .sessions import prepare_sessions

STRUCTURAL = ("schema", "ohlcv", "timestamps", "calendar", "session_grid")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.is_file():
        return {}, None
    payload = path.read_bytes()

    def reject_constant(value: str) -> None:
        raise ValueError(f"JSON非有限値は禁止です: {value}")

    value = json.loads(payload, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise DataValidationError(f"JSON objectが必要です: {path}")
    return value, sha256(payload)


def assess_quality(
    frame: pd.DataFrame,
    config: ExperimentConfig,
    dataset_sha256: str,
    *,
    metadata: dict[str, Any] | None = None,
    metadata_sha256: str | None = None,
    evidence: dict[str, Any] | None = None,
    reference: pd.DataFrame | None = None,
    reference_metadata: dict[str, Any] | None = None,
    reference_sha256: str | None = None,
) -> dict[str, Any]:
    """全検査をpass/fail/unknownで返す。欠損を補間せず、既存session処理を再利用。"""
    checks: dict[str, Any] = {}
    report: dict[str, Any] = dict(
        schema_version=1,
        validated_at=datetime.now(timezone.utc).isoformat(),
        input_path=str(Path(config.data.path).resolve()),
        dataset_sha256=dataset_sha256,
        metadata_sha256=metadata_sha256,
        input_rows=len(frame),
        specification=dict(
            symbol=config.data.symbol,
            timeframe=config.data.timeframe,
            naive_timezone=config.data.timezone,
            bar_timestamp=config.data.bar_timestamp,
            adjustment=config.data.adjustment,
            session="regular",
            calendar="XNYS",
            start=config.start,
            end=config.end,
            versions={
                name: version(name)
                for name in ("pandas", "exchange-calendars", "tzdata")
            },
        ),
        checks=checks,
        sessions=[],
        exclusions=[],
        daily_comparison=[],
        warnings=[],
        evidence=evidence or {},
    )

    def check(name: str, status: str, detail: Any) -> None:
        checks[name] = dict(status=status, detail=detail)

    duplicate_columns = frame.columns[frame.columns.duplicated()].tolist()
    # 重複列はschema失敗を記録する。後続の診断には先頭列のみ使用し、売買には流さない。
    frame = frame.loc[:, ~frame.columns.duplicated()].reset_index(drop=True)
    missing = set(REQUIRED_COLUMNS) - set(frame)
    bad_types = [
        c
        for c in NUMERIC_COLUMNS
        if c in frame and (not is_numeric_dtype(frame[c]) or is_bool_dtype(frame[c]))
    ]
    if "datetime" in frame and (
        is_numeric_dtype(frame.datetime) or is_bool_dtype(frame.datetime)
    ):
        bad_types.append("datetime")
    check(
        "schema",
        "fail" if missing or bad_types or duplicate_columns else "pass",
        dict(
            duplicate_columns=duplicate_columns,
            missing=sorted(missing),
            invalid_numeric_dtypes=bad_types,
            datetime_dtype=str(frame["datetime"].dtype)
            if "datetime" in frame
            else None,
        ),
    )
    normalized = frame.copy()
    try:
        if "datetime" not in frame:
            raise ValueError("datetime列なし")
        times = pd.to_datetime(frame.datetime, format="mixed", errors="raise")
        if not isinstance(times.dtype, pd.DatetimeTZDtype):
            times = times.dt.tz_localize(
                config.data.timezone, ambiguous="raise", nonexistent="raise"
            )
        times = times.dt.tz_convert("UTC")
        if times.isna().any():
            raise ValueError("datetime欠損")
        normalized["datetime"] = times
        check(
            "timestamps",
            "pass"
            if times.is_monotonic_increasing and not times.duplicated().any()
            else "fail",
            dict(
                duplicates=int(times.duplicated().sum()),
                source_sorted=bool(times.is_monotonic_increasing),
            ),
        )
    except (ValueError, TypeError, AttributeError) as error:
        check("timestamps", "fail", str(error))
    if missing or bad_types:
        check("ohlcv", "unknown", "必須列・数値型が不正のため検証不能")
    else:
        values = frame[list(NUMERIC_COLUMNS)].to_numpy(dtype=float, na_value=np.nan)
        invalid = (
            (~np.isfinite(values)).any(axis=1)
            | (values[:, :4] <= 0).any(axis=1)
            | (values[:, 4] < 0)
        )
        invalid |= (
            (frame.high < frame[["open", "low", "close"]].max(axis=1))
            .fillna(False)
            .to_numpy(dtype=bool)
        )
        invalid |= (
            (frame.low > frame[["open", "high", "close"]].min(axis=1))
            .fillna(False)
            .to_numpy(dtype=bool)
        )
        check(
            "ohlcv",
            "fail" if invalid.any() else "pass",
            dict(
                invalid_rows=np.flatnonzero(invalid).tolist(),
                zero_volume_bars=int(frame.volume.eq(0).sum()),
                fractional_volume_bars=int((frame.volume % 1 != 0).sum()),
            ),
        )

    meta = metadata or {}
    conflicts, absent = [], []
    expected = dict(
        dataset_sha256=dataset_sha256,
        symbol=config.data.symbol,
        ktype="K_5M",
        autype={"none": "NONE", "forward": "QFQ", "backward": "HFQ"}[
            config.data.adjustment
        ],
        session="regular",
        count=len(frame),
    )
    for key, value in expected.items():
        if key not in meta:
            absent.append(key)
        elif meta[key] != value:
            conflicts.append(f"{key}: {meta[key]!r} != {value!r}")
    label = meta.get("timestamp_label", {})
    if isinstance(label, dict):
        interpretation = label.get("interpretation")
        if interpretation in ("start", "end", "start_inferred", "end_inferred"):
            if interpretation.split("_")[0] != config.data.bar_timestamp:
                conflicts.append("timestamp_label != bar_timestamp")
    if checks["timestamps"]["status"] == "pass" and not normalized.empty:
        for key, value in [
            ("first_timestamp", normalized.datetime.iloc[0]),
            ("last_timestamp", normalized.datetime.iloc[-1]),
        ]:
            if key not in meta:
                absent.append(key)
            else:
                try:
                    if pd.Timestamp(meta[key]) != value:
                        conflicts.append(key)
                except (ValueError, TypeError):
                    conflicts.append(key)
        stored_tz = meta.get("timezone")
        # aware列は絶対時刻を保持するが、保存TZの申告が矛盾していれば拒否する。
        original = pd.to_datetime(frame.datetime, format="mixed")
        actual_tz = (
            str(original.dt.tz)
            if isinstance(original.dtype, pd.DatetimeTZDtype)
            else config.data.timezone
        )
        if stored_tz is not None and stored_tz != actual_tz:
            conflicts.append("datetime timezone != metadata.timezone")
        days = normalized.datetime.dt.tz_convert("America/New_York").dt.strftime(
            "%Y-%m-%d"
        )
        for key, comparison in [
            ("request_start", lambda d: (days < d).any()),
            ("effective_end", lambda d: (days > d).any()),
            ("request_end", lambda d: (days > d).any()),
        ]:
            if key in meta:
                try:
                    day = pd.Timestamp(meta[key]).strftime("%Y-%m-%d")
                    if comparison(day):
                        conflicts.append(key)
                except (ValueError, TypeError):
                    conflicts.append(key)
    for column in ("symbol", "code"):
        if column in frame and not frame[column].eq(config.data.symbol).all():
            conflicts.append(f"{column}列の銘柄不一致")
    check(
        "metadata",
        "fail" if conflicts else "unknown" if absent else "pass",
        dict(
            conflicts=conflicts,
            missing=absent,
            note="整合性検査。出所の信頼性は別途provenanceで確認する",
        ),
    )

    # 根拠は入力/metadata両方のハッシュへ拘束。宣言だけのverifiedフラグは禁止。
    ev = evidence or {}
    bound = (
        ev.get("dataset_sha256") == dataset_sha256
        and metadata_sha256 is not None
        and ev.get("metadata_sha256") == metadata_sha256
    )
    assertions = ev.get("assertions", {})
    for name, value in dict(
        timestamp_semantics=config.data.bar_timestamp,
        native_timezone="America/New_York",
        adjustment=config.data.adjustment,
        volume_unit="shares",
        session_scope="regular",
        finalization="closed_bars_only",
        provenance="reviewed_acquisition",
    ).items():
        assertion = assertions.get(name, {}) if isinstance(assertions, dict) else {}
        valid = (
            bound
            and isinstance(assertion, dict)
            and assertion.get("status") == "pass"
            and assertion.get("value") == value
            and all(
                isinstance(assertion.get(k), str) and assertion[k].strip()
                for k in ("source", "observation", "reviewed_by", "reviewed_at")
            )
        )
        conflict = (
            bound
            and isinstance(assertion, dict)
            and (
                assertion.get("status") == "fail"
                or (
                    assertion.get("status") == "pass"
                    and assertion.get("value") != value
                )
            )
        )
        check(
            name,
            "pass" if valid else "fail" if conflict else "unknown",
            assertion if valid or conflict else "入力に結び付いたレビュー済み根拠なし",
        )

    if checks["timestamps"]["status"] == "pass" and not normalized.empty:
        ends = normalized.datetime + (
            pd.Timedelta(minutes=5)
            if config.data.bar_timestamp == "start"
            else pd.Timedelta(0)
        )
        if (ends > pd.Timestamp(report["validated_at"])).any():
            check("finalization", "fail", "未確定・未来の足が含まれています")

    usable = all(
        checks[k]["status"] == "pass" for k in ("schema", "ohlcv", "timestamps")
    )
    aggregated = pd.DataFrame(columns=["date", *NUMERIC_COLUMNS])
    if usable:
        try:
            prepared = prepare_sessions(normalized, config)
            report["exclusions"] = prepared.exclusions
            report["warnings"].extend(prepared.warnings)
            calendar = xcals.get_calendar(
                "XNYS",
                start=pd.Timestamp(config.start) - pd.Timedelta(days=7),
                end=pd.Timestamp(config.end) + pd.Timedelta(days=7),
            )
            schedule = calendar.schedule.loc[config.start : config.end]
            starts = normalized.datetime - (
                pd.Timedelta(minutes=5)
                if config.data.bar_timestamp == "end"
                else pd.Timedelta(0)
            )
            dates = starts.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
            selected = (dates >= config.start) & (dates <= config.end)
            valid_days = schedule.index.strftime("%Y-%m-%d")
            holiday = selected & ~dates.isin(valid_days)
            outside = 0
            aggregates = []
            for label, row in schedule.iterrows():
                day = label.strftime("%Y-%m-%d")
                opened, closed = pd.Timestamp(row["open"]), pd.Timestamp(row["close"])
                expected_grid = pd.date_range(
                    opened, closed - pd.Timedelta(minutes=5), freq="5min"
                )
                observed = starts[dates == day]
                regular = observed[
                    (observed >= opened)
                    & (observed + pd.Timedelta(minutes=5) <= closed)
                ]
                outside += len(observed) - len(regular)
                actual = pd.DatetimeIndex(regular)
                missing_grid = expected_grid.difference(actual)
                extra = actual.difference(expected_grid)
                detail = dict(
                    date=day,
                    open=opened.isoformat(),
                    close=closed.isoformat(),
                    expected_bars=len(expected_grid),
                    actual_bars=len(regular),
                    missing=[t.isoformat() for t in missing_grid],
                    off_grid=[t.isoformat() for t in extra],
                    opening_six_present=all(t in actual for t in expected_grid[:6]),
                    final_bar_present=expected_grid[-1] in actual,
                    complete=not len(missing_grid) and not len(extra),
                )
                report["sessions"].append(detail)
                values_day = normalized.loc[regular.index]
                if not values_day.empty:
                    aggregates.append(
                        dict(
                            date=day,
                            open=float(values_day.open.iloc[0]),
                            high=float(values_day.high.max()),
                            low=float(values_day.low.min()),
                            close=float(values_day.close.iloc[-1]),
                            volume=float(values_day.volume.sum()),
                            complete=detail["complete"],
                        )
                    )
            aggregated = pd.DataFrame(aggregates)
            check(
                "calendar",
                "fail" if holiday.any() or outside or not len(schedule) else "pass",
                dict(
                    holiday_bars=int(holiday.sum()),
                    outside_regular_bars=outside,
                    expected_sessions=len(schedule),
                    note="XNYSのDST/休日/短縮立会を適用。時間外・休場足は除外する",
                ),
            )
            check(
                "session_grid",
                "fail" if prepared.exclusions or not prepared.sessions else "pass",
                dict(
                    complete_sessions=len(prepared.sessions),
                    excluded_sessions=len(prepared.exclusions),
                ),
            )
        except (ValueError, TypeError) as error:
            check("calendar", "fail", str(error))
            check("session_grid", "unknown", "セッションを構築できません")
    else:
        check("calendar", "unknown", "構造不正のため検査不能")
        check("session_grid", "unknown", "構造不正のため検査不能")
    daily, daily_check = compare_daily(
        aggregated,
        reference,
        reference_metadata or {},
        reference_sha256,
        config,
        checks,
    )
    report["daily_comparison"] = daily
    checks["daily_comparison"] = daily_check
    report["reference"] = dict(
        sha256=reference_sha256, metadata=reference_metadata or {}
    )
    report["structural_ready"] = all(checks[k]["status"] == "pass" for k in STRUCTURAL)
    report["backtest_ready"] = all(c["status"] == "pass" for c in checks.values())
    report["reasons"] = [
        f"{name}: {c['status']}" for name, c in checks.items() if c["status"] != "pass"
    ]
    report["errors"] = [
        f"{name}: {c['detail']}" for name, c in checks.items() if c["status"] == "fail"
    ]
    report["warnings"].extend(report["reasons"])
    return report


def write_quality(directory: Path, report: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# データ品質検証",
        "",
        f"backtest_ready: **{report['backtest_ready']}**",
        "",
        f"対象: `{report['input_path']}`",
        f"検証日時: {report['validated_at']}",
        "",
        "| 検査 | 判定 | 根拠 |",
        "| --- | --- | --- |",
    ]
    for name, check in report["checks"].items():
        detail = (
            json.dumps(check["detail"], ensure_ascii=False)
            .replace("|", "\\|")
            .replace("\n", " ")
        )
        lines.append(f"| {name} | {check['status']} | {detail} |")
    lines += [
        "",
        "## セッション検査",
        "",
        f"対象{len(report['sessions'])}日、除外{len(report['exclusions'])}日。全日の詳細はquality.json。",
        "",
        "| 日付 | 期待本数 | 実本数 | 最初6本 | 最終足 | 欠損・グリッド外 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for session in report["sessions"]:
        if not session["complete"]:
            lines.append(
                f"| {session['date']} | {session['expected_bars']} | {session['actual_bars']} | "
                f"{session['opening_six_present']} | {session['final_bar_present']} | "
                + ", ".join(session["missing"] + session["off_grid"])
                + " |"
            )
    lines += [
        "",
        "## 仕様・除外日・警告・参照条件",
        "",
        "```json",
        json.dumps(
            {
                k: report[k]
                for k in (
                    "specification",
                    "exclusions",
                    "warnings",
                    "errors",
                    "reference",
                )
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 日足差分",
        "",
        "各OHLCVの集計値・参照値・差分はdaily_comparison.csvとquality.jsonを参照。",
        "",
    ]
    detail = report["checks"]["daily_comparison"]["detail"]
    if isinstance(detail, dict) and "differences" in detail:
        lines += ["| 項目 | 差がある日数 | 最大絶対差 |", "| --- | --- | --- |"]
        for name, values in detail["differences"].items():
            lines.append(
                f"| {name} | {values['difference_days']} | {values['max_absolute_delta']} |"
            )
        lines += [
            "",
            f"比較条件の確認: {detail['conditions_verified']}。許容差: {detail['tolerance']}",
            "",
        ]
    (directory / "quality.md").write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame(report["daily_comparison"]).to_csv(
        directory / "daily_comparison.csv", index=False
    )
