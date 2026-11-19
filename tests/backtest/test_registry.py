"""registryのテスト。外部APIは使用しない。"""

import unittest

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.models import EntrySignal, ExitDecision

from .fixtures import configuration, execute


class RegistryTests(unittest.TestCase):
    def test_registry_can_run_yaml_named_custom_components_without_engine_change(self):
        from quant_lab.backtest.registry import ENTRY, EXIT, SIZING

        class Entry:
            def on_close(self, history, session):
                assert not session.bars
                return EntrySignal() if len(history) == 2 else None

        class Exit:
            def on_bar(self, bar, position):
                return ExitDecision(bar.close, "registered_exit", bar.end)

        class Sizing:
            def size(self, cash, price, signal):
                return 4

        for registry, implementation in [
            (ENTRY, Entry),
            (EXIT, Exit),
            (SIZING, Sizing),
        ]:
            registry.register("test_component", implementation)
            self.addCleanup(registry.factories.pop, "test_component")
        raw = configuration()
        for kind in ["entry", "exit", "sizing"]:
            raw[kind] = {"strategy": "test_component", "params": {}}
        trade = execute(raw=raw).trades[0]
        self.assertEqual(trade.shares, 4)
        self.assertEqual(trade.exit_reason, "registered_exit")

    def test_dataclass_strategy_runtime_state_is_not_a_config_parameter(self):
        from dataclasses import dataclass, field

        from quant_lab.backtest.registry import ENTRY

        @dataclass
        class Entry:
            threshold: int = 7
            calls: int = field(default=0, init=False)

            def on_close(self, history, session):
                self.calls += 1
                return EntrySignal() if len(history) == self.threshold else None

        ENTRY.register("stateful_entry", Entry)
        self.addCleanup(ENTRY.factories.pop, "stateful_entry")
        raw = configuration()
        raw["entry"] = {"strategy": "stateful_entry", "params": {}}
        config = parse_config(raw)
        self.assertEqual(config.entry.params, {"threshold": 7})
        # Runnerと同じスナップショット再読込でも実行できる。
        self.assertEqual(len(execute(raw=config.snapshot()).trades), 1)
