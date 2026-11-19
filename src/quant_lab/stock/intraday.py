"""OpenD行情APIによる米国株5分足の取得・品質検証・日足比較。"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_lab.backtest.data import validate_data
from quant_lab.stock.daily import NY, SYMBOL, VALUES, DailyError
from quant_lab.stock.storage import publish

DEFAULT = Path("data/stock/aapl_5m_2026")
HISTORY_DOC = "https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html"
TERMS = "https://www.moomoo.com/ja/terms/japan"


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class QuoteClient:
    """全APIを直列化。最大3回再試行し、権限・履歴枠不足は再試行しない。"""

    def __init__(self, ctx: Any, sdk: Any, *, pause: Any = time.sleep) -> None:
        self.ctx, self.sdk, self.pause = ctx, sdk, pause
        self.calls = 0
        self.retries = 0

    def call(self, method: str, **kwargs: Any) -> Any:
        for attempt in range(4):
            self.pause(1.1 if attempt == 0 else 5 * 2 ** (attempt - 1))
            self.calls += 1
            try:
                result = getattr(self.ctx, method)(**kwargs)
                if result[0] == self.sdk.RET_OK:
                    return result
                message = str(result[1])
            except (OSError, TimeoutError) as error:
                message = str(error)
            lower = message.lower()
            permanent = any(word in lower for word in
                            ("permission", "quota", "权限", "権限", "额度", "login", "登录"))
            transient = any(word in lower for word in (
                "frequen", "rate limit", "too many", "timeout", "timed out", "network",
                "temporar", "connection", "频", "頻", "超时", "连接"))
            if permanent or not transient or attempt == 3:
                raise DailyError(f"{method}失敗（再試行{attempt}回）: {message}")
            self.retries += 1
        raise AssertionError("unreachable")

    def history(self, start: date, end: date, *, daily: bool = False,
                sample: bool = False) -> pd.DataFrame:
        pages = []
        key = None
        seen = set()
        for _ in range(200):
            _, data, next_key = self.call(
                "request_history_kline", code=SYMBOL, start=start.isoformat(),
                end=end.isoformat(), ktype=self.sdk.KLType.K_DAY if daily else self.sdk.KLType.K_5M,
                autype=self.sdk.AuType.NONE, extended_time=False,
                max_count=1000, page_req_key=key)
            pages.append(data)
            if next_key is None or sample:
                return pd.concat(pages, ignore_index=True)
            if next_key in seen:
                raise DailyError("ページキー循環")
            seen.add(next_key)
            key = next_key
        raise DailyError("ページ数が上限200を超えました")


def completed_sessions(calendar: list[dict[str, str]], end: date,
                       now: datetime | None = None) -> list[dict[str, str]]:
    current = (now or datetime.now(timezone.utc)).astimezone(NY)
    sessions = []
    for entry in calendar:
        day = date.fromisoformat(entry["time"])
        if entry["trade_date_type"] not in ("WHOLE", "MORNING"):
            raise DailyError(f"未対応の立会種別: {entry}")
        closing = pd.Timestamp(f"{day} {'13:00' if entry['trade_date_type'] == 'MORNING' else '16:00'}",
                               tz=NY)
        # 引け直後の配信遅延も避けるため、終了から15分待つ。
        if day <= end and closing + pd.Timedelta(minutes=15) <= current:
            sessions.append(entry)
    return sorted(sessions, key=lambda item: item["time"])


def assess(raw: pd.DataFrame, sessions: list[dict[str, str]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """終了ラベルの仮説で検証。欠落は警告として列挙し、一切補間しない。"""
    if raw.empty or not {"time_key", *VALUES}.issubset(raw.columns):
        raise DailyError("空データまたは必須列不足")
    if "code" in raw and not raw.code.eq(SYMBOL).all():
        raise DailyError("銘柄不一致")
    eastern = pd.to_datetime(raw.time_key, errors="raise")
    if eastern.dt.tz is not None:
        raise DailyError("SDK time_keyはnaiveな米国東部時間を想定しています")
    eastern = eastern.dt.tz_localize(NY, ambiguous="raise", nonexistent="raise")
    data = raw[VALUES].copy()
    data["datetime"] = eastern.dt.tz_convert("UTC")
    data = validate_data(data)
    data["datetime_et"] = data.datetime.dt.tz_convert(NY)
    data["date"] = data.datetime_et.dt.strftime("%Y-%m-%d")
    data["interval_start_utc"] = data.datetime - pd.Timedelta(minutes=5)
    expected: dict[str, pd.DatetimeIndex] = {}
    for item in sessions:
        day = item["time"]
        close = "13:00" if item["trade_date_type"] == "MORNING" else "16:00"
        expected[day] = pd.date_range(day + " 09:35", day + " " + close, freq="5min", tz=NY)
    if not set(data.date).issubset(expected):
        raise DailyError("カレンダーに存在しない取引日の足があります")
    details: list[dict[str, Any]] = []
    for day, grid in sorted(expected.items()):
        observed = pd.DatetimeIndex(data.loc[data.date == day, "datetime_et"])
        extra = observed.difference(grid)
        if len(extra):
            raise DailyError(f"立会時間外・5分グリッド外（終了ラベル仮説）: {extra.tolist()[:5]}")
        missing = grid.difference(observed)
        details.append(dict(date=day, expected=len(grid), actual=len(observed),
                            missing=[stamp.isoformat() for stamp in missing],
                            complete=not len(missing)))
    missing_count = sum(len(item["missing"]) for item in details)
    quality = dict(status="needs_review" if missing_count else "passed",
                   expected_bars=sum(len(grid) for grid in expected.values()),
                   actual_bars=len(data), missing_bars=missing_count,
                   gap_cause="halt_or_provider_gap_unconfirmed", sessions=details,
                   zero_volume_bars=int(data.volume.eq(0).sum()),
                   fractional_volume_bars=int(np.not_equal(data.volume, np.floor(data.volume)).sum()),
                   duplicates=0, missing_ohlcv=0, outside_session=0,
                   source_sorted=bool(eastern.is_monotonic_increasing))
    return data[["datetime", "datetime_et", "interval_start_utc", "date", *VALUES]], quality


def aggregate(data: pd.DataFrame, quality: dict[str, Any]) -> pd.DataFrame:
    result = data.sort_values("datetime").groupby("date").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), bars=("close", "size")).reset_index()
    complete = {item["date"]: item["complete"] for item in quality["sessions"]}
    result["complete"] = result.date.map(complete)
    return result


def compare(aggregated: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """外部データ側だけの日も隠さず残す。差分に勝手な許容差を設けない。"""
    ref = reference.copy()
    ref.columns = [str(name).lower() for name in ref.columns]
    if not {"date", *VALUES}.issubset(ref):
        raise DailyError("比較CSVはdate,open,high,low,close,volumeが必要です（未調整）")
    ref["date"] = pd.to_datetime(ref.date, errors="raise").dt.strftime("%Y-%m-%d")
    if ref.date.duplicated().any():
        raise DailyError("比較データの日付重複")
    check = ref[VALUES].copy()
    check["datetime"] = pd.to_datetime(ref.date, utc=True)
    validate_data(check)
    out = aggregated.merge(ref[["date", *VALUES]], on="date", how="outer",
                           suffixes=("_5m", "_reference"), indicator=True)
    out["match_status"] = out["_merge"].astype(str).map(
        {"both": "compared", "left_only": "aggregate_only", "right_only": "reference_only"})
    for col in VALUES:
        out[col + "_delta"] = out[col + "_5m"] - pd.to_numeric(out[col + "_reference"])
    return out.drop(columns="_merge").sort_values("date")


def fetch(client: QuoteClient, start: date, end: date, base: Path,
          raw_source: Path | None = None) -> dict[str, Any]:
    today = datetime.now(timezone.utc).astimezone(NY).date()
    if start > min(end, today):
        raise DailyError("開始日・終了日が不正です")
    _, calendar = client.call("request_trading_days", code=SYMBOL,
                              start=start.isoformat(), end=min(end, today).isoformat())
    sessions = completed_sessions(calendar, end)
    if not sessions:
        raise DailyError("確定済み営業日なし")
    effective = date.fromisoformat(sessions[-1]["time"])
    raw = pd.read_parquet(raw_source) if raw_source else client.history(start, effective)
    try:
        data, quality = assess(raw, sessions)
    except ValueError as error:
        publish(Path(str(base) + "_failed"), {
            "_raw.parquet": raw.to_parquet(index=False),
            "_quality.json": json_bytes(dict(status="invalid", error=str(error)))})
        raise
    aggregated = aggregate(data, quality)
    ref = client.history(start, effective, daily=True)
    ref = ref.rename(columns={"time_key": "date"})
    internal_comparison = compare(aggregated, ref)
    parquet = data.to_parquet(index=False)
    dataset_id = hashlib.sha256(parquet).hexdigest()
    internal_summary = dict(
        price_difference_days=int(internal_comparison[[col + "_delta" for col in VALUES[:4]]]
                                  .abs().gt(1e-6).any(axis=1).sum()),
        volume_difference_days=int(internal_comparison.volume_delta.abs().gt(1e-6).sum()),
        max_absolute_volume_delta=float(internal_comparison.volume_delta.abs().max()),
        status="compared_not_verified")
    quality["scope"] = "structural_validation_only"
    quality["dataset_sha256"] = dataset_id
    metadata = dict(source="moomoo", symbol=SYMBOL, ktype="K_5M", autype="NONE",
                    session="regular", request_start=start.isoformat(), request_end=end.isoformat(),
                    effective_end=effective.isoformat(), count=len(data),
                    first_timestamp=data.datetime.iloc[0].isoformat(),
                    last_timestamp=data.datetime.iloc[-1].isoformat(),
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    raw_source=str(raw_source) if raw_source else "live SDK request",
                    raw_file_modified_at=datetime.fromtimestamp(raw_source.stat().st_mtime,
                                                                timezone.utc).isoformat() if raw_source else None,
                    sdk_version=version("moomoo-api"), timezone="UTC",
                    exchange_timezone="America/New_York", quality_status=quality["status"],
                    dataset_sha256=dataset_id, quality_scope="structural_validation_only",
                    backtest_ready=False,
                    backtest_review_reasons=["timestamp_label_officially_unconfirmed",
                                             "inspect_daily_comparison_price_and_volume_deltas"],
                    internal_daily_comparison=internal_summary,
                    timestamp_label=dict(interpretation="end_inferred", official_confirmation="unconfirmed",
                                         evidence="Observed 09:35 to 16:00 for full sessions; 5-minute spacing",
                                         source=HISTORY_DOC,
                                         interval_start="derived by subtracting 5 minutes; not official"),
                    requests=client.calls, retries=client.retries,
                    external_comparison="not_yet_performed; internal moomoo K_DAY comparison only",
                    terms=dict(url=TERMS, review_status="indexed terms inspected; full page returned 403",
                               scope="private local research; API storage license not conclusively verified; no redistribution"))
    paths = publish(base, {".parquet": parquet,
                           ".csv": data.to_csv(index=False).encode("utf-8"),
                           "_raw.parquet": raw.to_parquet(index=False),
                           "_metadata.json": json_bytes(metadata), "_quality.json": json_bytes(quality),
                           "_daily.csv": aggregated.to_csv(index=False).encode("utf-8"),
                           "_moomoo_daily_comparison.csv": internal_comparison.to_csv(index=False).encode("utf-8"),
                           "_external_comparison_status.json": json_bytes(dict(
                               status="not_performed", dataset_sha256=dataset_id))})
    return dict(status=quality["status"], count=len(data), effective_end=effective.isoformat(),
                missing_bars=quality["missing_bars"], paths=paths)


def probe(client: QuoteClient, years: list[int], base: Path,
          window_start: date | None = None) -> dict[str, Any]:
    metadata = json.loads(Path(str(base) + "_metadata.json").read_text())
    if metadata.get("ktype") != "K_5M":
        raise DailyError("先に2026年分の5分足を取得してください")
    results: list[dict[str, Any]] = []
    windows = [(date(year, 1, 2), date(year, 1, 8)) for year in years]
    if window_start is not None:
        windows = [(window_start, window_start + timedelta(days=6))]
    for start, end in windows:
        year = start.year
        try:
            raw = client.history(start, end, sample=True)
            if raw.empty:
                results.append(dict(year=year, start=str(start), end=str(end), status="empty", count=0))
                break
            _, calendar = client.call("request_trading_days", code=SYMBOL, start=str(start), end=str(end))
            data, quality = assess(raw, calendar)
            parquet = data.to_parquet(index=False)
            digest = hashlib.sha256(parquet).hexdigest()
            quality.update(dataset_sha256=digest, request_start=str(start), request_end=str(end))
            # 別の調査窓や同じ窓の改訂データで過去レポートの証拠を上書きしない。
            evidence = Path(str(base) + f"_probe_{start}_{end}_{digest}")
            paths = publish(evidence, {
                ".parquet": parquet, "_quality.json": json_bytes(quality)})
            results.append(dict(year=year, start=str(start), end=str(end), status=quality["status"],
                                count=len(data), first=data.datetime_et.iloc[0].isoformat(),
                                last=data.datetime_et.iloc[-1].isoformat(), dataset_sha256=digest, paths=paths))
        except ValueError as error:
            results.append(dict(year=year, start=str(start), end=str(end), status="error", error=str(error)))
            break
    confirmed = [r["first"] for r in results if "first" in r]
    report = dict(results=results, earliest_confirmed=min(confirmed) if confirmed else None,
                  absolute_earliest=None, scope="記録された最大7日間の窓のみ。最初の空応答/エラーで停止。他の期間は未確認。",
                  fetched_at=datetime.now(timezone.utc).isoformat())
    publish(base, {"_history_boundary_probe.json" if window_start else "_history_probe.json": json_bytes(report)})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "probe"])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--to", dest="end", type=date.fromisoformat,
                        default=datetime.now(timezone.utc).astimezone(NY).date())
    parser.add_argument("--output", type=Path, default=DEFAULT, help="拡張子なしの保存先")
    parser.add_argument("--raw-source", type=Path, help="保存済みSDK生データを再検証（再取得しない）")
    parser.add_argument("--years", type=int, nargs="+", default=[2025, 2024])
    parser.add_argument("--window-start", type=date.fromisoformat,
                        help="年単位の調査に代えて指定日から7日間だけ境界を検証")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeoutは正の有限値")
    if args.command == "probe" and (not args.years or args.years != sorted(set(args.years), reverse=True)
                                    or any(year < 2017 or year >= 2026 for year in args.years)):
        parser.error("--yearsは2025から2017までの降順・重複なしで指定")
    if not args.worker:
        try:
            return subprocess.run([sys.executable, "-m", "quant_lab.stock.intraday",
                                   *(argv if argv is not None else sys.argv[1:]), "--worker"],
                                  timeout=args.timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            print(json.dumps(dict(status="error", stage="timeout")))
            return 2
    ctx = None
    stage = "connection"
    try:
        with socket.create_connection(("127.0.0.1", 11111), timeout=3):
            pass
        sdk = importlib.import_module("moomoo")
        stage = "authentication"
        ctx = sdk.OpenQuoteContext(host="127.0.0.1", port=11111)
        client = QuoteClient(ctx, sdk)
        _, state = client.call("get_global_state")
        if state.get("qot_logined") is not True:
            raise DailyError("行情ログイン未確認")
        stage = args.command
        result = fetch(client, args.start, args.end, args.output, args.raw_source) if args.command == "fetch" else probe(
            client, args.years, args.output, args.window_start)
        print(json.dumps(result, ensure_ascii=False))
        if args.command == "fetch":
            return 0 if result["status"] == "passed" else 3
        return 2 if any(item["status"] == "error" for item in result["results"]) else 0
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        failure = dict(status="error", stage=stage, error=str(error))
        print(json.dumps(failure, ensure_ascii=False))
        publish(args.output, {"_last_failure.json": json_bytes(failure)})
        return 2
    finally:
        if ctx is not None:
            ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
