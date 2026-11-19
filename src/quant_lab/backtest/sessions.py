"""明示的な時刻仕様とXNYSカレンダーから完全な立会だけを作る。"""

import warnings

import exchange_calendars as xcals  # type: ignore[import-untyped]
import pandas as pd

from .config import ExperimentConfig
from .data import DataValidationError, validate_data
from .models import Bar, PreparedData, Session


def prepare_sessions(data: pd.DataFrame, config: ExperimentConfig) -> PreparedData:
    frame = data.copy()
    if "datetime" not in frame:
        raise DataValidationError("必須カラムが不足しています: datetime")
    try:
        # naive値は必ず設定timezoneで解釈。曖昧/存在しないDST時刻は拒否。
        times = pd.to_datetime(frame["datetime"], format="mixed", errors="raise")
        if not isinstance(times.dtype, pd.DatetimeTZDtype):
            times = times.dt.tz_localize(
                config.data.timezone, ambiguous="raise", nonexistent="raise"
            )
        times = times.dt.tz_convert("UTC")
    except (ValueError, TypeError, AttributeError) as error:
        raise DataValidationError(
            f"datetimeのtimezoneを解釈できません: {error}"
        ) from error
    frame["datetime"] = times
    # 空データでも期間内の全営業日を欠損として報告する。
    if not frame.empty:
        frame = validate_data(frame)
    else:
        missing = {"open", "high", "low", "close", "volume"} - set(frame.columns)
        if missing:
            raise DataValidationError(f"必須カラム不足: {sorted(missing)}")
    offset = pd.Timedelta(minutes=5)
    frame["start"] = (
        frame.datetime - offset
        if config.data.bar_timestamp == "end"
        else frame.datetime
    )
    frame["end"] = frame.start + offset
    frame["date"] = frame.start.dt.tz_convert("America/New_York").dt.strftime(
        "%Y-%m-%d"
    )
    frame = frame[(frame.date >= config.start) & (frame.date <= config.end)]
    # カレンダー生成の端に休場日のみが指定されても正しく空の期間にできる。
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            calendar = xcals.get_calendar(
                "XNYS",
                start=pd.Timestamp(config.start) - pd.Timedelta(days=7),
                end=pd.Timestamp(config.end) + pd.Timedelta(days=7),
            )
            schedule = calendar.schedule.loc[config.start : config.end]
        except (ValueError, Warning) as error:
            raise DataValidationError(
                f"市場カレンダーを作成できません: {error}"
            ) from error
    result = PreparedData(total_sessions=len(schedule))
    session_dates = {label.strftime("%Y-%m-%d") for label in schedule.index}
    non_session = int((~frame.date.isin(session_dates)).sum())
    if non_session:
        result.warnings.append(f"休場日の足を除外: {non_session}本")
    for label, row in schedule.iterrows():
        day = label.strftime("%Y-%m-%d")
        opened, closed = pd.Timestamp(row["open"]), pd.Timestamp(row["close"])
        observed = frame[frame.date == day]
        regular = observed[(observed.start >= opened) & (observed.end <= closed)]
        extended = len(observed) - len(regular)
        if extended:
            result.warnings.append(f"{day}: 立会時間外の足を除外: {extended}本")
        expected = pd.date_range(opened, closed - offset, freq="5min")
        actual = pd.DatetimeIndex(regular.start)
        if list(actual) != list(expected):
            missing_count = len(expected.difference(actual))
            extra_count = len(actual.difference(expected))
            result.exclusions.append(
                {
                    "date": day,
                    "reason": "incomplete_session",
                    "detail": f"欠落={missing_count}本, グリッド外={extra_count}本",
                }
            )
            continue
        bars = tuple(
            Bar(
                pd.Timestamp(r.start),
                pd.Timestamp(r.end),
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                float(r.volume),
            )
            for r in regular.itertuples(index=False)
        )
        result.sessions.append(Session(day, opened, closed, bars))
    return result
