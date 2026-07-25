"""Responsive Streamlit chart for persisted USD/JPY daily KLines."""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import streamlit as st

from chart import (
    DEFAULT_KLINE_HISTORY_PATH,
    ChartDataError,
    ChartDataMissingError,
    ChartPoint,
    load_chart_points,
)

CHART_PATH_ENVIRONMENT_VARIABLE = "USD_JPY_KLINE_PATH"


def build_chart(points: list[ChartPoint]) -> alt.Chart:
    """Build a responsive, dark-mode-friendly USD/JPY line chart."""
    values = [
        {
            "date": point.trading_date.isoformat(),
            "close": float(point.close),
        }
        for point in points
    ]
    return (
        alt.Chart(alt.Data(values=values))
        .mark_line(point=True, strokeWidth=2.5, color="#60A5FA")
        .encode(
            x=alt.X(
                "date:T",
                title="日付",
                axis=alt.Axis(format="%m/%d", grid=True, labelAngle=-45),
            ),
            y=alt.Y(
                "close:Q",
                title="Close価格",
                scale=alt.Scale(zero=False),
                axis=alt.Axis(grid=True),
            ),
            tooltip=[
                alt.Tooltip("date:T", title="日付", format="%Y-%m-%d"),
                alt.Tooltip("close:Q", title="Close", format=".3f"),
            ],
        )
        .properties(height=460)
        .interactive()
    )


def render() -> None:
    st.set_page_config(page_title="USD/JPY", layout="wide")
    st.title("USD/JPY")

    csv_path = Path(
        os.getenv(CHART_PATH_ENVIRONMENT_VARIABLE, str(DEFAULT_KLINE_HISTORY_PATH))
    )
    try:
        with st.spinner("日足データを読み込んでいます..."):
            points = load_chart_points(csv_path)
    except ChartDataMissingError as error:
        st.info(
            f"{error}\n"
            "先に `uv run python daily_kline.py --from-year 2023 --to-year 2026` "
            "を実行して日足データを取得してください。"
        )
        return
    except (ChartDataError, OSError) as error:
        st.error(f"日足データを読み込めませんでした: {error}")
        return

    if not points:
        st.info("表示できる日足データがありません。")
        return

    st.caption(f"過去{len(points)}営業日の仲値Close")
    st.altair_chart(build_chart(points), width="stretch", theme="streamlit")


if __name__ == "__main__":
    render()
