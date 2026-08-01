"""GMOコインからUSD/JPYの日足KLineを取得して保存する。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final, Literal

import requests

KLINES_URL: Final = "https://forex-api.coin.z.com/public/v1/klines"
SYMBOL: Final = "USD_JPY"
INTERVAL: Final = "1day"
HTTP_TIMEOUT_SECONDS: Final = 10
DEFAULT_KLINE_HISTORY_PATH: Final = Path("data/usd_jpy_1day.csv")
KLINE_HISTORY_COLUMNS: Final = [
    "open_time",
    "bid_open",
    "bid_high",
    "bid_low",
    "bid_close",
    "ask_open",
    "ask_high",
    "ask_low",
    "ask_close",
]
PRICE_FIELDS: Final = ("open", "high", "low", "close")
PriceType = Literal["BID", "ASK"]


@dataclass(frozen=True)
class Ohlc:
    """日足KLineのBIDまたはASK片側の四本値。"""

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class DailyKline:
    """同じ開始日時のBIDとASKをまとめたUSD/JPYの日足KLine。"""

    open_time: datetime
    bid: Ohlc
    ask: Ohlc

    def __post_init__(self) -> None:
        """開始日時にタイムゾーンが明示されていることを確認する。"""
        if self.open_time.tzinfo is None or self.open_time.utcoffset() is None:
            raise ValueError("DailyKline open_time must include a timezone")


@dataclass(frozen=True)
class SaveResult:
    """取得したKLineを履歴CSVへ統合した件数。"""

    fetched: int
    added: int
    updated: int


def _parse_open_time(value: object) -> datetime:
    """APIのミリ秒単位Unix時刻をUTCの日時へ変換する。"""
    if not isinstance(value, str):
        raise ValueError(f"KLine has an invalid openTime: {value!r}")
    try:
        milliseconds = int(value)
    except ValueError as error:
        raise ValueError(f"KLine has an invalid openTime: {value!r}") from error
    if milliseconds < 0 or str(milliseconds) != value:
        raise ValueError(f"KLine has an invalid openTime: {value!r}")
    try:
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            milliseconds=milliseconds
        )
    except OverflowError as error:
        raise ValueError(f"KLine has an invalid openTime: {value!r}") from error


def _parse_price(item: dict[str, object], field: str) -> Decimal:
    """APIのKLine要素から0より大きい有限の価格を読み取る。"""
    value = item.get(field)
    try:
        if value is None or isinstance(value, bool):
            raise InvalidOperation
        price = Decimal(str(value))
        if not price.is_finite() or price <= 0:
            raise InvalidOperation
        return price
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"KLine has an invalid {field}: {value!r}") from error


def _parse_ohlc(item: dict[str, object]) -> Ohlc:
    """APIのKLine要素を検証済みの四本値へ変換する。"""
    ohlc = Ohlc(**{field: _parse_price(item, field) for field in PRICE_FIELDS})
    if (
        ohlc.low > ohlc.high
        or not ohlc.low <= ohlc.open <= ohlc.high
        or not ohlc.low <= ohlc.close <= ohlc.high
    ):
        raise ValueError("KLine OHLC values are inconsistent")
    return ohlc


def fetch_price_klines(year: int, price_type: PriceType) -> dict[datetime, Ohlc]:
    """指定年のUSD/JPY日足KLineをBIDまたはASKについて取得する。

    GMOコインでは ``interval=1day`` の場合、``date`` に4桁の年を指定する。
    既存方針に従い、自動再試行は行わず、タイムアウト付きで1回だけ取得する。
    """
    if year < 1000 or year > 9999:
        raise ValueError(f"year must be a four-digit value: {year!r}")

    params = {
        "symbol": SYMBOL,
        "priceType": price_type,
        "interval": INTERVAL,
        "date": str(year),
    }
    try:
        response = requests.get(
            KLINES_URL,
            params=params,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.Timeout as error:
        raise RuntimeError(
            f"GMO KLine request timed out after {HTTP_TIMEOUT_SECONDS} seconds"
        ) from error
    except requests.RequestException as error:
        raise RuntimeError(f"GMO KLine HTTP request failed: {error}") from error

    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError) as error:
        raise ValueError("GMO KLine API returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("GMO KLine API response must be a JSON object")
    if payload.get("status") != 0:
        raise ValueError(
            f"GMO KLine API returned unsuccessful status: {payload.get('status')!r}"
        )
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("GMO KLine API response has an invalid data array")

    klines: dict[datetime, Ohlc] = {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("GMO KLine API data must contain JSON objects")
        open_time = _parse_open_time(item.get("openTime"))
        if open_time in klines:
            raise ValueError(
                f"GMO KLine API returned duplicate openTime for {price_type}: "
                f"{open_time.isoformat()}"
            )
        klines[open_time] = _parse_ohlc(item)
    return klines


def fetch_daily_klines(year: int) -> list[DailyKline]:
    """指定年のUSD/JPY日足KLineを取得し、BIDとASKを結合する。"""
    bids = fetch_price_klines(year, "BID")
    asks = fetch_price_klines(year, "ASK")
    if bids.keys() != asks.keys():
        missing_bid = sorted(time.isoformat() for time in asks.keys() - bids.keys())
        missing_ask = sorted(time.isoformat() for time in bids.keys() - asks.keys())
        raise ValueError(
            "BID and ASK KLine openTime values do not match "
            f"(missing BID: {missing_bid}, missing ASK: {missing_ask})"
        )
    return [
        DailyKline(open_time=open_time, bid=bids[open_time], ask=asks[open_time])
        for open_time in sorted(bids)
    ]


def fetch_daily_klines_for_years(from_year: int, to_year: int) -> list[DailyKline]:
    """開始年から終了年までのUSD/JPY日足KLineを年の昇順で取得する。"""
    if from_year > to_year:
        raise ValueError("from_year must be less than or equal to to_year")
    return [
        kline
        for year in range(from_year, to_year + 1)
        for kline in fetch_daily_klines(year)
    ]


def _kline_to_row(kline: DailyKline) -> dict[str, str]:
    return {
        "open_time": kline.open_time.astimezone(timezone.utc).isoformat(),
        "bid_open": str(kline.bid.open),
        "bid_high": str(kline.bid.high),
        "bid_low": str(kline.bid.low),
        "bid_close": str(kline.bid.close),
        "ask_open": str(kline.ask.open),
        "ask_high": str(kline.ask.high),
        "ask_low": str(kline.ask.low),
        "ask_close": str(kline.ask.close),
    }


def _validate_csv_row(row: dict[str, str]) -> DailyKline:
    try:
        open_time = datetime.fromisoformat(row["open_time"])
    except (KeyError, ValueError) as error:
        raise ValueError(
            f"CSV has an invalid open_time: {row.get('open_time')!r}"
        ) from error
    if open_time.tzinfo is None:
        raise ValueError(f"CSV open_time must include a timezone: {row['open_time']!r}")
    item: dict[str, object] = {}
    bid_values = {field: row.get(f"bid_{field}") for field in PRICE_FIELDS}
    ask_values = {field: row.get(f"ask_{field}") for field in PRICE_FIELDS}
    item.update(bid_values)
    bid = _parse_ohlc(item)
    item.clear()
    item.update(ask_values)
    ask = _parse_ohlc(item)
    return DailyKline(open_time.astimezone(timezone.utc), bid, ask)


def _read_history(path: Path) -> dict[datetime, DailyKline]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != KLINE_HISTORY_COLUMNS:
            raise ValueError(f"CSV has an unsupported header: {reader.fieldnames}")
        history: dict[datetime, DailyKline] = {}
        for row in reader:
            kline = _validate_csv_row(row)
            if kline.open_time in history:
                raise ValueError(
                    f"CSV has a duplicate open_time: {kline.open_time.isoformat()}"
                )
            history[kline.open_time] = kline
    return history


def _write_history(path: Path, klines: list[DailyKline]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        newline="",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as file:
        temporary_path = Path(file.name)
        writer = csv.DictWriter(file, fieldnames=KLINE_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(_kline_to_row(kline) for kline in klines)
    temporary_path.replace(path)


def save_daily_klines(
    klines: list[DailyKline],
    csv_path: str | Path = DEFAULT_KLINE_HISTORY_PATH,
) -> SaveResult:
    """KLineを ``open_time`` ごとにCSVへ統合し、UTCの日時順で保存する。"""
    path = Path(csv_path)
    history = _read_history(path)
    incoming: dict[datetime, DailyKline] = {}
    for kline in klines:
        utc_kline = DailyKline(
            kline.open_time.astimezone(timezone.utc),
            kline.bid,
            kline.ask,
        )
        if utc_kline.open_time in incoming:
            raise ValueError(
                f"Fetched KLines contain duplicate open_time: "
                f"{utc_kline.open_time.isoformat()}"
            )
        incoming[utc_kline.open_time] = utc_kline

    added = sum(open_time not in history for open_time in incoming)
    updated = sum(
        open_time in history and history[open_time] != kline
        for open_time, kline in incoming.items()
    )
    history.update(incoming)
    if added or updated or not path.exists():
        _write_history(path, [history[open_time] for open_time in sorted(history)])
    return SaveResult(fetched=len(incoming), added=added, updated=updated)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and save GMO Coin USD/JPY daily KLines."
    )
    parser.add_argument("--from-year", type=int, required=True)
    parser.add_argument("--to-year", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_KLINE_HISTORY_PATH,
    )
    return parser.parse_args()


def main() -> None:
    """日足KLine取得用のコマンドライン処理を実行する。"""
    args = _parse_args()
    klines = fetch_daily_klines_for_years(args.from_year, args.to_year)
    result = save_daily_klines(klines, args.output)
    print(f"Fetched: {result.fetched}")
    print(f"Added: {result.added}")
    print(f"Updated: {result.updated}")
    print(f"CSV: {args.output}")


if __name__ == "__main__":
    main()
