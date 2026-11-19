"""moomoo行情API専用の日足取得CLI。取引APIは使用しない。"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from tempfile import mkdtemp
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quant_lab.backtest.data import validate_data

NY = ZoneInfo("America/New_York")
SYMBOL = "US.AAPL"
VALUES = ["open", "high", "low", "close", "volume"]


class DailyError(ValueError):
    """設定・通信・品質検証の失敗。"""


def completed_end(requested: date, now: datetime | None = None,
                  close_hour: int = 16) -> date:
    """東部時間の立会終了前は当日を除外する。"""
    current = (now or datetime.now(timezone.utc)).astimezone(NY)
    last = current.date() if current.hour >= close_hour else current.date() - timedelta(days=1)
    return min(requested, last)


def history(ctx: Any, sdk: Any, start: date, end: date, *,
            pause: Any = time.sleep, sample: bool = False) -> pd.DataFrame:
    pages = []
    key = None
    seen = set()
    for _ in range(100):
        ret, data, next_key = ctx.request_history_kline(
            SYMBOL, start=start.isoformat(), end=end.isoformat(),
            ktype=sdk.KLType.K_DAY, autype=sdk.AuType.NONE,
            extended_time=False, max_count=10 if sample else 500,
            page_req_key=key,
        )
        if ret != sdk.RET_OK:
            raise DailyError(f"history APIエラー（権限・履歴枠・頻度制限を確認）: {data}")
        pages.append(data)
        if next_key is None or sample:
            return pd.concat(pages, ignore_index=True)
        if next_key in seen:
            raise DailyError("ページキーが循環しています")
        seen.add(next_key)
        key = next_key
        pause(2.1)
    raise DailyError("ページ数が上限を超えました")


def normalize(raw: pd.DataFrame, sessions: list[str], start: date,
              end: date) -> pd.DataFrame:
    if raw.empty:
        raise DailyError("日足が空です")
    if not {"time_key", *VALUES}.issubset(raw.columns):
        raise DailyError("日足の必須列が不足しています")
    if "code" in raw and not raw.code.eq(SYMBOL).all():
        raise DailyError("応答の銘柄がUS.AAPLではありません")
    dates = pd.to_datetime(raw["time_key"], errors="raise").dt.date
    if dates.isna().any() or dates.duplicated().any():
        raise DailyError("日付の欠損または重複があります")
    if any(d < start or d > end for d in dates):
        raise DailyError("リクエスト範囲外の日足があります")
    if any(d.weekday() >= 5 for d in dates):
        raise DailyError("土日の日足があります")
    actual = {d.isoformat() for d in dates}
    expected = set(sessions)
    if actual != expected:
        raise DailyError(f"営業日不整合: missing={sorted(expected - actual)}, "
                         f"unexpected={sorted(actual - expected)}")
    result = raw[VALUES].copy()
    result["datetime"] = pd.to_datetime(dates).dt.tz_localize(NY).dt.tz_convert("UTC")
    result = validate_data(result)
    result.insert(0, "date", result.datetime.dt.tz_convert(NY).dt.strftime("%Y-%m-%d"))
    return result[["date", "datetime", *VALUES]]


def save(data: pd.DataFrame, base: Path, metadata: dict[str, Any]) -> dict[str, str]:
    base.parent.mkdir(parents=True, exist_ok=True)
    paths = {kind: str(Path(str(base) + suffix)) for kind, suffix in
             [("csv", ".csv"), ("parquet", ".parquet"),
              ("metadata", "_metadata.json")]}
    metadata = {**metadata, "count": len(data), "first_date": data.date.iloc[0],
                "last_date": data.date.iloc[-1]}
    # 同じファイルシステムで全出力と旧版のコピーを準備してから置換する。
    staging = Path(mkdtemp(prefix=f".{base.name}-", dir=base.parent))
    backups: dict[str, Path] = {}
    replaced: list[str] = []
    keep_recovery = False
    try:
        data[["date", *VALUES]].to_csv(staging / "csv", index=False, encoding="utf-8")
        data.to_parquet(staging / "parquet", index=False)
        (staging / "metadata").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for kind, target in paths.items():
            if Path(target).exists():
                backup = staging / f"previous-{kind}"
                shutil.copy2(target, backup)
                backups[kind] = backup
        for kind, target in paths.items():
            os.replace(staging / kind, target)
            replaced.append(kind)
    except BaseException as error:
        failed = []
        for kind in reversed(replaced):
            try:
                if kind in backups:
                    os.replace(backups[kind], paths[kind])
                else:
                    Path(paths[kind]).unlink()
            except OSError:
                failed.append(paths[kind])
        if failed:
            keep_recovery = True
            raise DailyError(
                f"保存失敗後の復元に失敗しました: {failed}。旧版の退避先: {staging}"
            ) from error
        raise
    finally:
        if not keep_recovery:
            shutil.rmtree(staging)
    return paths


def fetch(ctx: Any, sdk: Any, start: date, requested_end: date,
          output: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    calendar_end = min(requested_end, now.astimezone(NY).date())
    if start > calendar_end:
        raise DailyError("開始日は確定済みの終了日以前を指定してください")
    ret, calendar = ctx.request_trading_days(
        code=SYMBOL, start=start.isoformat(), end=calendar_end.isoformat())
    if ret != sdk.RET_OK:
        raise DailyError(f"取引日カレンダーAPIエラー: {calendar}")
    sessions = sorted({item["time"] for item in calendar
                       if date.fromisoformat(item["time"]) <= completed_end(
                           requested_end, now,
                           13 if item["trade_date_type"] == "MORNING" else 16)})
    if not sessions:
        raise DailyError("対象期間に確定済み営業日がありません")
    end = date.fromisoformat(sessions[-1])
    raw = history(ctx, sdk, start, end)
    data = normalize(raw, sessions, start, end)
    metadata = dict(source="moomoo", symbol=SYMBOL, ktype="K_DAY", autype="NONE",
                    session="regular", fetched_at=datetime.now(timezone.utc).isoformat(),
                    request_start=start.isoformat(), request_end=requested_end.isoformat(),
                    effective_end=end.isoformat(), timezone="America/New_York",
                    datetime_timezone="UTC", expected_sessions=len(sessions),
                    source_dates_ascending=bool(pd.to_datetime(raw.time_key).is_monotonic_increasing),
                    sdk_version=version("moomoo-api"),
                    quality="passed", missing_sessions=[], unexpected_sessions=[])
    paths = save(data, output, metadata)
    return dict(status="fetched", count=len(data), first_date=data.date.iloc[0],
                last_date=data.date.iloc[-1], paths=paths,
                head=data[["date", *VALUES]].head().to_dict("records"),
                tail=data[["date", *VALUES]].tail().to_dict("records"))


def probe(ctx: Any, sdk: Any, output: Path) -> dict[str, Any]:
    """短い期間の先頭最大10本だけを取得。全履歴の最古とは断定しない。"""
    results = []
    for year in (2025, 2020, 2010, 2006):
        start, end = date(year, 1, 1), date(year, 1, 15)
        result: dict[str, Any] = dict(start=start.isoformat(), end=end.isoformat())
        try:
            raw = history(ctx, sdk, start, end, sample=True)
            if raw.empty:
                result.update(status="empty", count=0)
            else:
                days = pd.to_datetime(raw.time_key).dt.date
                # 誤った銘柄・範囲の応答を成功として記録しない。
                data = normalize(raw, [d.isoformat() for d in days], start, end)
                result.update(status="sampled", count=len(data),
                              first_date=data.date.iloc[0], last_date=data.date.iloc[-1])
        except ValueError as error:
            result.update(status="error", error=str(error), count=0)
        results.append(result)
        time.sleep(2.1)
    confirmed = [r["first_date"] for r in results if r["status"] == "sampled"]
    report = dict(symbol=SYMBOL, source="moomoo", autype="NONE", ktype="K_DAY",
                  fetched_at=datetime.now(timezone.utc).isoformat(), results=results,
                  earliest_confirmed=min(confirmed) if confirmed else None,
                  absolute_earliest=None,
                  scope="各年1月1〜15日の先頭最大10本のみ。その他の期間と全履歴の最古は未確認")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "probe"])
    parser.add_argument("--from", dest="start", type=date.fromisoformat,
                        default=date(2026, 1, 1))
    parser.add_argument("--to", dest="end", type=date.fromisoformat,
                        default=date(2026, 11, 18))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeoutは正の有限な秒数を指定してください")
    if not args.worker:
        try:
            return subprocess.run(
                [sys.executable, "-m", "quant_lab.stock.daily",
                 *(argv if argv is not None else sys.argv[1:]), "--worker"],
                timeout=args.timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            print(json.dumps(dict(status="error", stage="timeout",
                                  error="OpenD/API処理の制限時間超過")))
            return 2
    stage = "connection"
    ctx = None
    try:
        with socket.create_connection(("127.0.0.1", 11111), timeout=3):
            pass
        stage = "sdk"
        sdk = importlib.import_module("moomoo")
        stage = "handshake"
        ctx = sdk.OpenQuoteContext(host="127.0.0.1", port=11111)
        stage = "authentication"
        ret, state = ctx.get_global_state()
        if ret != sdk.RET_OK or state.get("qot_logined") is not True:
            raise DailyError("行情サーバーへのログインを確認できません")
        stage = args.command
        if args.command == "fetch":
            result = fetch(ctx, sdk, args.start, args.end,
                           args.output or Path("data/stock/aapl_daily_2026"))
        else:
            result = probe(ctx, sdk, args.output or Path("data/stock/aapl_history_probe.json"))
        print(json.dumps(result, ensure_ascii=False))
        if args.command == "probe" and any(r["status"] == "error" for r in result["results"]):
            return 2
        return 0
    except (OSError, ImportError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(status="error", stage=stage, error=str(error)),
                         ensure_ascii=False))
        return 2
    finally:
        if ctx is not None:
            ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
