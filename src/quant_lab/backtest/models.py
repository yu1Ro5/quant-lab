"""実験系の値オブジェクト。価格はUSD、時刻はUTC。"""

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Bar:
    start: pd.Timestamp
    end: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Session:
    date: str
    open: pd.Timestamp
    close: pd.Timestamp
    bars: tuple[Bar, ...] = ()


@dataclass(frozen=True)
class EntrySignal:
    details: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitDecision:
    price: float
    reason: str
    time: pd.Timestamp


@dataclass(frozen=True)
class Position:
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    shares: int
    reference_price: float
    entry_price: float
    signal: EntrySignal
    entry_fees: float = 0.0


@dataclass(frozen=True)
class CompletedTrade:
    date: str
    symbol: str
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    shares: int
    entry_reference_price: float
    exit_reference_price: float
    entry_price: float
    exit_price: float
    exit_reason: str
    orb_high: float | None
    orb_low: float | None
    gross_pnl: float
    commission: float
    slippage: float
    spread: float
    total_cost: float
    net_pnl: float
    sec_fee: float = 0.0
    finra_fee: float = 0.0
    cat_fee: float = 0.0


@dataclass
class PreparedData:
    sessions: list[Session] = field(default_factory=list)
    exclusions: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_sessions: int = 0


@dataclass
class ExperimentResult:
    trades: list[CompletedTrade]
    equity: list[dict[str, object]]
    exclusions: list[dict[str, str]]
    warnings: list[str]
    total_sessions: int
    valid_sessions: int
    final_cash: float
