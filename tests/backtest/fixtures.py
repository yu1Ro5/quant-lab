"""外部API不要の人工データと、実コンポーネントをつなぐ共通ヘルパー。"""

import pandas as pd

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.intraday_engine import ExperimentEngine
from quant_lab.backtest.sessions import prepare_sessions


def configuration():
    return {
        "experiment": {"name": "test"},
        "data": {
            "symbol": "US.AAPL",
            "path": "input.parquet",
            "timeframe": "5m",
            "timezone": "America/New_York",
            "bar_timestamp": "end",
            "adjustment": "none",
        },
        "period": {"start": "2026-03-09", "end": "2026-03-09"},
        "entry": {"strategy": "orb", "params": {"opening_range_minutes": 30}},
        "exit": {
            "strategy": "fixed_stop",
            "params": {
                "stop_loss_pct": 0.5,
                "take_profit_pct": 1.0,
                "close_at_market_end": True,
            },
        },
        "sizing": {"strategy": "fixed_shares", "params": {"shares": 1}},
        "execution": {
            "commission_usd_per_side": 0.1,
            "slippage_bps_per_side": 2,
            "spread_bps_per_side": 0,
        },
        "portfolio": {"initial_capital_usd": 5000, "max_trades_per_day": 1},
        "output": {"directory": "results"},
    }


def candles(day="2026-03-09", close="16:00"):
    times = pd.date_range(
        f"{day} 09:35", f"{day} {close}", freq="5min", tz="America/New_York"
    )
    frame = pd.DataFrame(
        {
            "datetime": times,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 10.0,
        }
    )
    # 確定10:05でシグナル、10:05開始の足で約定、同足の損切り。
    frame.loc[6, ["high", "close"]] = [102.0, 102.0]
    frame.loc[7, ["open", "high", "low", "close"]] = [103.0, 105.0, 100.0, 103.0]
    return frame


def execute(frame=None, raw=None, **components):
    config = parse_config(raw or configuration())
    prepared = prepare_sessions(candles() if frame is None else frame, config)
    return ExperimentEngine(config, **components).run(prepared)
