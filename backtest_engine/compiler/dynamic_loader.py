"""
Deterministic runtime compiler from spec dict to Backtrader strategy class.
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Type

import backtrader as bt

AUTOGEN_DIR = Path("/home/ml/projects/autonomous_quant_agent/backtest_engine/generated_strategies")
AUTOGEN_DIR.mkdir(parents=True, exist_ok=True)


def _ind(module: ModuleType, indicator: Dict[str, Any]) -> Any:
    name = indicator["name"]
    period = int(indicator.get("lookback", 20))
    params = indicator.get("params", {})
    src = module.data.close
    if name == "sma":
        return bt.indicators.SMA(src, period=period, **params)
    if name == "ema":
        return bt.indicators.EMA(src, period=period, **params)
    if name == "wma":
        return bt.indicators.WMA(src, period=period, **params)
    if name == "rsi":
        return bt.indicators.RSI(src, period=period, **params)
    if name == "macd":
        return bt.indicators.MACD(src, period_me1=int(params.get("fast", 12)), period_me2=int(params.get("slow", 26)), period_signal=int(params.get("signal", 9)), **{k: v for k, v in params.items() if k not in {"fast", "slow", "signal"}})
    if name == "macd_hist":
        macd = bt.indicators.MACD(src, period_me1=int(params.get("fast", 12)), period_me2=int(params.get("slow", 26)), period_signal=int(params.get("signal", 9)), **{k: v for k, v in params.items() if k not in {"fast", "slow", "signal"}})
        return macd.lines.macd - macd.lines.signal
    if name == "stoch":
        return bt.indicators.Stochastic(module.data, period=period, period_dfast=int(params.get("dfast", 3)), period_dslow=int(params.get("dslow", 3)), **{k: v for k, v in params.items() if k not in {"dfast", "dslow"}})
    if name == "stoch_rsi":
        return bt.indicators.StochasticRSI(module.data, period=period, period_dfast=int(params.get("dfast", 3)), period_dslow=int(params.get("dslow", 3)), **{k: v for k, v in params.items() if k not in {"dfast", "dslow"}})
    if name == "adx":
        return bt.indicators.ADX(module.data, period=period, **params)
    if name == "atr":
        return bt.indicators.ATR(module.data, period=period, **params)
    if name == "bbands":
        dev = float(params.get("dev", 2.0))
        bb = bt.indicators.BollingerBands(module.data, period=period, devfactor=dev, **{k: v for k, v in params.items() if k != "dev"})
        return bb
    if name == "donchian":
        return bt.indicators.DonchianChannels(module.data, period=period, **params)
    if name == "psar":
        return bt.indicators.ParabolicSAR(module.data, **params)
    if name == "ao":
        fast = int(params.get("fast", 5))
        slow = int(params.get("slow", 34))
        fast_line = bt.indicators.EMA(module.data.hl2(period=1), period=fast, **{k: v for k, v in params.items() if k != "fast"})
        slow_line = bt.indicators.EMA(module.data.hl2(period=1), period=slow, **{k: v for k, v in params.items() if k != "slow"})
        return fast_line - slow_line
    if name == "cci":
        return bt.indicators.CCI(module.data, period=period, **params)
    if name == "vwap":
        return bt.indicators.VolumeWeightedAveragePrice(module.data, period=period, **params)
    raise ValueError(f"unsupported indicator '{name}'")


def build_strategy_class(spec: Dict[str, Any]) -> Type[bt.Strategy]:
    class_name = f"Generated{spec['spec_id']}"
    indicators = spec.get("indicators", [])
    entry_rules = spec.get("entry_rules", [])
    exit_rules = spec.get("exit_rules", [])

    class StrategyClass(bt.Strategy):
        def __init__(self, *args, **kwargs):
            self._inds: Dict[str, Any] = {}
            for ind in indicators:
                self._inds[ind["name"]] = _ind(self, ind)
            self._exit_rules = exit_rules

        def next(self):
            traded = False
            if not self.position:
                for rule in entry_rules:
                    if _eval_condition(self, rule.get("condition", ""), rule.get("direction", "long")):
                        self.buy(size=1, tradeid="entry_long")
                        traded = True
                        break
            else:
                for rule in self._exit_rules:
                    if _apply_exit(self, rule):
                        traded = True
                        break
            if not traded:
                return

    StrategyClass.__name__ = class_name
    StrategyClass.__qualname__ = class_name
    return StrategyClass


def _eval_condition(strategy: bt.Strategy, condition: str, direction: str) -> bool:
    # MVP honest behavior: no opaque condition evaluation.
    # Only 'placeholder' conditions are supported.
    if condition == "__always__":
        return True
    return False


def _apply_exit(strategy: bt.Strategy, rule: Dict[str, Any]) -> bool:
    params = rule.get("params", {})
    if rule.get("exit_type") == "tp" and strategy.position:
        pct = float(params.get("value", 0.01))
        price = strategy.position.price * (1.0 + pct)
        strategy.sell(exectype=bt.Order.Limit, price=price, size=strategy.position.size, tradeid="tp")
        return True
    if rule.get("exit_type") == "stop" and strategy.position:
        pct = float(params.get("value", 0.007))
        price = strategy.position.price * (1.0 - pct)
        strategy.sell(exectype=bt.Order.Stop, price=price, size=strategy.position.size, tradeid="sl")
        return True
    if rule.get("exit_type") == "time" and strategy.position:
        if len(strategy.data) % max(1, int(params.get("bars", 24))) == 0:
            strategy.close(tradeid="time_exit")
            return True
    return False


def compile_spec(spec: Dict[str, Any]) -> Type[bt.Strategy]:
    return build_strategy_class(spec)
