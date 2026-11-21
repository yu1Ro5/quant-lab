"""バックテスト結果の集計。"""

from dataclasses import dataclass
from typing import Any, Iterable

from .broker import Trade
from .models import ExperimentResult


@dataclass(frozen=True)
class BacktestMetrics:
    """MVPで出力する最小限の評価指標。"""

    total_trades: int
    win_rate: float
    total_profit_loss: float


def calculate_metrics(trades: Iterable[Trade]) -> BacktestMetrics:
    """完了した取引から取引回数、勝率、総損益を集計する。"""
    completed = list(trades)
    total_trades = len(completed)
    wins = sum(trade.profit_loss > 0 for trade in completed)
    return BacktestMetrics(
        total_trades=total_trades,
        win_rate=wins / total_trades if total_trades else 0.0,
        total_profit_loss=sum(trade.profit_loss for trade in completed),
    )


def experiment_metrics(
    result: ExperimentResult, initial_capital: float
) -> dict[str, Any]:
    trades = result.trades
    net = [t.net_pnl for t in trades]
    positive = sum(p for p in net if p > 0)
    negative = -sum(p for p in net if p < 0)
    peak = initial_capital
    drawdown = drawdown_pct = 0.0
    for point in result.equity:
        equity = float(point["equity"])  # type: ignore[arg-type]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        drawdown_pct = max(drawdown_pct, (peak - equity) / peak)
    trading_days = len({t.date for t in trades})
    wins = [p for p in net if p > 0]
    losses = [p for p in net if p < 0]
    average_profit = sum(wins) / len(wins) if wins else 0
    average_loss = sum(losses) / len(losses) if losses else 0

    def groups(key: str) -> list[dict[str, Any]]:
        values = sorted(
            {t.exit_reason if key == "reason" else t.date[:7] for t in trades}
        )
        rows = []
        for value in values:
            selected = [
                t
                for t in trades
                if (t.exit_reason if key == "reason" else t.date[:7]) == value
            ]
            rows.append(
                dict(
                    group=value,
                    total_trades=len(selected),
                    win_rate=sum(t.net_pnl > 0 for t in selected) / len(selected),
                    gross_pnl=sum(t.gross_pnl for t in selected),
                    total_cost=sum(t.total_cost for t in selected),
                    net_pnl=sum(t.net_pnl for t in selected),
                )
            )
        return rows

    return dict(
        average_profit=average_profit,
        average_loss=average_loss,
        profit_loss_ratio=average_profit / abs(average_loss) if average_loss else None,
        exit_reason_performance=groups("reason"),
        monthly_performance=groups("month"),
        total_trades=len(trades),
        win_rate=sum(p > 0 for p in net) / len(net) if net else 0,
        gross_pnl=sum(t.gross_pnl for t in trades),
        total_cost=sum(t.total_cost for t in trades),
        commission=sum(t.commission for t in trades),
        sec_fee=sum(t.sec_fee for t in trades),
        finra_fee=sum(t.finra_fee for t in trades),
        cat_fee=sum(t.cat_fee for t in trades),
        slippage=sum(t.slippage for t in trades),
        spread=sum(t.spread for t in trades),
        net_pnl=sum(net),
        average_net_pnl=sum(net) / len(net) if net else 0,
        profit_factor=positive / negative if negative else None,
        profit_factor_status="finite"
        if negative
        else ("no_losses" if positive else "no_trades_or_flat"),
        max_drawdown_usd=drawdown,
        max_drawdown_fraction=drawdown_pct,
        equity_basis="initial capital and regular bar-close mark-to-market; incurred costs included; future exit fees excluded",
        total_sessions=result.total_sessions,
        trading_days=trading_days,
        no_trade_days=result.valid_sessions - trading_days,
        excluded_days=len(result.exclusions),
        exclusions=result.exclusions,
        warnings=result.warnings,
        initial_capital_usd=initial_capital,
        final_cash_usd=result.final_cash,
        currency="USD",
    )
