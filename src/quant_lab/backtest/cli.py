"""バックテストMVPを実行するコマンドライン処理。"""

import argparse
import sys
from collections.abc import Sequence

from .broker import DummyBroker
from .config import ConfigError, load_config
from .data import DataValidationError, load_parquet
from .engine import BacktestEngine
from .metrics import calculate_metrics
from .runner import run_experiment
from .strategy import BreakoutStrategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="単一銘柄のOHLCV Parquetを使ってバックテストを実行します"
    )
    parser.add_argument("parquet_path", nargs="?", help="入力するParquetファイルのパス")
    parser.add_argument("--config", help="YAML実験設定のパス")
    parser.add_argument(
        "--diagnostic", action="store_true", help="未検証入力による診断専用の暫定実行"
    )
    parser.add_argument(
        "--quality-only", action="store_true", help="品質検証レポートのみ生成"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.parquet_path):
        parser.error("Parquetパスまたは--configの一方だけを指定してください")
    if (args.diagnostic or args.quality_only) and not args.config:
        parser.error("--diagnostic/--quality-onlyは--configと併用してください")
    if args.config:
        try:
            directory = run_experiment(
                load_config(args.config),
                diagnostic=args.diagnostic,
                quality_only=args.quality_only,
            )
        except (ConfigError, DataValidationError, OSError, ValueError) as error:
            print(f"エラー: {error}", file=sys.stderr)
            return 1
        if args.quality_only:
            import json

            ready = json.loads((directory / "quality.json").read_text())[
                "backtest_ready"
            ]
            print(f"品質レポート: {directory / 'quality.md'}; backtest_ready={ready}")
            return 0 if ready else 2
        print(f"実験結果: {directory}")
        print(f"実験結果HTML：{directory / 'equity_curve.html'}")
        return 0
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
