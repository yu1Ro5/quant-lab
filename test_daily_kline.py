"""Tests for USD/JPY daily KLine fetching and persistence."""

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

import pytest
import requests

import daily_kline


def kline_item(
    open_time: str = "1704067200000",
    *,
    open_price: str = "140.100",
    high: str = "141.200",
    low: str = "139.900",
    close: str = "140.800",
) -> dict[str, str]:
    return {
        "openTime": open_time,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


def response(payload: object) -> Mock:
    result = Mock()
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


def test_fetches_bid_and_ask_and_converts_api_response() -> None:
    bid_payload = {"status": 0, "data": [kline_item()]}
    ask_payload = {
        "status": 0,
        "data": [
            kline_item(
                open_price="140.110",
                high="141.210",
                low="139.910",
                close="140.810",
            )
        ],
    }
    with patch(
        "daily_kline.requests.get",
        side_effect=[response(bid_payload), response(ask_payload)],
    ) as request:
        result = daily_kline.fetch_daily_klines(2024)

    assert request.call_args_list == [
        call(
            daily_kline.KLINES_URL,
            params={
                "symbol": "USD_JPY",
                "priceType": "BID",
                "interval": "1day",
                "date": "2024",
            },
            timeout=daily_kline.HTTP_TIMEOUT_SECONDS,
        ),
        call(
            daily_kline.KLINES_URL,
            params={
                "symbol": "USD_JPY",
                "priceType": "ASK",
                "interval": "1day",
                "date": "2024",
            },
            timeout=daily_kline.HTTP_TIMEOUT_SECONDS,
        ),
    ]
    assert result == [
        daily_kline.DailyKline(
            open_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            bid=daily_kline.Ohlc(
                open=Decimal("140.100"),
                high=Decimal("141.200"),
                low=Decimal("139.900"),
                close=Decimal("140.800"),
            ),
            ask=daily_kline.Ohlc(
                open=Decimal("140.110"),
                high=Decimal("141.210"),
                low=Decimal("139.910"),
                close=Decimal("140.810"),
            ),
        )
    ]


def test_fetches_multiple_years_in_requested_order() -> None:
    first = daily_kline.DailyKline(
        datetime(2023, 1, 1, tzinfo=timezone.utc),
        daily_kline.Ohlc(*(Decimal(value) for value in ("1", "2", "1", "2"))),
        daily_kline.Ohlc(*(Decimal(value) for value in ("1", "2", "1", "2"))),
    )
    second = daily_kline.DailyKline(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        first.bid,
        first.ask,
    )
    with patch(
        "daily_kline.fetch_daily_klines",
        side_effect=[[first], [second]],
    ) as fetch:
        result = daily_kline.fetch_daily_klines_for_years(2023, 2024)

    assert result == [first, second]
    assert fetch.call_args_list == [call(2023), call(2024)]


def test_detects_mismatched_bid_and_ask_open_times() -> None:
    bid_payload = {"status": 0, "data": [kline_item("1704067200000")]}
    ask_payload = {"status": 0, "data": [kline_item("1704153600000")]}
    with (
        patch(
            "daily_kline.requests.get",
            side_effect=[response(bid_payload), response(ask_payload)],
        ),
        pytest.raises(ValueError, match="do not match"),
    ):
        daily_kline.fetch_daily_klines(2024)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"status": 5, "data": []}, "unsuccessful status"),
        ({"status": 0, "data": "invalid"}, "invalid data array"),
        ([], "JSON object"),
    ],
)
def test_rejects_unsuccessful_or_invalid_responses(
    payload: object,
    message: str,
) -> None:
    with (
        patch("daily_kline.requests.get", return_value=response(payload)),
        pytest.raises(ValueError, match=message),
    ):
        daily_kline.fetch_price_klines(2024, "BID")


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (kline_item(open_price="NaN"), "invalid open"),
        (kline_item(open_time="not-a-timestamp"), "invalid openTime"),
        (kline_item(high="139.000"), "inconsistent"),
    ],
)
def test_rejects_invalid_price_open_time_and_ohlc(
    item: dict[str, str],
    message: str,
) -> None:
    payload = {"status": 0, "data": [item]}
    with (
        patch("daily_kline.requests.get", return_value=response(payload)),
        pytest.raises(ValueError, match=message),
    ):
        daily_kline.fetch_price_klines(2024, "BID")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (requests.HTTPError("503 Server Error"), "HTTP request failed"),
        (requests.Timeout("slow"), "timed out"),
    ],
)
def test_handles_http_error_and_timeout(
    error: requests.RequestException,
    message: str,
) -> None:
    with (
        patch("daily_kline.requests.get", side_effect=error) as request,
        pytest.raises(RuntimeError, match=message),
    ):
        daily_kline.fetch_price_klines(2024, "BID")
    request.assert_called_once()


def test_saves_sorted_rows_and_merges_without_duplicates() -> None:
    bid = daily_kline.Ohlc(*(Decimal(value) for value in ("140", "142", "139", "141")))
    ask = daily_kline.Ohlc(*(Decimal(value) for value in ("141", "143", "140", "142")))
    old = daily_kline.DailyKline(
        datetime(2024, 1, 2, tzinfo=timezone.utc),
        bid,
        ask,
    )
    new = daily_kline.DailyKline(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        bid,
        ask,
    )
    with TemporaryDirectory() as directory:
        path = Path(directory) / "history.csv"
        first = daily_kline.save_daily_klines([old], path)
        second = daily_kline.save_daily_klines([new, old], path)
        third = daily_kline.save_daily_klines([new, old], path)
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

    assert first == daily_kline.SaveResult(fetched=1, added=1, updated=0)
    assert second == daily_kline.SaveResult(fetched=2, added=1, updated=0)
    assert third == daily_kline.SaveResult(fetched=2, added=0, updated=0)
    assert [row["open_time"] for row in rows] == [
        "2024-01-01T00:00:00+00:00",
        "2024-01-02T00:00:00+00:00",
    ]


def test_updates_existing_row_without_destroying_other_rows() -> None:
    bid = daily_kline.Ohlc(*(Decimal(value) for value in ("140", "142", "139", "141")))
    ask = daily_kline.Ohlc(*(Decimal(value) for value in ("141", "143", "140", "142")))
    original = daily_kline.DailyKline(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        bid,
        ask,
    )
    updated = daily_kline.DailyKline(
        original.open_time,
        daily_kline.Ohlc(
            Decimal("140"),
            Decimal("142"),
            Decimal("139"),
            Decimal("141.5"),
        ),
        ask,
    )
    other = daily_kline.DailyKline(
        datetime(2024, 1, 2, tzinfo=timezone.utc),
        bid,
        ask,
    )
    with TemporaryDirectory() as directory:
        path = Path(directory) / "history.csv"
        daily_kline.save_daily_klines([original, other], path)
        result = daily_kline.save_daily_klines([updated], path)
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

    assert result == daily_kline.SaveResult(fetched=1, added=0, updated=1)
    assert len(rows) == 2
    assert rows[0]["bid_close"] == "141.5"
    assert rows[1]["bid_close"] == "141"
