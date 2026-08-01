"""バックテストMVPを実行するコマンドライン処理。"""

import argparse
import sys
from collections.abc import Sequence

from .broker import DummyBroker
from .data import DataValidationError, load_parquet
from .engine import BacktestEngine
from .metrics import calculate_metrics
from .strategy import BreakoutStrategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="単一銘柄のOHLCV Parquetを使ってバックテストを実行します"
    )
    parser.add_argument("parquet_path", help="入力するParquetファイルのパス")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_parquet(args.parquet_path)
    except DataValidationError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    broker = DummyBroker()
    trades = BacktestEngine(BreakoutStrategy(), broker).run(data)
    metrics = calculate_metrics(trades)

    print(f"Parquet読込成功: {len(data)}件")
    print(f"総取引回数: {metrics.total_trades}")
    print(f"勝率: {metrics.win_rate:.2%}")
    print(f"総損益: {metrics.total_profit_loss:.2f}円")
    return 0
