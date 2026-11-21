"""戦略名に依存せず、確定足・次足始値・当日決済を連携するEngine。"""

from .config import ExperimentConfig
from .execution import SimulationBroker
from .models import EntrySignal, ExitDecision, ExperimentResult, PreparedData, Session
from .registry import ENTRY, EXIT, SIZING
from .strategies.entry import EntryStrategy
from .strategies.exit import ExitStrategy
from .strategies.sizing import SizingStrategy


class ExperimentEngine:
    def __init__(
        self,
        config: ExperimentConfig,
        *,
        entry: EntryStrategy | None = None,
        exit: ExitStrategy | None = None,
        sizing: SizingStrategy | None = None,
    ) -> None:
        self.config = config
        self.entry = (
            entry
            if entry is not None
            else ENTRY.create(config.entry.strategy, config.entry.params)
        )
        self.exit = (
            exit
            if exit is not None
            else EXIT.create(config.exit.strategy, config.exit.params)
        )
        self.sizing = (
            sizing
            if sizing is not None
            else SIZING.create(config.sizing.strategy, config.sizing.params)
        )

    def run(self, prepared: PreparedData) -> ExperimentResult:
        config = self.config
        broker = SimulationBroker(config.initial_capital, config.execution)
        result = ExperimentResult(
            [],
            [],
            list(prepared.exclusions),
            list(prepared.warnings),
            prepared.total_sessions,
            len(prepared.sessions),
            broker.cash,
        )
        completed_cost = 0.0
        for session in prepared.sessions:
            # Entryにはセッション時刻だけ公開し、未確定足を渡さない。
            context = Session(session.date, session.open, session.close)
            result.equity.append(
                {
                    "time": session.open,
                    "equity": broker.cash,
                    "gross_equity": broker.cash + completed_cost,
                }
            )
            pending: EntrySignal | None = None
            entries = 0
            for index, bar in enumerate(session.bars):
                if pending is not None:
                    signal = pending
                    shares = self.sizing.size(
                        broker.cash, broker.fill_price(bar.open, buy=True), signal
                    )
                    if broker.buy(
                        bar.start, bar.open, shares, session.bars[index - 1].end, signal
                    ):
                        entries += 1
                    else:
                        result.warnings.append(
                            f"{session.date} {bar.start.isoformat()}: 買付見送り（余力不足または0株）"
                        )
                    pending = None
                if broker.position is not None:
                    decision = self.exit.on_bar(bar, broker.position)
                    if decision is None and index == len(session.bars) - 1:
                        decision = ExitDecision(bar.close, "market_close", bar.end)
                    if decision is not None:
                        if not bar.start <= decision.time <= bar.end:
                            raise ValueError("Exit時刻が現在足の外です")
                        result.trades.append(
                            broker.sell(
                                decision.time,
                                decision.price,
                                decision.reason,
                                session.date,
                                config.data.symbol,
                            )
                        )
                        completed_cost += result.trades[-1].total_cost
                # 資産は各確定足終値で時価評価。売却予定費用はここでは控除しない。
                equity = broker.cash
                incurred_cost = completed_cost
                if broker.position is not None:
                    position = broker.position
                    equity += position.shares * bar.close
                    incurred_cost += (
                        position.entry_price - position.reference_price
                    ) * position.shares + position.entry_fees
                result.equity.append(
                    {
                        "time": bar.end,
                        "equity": equity,
                        "gross_equity": equity + incurred_cost,
                    }
                )
                # 最終足への新規購入も禁止。最後の2本でシグナルを作らない。
                if (
                    broker.position is None
                    and entries < config.max_trades_per_day
                    and index < len(session.bars) - 2
                ):
                    pending = self.entry.on_close(session.bars[: index + 1], context)
            if broker.position is not None:
                raise RuntimeError("日またぎポジションを検出しました")
        result.final_cash = broker.cash
        return result
