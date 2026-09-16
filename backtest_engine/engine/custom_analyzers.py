"""
Custom Analyzers for the Backtest Engine v2.

Provides specialized analyzers that produce per-trade diagnostics
tailored for the LLM Critic.

Price correctness rule: per-leg prices come from order.executed.price
(captured in notify_order), NOT from trade.price (which is the AVERAGE
entry price and was previously mis-reported as BOTH entry and exit price).
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

import backtrader as bt


class CriticAnalyzer(bt.Analyzer):
    """
    Captures every closed trade — both LONG and SHORT — with real per-leg
    diagnostics (entry/exit prices, commissions, MAFE/MFE).
    """

    def __init__(self):
        self.trades: List[Dict[str, Any]] = []
        self._max_position_size: float = 0.0

        # Per-trade tracking
        self._entry_price: Optional[float] = None
        self._exit_price: Optional[float] = None
        self._entry_size: Optional[int] = None
        self._entry_date: Optional[datetime] = None
        self._highest_price: Optional[float] = None
        self._lowest_price: Optional[float] = None
        self._commission: float = 0.0
        self._open_entry_orders = 0

    def notify_order(self, order):
        """Capture per-leg executed prices (entry and exit)."""
        if order.status == order.Completed:
            self._commission += order.executed.comm
            # Determine if this is an entry or exit leg.
            pos = self.strategy.getposition(order.data)
            # An entry leg is one that opens/moves the position in its
            # direction; an exit leg reduces or reverses it. Simple robust
            # heuristic: if we had no tracked entry yet, this is entry.
            if self._entry_price is None:
                self._entry_price = order.executed.price
                self._entry_size = order.executed.size
                self._entry_date = self._get_current_datetime()
                self._highest_price = order.executed.price
                self._lowest_price = order.executed.price
                self._commission = 0.0
            else:
                # exit leg
                self._exit_price = order.executed.price
        elif order.status == order.Canceled:
            pass

    def notify_trade(self, trade):
        """Finalize a closed trade (trade.status == Closed).

        When a trade fully closes, ``trade.size`` is 0, so the trade's size
        and direction must come from the entry leg captured in
        ``notify_order`` (``self._entry_size``, which is signed: +buy / -sell),
        not from ``trade.size``.
        """
        if trade.status != bt.Trade.Closed:
            return

        entry_price = self._entry_price if self._entry_price is not None else trade.price
        exit_price = self._exit_price if self._exit_price is not None else trade.price
        # A fully closed trade has trade.size == 0; use the captured entry size.
        size = abs(self._entry_size) if self._entry_size else (abs(trade.size) if trade.size else 0)
        is_long = bool(self._entry_size and self._entry_size > 0)

        # Fallback if per-leg capture missed (should be rare).
        if entry_price is None or exit_price is None or size == 0:
            self._reset()
            return

        trade_value = abs(entry_price * size)
        # trade.pnlcomm = PnL net of commission (broker's own numbers).
        pnl_net = trade.pnlcomm
        pnl_gross = trade.pnl
        pnl_pct = (pnl_net / trade_value * 100.0) if trade_value else 0.0

        # MAFE/MFE — direction-aware, positive magnitude, from the tracked
        # price excursion (consistent with the compiled-strategy convention).
        mafe = 0.0
        mfe = 0.0
        if self._highest_price is not None and self._lowest_price is not None:
            if is_long:
                mafe = max(entry_price - self._lowest_price, 0.0)
                mfe = max(self._highest_price - entry_price, 0.0)
            else:  # short
                mafe = max(self._highest_price - entry_price, 0.0)
                mfe = max(entry_price - self._lowest_price, 0.0)

        self.trades.append({
            "entry_date": str(self._entry_date) if self._entry_date else None,
            "exit_date": str(self._get_current_datetime()),
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "trade_size": size,
            "pnl_gross": round(pnl_gross, 4),
            "pnl_net": round(pnl_net, 4),
            "pnl_percent": round(pnl_pct, 4),
            "commission_paid": round(self._commission, 4),
            "mafe": round(mafe, 4),
            "mfe": round(mfe, 4),
        })
        self._reset()

    def next(self):
        """Track price excursion while a position is open."""
        pos = self.strategy.broker.getposition(self.strategy.data)
        self._max_position_size = max(self._max_position_size, abs(pos.size))
        if pos.size != 0 and self._entry_price is not None:
            cp = self.strategy.data.close[0]
            if self._highest_price is None or cp > self._highest_price:
                self._highest_price = cp
            if self._lowest_price is None or cp < self._lowest_price:
                self._lowest_price = cp

    def _reset(self):
        self._entry_price = None
        self._exit_price = None
        self._entry_size = None
        self._entry_date = None
        self._highest_price = None
        self._lowest_price = None
        self._commission = 0.0

    def _get_current_datetime(self) -> datetime:
        try:
            return self.strategy.datas[0].datetime.datetime(0)
        except Exception:
            return datetime.utcnow()

    def get_analysis(self):
        return {
            "trades": self.trades,
            "max_position_size": self._max_position_size,
        }
