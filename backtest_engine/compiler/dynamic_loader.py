"""
Deterministic runtime compiler: spec dict -> runnable bt.Strategy.

Correctness rules (MVP honest execution):
  - Entry conditions come from the shared grammar (synthesis_layer/grammar.py):
    explicit whitelist, fail-closed. No eval, no string tricks.
  - Position size comes from the spec: size = lots * 100 oz (1 lot = 100 oz).
  - TP/SL are REAL pending orders (Limit/Stop) placed when the entry fills.
    Timing (coo off, coc off): a signal on bar i -> market entry fills at the
    close of bar i+1; TP/SL are queued and can fill from bar i+2 onward when
    the bar's range crosses the level. This models next-bar fills without
    lookahead and guarantees the position ALWAYS closes:
      * time exit -> cancel pendings + market close
      * end of data -> cancel pendings + market close (marked if no bar left)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

import backtrader as bt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from synthesis_layer.grammar import (  # noqa: E402
    DEFAULT_PERIOD,
    evaluate_condition,
    parse_condition,
)

AUTOGEN_DIR = PROJECT_ROOT / "generated_strategies"
AUTOGEN_DIR.mkdir(parents=True, exist_ok=True)


def _ind(module: Any, name: str, period: int, params: Dict[str, Any]) -> Any:
    """Instantiate a backtrader indicator by (name, period)."""
    if name == "sma":
        return bt.indicators.SMA(module.data.close, period=period, **params)
    if name == "ema":
        return bt.indicators.EMA(module.data.close, period=period, **params)
    if name == "wma":
        return bt.indicators.WMA(module.data.close, period=period, **params)
    if name == "rsi":
        return bt.indicators.RSI(module.data.close, period=period, **params)
    if name == "macd":
        return bt.indicators.MACD(
            module.data.close,
            period_me1=int(params.get("fast", 12)),
            period_me2=int(params.get("slow", 26)),
            period_signal=int(params.get("signal", 9)),
        )
    if name == "macd_hist":
        macd = bt.indicators.MACD(
            module.data.close,
            period_me1=int(params.get("fast", 12)),
            period_me2=int(params.get("slow", 26)),
            period_signal=int(params.get("signal", 9)),
        )
        return macd.lines.macd - macd.lines.signal
    if name == "stoch":
        st = bt.indicators.Stochastic(
            module.data, period=period,
            period_dfast=int(params.get("dfast", 3)),
            period_dslow=int(params.get("dslow", 3)),
        )
        return st.lines.k
    if name == "stoch_k":
        st = bt.indicators.Stochastic(
            module.data, period=period,
            period_dfast=int(params.get("dfast", 3)),
            period_dslow=int(params.get("dslow", 3)),
        )
        return st.lines.k
    if name == "stoch_rsi":
        return bt.indicators.StochasticRSI(
            module.data, period=period,
            period_dfast=int(params.get("dfast", 3)),
            period_dslow=int(params.get("dslow", 3)),
        )
    if name == "adx":
        return bt.indicators.ADX(module.data, period=period, **params)
    if name == "atr":
        return bt.indicators.ATR(module.data, period=period, **params)
    if name == "bbands":
        dev = float(params.get("dev", 2.0))
        return bt.indicators.BollingerBands(module.data, period=period, devfactor=dev)
    if name == "donchian":
        return bt.indicators.DonchianChannels(module.data, period=period, **params)
    if name == "psar":
        return bt.indicators.ParabolicSAR(module.data, **params)
    if name == "ao":
        fast = int(params.get("fast", 5))
        slow = int(params.get("slow", 34))
        return bt.indicators.EMA(module.data.hl2(period=1), period=fast) - bt.indicators.EMA(module.data.hl2(period=1), period=slow)
    if name == "cci":
        return bt.indicators.CCI(module.data, period=period, **params)
    if name == "mfi":
        return bt.indicators.MoneyFlowIndex(module.data, period=period, **params)
    if name == "obv":
        return bt.indicators.OnBalanceVolume(module.data)
    if name == "vwap":
        # backtrader has no built-in VWAP. Use a rolling
        # sum(typical_price * volume) / sum(volume) over `period` bars.
        # Typical price = (high + low + close) / 3.
        class _RollingVWAP(bt.Indicator):
            lines = ("vwap",)
            params = (("period", 20),)

            def __init__(self):
                tp = (self.data.high + self.data.low + self.data.close) / 3.0
                self._tp = tp
                self._vol = self.data.volume
                self._tp_vol_sum = bt.indicators.SMA(tp * self.data.volume, period=self.p.period)
                self._vol_sum = bt.indicators.SMA(self.data.volume, period=self.p.period)

            def next(self):
                vsum = self._vol_sum[0]
                self.vwap[0] = (self._tp_vol_sum[0] / vsum) if vsum else 0.0

        return _RollingVWAP(module.data, period=period)
    if name == "volume_ratio":
        vol_sma = bt.indicators.SMA(module.data.volume, period=period)
        return module.data.volume / vol_sma
    raise ValueError(f"unsupported indicator '{name}'")


def build_strategy_class(spec: Dict[str, Any]) -> Type[bt.Strategy]:
    class_name = f"Generated{spec['spec_id']}"
    indicators = spec.get("indicators", [])
    entry_rules = spec.get("entry_rules", [])
    lots = float(spec.get("sizing", {}).get("lots", 0.01))
    size_oz = int(round(lots * 100))  # 0.01 lot = 1 oz
    if size_oz <= 0:
        raise ValueError(f"spec sizing.lots={lots} resolves to 0 oz")

    # Pre-parse all entry conditions (fail-closed at compile time).
    parsed_entries: List[Tuple[str, List[Dict[str, Any]]]] = []
    for rule in entry_rules:
        direction = str(rule.get("direction", "long")).lower()
        if direction not in ("long", "short"):
            raise ValueError(f"entry direction must be long|short, got {direction!r}")
        primitives = parse_condition(rule.get("condition", ""))
        parsed_entries.append((direction, primitives))
    if not parsed_entries:
        raise ValueError("spec has no entry rules")

    # Declared indicators: name -> (period, params).
    declared: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for ind in indicators:
        name = ind["name"]
        declared[name] = (int(ind.get("lookback", DEFAULT_PERIOD.get(name, 20))), dict(ind.get("params", {})))

    # Condition refs must be covered by declarations (same name; if the
    # condition pins a period it must match the declaration). Fail-closed.
    from synthesis_layer.grammar import condition_refs as _crefs
    cond_refs = set()
    for _, primitives in parsed_entries:
        cond_refs |= _crefs(primitives)
    for name, period in sorted(cond_refs):
        if name not in declared:
            raise ValueError(f"condition references undeclared indicator '{name}'")
        if period is not None and declared[name][0] != period:
            raise ValueError(f"condition period {period} for '{name}' != declared {declared[name][0]}")

    # Exit config: (tp_mode, tp_value), (sl_mode, sl_value), max_bars.
    tp_spec = sl_spec = None
    max_bars = 0
    for rule in spec.get("exit_rules", []):
        etype = rule.get("exit_type")
        params = rule.get("params", {})
        if etype == "tp" and tp_spec is None:
            tp_spec = (params.get("mode", "percent"), float(params.get("value", 0.0)))
        elif etype == "stop" and sl_spec is None:
            sl_spec = (params.get("mode", "percent"), float(params.get("value", 0.0)))
        elif etype == "time":
            max_bars = max(max_bars, int(params.get("bars", 0)))

    # Freeze what the strategy class needs (no closure-over-later-mutation).
    ind_defs = list(declared.items())
    spec_json = json.dumps(spec, default=str)

    class StrategyClass(bt.Strategy):
        params = (
            ("start_date", None),
            ("warmup_bars", 300),
            ("size", size_oz),
            ("spec", spec_json),
            ("swap_enabled", True),
            ("swap_rate_long_per_lot", -71.50),
            ("swap_rate_short_per_lot", 32.50),
        )

        def __init__(self):
            self._inds: Dict[str, Any] = {}
            for name, (period, params) in ind_defs:
                self._inds[name] = _ind(self, name, period, params)
            self._in_market = False
            self._entry_side = 0
            self._entry_price = 0.0
            self._entry_date = None
            self._bars_held = 0
            # Swap/rollover accounting (charged once per trading day held,
            # Wed/Fri triple, per manifest rates; 1 lot = 100 oz).
            self._swap_day: Optional[Any] = None
            self._total_swap = 0.0
            self._swap_for_trade = 0.0
            # Hold the ACTUAL order objects returned by buy()/close() so they
            # can be passed to self.cancel() (the broker's pending list holds
            # exactly these objects). In backtrader once-mode the wrappers
            # delivered to notify_order are re-created per status, so we match
            # notifications by the stable order.ref.
            self._entry_order: Optional[Any] = None
            self._entry_ref: Optional[int] = None
            self._tp_order: Optional[Any] = None
            self._tp_ref: Optional[int] = None
            self._sl_order: Optional[Any] = None
            self._sl_ref: Optional[int] = None
            self._close_order: Optional[Any] = None
            self._close_ref: Optional[int] = None
            self._highest = 0.0
            self._lowest = 1e18
            self.manual_trade_log: List[Dict[str, Any]] = []

        def _charge_swap(self):
            """Charge overnight swap once per trading day a position is held.
            Wed/Fri carry a triple charge (they absorb the weekend/3-day
            rollover). 1 standard lot = 100 oz; size is in oz."""
            if not self.p.swap_enabled:
                return
            try:
                dt = self.data.datetime.date(0)
            except Exception as e:
                self._dt_err = repr(e)
                return
            if self._swap_day is None:
                # First bar of the trade: no overnight held yet on entry day.
                self._swap_day = dt
                return
            if dt == self._swap_day:
                return  # same day already charged
            # New trading day: charge for the night(s) held.
            weekday = dt.weekday()
            if weekday >= 5:
                self._swap_day = dt
                return
            multiplier = 3 if weekday in (2, 4) else 1
            lots = self.p.size / 100.0
            rate = self.p.swap_rate_long_per_lot if self._entry_side > 0 else self.p.swap_rate_short_per_lot
            swap = lots * rate * multiplier
            if swap != 0.0:
                self.broker.add_cash(swap)
                self._swap_for_trade += swap
                self._total_swap += swap
            self._swap_day = dt

        def _values(self) -> Dict[str, Any]:
            vals: Dict[str, Any] = {}
            for name, ind in self._inds.items():
                try:
                    vals[name] = ind
                except Exception:
                    vals[name] = None
            return vals

        def _eval_entry(self, primitives: List[Dict[str, Any]]) -> bool:
            close = self.data.close[0]
            if close is None:
                return False
            return evaluate_condition(primitives, close, self._values())

        def next(self):
            if self.p.warmup_bars and len(self) < self.p.warmup_bars:
                return

            if self._in_market:
                self._bars_held += 1
                cp = self.data.close[0]
                if cp > self._highest:
                    self._highest = cp
                if cp < self._lowest:
                    self._lowest = cp
                self._charge_swap()
                if max_bars > 0 and self._bars_held >= max_bars:
                    self._exit("time_exit", market=True)
                return

            for direction, primitives in parsed_entries:
                if not self._eval_entry(primitives):
                    continue
                if direction == "long":
                    self._entry_side = 1
                    self._entry_order = self.buy(size=self.p.size)
                else:
                    self._entry_side = -1
                    self._entry_order = self.sell(size=self.p.size)
                self._entry_ref = self._entry_order.ref if self._entry_order else None
                self._in_market = True
                self._entry_price = 0.0
                self._bars_held = 0
                self._swap_for_trade = 0.0
                self._swap_day = None  # reset per-trade swap accounting
                self._highest = self.data.high[0]
                self._lowest = self.data.low[0]
                break

        def _resolve_level(self, side_spec, entry_price: float, side: int) -> float:
            mode, value = side_spec
            is_tp = side_spec is tp_spec
            if mode == "percent":
                if is_tp:
                    return entry_price * (1.0 + value) if side > 0 else entry_price * (1.0 - value)
                return entry_price * (1.0 - value) if side > 0 else entry_price * (1.0 + value)
            if mode == "atr":
                atr_line = self._inds.get("atr")
                if atr_line is None:
                    return 0.0
                atr_val = atr_line[0]
                if atr_val is None or atr_val <= 0:
                    return 0.0
                if is_tp:
                    return entry_price + value * atr_val if side > 0 else entry_price - value * atr_val
                return entry_price - value * atr_val if side > 0 else entry_price + value * atr_val
            return 0.0

        def _place_exits(self, entry_price: float, side: int):
            """Place real TP/SL pending orders (fill when the bar range
            crosses the level, from the next bar onward)."""
            if tp_spec:
                tp_price = self._resolve_level(tp_spec, entry_price, side)
                if tp_price > 0:
                    self._tp_order = bt.Strategy.close(self, size=self.p.size, exectype=bt.Order.Limit, price=tp_price)
                    self._tp_ref = self._tp_order.ref if self._tp_order else None
            if sl_spec:
                sl_price = self._resolve_level(sl_spec, entry_price, side)
                if sl_price > 0:
                    self._sl_order = bt.Strategy.close(self, size=self.p.size, exectype=bt.Order.Stop, price=sl_price)
                    self._sl_ref = self._sl_order.ref if self._sl_order else None

        def _cancel_held(self, order, ref_attr):
            """Cancel a held order object if it is still open."""
            if order is None:
                return
            try:
                self.cancel(order)
            except Exception:
                pass

        def _exit(self, reason: str, market: bool = False):
            """Cancel pendings and market-close (market close always fills
            on the next bar; guarantees the position never stays stuck)."""
            if self._tp_order is not None:
                self._cancel_held(self._tp_order, "tp")
            if self._sl_order is not None:
                self._cancel_held(self._sl_order, "sl")
            self._tp_order = None
            self._tp_ref = None
            self._sl_order = None
            self._sl_ref = None
            if market and self.position:
                self._close_order = bt.Strategy.close(self)
                self._close_ref = self._close_order.ref if self._close_order else None

        def notify_order(self, order):
            if order.owner is not self:
                return
            ref = order.ref
            if order.status == order.Completed:
                if ref == self._entry_ref and self._entry_ref is not None:
                    # Entry filled: place real TP/SL this bar.
                    self._entry_order = None
                    self._entry_ref = None
                    self._entry_price = order.executed.price
                    self._entry_date = str(self.data.datetime.date(0))
                    self._highest = self.data.high[0]
                    self._lowest = self.data.low[0]
                    self._place_exits(order.executed.price, self._entry_side)
                elif ref == self._tp_ref and self._tp_ref is not None:
                    self._log_trade("tp_hit", order.executed.price, order)
                elif ref == self._sl_ref and self._sl_ref is not None:
                    self._log_trade("sl_hit", order.executed.price, order)
                elif ref == self._close_ref and self._close_ref is not None:
                    self._log_trade("market_close", order.executed.price, order)
            elif order.status in (bt.Order.Canceled, bt.Order.Margin, bt.Order.Rejected):
                if ref == self._entry_ref and self._entry_ref is not None:
                    # Entry never filled: abort this attempt.
                    self._entry_order = None
                    self._entry_ref = None
                    self._in_market = False
                    self._entry_side = 0

        def _log_trade(self, reason: str, exit_price: float, order: Any):
            # A position just closed: cancel ANY remaining pending orders
            # (the surviving side of the TP/SL pair). Stale pending orders
            # from a closed trade would otherwise fire later and close a
            # NEWER position at the wrong price — a correctness violation.
            self._cancel_held(self._tp_order, "tp")
            self._cancel_held(self._sl_order, "sl")
            self._cancel_held(self._close_order, "close")
            entry_price = self._entry_price
            side = self._entry_side
            size = self.p.size
            if side > 0:
                pnl_gross = (exit_price - entry_price) * size
            else:
                pnl_gross = (entry_price - exit_price) * size
            comm = order.executed.comm if order is not None else 0.0
            swap = self._swap_for_trade  # negative = cost paid
            pnl_net = pnl_gross - comm + swap
            trade_value = abs(entry_price * size)
            pnl_pct = (pnl_net / trade_value * 100.0) if trade_value else 0.0
            mafe = (self._lowest - entry_price) * size if side > 0 else (entry_price - self._highest) * size
            mfe = (self._highest - entry_price) * size if side > 0 else (entry_price - self._lowest) * size
            self.manual_trade_log.append({
                "entry_date": self._entry_date,
                "exit_date": str(self.data.datetime.date(0)),
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "trade_size": size,
                "exit_type": reason,
                "pnl_gross": round(pnl_gross, 4),
                "pnl_net": round(pnl_net, 4),
                "pnl_percent": round(pnl_pct, 4),
                "commission_paid": round(comm, 4),
                "swap_paid": round(swap, 4),
                "bars_held": self._bars_held,
                "mafe": round(mafe, 4),
                "mfe": round(mfe, 4),
            })
            self._in_market = False
            self._entry_side = 0
            self._entry_price = 0.0
            self._entry_date = None
            self._bars_held = 0
            self._swap_for_trade = 0.0
            self._swap_day = None
            self._entry_order = None
            self._entry_ref = None
            self._tp_order = None
            self._tp_ref = None
            self._sl_order = None
            self._sl_ref = None
            self._close_order = None
            self._close_ref = None

        def stop(self):
            """End of data: any open position is marked to the final close
            (realized for reporting; no bar remains to fill a market order)."""
            if self.position.size != 0:
                final_close = float(self.data.close[0])
                entry_price = self._entry_price
                side = self._entry_side
                size = self.p.size
                if side > 0:
                    pnl_gross = (final_close - entry_price) * size
                else:
                    pnl_gross = (entry_price - final_close) * size
                mafe = (self._lowest - entry_price) * size if side > 0 else (entry_price - self._highest) * size
                mfe = (self._highest - entry_price) * size if side > 0 else (entry_price - self._lowest) * size
                self.manual_trade_log.append({
                    "entry_date": self._entry_date,
                    "exit_date": str(self.data.datetime.date(0)),
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(final_close, 4),
                    "trade_size": size,
                    "exit_type": "end_of_data",
                    "pnl_gross": round(pnl_gross, 4),
                    "pnl_net": round(pnl_gross, 4),
                    "pnl_percent": round((pnl_gross / abs(entry_price * size) * 100.0) if entry_price * size else 0.0, 4),
                    "commission_paid": 0.0,
                    "bars_held": self._bars_held,
                    "mafe": round(mafe, 4),
                    "mfe": round(mfe, 4),
                })
            self._in_market = False

    StrategyClass.__name__ = class_name
    return StrategyClass


def compile_spec(spec: Dict[str, Any]) -> Type[bt.Strategy]:
    return build_strategy_class(spec)
